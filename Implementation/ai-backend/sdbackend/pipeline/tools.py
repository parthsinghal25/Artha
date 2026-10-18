"""AI-PA-02 bounded, host-authorised read tools (private; not public operations).

The host injects run/context/permission/cutoff; model arguments are closed (unknown keys or tools reject) and can
only narrow scope. Every return is reauthorised and time-filtered through the same Repo used by the public API.
Retrieval prefilters the eligible corpus *before* ranking so later/denied evidence cannot crowd out permitted
candidates. Envelope fields follow AI/02 §4; `TOOL_VERSION` pins the private DTO.
"""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator

from ..api.repo import Repo
from ..canonical import canonical_sha256, derived_id
from ..models.base import ToolSpec
from .passages import passages

TOOL_VERSION = "sdb-tools-1.1.0"
ID = {"type": "string", "minLength": 1, "maxLength": 160}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 20}
SOURCE_FAMILIES = [f"S{i:02d}" for i in range(1, 13)]
RELATION_KINDS = ["provenance", "copy", "episode", "contact", "target", "work", "obligation", "afca", "procedure"]
MAX_PASSAGES_PER_READ = 6
MAX_EXCERPT_CHARS = 36000            # ≈12k tokens aggregate per tool response


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_SPECS = [
    ToolSpec("search_evidence", "Lexical search over permitted original source passages (all case statuses, "
             "including closed). Returns passage handles with short excerpts. Use intent to say why you search.",
             _obj({"query": {"type": "string", "minLength": 1, "maxLength": 1000},
                   "intent": {"type": "string", "enum": ["support", "contrary", "alternative", "discovery"]},
                   "case_ids": {"type": "array", "items": ID, "maxItems": 20},
                   "source_families": {"type": "array", "items": {"type": "string", "enum": SOURCE_FAMILIES}, "maxItems": 12},
                   "limit": LIMIT}, ["query", "intent"])),
    ToolSpec("read_passages", "Read the exact text of up to 6 passage handles (exact source binding and offsets).",
             _obj({"passage_handles": {"type": "array", "items": ID, "minItems": 1, "maxItems": 6}},
                  ["passage_handles"])),
    ToolSpec("read_case_sources", "List the permitted sources of one case over its whole eligible history, with "
             "metadata and passage handles.",
             _obj({"case_id": ID, "limit": LIMIT, "cursor": {"type": ["string", "null"]}}, ["case_id"])),
    ToolSpec("read_related_entities", "Read accepted source-backed related entities (episodes, contacts, targets, "
             "work, obligations, AFCA) for an entity. Unknown relationships stay unknown.",
             _obj({"entity_id": ID, "entity_kind": {"type": "string"},
                   "relation_kinds": {"type": "array", "items": {"type": "string", "enum": RELATION_KINDS}},
                   "limit": LIMIT}, ["entity_id", "entity_kind"])),
    ToolSpec("read_procedures", "Read applicable procedure versions effective at a time.",
             _obj({"process_query": {"type": "string", "minLength": 1, "maxLength": 1000},
                   "effective_date": {"type": ["string", "null"]}, "limit": LIMIT}, ["process_query"])),
    ToolSpec("inspect_candidate_facts", "Inspect run-local validated candidate facts (drafts, not independent "
             "corroboration) with their original source refs.",
             _obj({"case_ids": {"type": "array", "items": ID, "maxItems": 20},
                   "field_keys": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                   "limit": LIMIT}, [])),
]
_VALIDATORS = {t.name: Draft202012Validator(t.parameters) for t in TOOL_SPECS}


def build_index(rt, release_id: str) -> str:
    """Rebuildable FTS5 passage index for a release's sources. Returns index_version."""
    release = rt.db.get_object("release", release_id)
    ids = sorted(e["immutable_id"] for e in release["entries"] if e["kind"] == "input"
                 and rt.db.get_object("source", e["immutable_id"]))
    index_version = derived_id("IDX", release_id, [(i, rt.db.object_sha("source", i)) for i in ids], TOOL_VERSION)
    conn = rt.db.connect()
    if conn.execute("SELECT 1 FROM passages_fts WHERE index_version=? LIMIT 1", (index_version,)).fetchone():
        return index_version
    rows = []
    for sid in ids:
        src = rt.db.get_object("source", sid)["data"]
        for p in passages(src["text"]):
            pid = f"{sid}~{p['handle']}"
            rows.append((pid, index_version, sid, p["text"]))
    with rt.db.tx() as c:
        c.executemany("INSERT INTO passages_fts(passage_id, index_version, source_id, text) VALUES (?,?,?,?)", rows)
    rt.db.put_object("index_manifest", index_version, {"index_version": index_version, "release_id": release_id,
                                                        "source_ids": ids, "passages": len(rows),
                                                        "tool_version": TOOL_VERSION, "kind": "fts5-unicode61"})
    return index_version


