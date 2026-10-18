"""Measured scale forecast and executable preflight for a larger corpus (e.g. accepted500).

The forecast uses only MEASURED usage from this store's ledger (no vendor numbers): per-case memory calls and wall
time scaled by narrative characters, plus collective discovery pages and assessment batches. The preflight checks
whether a named corpus is actually present and bindable; if it is not, it reports `unrun` — it never fabricates."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from ..config import BACKEND_ROOT
from . import discovery


def _case_rows(rt) -> list[dict]:
    c = rt.db.connect()
    rows = c.execute("SELECT task_id, role, status, latency_ms, input_tokens, output_tokens, peak_memory_bytes, provider, "
                     "model FROM usage_ledger WHERE role LIKE 'case_%' OR role LIKE 'collective_%' OR role LIKE "
                     "'membership_%'").fetchall()
    return [dict(r) for r in rows]


def measured(rt) -> dict:
    rows = _case_rows(rt)
    per_task: dict[str, dict] = {}
    for r in rows:
        if not r["role"].startswith("case_"):
            continue
        t = per_task.setdefault(r["task_id"], {"calls": 0, "ms": 0, "in": 0})
        t["calls"] += 1
        t["ms"] += r["latency_ms"] or 0
        t["in"] += r["input_tokens"] or 0
    mems = [m for m in rt.db.list_objects("case_memory") if m["producer"]["requests"]]
    chars = []
    for m in mems:
        n = sum(len((rt.db.get_object("source", s["source_id"]) or {"data": {"text": ""}})["data"]["text"])
                for s in m["inputs"]["narrative_sources"])
        chars.append((n, m["producer"]["requests"]))
    secs = sorted(t["ms"] / 1000 for t in per_task.values())
    coll = [r for r in rows if not r["role"].startswith("case_") and r["status"] == "ok"]
    return {"case_tasks": len(per_task),
            "case_seconds": {"p50": statistics.median(secs) if secs else None,
                             "p95": secs[min(len(secs) - 1, int(0.95 * (len(secs) - 1) + 0.5))] if secs else None,
                             "mean": statistics.mean(secs) if secs else None},
            "case_calls_mean": statistics.mean(t["calls"] for t in per_task.values()) if per_task else None,
            "narrative_chars_mean": statistics.mean(c for c, _ in chars) if chars else None,
            "collective_call_seconds_p50": statistics.median(r["latency_ms"] / 1000 for r in coll
                                                            if r["latency_ms"]) if coll else None,
            "peak_memory_bytes_max": max((r["peak_memory_bytes"] or 0) for r in rows) if rows else None,
            "providers": sorted({f"{r['provider']}:{r['model']}" for r in rows})}


def forecast(rt, cases: int, chars_per_case: float | None = None) -> dict:
    m = measured(rt)
    if not m["case_tasks"]:
        return {"state": "no_measurements", "note": "run at least one real memory step first"}
    scale = (chars_per_case / m["narrative_chars_mean"]) if chars_per_case and m["narrative_chars_mean"] else 1.0
    mem_s = cases * m["case_seconds"]["mean"] * scale
    mem_calls = cases * m["case_calls_mean"] * max(1.0, scale)
    pages = math.ceil(cases * 600 / discovery.SCAN_PAGE_CHARS)          # ~600 chars per compact scan line
    coll_p50 = m["collective_call_seconds_p50"] or m["case_seconds"]["p50"] / max(1, m["case_calls_mean"])
    return {"state": "forecast", "basis": m, "cases": cases, "chars_scale": round(scale, 2),
            "memory": {"calls": round(mem_calls), "wall_hours_serial": round(mem_s / 3600, 2)},
            "collective_full_pass": {"discovery_pages": pages,
                                     "assessment_calls": math.ceil(cases / discovery.ASSESS_BATCH),
                                     "note": "one assessment call covers a batch of cases against all their pending "
                                             "issue definitions; retrieval re-asks add up to one call per batch",
                                     "wall_hours": round((pages + 2 * math.ceil(cases / discovery.ASSESS_BATCH))
                                                         * coll_p50 / 3600, 2)},
            "daily_update": "only new/changed cases are read; a day touching k cases costs ≈ k × memory mean plus "
                            "assessment of those k memories against active issues (new issues: all memories)",
            "note": "serial single-device estimate from this store's measured local calls; a hosted model changes "
                    "per-call latency but not the call counts"}


def preflight(corpus_path: str | None) -> dict:
    from .corpus import CorpusError, load_corpus
    if not corpus_path:
        return {"state": "unrun", "reason": "no corpus file named"}
    p = Path(corpus_path)
    if not p.is_file() and not (BACKEND_ROOT / corpus_path).is_file():
        return {"state": "unrun", "corpus": corpus_path, "reason": "this corpus file is not present. That says nothing "
                "about whether data was delivered: the combined P4+cloud480 binding is "
                "config/demo/combined500-v1.corpus.json (see intel/preflight.py)"}
    try:
        c = load_corpus(corpus_path)
    except CorpusError as exc:
        return {"state": "refused", "corpus": corpus_path, "reason": str(exc)}
    wl = c.body.get("worklist") or {}
    checks = {"bundle_manifest_bound": True, "explicit_worklist": bool(wl.get("eligible_member_references")),
              "early_evaluation_reserved": bool(wl.get("early_evaluation_member_references")),
              "separate_store": "var/demo/" in json.dumps(c.body.get("store", {}))}
    return {"state": "ready" if all(checks.values()) else "incomplete", "corpus": corpus_path, "checks": checks}
