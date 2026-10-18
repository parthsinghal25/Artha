"""Antigravity CLI adapter (official `agy` headless mode, the participant's personal Google AI Pro sign-in).

The CLI is the inference transport only; the host keeps stage prompts, permitted evidence, validation and
publication. One GenRequest = one headless invocation in a FRESH, EMPTY, host-made workspace:

* Credentials stay in the OS keyring under the CLI's own sign-in; nothing here reads, copies or prints them.
* Isolation is enforced, not prompted: every invocation's workspace holds only `.agents/hooks.json` whose
  PreToolUse gate (`gate.py`) DENIES every agent tool except the harness's own `finish` (the CLI's structured-output
  terminal step) and logs each attempt; slash-command/skill expansion is disabled; the terminal sandbox is on;
  `allowNonWorkspaceAccess` stays false. The stream's `init` (model, tool list, cwd) and every tool step are recorded
  in a per-invocation trace, and any non-`finish` tool that *executed* fails the call.
* The model slug is pinned (`--model`, default `gemini-3.8-flash-low`) with `--effort low`; the init event must echo
  the same model or the call is refused (no silent substitution). The CLI does not expose a returned model version.
* Text-only, single-turn, schema-shaped stages only. Requests carrying native function declarations or media are
  refused (ProviderUnavailable): the CLI's agent harness cannot transport host-dispatched function calls.
* Schema mode `prompt` (default) states the host's model-facing JSON Schema in the prompt and the host validates
  the reply exactly as for every other provider (1 model turn). Mode `cli` passes it as `--json-schema` (the CLI's
  finish-step structured output; ≈2 model turns per invocation).
* Terminal status is parsed from the `result` event (SUCCESS required; exit code alone is not trusted). Quota/limit
  errors raise QuotaExhausted → the run pauses; there is no paid fallback (`useG1Credits` must be false, and an
  operator attestation that AI Credit Overages = Never is required).
* One invocation may contain several internal model turns: `num_turns` and the summed usage are recorded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .base import GenRequest, GenResult, OutputInvalid, ProviderUnavailable, QuotaExhausted, TransientError, Usage, \
    extract_json

PROVIDER = "antigravity-cli"
HARNESS_VERSION = "sdb-agy-harness-1.0.0"
AGY_SETTINGS = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"

GATE_PY = """import json, sys
p = json.load(sys.stdin)
name = (p.get("toolCall") or {}).get("name")
with open("tool-attempts.jsonl", "a") as f:
    f.write(json.dumps({"name": name, "stepIdx": p.get("stepIdx"), "model": p.get("modelName")}) + "\\n")
print(json.dumps({"decision": "allow"} if name == "finish" else
                 {"decision": "deny", "reason": "tool use is disabled by the host; reply only with the final JSON"}))