class ToolHost:
    """Executes validated tool calls inside a sealed run context."""

    def __init__(self, rt, repo: Repo, *, run_id: str, index_version: str, case_scope: list[str] | None = None,
                 candidate_stage_ids: list[str] | None = None):
        self.rt, self.repo, self.run_id, self.index_version = rt, repo, run_id, index_version
        self.candidate_stage_ids = candidate_stage_ids or []
        self.case_scope = case_scope
        self.calls: list[dict] = []
        self.seen_requests: set[str] = set()
        self.search_scopes: list[dict] = []          # negative-search scopes for targeted invalidation

    def _failed_assets(self) -> list[str]:
        """Assets in scope whose extraction failed: visible in coverage, never silently absent."""
        out = []
        for e in self.repo.rv.release["entries"]:
            if e["kind"] != "asset":
                continue
            a = self.repo.asset(e["immutable_id"])
            if a is None or (self.case_scope is not None and not set(a["case_ids"]) & set(self.case_scope)):
                continue
            ext = self.repo.extraction(a["extraction_ids"][0]) if a["extraction_ids"] else None
            if ext is not None and ext["state"] == "failed":
                out.append(a["asset_version_id"])
        return sorted(out)

    def _envelope(self, items: list, *, truncated=False, limitations=None, cursor=None, eligible=None,
                  outcome: str | None = None) -> dict:
        ids = self.repo.all_source_ids()
        failed = self._failed_assets()
        lim = list(limitations or [])
        if failed:
            lim.append(f"{len(failed)} in-scope file(s) failed extraction and are not searchable/readable as text")
        return {"status": "ok", "items": items, "next_cursor": cursor, "scope_revision": self.repo.auth.permission_revision,
                "source_set_version": self.repo.rv.control["source_set_version"],
                "effective_evidence_as_of": self.repo.cutoff.isoformat(), "evidence_boundary": self.repo.boundary,
                "index_version": self.index_version, "truncated": truncated, "limitations": lim,
                "outcome": outcome or ("truncated" if truncated else "ok" if items else "no_match"),
                "coverage": {"eligible_source_count": len(ids) if eligible is None else eligible,
                             "indexed_source_count": len(ids), "failed_source_count": len(failed),
                             "failed_asset_version_ids": failed, "assessment_complete": False}}

    def _error(self, message: str) -> dict:
        return {"status": "unavailable", "error": {"code": "INVALID_REQUEST", "message": message, "retryable": False},
                "scope_revision": self.repo.auth.permission_revision,
                "source_set_version": self.repo.rv.control["source_set_version"],
                "effective_evidence_as_of": self.repo.cutoff.isoformat(), "evidence_boundary": self.repo.boundary}

    def execute(self, name: str, args: dict) -> dict:
        validator = _VALIDATORS.get(name)
        arg_hash = canonical_sha256({"name": name, "args": args})
        record = {"tool": name, "args_sha256": arg_hash, "intent": args.get("intent") if isinstance(args, dict) else None}
        if validator is None:
            out = self._error(f"unknown tool {name}")
        elif list(validator.iter_errors(args)):
            out = self._error("arguments rejected: " + next(validator.iter_errors(args)).message[:200])
        elif arg_hash in self.seen_requests:
            out = self._envelope([], limitations=["identical request already answered in this run; no new information"])
        else:
            self.seen_requests.add(arg_hash)
            out = getattr(self, "t_" + name)(**args)
        record.update(status=out["status"], returned_sources=sorted({i.get("source_id") for i in out.get("items", [])
                                                                     if isinstance(i, dict) and i.get("source_id")}))
        self.calls.append(record)
        return out

    def _case_ok(self, case_ids: list[str]) -> list[str]:
        allowed = [c for c in case_ids if self.case_scope is None or c in self.case_scope]
        return allowed

    def _passage(self, pid: str) -> dict | None:
        if "~" not in pid:
            return None
        sid, handle = pid.split("~", 1)
        rec = self.repo.source_record(sid)
        if rec is None:
            return None
        if self.case_scope is not None and not set(rec["data"]["case_ids"]) & set(self.case_scope):
            return None
        for p in passages(rec["data"]["text"]):
            if p["handle"] == handle:
                return {"passage_handle": pid, "source_id": sid, "content_sha256": rec["data"]["content_sha256"],
                        "start_codepoint": p["start"], "end_codepoint": p["end"], "text": p["text"],
                        "source_family": rec["data"]["source_family"], "subtype": rec["data"]["subtype"],
                        "author_role": rec["data"]["author_role"], "available_at": rec["envelope"]["available_at"],
                        "occurred_at": rec["envelope"]["occurred_at"]}
        return None

    # ---- tools -------------------------------------------------------------
    def t_search_evidence(self, query, intent, case_ids=(), source_families=(), limit=8):
        eligible = []
        for sid in self.repo.all_source_ids():
            data = self.repo.source_record(sid)["data"]
            if case_ids and not set(data["case_ids"]) & set(self._case_ok(list(case_ids))):
                continue
            if self.case_scope is not None and not set(data["case_ids"]) & set(self.case_scope):
                continue
            if source_families and data["source_family"] not in source_families:
                continue
            eligible.append(sid)
        terms = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", query) if len(t) > 1][:24]
        self.search_scopes.append({"query_terms": sorted(set(t.lower() for t in terms)), "intent": intent,
                                   "eligible_sha256": canonical_sha256(sorted(eligible)), "case_scope": self.case_scope})
        if not terms or not eligible:
            return self._envelope([], limitations=["no searchable terms or no eligible sources"], eligible=len(eligible),
                                  outcome="no_data")
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        marks = ",".join("?" * len(eligible))
        rows = self.rt.db.connect().execute(
            f"SELECT passage_id, source_id, bm25(passages_fts) AS score, snippet(passages_fts, 3, '[', ']', ' … ', 18) AS snip "
            f"FROM passages_fts WHERE passages_fts MATCH ? AND index_version=? AND source_id IN ({marks}) "
            f"ORDER BY score LIMIT ?", (match, self.index_version, *eligible, limit + 1)).fetchall()
        items = [{"passage_handle": r["passage_id"], "source_id": r["source_id"], "excerpt": r["snip"],
                  "score": round(-r["score"], 4), "mode": "lexical_bm25"} for r in rows[:limit]]
        return self._envelope(items, truncated=len(rows) > limit, eligible=len(eligible),
                              limitations=["lexical retrieval; score is ranking only, not evidence strength"])

    def t_read_passages(self, passage_handles):
        items, total, truncated = [], 0, False
        for pid in passage_handles[:MAX_PASSAGES_PER_READ]:
            p = self._passage(pid)
            if p is None:
                items.append({"passage_handle": pid, "status": "unavailable"})
                continue
            if total + len(p["text"]) > MAX_EXCERPT_CHARS:
                truncated = True
                break
            total += len(p["text"])
            items.append(p)
        return self._envelope(items, truncated=truncated)

    def t_read_case_sources(self, case_id, limit=20, cursor=None):
        if not self._case_ok([case_id]):
            return self._envelope([], limitations=["case outside the sealed run scope"])
        ids = self.repo.case_source_ids(case_id)
        start = int(cursor) if cursor and cursor.isdigit() else 0
        items = []
        for sid in ids[start:start + limit]:
            rec = self.repo.source_record(sid)
            d, env = rec["data"], rec["envelope"]
            items.append({"source_id": sid, "content_sha256": d["content_sha256"], "source_family": d["source_family"],
                          "subtype": d["subtype"], "author_role": d["author_role"], "available_at": env["available_at"],
                          "occurred_at": env["occurred_at"],
                          "passage_handles": [f"{sid}~{p['handle']}" for p in passages(d["text"])],
                          "opening_excerpt": d["text"][:300]})
        nxt = str(start + limit) if start + limit < len(ids) else None
        return self._envelope(items, cursor=nxt, eligible=len(ids))

    def t_read_related_entities(self, entity_id, entity_kind, relation_kinds=(), limit=10):
        """Accepted, published, source-backed analysis records linked to the entity (case scope enforced)."""
        from ..api.analysis_read import analysis_set_for
        aset = analysis_set_for(self.rt, self.repo.rv.release_id)
        if aset is None:
            return self._envelope([], limitations=["no accepted analysis set is published for this release; "
                                                   "relationships remain unknown (not zero)"], outcome="no_data")
        items = []
        for rid in aset.get("record_ids", []):
            rec = self.rt.db.get_object("analysis_record", rid)
            if rec is None or not self.repo.auth.record_permitted(rec) or not self.repo.available(rec["envelope"]["available_at"]):
                continue
            d = rec["data"]
            cases = set(d.get("case_ids") or []) | ({d["case_id"]} if d.get("case_id") else set())
            if self.case_scope is not None and not cases & set(self.case_scope):
                continue
            linked = entity_id in json.dumps(d)
            if not linked or (relation_kinds and not _relation(rec["kind"]) in relation_kinds):
                continue
            if any(self.repo.source_record(r["source_id"]) is None for r in rec["envelope"]["evidence_refs"]):
                continue
            items.append({"record_id": rid, "kind": rec["kind"], "relation": _relation(rec["kind"]), "data": d,
                          "source_refs": rec["envelope"]["evidence_refs"]})
        return self._envelope(items[:limit], truncated=len(items) > limit,
                              limitations=["accepted records only; unknown relationships stay unknown"])

    def t_read_procedures(self, process_query, effective_date=None, limit=5):
        procs = []
        for e in self.repo.rv.release["entries"]:
            rec = self.rt.db.get_object("native_record", e["immutable_id"]) if e["kind"] == "input" else None
            if rec is None or rec["kind"] != "procedure" or not self.repo.auth.record_permitted(rec) \
                    or not self.repo.available(rec["envelope"]["available_at"]):
                continue
            procs.append(rec)
        if not procs:
            return self._envelope([], limitations=["no procedure versions were delivered in this release"],
                                  outcome="no_data")
        terms = {t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", process_query)}
        items = []
        for rec in procs:
            d = rec["data"]
            text = json.dumps(d).lower()
            if terms and not any(t in text for t in terms):
                continue
            eff_from, eff_to = d.get("effective_from"), d.get("effective_to_exclusive") or d.get("effective_to")
            if effective_date and ((eff_from and effective_date < eff_from) or (eff_to and effective_date >= eff_to)):
                continue
            items.append({"record_id": rec["envelope"]["record_id"], "data": d,
                          "source_refs": rec["envelope"]["evidence_refs"]})
        return self._envelope(items[:limit], truncated=len(items) > limit,
                              limitations=["lexical procedure match; effective-date filter applied when supplied"])

    def t_inspect_candidate_facts(self, case_ids=(), field_keys=(), limit=20):
        wanted = set(self._case_ok(list(case_ids))) if case_ids else None
        if case_ids and not wanted:
            return self._envelope([], limitations=["requested cases are outside the sealed run scope"], outcome="no_data")
        items = []
        for stage_id in self.candidate_stage_ids:
            stage = self.rt.db.get_object("stage_s02", stage_id)
            for f in stage["facts"]:
                d = f["data"]
                ref = d["value"]["evidence_refs"][0]
                src = self.repo.source_record(ref["source_id"])
                if src is None:
                    continue
                fact_cases = set(src["data"]["case_ids"])
                if self.case_scope is not None and not fact_cases & set(self.case_scope):
                    continue                          # host scope: never another case's drafts
                if wanted is not None and not fact_cases & wanted:
                    continue
                if field_keys and d["field_key"] not in field_keys:
                    continue
                items.append({"fact_id": d["fact_id"], "field_key": d["field_key"], "value": d["value"]["value"],
                              "state": d["value"]["state"], "asserted_by_role": d["asserted_by_role"],
                              "source_id": ref["source_id"], "draft_status": "candidate_not_accepted"})
        return self._envelope(items[:limit], truncated=len(items) > limit,
                              limitations=["run-local drafts; not independent corroboration"])


def _relation(kind: str) -> str:
    return {"episode": "episode", "contact_link": "contact", "work_entry": "work", "obligation": "obligation",
            "financial_event": "obligation", "afca_matter": "afca", "afca_link": "afca", "outcome_target": "target",
            "membership_interval": "episode"}.get(kind, "provenance")
