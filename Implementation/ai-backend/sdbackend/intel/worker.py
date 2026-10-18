"""Resumable incremental update worker (M4): `intel plan|update --as-of D`.

One step = one knowledge view (end of local day D under `demo-held-day-v1`). The plan is deterministic and makes
no model call: it compares each eligible case's current eligible inputs with its head memory and classifies it
`new`, `update` (new/changed narrative evidence), `withdrawal` (retained claims lost their source),
`table_refresh` (only lifecycle/ledger/work rows changed → new memory revision without inference) or `unchanged`.
Not-eligible cases are listed as unassessed. Model work then runs as fenced, leased, idempotent tasks under one
sealed RunSpec, so an interrupted step resumes without duplicating memories or contributions. Explicit request /
wall-time budgets are checked before every model task; exhaustion checkpoints the step (`budget_exhausted`) and a
later identical command resumes it. A model-call timeout cannot stop an in-process local generation, so the worker
records the timeout, releases the lease and exits the process (code 75); the next command resumes on a clean
process. Reads of the API never call this module.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from datetime import date

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..config import BACKEND_ROOT
from ..models.base import TransientError
from ..models.gateway import CallTimeout, FALLBACK_ERRORS, ModelGateway, choose_provider
from ..pipeline import control
from ..policy import permission_revision
from . import memory, temporal
from .corpus import Corpus, case_members, demo_release_view, eligible_cases, native_cases
from .intake import availability_sidecar, crosswalk_for
from .tables import project_release

WORKER_VERSION = "sdb-intel-worker-1.1.0"
DDL = """
CREATE TABLE IF NOT EXISTS intel_heads (case_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, revision INTEGER NOT NULL,
  input_digest TEXT NOT NULL, knowledge_end TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intel_steps (step_id TEXT PRIMARY KEY, view_date TEXT NOT NULL, knowledge_end TEXT NOT NULL,
  state TEXT NOT NULL, run_ids TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intel_registry_head (singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  registry_id TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
EST_CALL_SECONDS = 75.0


def ensure_schema(rt) -> None:
    rt.db.connect().executescript(DDL)


def head(rt, case_id: str) -> dict | None:
    row = rt.db.connect().execute("SELECT memory_id FROM intel_heads WHERE case_id=?", (case_id,)).fetchone()
    return rt.db.get_object("case_memory", row["memory_id"]) if row else None


def heads(rt) -> dict[str, dict]:
    rows = rt.db.connect().execute("SELECT case_id, memory_id FROM intel_heads ORDER BY case_id").fetchall()
    return {r["case_id"]: rt.db.get_object("case_memory", r["memory_id"]) for r in rows}


def _set_head(rt, mem: dict) -> None:
    with rt.db.tx() as c:
        row = c.execute("SELECT revision, memory_id FROM intel_heads WHERE case_id=?", (mem["case_id"],)).fetchone()
        if row is not None and (row["revision"] > mem["revision"] or
                                (row["revision"] == mem["revision"] and row["memory_id"] != mem["memory_id"])):
            return                                    # never move a head backwards or fork it
        c.execute("INSERT OR REPLACE INTO intel_heads VALUES (?,?,?,?,?,?)",
                  (mem["case_id"], mem["memory_id"], mem["revision"], mem["inputs"]["digest"],
                   mem["knowledge_end_exclusive"], utc_now_iso()))


def reconcile_heads(rt) -> list[str]:
    """Deterministic sweep: a memory sealed by a fenced task whose head update was lost (crash between commit and
    head write) is applied now, if it directly succeeds the current head."""
    fixed = []
    rows = rt.db.connect().execute("SELECT output_object_id FROM tasks WHERE task_kind='intel_case_memory' AND "
                                   "state='staged' AND output_object_id IS NOT NULL ORDER BY updated_at").fetchall()
    for r in rows:
        mem = rt.db.get_object("case_memory", r["output_object_id"])
        if mem is None:
            continue
        cur = head(rt, mem["case_id"])
        if (cur is None and mem["revision"] == 1) or (cur and mem["predecessor_id"] == cur["memory_id"]):
            _set_head(rt, mem)
            fixed.append(mem["memory_id"])
    return fixed


# ------------------------------------------------------------------ plan
class Context:
    def __init__(self, rt, corpus: Corpus, view_date: str):
        self.rt, self.corpus = rt, corpus
        self.view_date = date.fromisoformat(view_date)
        self.knowledge_end = temporal.end_of_day(self.view_date)
        self.rv = demo_release_view(rt)
        if self.rv is None:
            raise SystemExit("no served demo release; run `intel intake` first")
        self.tables = project_release(rt, self.rv)
        self.sidecar = availability_sidecar(rt, self.rv.release_id)
        self.crosswalk = crosswalk_for(rt, self.rv.release_id) or {"rows": [], "participants": {}}
        self.natives = native_cases(rt, self.rv)
        self.members = case_members(rt, self.rv)
        self.eligible, self.not_eligible = eligible_cases(rt, self.rv, corpus)

    def in_view(self, case_id: str) -> bool:
        n = self.natives.get(case_id)
        return n is not None and temporal.available(n["envelope"]["available_at"], None, self.knowledge_end)

    def inputs(self, case_id: str) -> memory.CaseInputs:
        return memory.gather(self.rt, self.rv, case_id, self.knowledge_end, tables=self.tables, sidecar=self.sidecar,
                             crosswalk=self.crosswalk, natives=self.natives, members=self.members)


MODEL_ACTIONS = ("new", "update", "withdrawal", "resynthesis")


def plan(ctx: Context) -> dict:
    ensure_schema(ctx.rt)
    reconcile_heads(ctx.rt)
    cases = []
    for cid in ctx.eligible:
        if not ctx.in_view(cid):
            continue
        inp = ctx.inputs(cid)
        cur = head(ctx.rt, cid)
        new_src = [s["data"]["source_id"] for s in inp.narrative]
        if cur is None:
            action = "new" if inp.narrative else "no_narrative_evidence"
            changed, removed = new_src, []
        elif cur["inputs"]["digest"] == inp.input_digest():
            action = "resynthesis" if memory.stale_interpretation(cur) else "unchanged"
            changed, removed = [], []
        elif temporal.parse_instant(cur["knowledge_end_exclusive"]) > ctx.knowledge_end:
            # heads never move backwards: an earlier view is served from its own publication, never re-investigated
            action, changed, removed = "later_head_retained", [], []
        else:
            prior = {s["source_id"]: s["content_sha256"] for s in cur["inputs"]["narrative_sources"]}
            now = {s["data"]["source_id"]: s["data"]["content_sha256"] for s in inp.narrative}
            changed = sorted(k for k, v in now.items() if prior.get(k) != v)
            removed = sorted(set(prior) - set(now))
            action = "update" if changed else "withdrawal" if removed else "table_refresh"
        chars = sum(len(s["data"]["text"]) for s in inp.narrative if s["data"]["source_id"] in set(changed))
        calls = 0 if action in ("unchanged", "table_refresh", "no_narrative_evidence", "later_head_retained") else \
            2 if action == "resynthesis" else (len(memory.packets(inp, [s for s in inp.narrative if s["data"]["source_id"] in set(changed)])) + 2)
        cases.append({"case_id": cid, "action": action, "changed_sources": changed, "removed_sources": removed,
                      "narrative_sources": len(inp.narrative), "input_digest": inp.input_digest(),
                      "estimated_calls": calls, "estimated_new_input_chars": chars,
                      "head_memory_id": cur["memory_id"] if cur else None})
    unassessed = [c for c in ctx.not_eligible if ctx.in_view(c)]
    model_calls = sum(c["estimated_calls"] for c in cases)
    p50 = _measured_call_p50(ctx.rt) or EST_CALL_SECONDS
    return {"view_date": ctx.view_date.isoformat(), "knowledge_end_exclusive": ctx.knowledge_end.isoformat(),
            "temporal_policy": temporal.POLICY_ID, "release_id": ctx.rv.release_id,
            "tables_digest": ctx.tables["summary"]["digest"], "cases": cases,
            "counts": {a: sum(1 for c in cases if c["action"] == a) for a in
                       ("new", "update", "withdrawal", "resynthesis", "table_refresh", "unchanged",
                        "no_narrative_evidence",
                        "later_head_retained")},
            "unassessed_not_eligible_cases": unassessed,
            "estimate": {"memory_model_calls": model_calls, "call_seconds_basis": round(p50, 1),
                         "memory_wall_seconds": round(model_calls * p50),
                         "note": "estimate: packets + factor + state calls per changed case; refinalisation may add "
                                 "one; collective calls estimated separately"}}


def _measured_call_p50(rt) -> float | None:
    rows = rt.db.connect().execute("SELECT latency_ms FROM usage_ledger WHERE status='ok' AND role LIKE 'case_%' "
                                   "AND latency_ms IS NOT NULL").fetchall()
    return statistics.median(r["latency_ms"] for r in rows) / 1000 if rows else None


# ------------------------------------------------------------------ execute
class BudgetExhausted(RuntimeError):
    pass


class WorkerStopped(BaseException):
    """Raised in the main thread by SIGTERM (BaseException: never swallowed by provider retry handlers)."""


class Budget:
    def __init__(self, max_requests: int | None, max_wall_s: float | None):
        self.max_requests, self.max_wall_s = max_requests, max_wall_s
        self.t0 = time.monotonic()
        self.requests = 0

    def reserve(self, req=None) -> None:
        """Called by the gateway before every actual provider attempt (V06): failed, invalid and retried attempts
        all count. Wall time is a between-call bound — an active local generation is not interrupted by it."""
        self.check(1)
        self.requests += 1

    def check(self, upcoming: int) -> None:
        if self.max_requests is not None and self.requests + upcoming > self.max_requests:
            raise BudgetExhausted(f"request budget {self.max_requests} would be exceeded "
                                  f"({self.requests} used, {upcoming} next)")
        if self.max_wall_s is not None and time.monotonic() - self.t0 > self.max_wall_s:
            raise BudgetExhausted(f"wall-time budget {self.max_wall_s:.0f}s exhausted")


def _provider(rt, preference: str):
    name, reason = choose_provider(rt.settings, preference, rt.db, expected_calls=1)
    gw = ModelGateway(rt, name, "pending")
    fp = gw.fingerprint()
    return gw, {"provider": gw.provider.name, "model": gw.model, "fingerprint_sha256": fp["sha256"]}, reason


def _run(rt, ctx: Context, purpose: str, worklist: list[str], prov: dict, reason: str, versions: dict) -> dict:
    boundary = {"principal_authority": "pipeline-service", "view_id": "complaints-leadership",
                "evidence_cutoff": ctx.knowledge_end.isoformat(), "evidence_boundary": "exclusive_stock_endpoint",
                "temporal_policy": temporal.POLICY_ID, "analysis_mode": "reconstructed",
                "permission_revision": permission_revision()}
    budget = {"profile": "intel bounded local/free", "max_requests_per_case": memory.MAX_REQUESTS}
    key = control.compat_key(purpose=purpose, release_id=ctx.rv.release_id, boundary=boundary, worklist=worklist,
                             provider=prov, versions=versions, budget=budget)
    run = control.find_resumable(rt, key)
    if run is None:
        run = control.seal_runspec(rt, purpose=purpose, release_id=ctx.rv.release_id,
                                   source_set_version=ctx.rv.control["source_set_version"], boundary=boundary,
                                   worklist=worklist, provider=prov, versions=versions, budget=budget,
                                   parent_run_id=None, selection_reason=reason)
        if run["compat_key"] != key:
            with rt.db.tx() as c:
                c.execute("UPDATE runs SET compat_key=? WHERE run_id=?", (key, run["run_id"]))
            run["compat_key"] = key
    return run


def in_shard(case_id: str, shard: tuple[int, int] | None) -> bool:
    """Deterministic disjoint case partition for parallel workers (`--shard i/N`): no two shards share a case."""
    if shard is None:
        return True
    import hashlib
    i, n = shard
    return int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % n == i


def memory_phase(rt, ctx: Context, pl: dict, *, gw, prov: dict, reason: str, budget: Budget, log,
                 shard: tuple[int, int] | None = None, limit_cases: int | None = None) -> dict:
    todo = [c for c in pl["cases"] if c["action"] in MODEL_ACTIONS and in_shard(c["case_id"], shard)]
    if limit_cases is not None:
        todo = todo[:limit_cases]          # bounded trial: the first N planned cases (deterministic plan order)
    refresh = [c for c in pl["cases"] if c["action"] == "table_refresh"]
    out = {"model_cases": len(todo), "processed": 0, "reused": 0, "failed": [], "deterministic_refresh": 0,
           "run_id": None}
    for c in refresh:                                   # no model: same understanding, refreshed table inputs
        inp = ctx.inputs(c["case_id"])
        prior = head(rt, c["case_id"])
        body = {k: prior[k] for k in ("claims", "narrative", "factors", "service", "commitments", "contacts",
                                      "member_effort", "responses", "handoffs", "contradictions",
                                      "unresolved_questions", "critique", "coverage")}
        body.update(requests=0, usage=[])
        mid, mem = memory.seal_memory(rt, inp, body, prior, reason="deterministic_table_refresh",
                                      provider={**prior["producer"], "fingerprint_sha256":
                                                prior["producer"]["fingerprint_sha256"]}, run_id="none")
        rt.db.put_object("case_memory", mid, mem, links={"case": [c["case_id"]], "predecessor": [prior["memory_id"]]})
        _set_head(rt, mem)
        out["deterministic_refresh"] += 1
    if not todo:
        return out
    versions = {"memory": memory.MEMORY_VERSION, "reader": memory.READER_PROMPT, "factors": memory.FACTOR_PROMPT,
                "synthesis": memory.SYNTH_PROMPT, "critic": memory.CRITIC_VERSION, "worker": WORKER_VERSION}
    run = _run(rt, ctx, "intel_case_memory", sorted(c["case_id"] + ":" + c["input_digest"] for c in todo), prov,
               reason, versions)
    gw.run_id = run["run_id"]
    out["run_id"] = run["run_id"]
    for c in todo:
        inp = ctx.inputs(c["case_id"])
        prior = head(rt, c["case_id"])
        if prior is not None and prior["memory_id"] != c["head_memory_id"] \
                and prior["inputs"]["digest"] == inp.input_digest() and not memory.stale_interpretation(prior):
            # another (parallel) worker sealed this case on these exact inputs after this plan was made: the case is
            # now unchanged and is never re-investigated with that just-sealed memory as its prior
            out["reused"] += 1
            continue
        rk = memory.reuse_key(inp, prov["fingerprint_sha256"], prior)
        task = control.ensure_task(rt, run, "intel_case_memory", c["case_id"], [rk])
        if task["state"] in ("staged", "accepted"):
            mem = rt.db.get_object("case_memory", task["output_object_id"])
            if mem:
                _set_head(rt, mem)
            out["reused"] += 1
            continue
        if task["state"] == "failed":                 # terminal within this RunSpec; a new run (changed inputs or
            out["failed"].append({"case_id": c["case_id"],  # versions) retries it
                                  "error": f"failed earlier in this run: {task.get('safe_error')}"})
            continue
        budget.check(c["estimated_calls"])
        token = control.claim(rt, task["task_id"])
        if token is None and control.recover_dead_lease(rt, task["task_id"]):
            log(f"  recovered lease of a stopped worker for {c['case_id']}")
            token = control.claim(rt, task["task_id"])
        if token is None:
            out["failed"].append({"case_id": c["case_id"], "error": "task held by another worker"})
            continue
        t0 = time.monotonic()
        log(f"memory {c['action']} {c['case_id']} ({c['estimated_calls']} est. calls)")
        try:
            body = memory.investigate(gw, inp, prior, task_id=task["task_id"],
                                      heartbeat=lambda: control.heartbeat(rt, task["task_id"], token),
                                      resynthesis=c["action"] == "resynthesis")
        except CallTimeout as exc:
            control.release_lease(rt, task["task_id"], token, "queued", f"timeout: {exc}")
            raise
        except WorkerStopped:
            control.release_lease(rt, task["task_id"], token, "queued", "worker stopped (SIGTERM); resumable")
            raise
        except BudgetExhausted:
            control.release_lease(rt, task["task_id"], token, "queued", "request/wall budget exhausted; resumable")
            raise
        except FALLBACK_ERRORS:
            control.release_lease(rt, task["task_id"], token, "queued", "provider unavailable; deferred")
            raise
        except TransientError as exc:                 # transport retries exhausted: keep resumable, move on
            control.release_lease(rt, task["task_id"], token, "queued", f"transient: {str(exc)[:160]}")
            out["failed"].append({"case_id": c["case_id"], "error": f"transient (re-queued): {str(exc)[:160]}"})
            continue
        except memory.MemoryFailed as exc:
            control.finish(rt, task["task_id"], token, "failed", error=str(exc)[:200])
            out["failed"].append({"case_id": c["case_id"], "error": str(exc)[:200]})
            continue
        reason_ = {"new": "initial", "update": "new_evidence", "withdrawal": "evidence_withdrawn",
                   "resynthesis": "interpretation_revised"}[c["action"]]
        mid, mem = memory.seal_memory(rt, inp, body, prior, reason=reason_, provider=prov, run_id=run["run_id"])
        deps = [("source", s["source_id"], s["content_sha256"], None) for s in mem["inputs"]["narrative_sources"]]
        ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=mid, dependency_digest=rk,
                            coverage={"claims": len(mem["claims"]), "factors": len(mem["factors"]),
                                      "requests": body["requests"]},
                            outputs=[("case_memory", mid, mem, {"case": [c["case_id"]], "run": [run["run_id"]],
                                                                 "predecessor": [prior["memory_id"]] if prior else []})],
                            dependencies=[("case_memory", mid, deps)])
        if ok:
            _set_head(rt, mem)
            out["processed"] += 1
            log(f"  sealed {mid} rev {mem['revision']} claims={len(mem['claims'])} factors={len(mem['factors'])} "
                f"calls={body['requests']} {time.monotonic() - t0:.0f}s")
        else:
            out["failed"].append({"case_id": c["case_id"], "error": "fenced completion refused"})
    return out


def usage_report(rt, run_ids: list[str], since: str | None = None) -> dict:
    """Calls recorded for these runs — only those started during this step when `since` is given (a resumed or
    reused run's earlier calls are not this step's work)."""
    if not run_ids:
        return {"calls": 0, "by_role": {}, "by_status": {}}
    q = ",".join("?" for _ in run_ids)
    rows = rt.db.connect().execute(f"SELECT * FROM usage_ledger WHERE run_id IN ({q})" +
                                   (" AND started_at >= ?" if since else ""),
                                   run_ids + ([since] if since else [])).fetchall()
    ok = [r for r in rows if r["status"] == "ok"]
    lat = sorted(r["latency_ms"] for r in ok if r["latency_ms"] is not None)

    def pct(p):
        if not lat:
            return None
        return lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))]
    def tot(col):
        vals = [r[col] for r in ok]
        return None if not vals or any(v is None for v in vals) else sum(vals)
    peaks = [r["peak_memory_bytes"] for r in rows if r["peak_memory_bytes"]]
    by_role = {}
    for r in rows:
        by_role.setdefault(r["role"], {"calls": 0, "ok": 0})
        by_role[r["role"]]["calls"] += 1
        by_role[r["role"]]["ok"] += r["status"] == "ok"
    return {"calls": len(rows), "ok": len(ok), "by_status": {s: sum(1 for r in rows if r["status"] == s)
                                                             for s in sorted({r["status"] for r in rows})},
            "by_role": by_role, "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": lat[-1] if lat else None,
                                               "sum": sum(lat) if lat else None},
            "tokens_ok_calls": {"input": tot("input_tokens"), "output": tot("output_tokens")},
            "peak_memory_bytes_max": max(peaks) if peaks else None,
            "providers": sorted({(r["provider"] + ":" + r["model"]) for r in rows}),
            "cost": {"currency": "AUD", "amount_minor": 0, "basis": "local on-device model; no billing"}
            if all(r["provider"].startswith("gemma") for r in rows) else
            {"currency": "AUD", "amount_minor": 0, "basis": "Google AI Pro subscription plan quota via the official "
             "Antigravity CLI; AI credit overages disabled (useG1Credits=false, attested Never); no billing"}
            if all(r["provider"] == "antigravity-cli" for r in rows) else {"basis": "unknown"}}


