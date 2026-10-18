"""Model gateway: provider selection, shared pacing reservations, bounded retries, usage ledger, fallback signals.

Fallback policy (operator-visible, measurable; R08):
* Provider choice happens only at a sealed run boundary (`choose_provider`). A run never switches provider; the
  run records the provider fingerprint (model + exact local weights/config/runtime hashes, or API model + SDK).
* `choose_provider` refuses Gemini without key + Free-tier attestation, and (for `auto`) when the projected ETA of
  the worklist under the configured RPM/RPD exceeds `fallback_max_eta_seconds`.
* Inside a run: transient 429/5xx/transport errors get at most MAX_TRANSPORT_RETRIES jittered retries. A daily quota,
  repeated retry exhaustion, an over-long pacing wait, or provider access denial/missing model raise
  `FallbackRequired` (SustainedQuota / ProviderUnavailable); the runner closes the run and, for `auto`, seals a NEW
  run on the local fallback. No key/account rotation and no paid upgrade is ever attempted.
* Pacing reservations live in SQLite (`rate_reservations`), so parallel workers/processes share RPM/TPM/RPD.
  Each attempt is written to the usage ledger as `in_flight` BEFORE dispatch and completed afterwards, so crashes
  leave visible spent attempts. Unknown usage stays NULL (never zero).
* Requests whose conservative token estimate exceeds the TPM limit are refused up front (`RequestTooLarge`).
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..canonical import canonical_sha256, utc_now_iso
from ..config import Settings
from .base import (GenRequest, GenResult, ModelError, OutputInvalid, ProviderUnavailable, QuotaExhausted,
                   TransientError)

MAX_TRANSPORT_RETRIES = 2


class CallTimeout(ProviderUnavailable):
    """A model call exceeded its wall-clock deadline. The hung call cannot be safely re-entered (local weights /
    unified memory), so this is a provider-unavailable condition: the task is deferred, never retried in place,
    and a late reply is discarded (the ledger row stays `timeout`)."""


def _with_deadline(fn, req, seconds: float):
    box: dict = {}

    def run():
        try:
            box["res"] = fn(req)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["exc"] = exc
    t = threading.Thread(target=run, name="model-call", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise CallTimeout(f"model call exceeded {seconds:.0f} s deadline")
    if "exc" in box:
        raise box["exc"]
    return box["res"]
MAX_PACING_WAIT_SECONDS = 180
PACIFIC = ZoneInfo("America/Los_Angeles")


class SustainedQuota(ModelError):
    pass


class RequestTooLarge(ModelError):
    pass


FALLBACK_ERRORS = (SustainedQuota, ProviderUnavailable)


def make_provider(name: str, settings: Settings):
    if name == "vertex":
        from .vertex import VertexProvider
        return VertexProvider(settings.vertex_project, settings.vertex_location, settings.vertex_model,
                              budget_path=settings.data_dir / "vertex-budget.json")
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(settings.gemini_model, free_tier_attested=settings.gemini_free_tier_attested)
    if name == "agy":
        from .agy_cli import AgyProvider
        return AgyProvider()
    if name == "gemma":
        from .gemma_mlx import GemmaProvider
        return GemmaProvider(settings.gemma_model_path, settings.gemma_model_id)
    raise ProviderUnavailable(f"unknown provider {name}")


def provider_status(settings: Settings) -> dict:
    return {
        "preference": settings.model_provider,
        "vertex": {"project": settings.vertex_project, "location": settings.vertex_location,
                   "model": settings.vertex_model, "configured": bool(settings.vertex_project),
                   "authentication": "ADC; access not verified"},
        "gemini": {"model": settings.gemini_model, "api_key_configured": settings.gemini_api_key_present,
                   "free_tier_attested": settings.gemini_free_tier_attested,
                   "usable": settings.gemini_api_key_present and settings.gemini_free_tier_attested,
                   "configured_limits": {"rpm": settings.gemini_rpm, "rpd": settings.gemini_rpd, "tpm": settings.gemini_tpm}},
        "gemma": {"model": settings.gemma_model_id, "weights_present": (settings.gemma_model_path / "config.json").is_file()},
    }


def validate_limits(settings: Settings) -> list[str]:
    problems = []
    for k in ("gemini_rpm", "gemini_rpd", "gemini_tpm"):
        if getattr(settings, k) <= 0:
            problems.append(f"{k} must be a positive integer")
    return problems


def projected_eta_seconds(settings: Settings, calls: int, mean_latency_s: float, used_today: int = 0) -> float:
    """Wall-clock ETA of `calls` Gemini requests under configured RPM/RPD (days roll over at Pacific midnight)."""
    rpm, rpd = max(1, settings.gemini_rpm), max(1, settings.gemini_rpd)
    per_min = calls / rpm * 60
    overflow = max(0, calls - max(0, rpd - used_today))
    return max(per_min, calls * mean_latency_s) + (-(-overflow // rpd)) * 86400


def choose_provider(settings: Settings, preference: str, db=None, *, exclude: set[str] = frozenset(),
                    expected_calls: int | None = None, mean_latency_s: float = 20.0) -> tuple[str, str]:
    """Return (provider, reason). Called only when sealing a new run."""
    status = provider_status(settings)
    if preference == "vertex":
        if not settings.vertex_project or not settings.vertex_location or not settings.vertex_model:
            raise ProviderUnavailable("explicit Vertex project/location/model required")
        return "vertex", "explicit standard Vertex ADC selection; no fallback"
    if preference == "agy":                 # explicit only: never part of `auto`, never a silent fallback
        from .agy_cli import AgyProvider
        AgyProvider()                       # raises ProviderUnavailable (not installed / not attested / settings)
        if db is not None:
            record_event(db, "agy", settings, "selected", "explicit agy (Antigravity CLI) selection")
        return "agy", "explicit agy (Antigravity CLI) selection"
    order = {"gemini": ["gemini"], "gemma": ["gemma"], "auto": ["gemini", "gemma"]}[preference]
    reasons = []
    for name in order:
        if name in exclude:
            reasons.append(f"{name} excluded after sustained quota/unavailability in this invocation")
            continue
        if name == "gemini":
            if not status["gemini"]["usable"]:
                reasons.append("gemini unusable: " + ("no API key configured" if not status["gemini"]["api_key_configured"]
                                                      else "Free-tier status not attested"))
                continue
            bad = validate_limits(settings)
            if bad:
                reasons.append("gemini unusable: " + "; ".join(bad))
                continue
            if expected_calls and preference == "auto":
                eta = projected_eta_seconds(settings, expected_calls, mean_latency_s)
                if eta > settings.fallback_max_eta_seconds:
                    reasons.append(f"gemini projected ETA {eta:.0f}s exceeds fallback threshold "
                                   f"{settings.fallback_max_eta_seconds}s")
                    continue
        if name == "gemma" and not status["gemma"]["weights_present"]:
            reasons.append("gemma weights not present")
            continue
        reason = "; ".join(reasons + [f"selected {name}"])
        if db is not None:
            record_event(db, name, settings, "selected", reason)
        return name, reason
    raise ProviderUnavailable("; ".join(reasons) or "no provider available")


def record_event(db, provider: str, settings: Settings, kind: str, detail: str) -> None:
    import os
    model = settings.vertex_model if provider == "vertex" else settings.gemini_model if provider == "gemini" else \
        (os.environ.get("SDB_AGY_MODEL") or "gemini-3.8-flash-low") if provider == "agy" else settings.gemma_model_id
    with db.tx() as c:
        c.execute("INSERT INTO provider_events(provider, model, kind, detail, at) VALUES (?,?,?,?,?)",
                  (provider, model, kind, detail[:500], utc_now_iso()))


def estimate_tokens(req: GenRequest) -> int:
    """Conservative estimate over ALL context: system, text, media, tool specs, schema, tool results and opaque
    native history, plus the output reservation (≈3 chars/token, 300 per media part)."""
    chars = len(req.system)
    media = 0
    for m in req.messages:
        for p in m.parts:
            chars += len(p.text or "")
            media += p.kind != "text"
        for r in m.tool_results:
            chars += len(json.dumps(r.response, ensure_ascii=False, default=str))
        for c in m.tool_calls:
            chars += len(json.dumps(c.args, default=str))
        if m.native is not None:
            chars += len(str(m.native))
    chars += sum(len(json.dumps(t.parameters)) + len(t.description) for t in req.tools)
    if req.response_schema is not None:
        chars += len(json.dumps(req.response_schema))
    return chars // 3 + 300 * media + req.max_output_tokens


class Pacer:
    """Shared RPM/TPM/RPD reservations in SQLite (process-independent). Provider limits remain authoritative."""

    def __init__(self, rpm: int, tpm: int, rpd: int, db, provider: str, max_wait: float = MAX_PACING_WAIT_SECONDS):
        if min(rpm, tpm, rpd) <= 0:
            raise ProviderUnavailable("invalid pacing limits (RPM/TPM/RPD must be positive)")
        self.rpm, self.tpm, self.rpd, self.db, self.provider, self.max_wait = rpm, tpm, rpd, db, provider, max_wait

    def _day_start_epoch(self) -> float:
        now = datetime.now(PACIFIC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def calls_today(self) -> int:
        row = self.db.connect().execute("SELECT COUNT(*) AS n FROM rate_reservations WHERE provider=? AND at_epoch>=?",
                                        (self.provider, self._day_start_epoch())).fetchone()
        return row["n"]

    def reserve(self, est_tokens: int, sleep=time.sleep) -> tuple[int, float]:
        if est_tokens > self.tpm:
            raise RequestTooLarge(f"request estimate {est_tokens} tokens exceeds the TPM limit {self.tpm}")
        waited = 0.0
        while True:
            now = time.time()
            with self.db.tx() as c:
                day = c.execute("SELECT COUNT(*) AS n FROM rate_reservations WHERE provider=? AND at_epoch>=?",
                                (self.provider, self._day_start_epoch())).fetchone()["n"]
                if day >= self.rpd:
                    raise SustainedQuota(f"local RPD budget {self.rpd} reached for today (Pacific)")
                row = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(tokens),0) AS t, MIN(at_epoch) AS first "
                                "FROM rate_reservations WHERE provider=? AND at_epoch>?", (self.provider, now - 60)).fetchone()
                if row["n"] < self.rpm and row["t"] + est_tokens <= self.tpm:
                    cur = c.execute("INSERT INTO rate_reservations(provider, at_epoch, tokens, state) VALUES (?,?,?,?)",
                                    (self.provider, now, est_tokens, "reserved"))
                    return cur.lastrowid, waited
                delay = max(0.5, 60 - (now - row["first"])) if row["first"] else 1.0
            if waited + min(delay, 5.0) > self.max_wait:
                raise SustainedQuota(f"pacing wait would exceed {self.max_wait}s")
            sleep(min(delay, 5.0))
            waited += min(delay, 5.0)


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def local_weights_fingerprint(model_dir: Path) -> dict:
    """Exact local model identity: SHA-256 of every weight shard (cached by size+mtime in a sidecar), config,
    tokenizer, chat template, the HF revision recorded at download, and the runtime package versions."""
    from importlib.metadata import PackageNotFoundError, version
    model_dir = Path(model_dir)
    sidecar = model_dir / ".sdb-fingerprint.json"
    cache = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    files = {}
    for p in sorted(model_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()
        key = f"{p.name}|{st.st_size}|{st.st_mtime_ns}"
        if cache.get(key) is None:
            cache[key] = _file_sha(p)
        files[p.name] = cache[key]
    try:
        sidecar.write_text(json.dumps(cache, indent=1))
    except OSError:
        pass
    trees = model_dir / ".cache" / "huggingface" / "trees"
    revision = sorted(p.stem for p in trees.glob("*.json")) if trees.is_dir() else []
    runtime = {}
    for pkg in ("mlx", "mlx-vlm", "mlx-lm", "transformers"):
        try:
            runtime[pkg] = version(pkg)
        except PackageNotFoundError:
            runtime[pkg] = None
    return {"files": files, "hf_revision": revision, "runtime": runtime}


class ModelGateway:
    def __init__(self, rt, provider_name: str, run_id: str):
        self.rt, self.provider_name, self.run_id = rt, provider_name, run_id
        self.provider = make_provider(provider_name, rt.settings)
        s = rt.settings
        self.pacer = Pacer(s.gemini_rpm, s.gemini_tpm, s.gemini_rpd, rt.db, self.provider.name) \
            if provider_name == "gemini" else None
        self.consecutive_quota = 0
        self.semaphore = threading.Semaphore(max(1, s.model_concurrency))
        self._fingerprint = None
        # optional hook run immediately before EVERY provider attempt (incl. transport retries); a caller's request
        # / wall budget raises here, so no attempt can start once the budget is spent
        self.call_guard = None

    @property
    def model(self) -> str:
        return self.provider.model

    def fingerprint(self) -> dict:
        """Provider identity for RunSpecs and reuse keys (model ID alone is insufficient)."""
        if self._fingerprint is None:
            if self.provider_name == "gemma":
                fp = {"provider": self.provider.name, "model": self.model,
                      "local": local_weights_fingerprint(self.rt.settings.gemma_model_path)}
            elif self.provider_name == "agy":
                fp = dict(self.provider.identity())
            elif self.provider_name == "vertex":
                fp = self.provider.identity()
            else:
                from importlib.metadata import version
                fp = {"provider": self.provider.name, "model": self.model, "sdk": {"google-genai": version("google-genai")},
                      "thinking_level": getattr(self.provider, "thinking_level", None)}
            fp["sha256"] = canonical_sha256({k: v for k, v in fp.items() if k != "sha256"})
            self._fingerprint = fp
        return self._fingerprint

    def config_sha256(self, req: GenRequest) -> str:
        return canonical_sha256({"provider": self.provider.name, "model": self.provider.model,
                                 "temperature": req.temperature, "max_output_tokens": req.max_output_tokens,
                                 "tools": [t.name for t in req.tools], "schema": req.response_schema,
                                 "prompt_version": req.prompt_version})

    def _start(self, task_id: str, attempt: int, req: GenRequest, retries: int, est: int) -> str:
        started = utc_now_iso()
        key = f"{self.run_id}|{task_id}|{attempt}|r{retries}|{started}|{random.random():.6f}"
        with self.rt.db.tx() as c:
            c.execute("INSERT INTO usage_ledger(ledger_key, run_id, task_id, attempt, provider, model, role, status, "
                      "usage_source, retries, config_sha256, prompt_version, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (key, self.run_id, task_id, attempt, self.provider.name, self.provider.model, req.role,
                       "in_flight", f"unknown(estimate {est})", retries, self.config_sha256(req), req.prompt_version,
                       started))
        return key

    def _end(self, key: str, status: str, res: GenResult | None, error: str | None) -> None:
        u = res.usage if res else None
        peak = getattr(res, "peak_memory_bytes", None) if res else None
        with self.rt.db.tx() as c:
            c.execute("UPDATE usage_ledger SET status=?, input_tokens=?, cached_tokens=?, thinking_tokens=?, "
                      "output_tokens=?, total_tokens=?, usage_source=COALESCE(?, usage_source), latency_ms=?, "
                      "error_class=?, ended_at=?, peak_memory_bytes=? WHERE ledger_key=?",
                      (status, u.input_tokens if u else None, u.cached_tokens if u else None,
                       u.thinking_tokens if u else None, u.output_tokens if u else None, u.total_tokens if u else None,
                       u.source if u else None, res.latency_ms if res else None, error, utc_now_iso(), peak, key))

    def call(self, req: GenRequest, *, task_id: str, attempt: int = 1) -> GenResult:
        est = estimate_tokens(req)
        retries = 0
        with self.semaphore:
            while True:
                if self.call_guard is not None:
                    self.call_guard(req)
                if self.pacer:
                    self.pacer.reserve(est)
                key = self._start(task_id, attempt, req, retries, est)
                try:
                    res = _with_deadline(self.provider.generate, req,
                                         float(getattr(self.rt.settings, "model_call_timeout_s", 1200.0)))
                except CallTimeout as exc:
                    self._end(key, "timeout", None, str(exc)[:200])
                    record_event(self.rt.db, self.provider_name, self.rt.settings, "provider_unavailable", str(exc))
                    raise
                except QuotaExhausted as exc:
                    self._end(key, "quota_exhausted", None, f"quota:{exc.scope}")
                    record_event(self.rt.db, self.provider_name, self.rt.settings, "quota_exhausted", str(exc))
                    raise SustainedQuota(f"provider reported {exc.scope} quota exhaustion") from exc
                except TransientError as exc:
                    self._end(key, "transient_error", None, "transient")
                    if retries >= MAX_TRANSPORT_RETRIES:
                        self.consecutive_quota += 1
                        record_event(self.rt.db, self.provider_name, self.rt.settings, "retries_exhausted", str(exc))
                        if self.consecutive_quota >= self.rt.settings.fallback_consecutive_quota_failures:
                            raise SustainedQuota(f"{self.consecutive_quota} consecutive calls exhausted retries") from exc
                        raise
                    retries += 1
                    delay = exc.retry_after if exc.retry_after else (2 ** retries) * 2
                    time.sleep(min(60.0, delay) * (0.8 + 0.4 * random.random()))
                    continue
                except ProviderUnavailable as exc:
                    self._end(key, "ProviderUnavailable", None, str(exc)[:200])
                    record_event(self.rt.db, self.provider_name, self.rt.settings, "provider_unavailable", str(exc))
                    raise
                except OutputInvalid as exc:
                    self._end(key, "OutputInvalid", None, str(exc)[:200])
                    raise
                except Exception as exc:  # noqa: BLE001 - any adapter fault is a bounded computation failure
                    self._end(key, "adapter_error", None, f"{type(exc).__name__}: {str(exc)[:160]}")
                    raise OutputInvalid(f"adapter error {type(exc).__name__}") from exc
                self.consecutive_quota = 0
                self._end(key, "ok", res, None)
                return res
