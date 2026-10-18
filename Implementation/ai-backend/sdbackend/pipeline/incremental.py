"""Incremental scheduling and incremental-versus-full comparison (R10.5 baseline). No model.

`schedule` turns source changes into the minimal work list: S02 for added/changed sources only, S03 for affected
cases, S04 whenever any consumed S02 output changes (deterministic and cheap: always rebuilt whole), S05 for the
affected collective stages, and the stored results whose recorded inputs touch an affected stage. Unaffected
staged outputs and results are reused untouched.

`compare` checks an incremental rebuild against a full deterministic rebuild of the same inputs: identical stage
identity, identical record digests and identical result cell values. Any difference is reported, never hidden.
"""
from __future__ import annotations

from ..canonical import canonical_sha256
from .invalidation import affected


def schedule(rt, changes: dict, aset: dict | None = None) -> dict:
    hit = affected(rt, changes)
    kinds = {s["kind"] for s in hit["affected_stages"]}
    stage_ids = {s["stage_id"] for s in hit["affected_stages"]}
    s02 = sorted(set(changes["added"]) | set(changes["changed"]))
    # downstream of a re-run S02: every S04 that consumed an S02 stage of a changed/removed source
    conn = rt.db.connect()
    for sid in changes["changed"] + changes["removed"]:
        for r in conn.execute("SELECT DISTINCT dependent_id FROM dependency_edges WHERE dependent_kind='stage_s04' "
                              "AND dep_kind='source' AND dep_id=?", (sid,)):
            stage_ids.add(r["dependent_id"])
            kinds.add("stage_s04")
    results = []
    if aset is not None:
        records = (aset.get("dependency_closure") or {}).get("records", {})
        hit_records = {rid for rid, st in records.items() if st in stage_ids}
        for rid in aset.get("result_ids", []) + [r["result_id"] for r in rt.db.list_objects("result")]:
            res = rt.db.get_object("result", rid)
            calc = rt.db.get_object("calculation", res["result_context"]["calculation_id"]) if res else None
            used = set(calc["input_record_ids"]) if calc else set()
            if used & hit_records or {f"stage:{s}" for s in stage_ids} & used:
                results.append(rid)
    return {"s02_sources": s02, "s03_cases": sorted(hit["affected_cases"]) if "stage_s03" in kinds or s02 else [],
            "s04_rebuild": bool(s02 or changes["removed"] or "stage_s04" in kinds),
            "s05_stages": sorted(s["stage_id"] for s in hit["affected_stages"] if s["kind"] == "stage_s05"),
            "affected_stages": sorted(stage_ids), "results": sorted(set(results)),
            "reused_note": "every staged output and result not listed is reused unchanged"}


def _digest(stage: dict) -> str:
    return canonical_sha256(sorted(canonical_sha256(r) for r in stage.get("analysis_records", [])))


def compare(incremental: dict, full: dict, inc_results: dict | None = None, full_results: dict | None = None) -> dict:
    diffs = []
    if incremental.get("stage_id") != full.get("stage_id"):
        diffs.append("stage identity differs")
    if _digest(incremental) != _digest(full):
        diffs.append("analysis record digests differ")
    for key in sorted(set(inc_results or {}) | set(full_results or {})):
        a, b = (inc_results or {}).get(key), (full_results or {}).get(key)
        if a != b:
            diffs.append(f"result {key}: incremental {a} != full {b}")
    return {"equal": not diffs, "differences": diffs, "incremental_digest": _digest(incremental),
            "full_digest": _digest(full)}
