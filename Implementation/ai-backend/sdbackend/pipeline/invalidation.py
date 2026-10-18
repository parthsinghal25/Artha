"""Targeted invalidation from source changes and recorded search scopes (R10.5).

A staged output is affected when:
* it consumed a source that changed (different sha) or was removed (direct `source` dependency edge); or
* one of its recorded negative-search scopes could now match a new or changed source: the source is inside the
  scope's case scope (or the scope was release-wide, e.g. S05 discovery) and shares at least one query term
  (the lexical search is an OR query, so one shared term is sufficient to change the result set — conservative).

Affected cases are the cases of changed sources, the cases of directly affected S03 stages, and, for affected
release-wide (S05) scopes, every member case of that stage — this is how new evidence reaches an older or
closed case's mechanism membership. Nothing is recomputed here; the caller schedules the listed work.
"""
from __future__ import annotations

import json
import re

INVALIDATION_VERSION = "sdb-invalidation-1.0.0"
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN.findall(text) if len(t) > 1}


def source_changes(rt, old: dict[str, str], new_ids: list[str]) -> dict:
    """old = {source_id: sha256} that dependents were built against; new_ids = sources in the new release."""
    added, changed = [], []
    for sid in new_ids:
        sha = rt.db.object_sha("source", sid)
        if sid not in old:
            added.append(sid)
        elif old[sid] != sha:
            changed.append(sid)
    removed = sorted(set(old) - set(new_ids))
    return {"added": sorted(added), "changed": sorted(changed), "removed": removed}


def affected(rt, changes: dict) -> dict:
    conn = rt.db.connect()
    stages: dict[tuple[str, str], list[str]] = {}
    for sid in changes["changed"] + changes["removed"]:
        for r in conn.execute("SELECT dependent_kind, dependent_id FROM dependency_edges WHERE dep_kind='source' "
                              "AND dep_id=?", (sid,)):
            stages.setdefault((r["dependent_kind"], r["dependent_id"]), []).append(f"consumed source {sid} changed")
    fresh = []
    for sid in changes["added"] + changes["changed"]:
        rec = rt.db.get_object("source", sid)
        if rec is not None:
            fresh.append((sid, set(rec["data"]["case_ids"]), _tokens(rec["data"]["text"])))
    for r in conn.execute("SELECT dependent_kind, dependent_id, detail FROM dependency_edges "
                          "WHERE dep_kind='search_scope'"):
        scope = json.loads(r["detail"] or "{}")
        terms, case_scope = set(scope.get("query_terms", [])), scope.get("case_scope")
        for sid, cases, toks in fresh:
            if case_scope is not None and not cases & set(case_scope):
                continue
            hit = sorted(terms & toks)
            if hit:
                stages.setdefault((r["dependent_kind"], r["dependent_id"]), []).append(
                    f"search scope ({scope.get('intent')}) could now match {sid} on {hit[:4]}")
                break
    cases: set[str] = set()
    for sid, cs, _ in fresh:
        cases |= cs
    for (kind, did) in stages:
        body = rt.db.get_object(kind, did) or {}
        if body.get("case_id"):
            cases.add(body["case_id"])
        for rec in body.get("analysis_records", []):
            for m in rec["data"].get("memberships", []):
                cases.add(m["case_id"])
            cases |= set(rec["data"].get("case_ids") or [])
    return {"invalidation_version": INVALIDATION_VERSION, "changes": changes,
            "affected_stages": [{"kind": k, "stage_id": d, "reasons": sorted(set(v))} for (k, d), v in sorted(stages.items())],
            "affected_cases": sorted(cases)}
