"""Deterministic table projection for a demo release (no model): ledgers, recorded work and lifecycle.

Reuses the S04 resolver for ledger/work identity (native IDs, earliest-copy wins, conflicting copies unresolved)
and the lifecycle profile for administrative transitions. Pure function of the sealed sources: recomputed on every
publication and safe to compare between incremental and fresh builds.
"""
from __future__ import annotations

from collections import Counter

from ..canonical import canonical_sha256
from ..engine import native_lifecycle, native_ledger, native_work
from ..engine.resolvers import resolve

TABLES_VERSION = "sdb-intel-tables-1.0.0"


def release_sources(rt, rv) -> list[dict]:
    out = []
    for e in rv.release["entries"]:
        if e["kind"] == "input":
            rec = rt.db.get_object("source", e["immutable_id"])
            if rec is not None:
                out.append(rec)
    return sorted(out, key=lambda r: r["data"]["source_id"])


def project_release(rt, rv, *, source_ids: list[str] | None = None) -> dict:
    sources = release_sources(rt, rv)
    if source_ids is not None:
        keep = set(source_ids)
        sources = [s for s in sources if s["data"]["source_id"] in keep]
    native_ids = [s["data"]["source_id"] for s in sources
                  if native_work.is_work_log(s["data"]) or native_ledger.is_ledger(s["data"])]
    s04 = resolve(rt, [], persist=False, native_source_ids=native_ids,
                  release_source_ids=[s["data"]["source_id"] for s in sources])
    per = []
    life_unresolved = []
    life_sources = []
    for s in sources:
        if native_lifecycle.is_lifecycle(s["data"]):
            rows, unres = native_lifecycle.project(s)
            per.append(rows)
            life_unresolved += unres
            life_sources.append(s["data"]["source_id"])
    transitions, conflicts = native_lifecycle.merge(per)
    life_unresolved += conflicts
    by_case: dict[str, list[dict]] = {}
    for t in transitions:
        by_case.setdefault(t["case_id"], []).append(t)
    recs = s04["analysis_records"]
    kinds = Counter(r["kind"] for r in recs)
    shared_work = [r["data"]["work_id"] for r in recs if r["kind"] == "work_entry" and len(r["data"]["case_ids"]) > 1]
    afca_linked = [r["envelope"]["record_id"] for r in recs if r["kind"] in ("obligation", "work_entry")
                   and r["data"].get("afca_matter_ids")]
    table_rows = sum(len(t.rows) for s in sources for t in __import__("sdbackend.engine.native_tables",
                     fromlist=["read_tables"]).read_tables(s["data"])[0]
                     if native_work.header_map(t.names()) or native_ledger.header_map(t.names()))
    summary = {"tables_version": TABLES_VERSION, "ledger_sources": len(s04["native_ledgers"]),
               "work_sources": len(s04["native_work_logs"]), "lifecycle_sources": len(life_sources),
               "financial_work_table_rows": table_rows,
               "records": dict(kinds), "shared_work_entries": len(shared_work),
               "afca_linked_records": len(afca_linked),
               "unresolved_financial_work": [{k: u[k] for k in ("source_id", "subject", "reason")}
                                             for u in s04["unresolved"]],
               "lifecycle_transitions": len(transitions), "lifecycle_cases": len(by_case),
               "unresolved_lifecycle": life_unresolved,
               "digest": canonical_sha256([recs, transitions])}
    return {"s04": s04, "lifecycle": by_case, "lifecycle_sources": life_sources, "summary": summary}