def run_step(rt, corpus: Corpus, view_date: str, *, provider: str = "gemma", max_requests: int | None = None,
             max_wall_s: float | None = None, collective: bool = True, publish: bool = True, log=print,
             shard: tuple[int, int] | None = None, limit_cases: int | None = None,
             collective_only: bool = False, assess_shard: tuple[int, int] | None = None,
             publish_only: bool = False) -> dict:
    ensure_schema(rt)
    ctx = Context(rt, corpus, view_date)
    pl = plan(ctx)
    step_id = derived_id("STEP", corpus.corpus_id, pl["view_date"], pl["release_id"])
    started = utc_now_iso()
    t0 = time.monotonic()
    budget = Budget(max_requests, max_wall_s)
    report = {"step_id": step_id, "worker_version": WORKER_VERSION, "plan": pl, "started_at": started}
    state, run_ids = "complete", []
    gw = prov = reason = None
    needs_model = any(c["action"] in MODEL_ACTIONS for c in pl["cases"])

    if pl["counts"]["later_head_retained"]:
        report.update(state="historical_view", note="heads already reflect a later view; earlier views are served "
                      "from their own sealed publications (intelligence.get selection.view_date)",
                      usage={}, wall_seconds=0.0, ended_at=utc_now_iso())
        return report

    def _on_term(signum, frame):
        raise WorkerStopped()
    import signal as _signal
    import threading as _threading
    prev_handler = _signal.signal(_signal.SIGTERM, _on_term) \
        if _threading.current_thread() is _threading.main_thread() else None
    try:
        if publish_only:                       # no provider, no model call: seal what is already persisted
            collective, needs_model = False, False
        if needs_model or collective:
            gw, prov, reason = _provider(rt, provider)
            gw.call_guard = budget.reserve
        if assess_shard is not None and (publish or not collective_only):
            raise ValueError("--assess-shard requires --collective-only --no-publish (a final unsharded run publishes)")
        if shard is not None and (collective or publish):
            raise ValueError("--shard is for parallel case-memory workers only (use with --memory-only)")
        report["memory"] = {"model_cases": 0, "skipped": "collective_only: existing heads only; eligible cases "
                            "without a memory are published as pending"} if collective_only else \
            memory_phase(rt, ctx, pl, gw=gw, prov=prov, reason=reason, budget=budget, log=log, shard=shard,
                         limit_cases=limit_cases) if gw else {"model_cases": 0}
        if report["memory"].get("run_id"):
            run_ids.append(report["memory"]["run_id"])
        if collective:
            from .discovery import collective_phase
            report["collective"] = collective_phase(rt, ctx, gw=gw, prov=prov, reason=reason, budget=budget, log=log,
                                                    assess_shard=assess_shard)
            run_ids += [r for r in report["collective"].get("run_ids", []) if r]
        if publish:
            from .publish import publish_view
            report["publication"] = publish_view(rt, ctx, corpus, step_id=step_id)
    except BudgetExhausted as exc:
        state, report["stopped"] = "budget_exhausted", str(exc)
    except CallTimeout as exc:
        state, report["stopped"] = "interrupted_timeout", str(exc)
        _save_step(rt, step_id, pl, state, run_ids, report, started)
        _write_report(corpus, report)
        log(f"model call timeout: {exc}; exiting so the in-process generation is terminated (resume with the same "
            "command)")
        _kill_cli_children()
        os._exit(75)
    except FALLBACK_ERRORS as exc:
        state, report["stopped"] = "provider_unavailable", str(exc)
    except WorkerStopped:
        state, report["stopped"] = "stopped", "SIGTERM: leased task re-queued; rerun the same command to resume"
        report.update(state=state, usage=usage_report(rt, run_ids, since=started), wall_seconds=round(time.monotonic() - t0, 1),
                      ended_at=utc_now_iso())
        _save_step(rt, step_id, pl, state, run_ids, report, started)
        _write_report(corpus, report)
        log("stopped by SIGTERM; sealed work kept, in-flight task re-queued")
        _kill_cli_children()
        os._exit(143)      # an in-process generation thread may still be running; terminate it with the process
    finally:
        if prev_handler is not None:
            _signal.signal(_signal.SIGTERM, prev_handler)
    report["state"] = state
    report["usage"] = usage_report(rt, run_ids, since=started)
    report["wall_seconds"] = round(time.monotonic() - t0, 1)
    report["ended_at"] = utc_now_iso()
    _save_step(rt, step_id, pl, state, run_ids, report, started)
    _write_report(corpus, report)
    return report


def _kill_cli_children() -> None:
    """Cancellation: a stopped worker also terminates its live CLI invocations (agy), never leaving orphans."""
    import sys
    mod = sys.modules.get("sdbackend.models.agy_cli")
    if mod is not None:
        mod.terminate_children()


def _save_step(rt, step_id, pl, state, run_ids, report, started) -> None:
    with rt.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO intel_steps VALUES (?,?,?,?,?,?,?,?)",
                  (step_id, pl["view_date"], pl["knowledge_end_exclusive"], state, json.dumps(run_ids),
                   json.dumps(report, default=str), started, utc_now_iso()))


def _write_report(corpus: Corpus, report: dict) -> None:
    from .corpus import reports_dir
    d = reports_dir(corpus) / "steps"
    d.mkdir(parents=True, exist_ok=True)
    stamp = report.get("ended_at") or utc_now_iso()
    (d / f"{report['plan']['view_date']}--{stamp.replace(':', '')}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