"""
HOOKS = {"sdb-host-tool-gate": {"PreToolUse": [{"matcher": "*", "hooks": [
    {"type": "command", "command": "/usr/bin/python3 -I gate.py", "timeout": 10}]}]}}
PREAMBLE = ("You are being used as a text-only JSON function by a host analysis pipeline. Do not use any tools, "
            "files, commands, browsing or web search: everything you may use is in this message. Reply with exactly "
            "one JSON object and nothing else.")
QUOTA_RX = re.compile(r"quota|rate.?limit|resource.?exhausted|\b429\b|limit (?:reached|exceeded)|usage limit|"
                      r"out of credits|exhausted", re.I)
AUTH_RX = re.compile(r"authenticat|sign.?in|log.?in required|unauthori[sz]ed|permission denied", re.I)

_children: set[subprocess.Popen] = set()
_children_lock = threading.Lock()


def terminate_children() -> int:
    """Kill every live CLI invocation of this process (cancellation on SIGTERM / worker stop)."""
    n = 0
    with _children_lock:
        for p in list(_children):
            try:
                os.killpg(p.pid, signal.SIGKILL)     # a CLI hung in its own shutdown ignores SIGTERM
                n += 1
            except (ProcessLookupError, PermissionError):
                pass
    return n


def harness_sha256() -> str:
    return hashlib.sha256((GATE_PY + json.dumps(HOOKS, sort_keys=True) + PREAMBLE + HARNESS_VERSION)
                          .encode()).hexdigest()


def cli_settings_check() -> dict:
    """Non-secret CLI settings that bound spending/access (the settings file holds no credentials)."""
    try:
        s = json.loads(AGY_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return {"useG1Credits": bool(s.get("useG1Credits", False)),
            "allowNonWorkspaceAccess": bool(s.get("allowNonWorkspaceAccess", False)),
            "enableTerminalSandbox": bool(s.get("enableTerminalSandbox", False))}


def parse_usage_table(text: str) -> dict:
    """`agy -p /usage` lines: '<group>\\t<limit name>\\t<pct>%\\t<reset>' → {group: {limit: {remaining_pct, reset}}}."""
    out: dict = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 3 and parts[2].endswith("%"):
            try:
                pct = float(parts[2].rstrip("%"))
            except ValueError:
                continue
            out.setdefault(parts[0], {})[parts[1]] = {"remaining_pct": pct, "reset": parts[3] if len(parts) > 3 else None}
    return out


class AgyProvider:
    name = PROVIDER

    def __init__(self, model: str | None = None, *, binary: str | None = None, work_root: str | None = None,
                 trace_dir: Path | None = None, attest_overages_never: bool | None = None):
        self.binary = binary or os.environ.get("SDB_AGY_BIN") or shutil.which("agy") or \
            str(Path.home() / ".local" / "bin" / "agy")
        if not Path(self.binary).is_file():
            raise ProviderUnavailable("Antigravity CLI (agy) is not installed")
        attested = attest_overages_never if attest_overages_never is not None else \
            os.environ.get("SDB_AGY_OVERAGES_NEVER_ATTESTED", "0").lower() in ("1", "true", "yes")
        if not attested:
            raise ProviderUnavailable("AI Credit Overages = Never is not attested "
                                      "(set SDB_AGY_OVERAGES_NEVER_ATTESTED=1 only after checking /credits)")
        settings = cli_settings_check()
        if settings["useG1Credits"]:
            raise ProviderUnavailable("agy settings useG1Credits=true would spend AI credits after quota; refused")
        if settings["allowNonWorkspaceAccess"]:
            raise ProviderUnavailable("agy settings allowNonWorkspaceAccess=true; refused (isolation)")
        self.model = model or os.environ.get("SDB_AGY_MODEL") or "gemini-3.8-flash-low"
        self.effort = os.environ.get("SDB_AGY_EFFORT", "low")
        self.schema_mode = os.environ.get("SDB_AGY_SCHEMA_MODE", "prompt")
        if self.schema_mode not in ("prompt", "cli"):
            raise ProviderUnavailable(f"unknown SDB_AGY_SCHEMA_MODE {self.schema_mode}")
        self.timeout_s = float(os.environ.get("SDB_AGY_TIMEOUT_S", "180"))
        self.max_turns = int(os.environ.get("SDB_AGY_MAX_TURNS", "4"))
        self.quota_floor_pct = float(os.environ.get("SDB_AGY_QUOTA_FLOOR_PCT", "3"))
        self.quota_check_every = int(os.environ.get("SDB_AGY_QUOTA_CHECK_EVERY", "25"))
        self.work_root = Path(work_root or os.environ.get("SDB_AGY_WORK_ROOT") or Path.home() / ".sdb-agy-work")
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.trace_dir = Path(trace_dir or os.environ.get("SDB_AGY_TRACE_DIR") or self.work_root / "traces")
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.cli_version = self._version()
        self.thinking_level = self.effort
        self._calls = 0
        self.last_quota: dict | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _version(self) -> str:
        try:
            r = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderUnavailable(f"agy --version failed: {type(exc).__name__}") from exc
        return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "unknown"

    def identity(self) -> dict:
        return {"provider": PROVIDER, "model": self.model, "effort": self.effort, "cli_version": self.cli_version,
                "schema_mode": self.schema_mode, "harness": HARNESS_VERSION, "harness_sha256": harness_sha256(),
                "account_tier": "Google AI Pro (personal sign-in; tier shown by the CLI banner, not machine-verified)",
                "model_version_exposed": False}

    def quota(self) -> dict:
        """Remaining plan quota via the CLI's own `/usage` (no model call)."""
        ws = self._workspace("quota")
        try:
            r = subprocess.run([self.binary, "-p", "/usage", "--print-timeout", "60s"], cwd=ws, capture_output=True,
                               text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return {"error": "usage check timed out"}
        table = parse_usage_table(r.stdout)
        self.last_quota = {"at": time.time(), "table": table}
        self._trace({"kind": "quota", "table": table})
        return table

    def check_quota(self) -> None:
        table = self.quota()
        gem = table.get("Gemini Models") or {}
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for limit, v in gem.items():
            try:
                reset = datetime.fromisoformat((v.get("reset") or "").replace("Z", "+00:00"))
            except ValueError:
                reset = None
            if reset is not None and reset <= now:
                continue        # cached reading from before its own reset time: stale, not evidence of exhaustion
            if v["remaining_pct"] <= self.quota_floor_pct:
                scope = "week" if "week" in limit.lower() else "five_hour"
                raise QuotaExhausted(f"Antigravity {limit} at {v['remaining_pct']}% (floor {self.quota_floor_pct}%), "
                                     f"resets {v['reset']}", scope="day", retry_after=None) from None

    def _workspace(self, tag: str) -> Path:
        ws = self.work_root / time.strftime("%Y%m%d") / f"{tag}-{uuid.uuid4().hex[:12]}"
        (ws / ".agents").mkdir(parents=True)
        (ws / ".agents" / "gate.py").write_text(GATE_PY, encoding="utf-8")
        (ws / ".agents" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")
        return ws

    def _trace(self, rec: dict) -> None:
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pid": os.getpid(), **rec}
        with self._lock:
            with open(self.trace_dir / f"agy-{time.strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _prompt(self, req: GenRequest) -> str:
        users = [m for m in req.messages if m.role == "user"]
        if len(req.messages) != 1 or len(users) != 1:
            raise ProviderUnavailable("agy adapter transports single-turn requests only")
        m = users[0]
        if m.tool_results or m.tool_calls or m.native is not None or any(p.kind != "text" for p in m.parts):
            raise ProviderUnavailable("agy adapter transports text-only requests (no media, tools or native state)")
        body = "".join(p.text or "" for p in m.parts)
        out = [PREAMBLE, "", "=== INSTRUCTIONS ===", req.system.strip(), "", "=== INPUT ===", body.strip()]
        if req.response_schema is not None and self.schema_mode == "prompt":
            out += ["", "=== OUTPUT CONTRACT ===",
                    "Reply with exactly one JSON object that validates against this JSON Schema (no prose, no code "
                    "fence):", json.dumps(req.response_schema, ensure_ascii=False, separators=(",", ":"))]
        return "\n".join(out)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    # ------------------------------------------------------------------ call
    def generate(self, req: GenRequest) -> GenResult:
        if req.tools:
            raise ProviderUnavailable("native function calls cannot be transported faithfully through the agy "
                                      "harness; stage refused")
        with self._lock:
            self._calls += 1
            due = self.quota_check_every > 0 and (self._calls == 1 or self._calls % self.quota_check_every == 0)
        if due:
            self.check_quota()
        prompt = self._prompt(req)
        ws = self._workspace(re.sub(r"[^a-z0-9_]+", "_", req.role.lower())[:24])
        cmd = [self.binary, "-p", prompt, "--model", self.model, "--effort", self.effort,
               "--output-format", "stream-json", "--print-timeout", f"{int(self.timeout_s)}s",
               "--disable-slash-commands", "--sandbox"]
        if req.response_schema is not None and self.schema_mode == "cli":
            sp = ws / ".agents" / "schema.json"
            sp.write_text(json.dumps(req.response_schema), encoding="utf-8")
            cmd += ["--json-schema", str(sp)]
        start = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
        except OSError as exc:
            raise ProviderUnavailable(f"agy could not start: {type(exc).__name__}") from exc
        with _children_lock:
            _children.add(proc)
        # Stream the events: the CLI can finish its conversation (terminal `result` event emitted) yet hang in its
        # own shutdown without closing stdout, so the result event — not EOF/exit — ends the invocation.
        lines: list[str] = []
        err_chunks: list[str] = []
        got_result = threading.Event()

        def _read_out():
            for ln in proc.stdout:
                lines.append(ln)
                if '"event": "result"' in ln or '"event":"result"' in ln:
                    got_result.set()
            got_result.set()

        def _read_err():
            err_chunks.append(proc.stderr.read())
        readers = [threading.Thread(target=_read_out, daemon=True), threading.Thread(target=_read_err, daemon=True)]
        for t in readers:
            t.start()
        lingered = False
        try:
            if not got_result.wait(self.timeout_s + 30):
                self._kill(proc)
                self._trace({"kind": "call", "role": req.role, "workspace": str(ws), "status": "host_timeout"})
                raise TransientError(f"agy invocation exceeded {self.timeout_s + 30:.0f}s") from None
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                lingered = True                       # result already received; the CLI hung on exit
                self._kill(proc)
        finally:
            if proc.poll() is None:
                self._kill(proc)
            with _children_lock:
                _children.discard(proc)
        readers[0].join(5)
        readers[1].join(1)
        stdout, stderr = "".join(lines), "".join(err_chunks)
        latency = int((time.monotonic() - start) * 1000)
        init, result, steps = None, None, []
        for line in stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "init":
                init = ev.get("init") or {}
            elif ev.get("event") == "result":
                result = ev.get("result") or {}
            elif ev.get("event") == "step_update":
                s = ev.get("step_update") or {}
                if s.get("state") in ("DONE", "ERROR") or s.get("tool_info"):
                    steps.append({k: s.get(k) for k in ("step_index", "step_type", "state", "tool_name")})
        attempts = []
        af = ws / ".agents" / "tool-attempts.jsonl"
        if af.is_file():
            attempts = [json.loads(x) for x in af.read_text(encoding="utf-8").splitlines() if x.strip()]
        executed = sorted({s["tool_name"] for s in steps if s.get("step_type") == "tool" and s.get("tool_name")
                           and s["tool_name"] != "finish" and s.get("state") == "DONE"})
        usage_raw = (result or {}).get("usage") or {}
        rec = {"kind": "call", "role": req.role, "prompt_version": req.prompt_version, "workspace": str(ws),
               "exit_code": proc.returncode, "conversation_id": (result or {}).get("conversation_id"),
               "init_model": (init or {}).get("model"), "init_cwd": (init or {}).get("cwd"),
               "init_tool_count": len((init or {}).get("tools") or []),
               "permission_mode": (init or {}).get("permission_mode"),
               "status": (result or {}).get("status"), "num_turns": (result or {}).get("num_turns"),
               "duration_seconds": (result or {}).get("duration_seconds"), "usage": usage_raw,
               "tool_attempts": [a.get("name") for a in attempts], "tools_executed_non_finish": executed,
               "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "prompt_chars": len(prompt),
               "latency_ms": latency, "killed_after_result": lingered, "error": ((result or {}).get("error") or stderr.strip()[-300:] or None)
               if (result or {}).get("status") != "SUCCESS" else None}
        self._trace(rec)
        if result is None:
            text = stderr.strip()[-400:]
            if AUTH_RX.search(text):
                raise ProviderUnavailable("agy authentication required (sign in interactively once)")
            if QUOTA_RX.search(text):
                raise QuotaExhausted(f"agy: {text[:200]}", scope="day")
            raise TransientError(f"agy produced no result event (exit {proc.returncode})")
        if init is not None and init.get("model") and init["model"] != self.model:
            raise ProviderUnavailable(f"agy ran model {init['model']} instead of pinned {self.model}")
        if executed:
            raise OutputInvalid(f"agy harness executed tools {executed}; output discarded (isolation)")
        status = result.get("status")
        if status != "SUCCESS":
            err = str(result.get("error") or stderr or "")[:400]
            if QUOTA_RX.search(err):
                raise QuotaExhausted(f"agy {status}: {err[:200]}", scope="day")
            if AUTH_RX.search(err):
                raise ProviderUnavailable(f"agy {status}: authentication")
            if status in ("CANCELED", "INTERRUPTED", "ERROR"):
                raise TransientError(f"agy terminal status {status}: {err[:160]}")
            raise OutputInvalid(f"agy terminal status {status}")
        if (result.get("num_turns") or 0) > self.max_turns:
            raise OutputInvalid(f"agy used {result.get('num_turns')} turns (> {self.max_turns}); discarded")
        usage = Usage(input_tokens=usage_raw.get("input_tokens"), cached_tokens=usage_raw.get("cache_read_tokens"),
                      thinking_tokens=usage_raw.get("thinking_tokens"), output_tokens=usage_raw.get("output_tokens"),
                      total_tokens=usage_raw.get("total_tokens"), source="provider" if usage_raw else "unknown")
        data = None
        text = result.get("response") or ""
        if req.response_schema is not None:
            if self.schema_mode == "cli" and result.get("structured_output") is not None:
                data = result["structured_output"]
                text = json.dumps(data, ensure_ascii=False)
            else:
                data = extract_json(text)
        res = GenResult(provider=PROVIDER, model=self.model, text=text, data=data, usage=usage, latency_ms=latency,
                        finish_reason=f"{status}; turns={result.get('num_turns')}", model_version=None)
        res.invocation = {"conversation_id": result.get("conversation_id"), "num_turns": result.get("num_turns"),
                          "tool_attempts_denied": [a.get("name") for a in attempts if a.get("name") != "finish"]}
        return res
