"""Delivery facts compiled from the store itself (no model calls): corpus, counts, model, usage, publications."""
from __future__ import annotations

import json
from collections import Counter

from ..canonical import utc_now_iso
from .corpus import reports_dir
from .evaluate import evaluate
from .publish import POINTER
from .worker import ensure_schema, heads


def compile_facts(rt, corpus) -> dict:
    ensure_schema(rt)
    c = rt.db.connect()
    intake = json.loads((reports_dir(corpus) / "INTAKE-SUMMARY.json").read_text())
    steps = [dict(r) for r in c.execute("SELECT step_id, view_date, state, run_ids, updated_at FROM intel_steps "
                                        "ORDER BY updated_at")]
    pubs = sorted(rt.db.list_objects("intel_publication"), key=lambda p: p["published_at"])
    cur = rt.db.get_pointer(POINTER)
    hs = heads(rt)
    mems = [rt.db.get_object("case_memory", h["memory_id"]) for h in hs.values()]
    all_mems = rt.db.list_objects("case_memory")
    tasks = Counter(r["state"] for r in c.execute("SELECT state FROM tasks WHERE task_kind LIKE 'intel_%'"))
    usage = [dict(r) for r in c.execute("SELECT role, status, provider, model, latency_ms, input_tokens, "
                                        "output_tokens, peak_memory_bytes, run_id FROM usage_ledger")]
    real = [u for u in usage if u["provider"] != "mock"]
    trial = [u for u in real if (u["run_id"] or "").startswith("TRIAL")]
    lat = sorted(u["latency_ms"] for u in real if u["status"] == "ok" and u["latency_ms"])
    latest = pubs[-1] if pubs else None
    cov = (latest or {}).get("coverage", {})
    elig_in_view = cov.get("eligible_in_view", 0)
    failed_tasks = [dict(r) for r in c.execute("SELECT subject, safe_error FROM tasks WHERE task_kind='intel_case_memory' "
                                                "AND state='failed'")]
    return {
        "generated_at": utc_now_iso(), "corpus_id": corpus.corpus_id, "bundle": str(corpus.body["bundle"]),
        "delivery_manifest_sha256": corpus.body["delivery_manifest_sha256"],
        "source_quality_basis": corpus.body["source_quality_basis"],
        "release": intake["release"], "population": intake["population"],
        "originals_by_extraction_state": intake["originals_by_extraction_state"],
        "failed_or_partial_originals": intake["failed_or_partial"], "tables": intake["tables"],
        "counts": {
            "cases_total": intake["population"]["cases"],
            "eligible_cases": intake["population"]["eligible_cases"],
            "not_eligible_unassessed_cases": intake["population"]["not_eligible_cases"],
            "eligible_in_latest_view": elig_in_view,
            "processed_memories_at_head": len(mems),
            "partial_memories_at_head": sum(m["coverage"]["state"] != "complete_read" for m in mems),
            "failed_case_tasks": failed_tasks,
            "pending_eligible_in_latest_view": cov.get("pending_memories", []),
            "unassessed_case_issue_pairs": cov.get("unassessed_pairs", []),
            "memory_revisions_total": len(all_mems),
            "memory_revisions_by_reason": dict(Counter(m["reason"] for m in all_mems)),
            "tasks_by_state": dict(tasks)},
        "steps": steps,
        "publications": [{"publication_id": p["publication_id"], "view_date": p["closure"]["view_date"],
                          "issues": len([i for i in p["issues"] if i["status"] == "active"]),
                          "memories": len(p["memory_by_case"]), "predecessor_id": p["predecessor_id"],
                          "published_at": p["published_at"]} for p in pubs],
        "current_pointer": dict(cur) if cur else None,
        "model": {"providers": sorted({f"{u['provider']}:{u['model']}" for u in real}),
                  "calls_total": len(real), "calls_ok": sum(u["status"] == "ok" for u in real),
                  "calls_by_status": dict(Counter(u["status"] for u in real)),
                  "calls_by_role": dict(Counter(u["role"] for u in real)),
                  "trial_calls_outside_pipeline": len(trial),
                  "latency_s": {"p50": lat[len(lat) // 2] / 1000 if lat else None,
                                "p95": lat[int(0.95 * (len(lat) - 1))] / 1000 if lat else None,
                                "total_hours": round(sum(lat) / 3_600_000, 2) if lat else None},
                  "tokens": {"input": sum(u["input_tokens"] or 0 for u in real),
                             "output": sum(u["output_tokens"] or 0 for u in real)},
                  "peak_memory_gb": round(max((u["peak_memory_bytes"] or 0) for u in real) / 1e9, 2) if real else None,
                  "cost": "0 (local on-device model; no paid calls, no billing)"},
        "evaluation": evaluate(rt, corpus)}


def write(rt, corpus) -> dict:
    facts = compile_facts(rt, corpus)
    (reports_dir(corpus) / "DELIVERY-FACTS.json").write_text(json.dumps(facts, indent=1, ensure_ascii=False,
                                                                        default=str), encoding="utf-8")
    return facts
