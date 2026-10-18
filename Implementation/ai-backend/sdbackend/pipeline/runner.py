"""Controlled offline analysis runner (never invoked by the read API).

* One sealed RunSpec per provider. `resume=True` continues the latest compatible unfinished run (same release,
  boundary, worklist, provider fingerprint, versions, budget); `--fresh` seals a new one.
* Tasks are claimed with a lease; long model work renews it through `heartbeat`, which also observes
  cancellation. Outputs are sealed in the same transaction as the fenced completion (no stale-worker outputs).
* Reuse keys include the exact provider fingerprint (local weight/config/runtime hashes or API model + SDK) and
  every prompt/DTO/grounding/tool/resolver/policy version. S03 reuse depends on the case's own sources and S02
  stages (not the whole release index); its negative-search scopes are recorded as dependency edges for targeted
  invalidation (`pipeline/invalidation.py`).
* Fallback: sustained quota, provider access denial/missing model, or an ETA above threshold close the run as
  `limited`; with provider=auto a NEW run is sealed on the local fallback (never mixed within a run).
* Failed/limited/held tasks are counted; a run containing them is `partial`, never "complete".
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from ..api.repo import ReleaseView
from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..config import BACKEND_ROOT
from ..models.base import ModelError, OutputInvalid
from ..models.gateway import FALLBACK_ERRORS, ModelGateway, RequestTooLarge, choose_provider, record_event
from ..policy import permission_revision
from ..runtime import Runtime, make_runtime
from . import control
from .source_interpret import DTO_VERSION, GROUNDING_VERSION, PROMPT_VERSION, interpret

BUDGET = {"source_interpretation": {"max_input_tokens": 16000, "max_output_tokens": 4096, "max_requests": 2},
          "case_investigation": {"max_requests": 8, "reserved_requests": 3, "max_tool_calls": 12,
                                 "max_output_tokens_per_request": 4096},
          "parallel_model_calls": 1, "max_transport_retries": 2, "max_schema_repairs": 1,
          "lease_seconds": control.LEASE_SECONDS, "profile": "local-gemma-e4b-m1-16gb (measured ≈51 s/source, ≈445 s/case)"}


def resolve_release(rt: Runtime, which: str) -> ReleaseView:
    key = {"current": "release:current", "candidate": "release:candidate"}.get(which)
    rid = rt.db.get_pointer(key)["target_id"] if key and rt.db.get_pointer(key) else which
    release = rt.db.get_object("release", rid)
    if release is None:
        raise SystemExit(f"release {which} not found")
    return ReleaseView(release, rt.db.get_object("release_control", rid))


def _image_for(rt: Runtime, source_id: str):
    for aid in rt.db.linked("asset", "source", source_id):
        a = rt.db.get_object("asset", aid)
        if a and a["media_type"] in ("image/png", "image/jpeg"):
            return rt.blobs.get(a["bytes_sha256"]), a["media_type"]
    return None


def s02_reuse_key(data: dict, fp: dict) -> str:
    return canonical_sha256([data["source_id"], data["content_sha256"], fp["sha256"], PROMPT_VERSION, DTO_VERSION,
                             GROUNDING_VERSION])


def _new_stats():
    return {"processed": 0, "reused": 0, "failed": 0, "limited": 0, "skipped_held": 0, "revoked": 0, "wall_s": [],
            "outputs": {}}


def _source_task(rt: Runtime, gw: ModelGateway, run: dict, source_id: str, stats: dict) -> str:
    rec = rt.db.get_object("source", source_id)
    data = rec["data"]
    reuse_key = s02_reuse_key(data, gw.fingerprint())
    stage_id = derived_id("STAGE", "s02", reuse_key)
    task = control.ensure_task(rt, run, "s02_source_interpret", source_id, [reuse_key])
    if task["state"] in ("staged", "accepted"):
        stats["reused"] += 1
        return "reused"
    token = control.claim(rt, task["task_id"])
    if token is None:
        stats["skipped_held"] += 1
        return "held"
    existing = rt.db.get_object("stage_s02", stage_id)
    if existing is not None:
        ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=stage_id,
                            output_sha256=rt.db.object_sha("stage_s02", stage_id), dependency_digest=reuse_key,
                            coverage={"reused_from": existing["run_id"]})
        stats["reused" if ok else "revoked"] += 1
        return "reused"
    t0 = time.monotonic()
    try:
        control.heartbeat(rt, task["task_id"], token)
        out = interpret(gw, rec, _image_for(rt, source_id), rt.contracts, task_id=task["task_id"])
    except FALLBACK_ERRORS:
        control.release_lease(rt, task["task_id"], token, "queued", "provider fallback required; deferred")
        raise
    except RequestTooLarge as exc:
        control.finish(rt, task["task_id"], token, "limited", error=str(exc)[:200])
        stats["limited"] += 1
        return "limited"
    except (OutputInvalid, ModelError) as exc:
        control.finish(rt, task["task_id"], token, "failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        stats["failed"] += 1
        return "failed"
    stage = {"stage_id": stage_id, "kind": "s02_source_interpretation", "run_id": run["run_id"],
             "source_binding": {"source_id": data["source_id"], "content_sha256": data["content_sha256"]},
             "provider": gw.provider.name, "model": gw.model, "provider_fingerprint_sha256": gw.fingerprint()["sha256"],
             "prompt_version": PROMPT_VERSION, "dto_version": DTO_VERSION, "produced_at": utc_now_iso(),
             "draft_status": "staged_not_accepted", **out}
    ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=stage_id, dependency_digest=reuse_key,
                        coverage={"atoms_proposed": out["atoms_proposed"], "facts": len(out["facts"]),
                                  "assertions": len(out["assertions"]), "rejected": len(out["rejected"])},
                        outputs=[("stage_s02", stage_id, stage, {"source": [source_id], "run": [run["run_id"]]})])
    if not ok:
        stats["revoked"] += 1
        return "revoked"
    stats["processed"] += 1
    stats["wall_s"].append(time.monotonic() - t0)
    o = stats["outputs"]
    for k, v in (("atoms_proposed", out["atoms_proposed"]), ("typed_facts", len(out["facts"])),
                 ("assertions", len(out["assertions"])), ("rejected_atoms", len(out["rejected"])),
                 ("ungrounded_values", sum("not grounded" in r["reason"] for r in out["rejected"]))):
        o[k] = o.get(k, 0) + v
    return "processed"


def _case_task(rt: Runtime, gw: ModelGateway, run: dict, rv: ReleaseView, case_id: str, stats: dict) -> str:
    from ..api.repo import Repo
    from ..policy import pipeline_authority
    from .case_assess import DTO_VERSION as S3_DTO, PROMPT_VERSION as S3_PROMPT, RESOLVER_VERSION, investigate
    from .tools import TOOL_VERSION, ToolHost, build_index
    index_version = build_index(rt, rv.release_id)
    repo = Repo(rt, rv, pipeline_authority(), run["context_boundary"]["evidence_cutoff"], "inclusive_cutoff")
    source_ids = repo.case_source_ids(case_id)
    deps = [(sid, rt.db.object_sha("source", sid)) for sid in source_ids]
    fp = gw.fingerprint()
    s02 = [derived_id("STAGE", "s02", s02_reuse_key(rt.db.get_object("source", sid)["data"], fp)) for sid in source_ids]
    s02 = [x for x in s02 if rt.db.get_object("stage_s02", x)]
    reuse_key = canonical_sha256([case_id, deps, run["context_boundary"], fp["sha256"], S3_PROMPT, S3_DTO,
                                  RESOLVER_VERSION, TOOL_VERSION, permission_revision(), sorted(s02)])
    stage_id = derived_id("STAGE", "s03", reuse_key)
    task = control.ensure_task(rt, run, "s03_case_assess", case_id, [reuse_key])
    if task["state"] in ("staged", "accepted"):
        stats["reused"] += 1
        return "reused"
    token = control.claim(rt, task["task_id"])
    if token is None:
        stats["skipped_held"] += 1
        return "held"
    if rt.db.get_object("stage_s03", stage_id) is not None:
        ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=stage_id,
                            output_sha256=rt.db.object_sha("stage_s03", stage_id), dependency_digest=reuse_key)
        stats["reused" if ok else "revoked"] += 1
        return "reused"
    host = ToolHost(rt, repo, run_id=run["run_id"], index_version=index_version, case_scope=[case_id],
                    candidate_stage_ids=s02)
    t0 = time.monotonic()
    try:
        out = investigate(gw, host, case_id, task_id=task["task_id"], contracts=rt.contracts,
                          source_set_version=rv.control["source_set_version"],
                          heartbeat=lambda: control.heartbeat(rt, task["task_id"], token))
    except FALLBACK_ERRORS:
        control.release_lease(rt, task["task_id"], token, "queued", "provider fallback required; deferred")
        raise
    except control.TaskRevoked:
        stats["revoked"] += 1
        raise
    except (RequestTooLarge,) as exc:
        control.finish(rt, task["task_id"], token, "limited", error=str(exc)[:200])
        stats["limited"] += 1
        return "limited"
    except (OutputInvalid, ModelError) as exc:
        state = "limited" if type(exc).__name__ == "BudgetExhausted" else "failed"
        control.finish(rt, task["task_id"], token, state, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        stats[state] += 1
        return state
    from .case_assess import reconcile_case
    out = reconcile_case(out, [repo.source_record(s) for s in source_ids], case_id,
                         run["context_boundary"]["evidence_cutoff"])
    stage = {"stage_id": stage_id, "kind": "s03_case_assessment", "run_id": run["run_id"], "case_id": case_id,
             "consumed_sources": [{"source_id": s, "object_sha256": h} for s, h in deps], "consumed_s02_stages": s02,
             "index_version": index_version, "provider": gw.provider.name, "model": gw.model,
             "provider_fingerprint_sha256": fp["sha256"], "search_scopes": host.search_scopes,
             "produced_at": utc_now_iso(), **out}
    ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=stage_id, dependency_digest=reuse_key,
                        coverage={"events": len(out["timeline_events"]), "dropped_citations": len(out["dropped_citations"]),
                                  "read_coverage": out["read_coverage"]},
                        outputs=[("stage_s03", stage_id, stage, {"case": [case_id], "run": [run["run_id"]]})],
                        dependencies=[("stage_s03", stage_id,
                                       [("source", s, h, None) for s, h in deps] +
                                       [("stage_s02", x, None, None) for x in s02] +
                                       [("search_scope", canonical_sha256(sc), None, sc) for sc in host.search_scopes])])
    if not ok:
        stats["revoked"] += 1
        return "revoked"
    stats["processed"] += 1
    stats["wall_s"].append(time.monotonic() - t0)
    o = stats["outputs"]
    for k, v in (("timeline_events", len(out["timeline_events"])), ("dropped_citations", len(out["dropped_citations"])),
                 ("requests", out["requests"]), ("tool_calls", out["tool_calls"]),
                 ("read_repairs", int(out["read_repair"]["performed"])),
                 ("unread_passages", out["read_coverage"]["unread_passages"])):
        o[k] = o.get(k, 0) + v
    return "processed"


def _staged_outputs(rt, run_id: str, kind: str) -> list[str]:
    rows = rt.db.connect().execute("SELECT output_object_id FROM tasks WHERE run_id=? AND task_kind=? AND "
                                   "state IN ('staged','accepted') AND output_object_id IS NOT NULL", (run_id, kind))
    return sorted(r["output_object_id"] for r in rows)


def _derived_tasks(rt: Runtime, gw, run: dict, rv, stages, stats: dict) -> None:
    """S04 (deterministic, no model) over this run's staged S02 outputs; S05 (bounded model) over staged S03."""
    from ..api.repo import Repo
    from ..engine.resolvers import RESOLVER_VERSION as S4_VERSION, dependency_edges, native_candidates, resolve
    from ..policy import pipeline_authority
    from .tools import ToolHost, build_index
    if "ledger" in stages:
        s02 = _staged_outputs(rt, run["run_id"], "s02_source_interpret")
        # trusted native tables (work logs, ledgers) are projected without depending on any model call
        inputs = [e["immutable_id"] for e in rv.release["entries"] if e["kind"] == "input"] if rv is not None else []
        natives = native_candidates(rt, inputs)
        release_sources = sorted(i for i in inputs if rt.db.get_object("source", i) is not None)
        s03 = _staged_outputs(rt, run["run_id"], "s03_case_assess")
        task = control.ensure_task(rt, run, "s04_ledger_resolve", "release",
                                   [S4_VERSION, s02] + ([natives, release_sources, s03] if inputs else []))
        if task["state"] in ("staged", "accepted"):
            stats["s04"]["reused"] += 1
        elif (token := control.claim(rt, task["task_id"])) is not None:
            t0 = time.monotonic()
            st = resolve(rt, s02, run_id=run["run_id"], persist=False,      # pure build; nothing sealed yet
                         native_source_ids=natives, release_source_ids=release_sources, s03_stage_ids=s03)
            sealed = rt.db.get_object("stage_s04", st["stage_id"]) is not None
            if control.finish(rt, task["task_id"], token, "staged", output_object_id=st["stage_id"],
                              outputs=[] if sealed else [("stage_s04", st["stage_id"], st, {"run": [run["run_id"]]})],
                              dependencies=[("stage_s04", st["stage_id"], dependency_edges(st))]):
                if sealed:
                    stats["s04"]["reused"] += 1
                else:
                    stats["s04"]["processed"] += 1
                stats["s04"]["wall_s"].append(time.monotonic() - t0)
                stats["s04"]["outputs"] = {"records": len(st["analysis_records"]), "unresolved": len(st["unresolved"])}
            else:
                stats["s04"]["revoked"] += 1
        else:
            stats["s04"]["skipped_held"] += 1
    if "collective" in stages:
        from .collective import PROMPT_VERSION as S5_PROMPT, build_stage
        s03 = _staged_outputs(rt, run["run_id"], "s03_case_assess")
        if not s03:
            return
        task = control.ensure_task(rt, run, "s05_collective", "release", [S5_PROMPT, s03])
        if task["state"] in ("staged", "accepted"):
            stats["s05"]["reused"] += 1
            return
        token = control.claim(rt, task["task_id"])
        if token is None:
            stats["s05"]["skipped_held"] += 1
            return
        index_version = build_index(rt, rv.release_id)
        repo = Repo(rt, rv, pipeline_authority(), run["context_boundary"]["evidence_cutoff"], "inclusive_cutoff")
        host = ToolHost(rt, repo, run_id=run["run_id"], index_version=index_version, case_scope=None)
        t0 = time.monotonic()
        try:
            stage_id, body, edges = build_stage(
                rt, gw, host, s03, run_id=run["run_id"], task_id=task["task_id"],
                assessed_snapshot=run["context_boundary"]["evidence_cutoff"],
                source_version=rv.control["source_set_version"],
                heartbeat=lambda: control.heartbeat(rt, task["task_id"], token))
        except FALLBACK_ERRORS:
            control.release_lease(rt, task["task_id"], token, "queued", "provider fallback required; deferred")
            raise
        except (OutputInvalid, ModelError) as exc:
            control.finish(rt, task["task_id"], token, "failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            stats["s05"]["failed"] += 1
            return
        sealed = rt.db.get_object("stage_s05", stage_id) is not None      # identical draft from an earlier run
        if control.finish(rt, task["task_id"], token, "staged", output_object_id=stage_id,
                          outputs=[] if sealed else [("stage_s05", stage_id, body, {"run": [run["run_id"]]})],
                          dependencies=[("stage_s05", stage_id, edges)]):
            stats["s05"]["processed"] += 1
            stats["s05"]["wall_s"].append(time.monotonic() - t0)
            stats["s05"]["outputs"] = {"findings": len(body["analysis_records"]),
                                       "dropped_citations": len(body["dropped_citations"])}
        else:
            stats["s05"]["revoked"] += 1


def run_release_analysis(which: str = "candidate", *, provider: str = "auto", stages=("source",),
                         max_sources: int | None = None, resume: bool = True) -> dict:
    rt = make_runtime()
    rv = resolve_release(rt, which)
    worklist = sorted(e["immutable_id"] for e in rv.release["entries"]
                      if e["kind"] == "input" and rt.db.get_object("source", e["immutable_id"]))
    if max_sources:
        worklist = worklist[:max_sources]
    cases = rv.control["case_ids"] if "case" in stages else []
    expected_calls = (len(worklist) * 1 if "source" in stages else 0) + len(cases) * 6
    excluded: set[str] = set()
    reports, parent = [], None
    while True:
        try:
            name, reason = choose_provider(rt.settings, provider, rt.db, exclude=excluded, expected_calls=expected_calls)
        except FALLBACK_ERRORS as exc:
            reports.append({"result": "no_provider_available", "reason": str(exc)})
            break
        try:
            gw = ModelGateway(rt, name, "pending")
            fp = gw.fingerprint()
        except FALLBACK_ERRORS as exc:
            excluded.add(name)
            record_event(rt.db, name, rt.settings, "provider_unavailable", str(exc))
            if provider != "auto":
                reports.append({"result": "provider_unavailable", "provider": name, "reason": str(exc)})
                break
            continue
        boundary = {"principal_authority": "pipeline-service", "view_id": "complaints-leadership",
                    "evidence_cutoff": f"{rv.release['observation_end_exclusive']}T00:00:00+10:00",
                    "evidence_boundary": "inclusive_cutoff", "analysis_mode": "reconstructed",
                    "permission_revision": permission_revision()}
        prov = {"provider": gw.provider.name, "model": gw.model, "fingerprint_sha256": fp["sha256"],
                "runtime": "mlx-vlm" if name == "gemma" else "google-genai"}
        from .case_assess import DTO_VERSION as S3_DTO, PROMPT_VERSION as S3_PROMPT, RESOLVER_VERSION
        from .tools import TOOL_VERSION
        versions = {"s02_prompt": PROMPT_VERSION, "s02_dto": DTO_VERSION, "grounding": GROUNDING_VERSION,
                    "s03_prompt": S3_PROMPT, "s03_dto": S3_DTO, "resolver": RESOLVER_VERSION, "tools": TOOL_VERSION,
                    "stages": list(stages)}
        key = control.compat_key(purpose="release_analysis", release_id=rv.release_id, boundary=boundary,
                                 worklist=worklist + cases, provider=prov, versions=versions, budget=BUDGET)
        run = control.find_resumable(rt, key) if resume else None
        resumed = run is not None
        if run is None:
            run = control.seal_runspec(rt, purpose="release_analysis", release_id=rv.release_id,
                                       source_set_version=rv.control["source_set_version"], boundary=boundary,
                                       worklist=worklist + cases, provider={**prov, "fingerprint": fp},
                                       versions=versions, budget=BUDGET, parent_run_id=parent, selection_reason=reason)
            if run["compat_key"] != key:          # compat key is computed on the provider summary, not the full fp
                with rt.db.tx() as c:
                    c.execute("UPDATE runs SET compat_key=? WHERE run_id=?", (key, run["run_id"]))
                run["compat_key"] = key
        gw.run_id = run["run_id"]
        stats = {"s02": _new_stats(), "s03": _new_stats(), "s04": _new_stats(), "s05": _new_stats()}
        t0 = time.monotonic()
        outcome = "complete"
        jobs = ([("source", sid) for sid in worklist] if "source" in stages else []) + [("case", c) for c in cases]
        try:
            for kind, subject in jobs:
                if kind == "source":
                    _source_task(rt, gw, run, subject, stats["s02"])
                else:
                    _case_task(rt, gw, run, rv, subject, stats["s03"])
            if "ledger" in stages or "collective" in stages:
                _derived_tasks(rt, gw, run, rv, stages, stats)
        except FALLBACK_ERRORS as exc:
            outcome = "limited"
            control.close_run(rt, run["run_id"], "limited", f"fallback required: {exc}")
            record_event(rt.db, name, rt.settings, "run_limited", f"{run['run_id']}: {exc}")
        except control.TaskRevoked as exc:
            if run["context_boundary"].get("permission_revision") != permission_revision():
                outcome = "authority_changed"             # policy changed mid-run: nothing further is sealed
                control.close_run(rt, run["run_id"], "expired", "access policy changed during the run")
            else:
                outcome = "cancelled"
            record_event(rt.db, name, rt.settings, f"run_{outcome}", f"{run['run_id']}: {exc}")
        if outcome == "complete" and run["context_boundary"].get("permission_revision") != permission_revision():
            outcome = "authority_changed"                 # a fenced finish refused; name the cause
            control.close_run(rt, run["run_id"], "expired", "access policy changed during the run")
        if outcome == "complete":
            counts = control.task_counts(rt, run["run_id"])
            bad = {k: v for k, v in counts.items() if k not in ("staged", "accepted")}
            outcome = "partial" if bad else "complete"
            control.close_run(rt, run["run_id"], outcome, json.dumps(counts))
        reports.append(_report(rt, run, stats, outcome, time.monotonic() - t0, resumed))
        if outcome == "limited" and provider == "auto":
            excluded.add(name)
            parent = run["run_id"]
            continue
        break
    out = {"release_id": rv.release_id, "release_status": rv.control["status"], "runs": reports}
    import os
    path = Path(os.environ.get("SDB_REPORT_DIR") or BACKEND_ROOT / "reports" / "pipeline")
    path.mkdir(parents=True, exist_ok=True)
    (path / f"run-{reports[-1].get('run_id', 'none')}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _stage_usage(rows) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    lat = [r["latency_ms"] for r in ok if r["latency_ms"] is not None]

    def total(col, subset=ok):
        vals = [r[col] for r in subset]
        return None if not vals or any(v is None for v in vals) else sum(vals)     # unknown stays unknown
    peaks = [r["peak_memory_bytes"] for r in rows if r["peak_memory_bytes"] is not None]
    return {"attempts": len(rows), "ok": len(ok), "in_flight_or_crashed": sum(r["status"] == "in_flight" for r in rows),
            "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
            "retries": sum(r["retries"] for r in rows),
            "tokens_ok_calls": {"input": total("input_tokens"), "output": total("output_tokens"),
                                "thinking": total("thinking_tokens")},
            "tokens_all_attempts": {"input": total("input_tokens", rows), "output": total("output_tokens", rows)},
            "latency_ms": {"sum": sum(lat) if lat else None, "mean": round(statistics.mean(lat)) if lat else None,
                           "p50": round(statistics.median(lat)) if lat else None, "max": max(lat) if lat else None},
            "peak_memory_bytes_max": max(peaks) if peaks else None}


def _report(rt: Runtime, run: dict, stats: dict, outcome: str, wall: float, resumed: bool) -> dict:
    rows = rt.db.connect().execute("SELECT u.*, t.task_kind FROM usage_ledger u LEFT JOIN tasks t ON t.task_id=u.task_id "
                                   "WHERE u.run_id=?", (run["run_id"],)).fetchall()
    stages = {}
    for st, kind in (("s02", "s02_source_interpret"), ("s03", "s03_case_assess"), ("s04", "s04_ledger_resolve"),
                     ("s05", "s05_collective")):
        s = stats[st]
        mean_s = statistics.mean(s["wall_s"]) if s["wall_s"] else None
        stages[st] = {"tasks": {k: s[k] for k in ("processed", "reused", "failed", "limited", "skipped_held", "revoked")},
                      "outputs": s["outputs"], "usage": _stage_usage([r for r in rows if r["task_kind"] == kind]),
                      "mean_task_seconds": round(mean_s, 1) if mean_s else None}
    return {"run_id": run["run_id"], "resumed": resumed, "outcome": outcome, "provider": run["provider"],
            "selection_reason": run["provider_selection_reason"], "task_states": control.task_counts(rt, run["run_id"]),
            "stages": stages, "wall_seconds": round(wall, 1),
            "notes": ["S02 and S03 are reported separately; rejected atoms may also be retained as assertions, so "
                      "categories are not disjoint", "unknown token usage is null, not zero"]}
