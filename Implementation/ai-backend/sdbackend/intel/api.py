"""Companion read operations (backend-owned `intelligence-v1`): `intelligence.get`, `case.memory.get`.

Same `/v1/operations` transport, authentication, neutral-denial and error conventions as the frozen twelve. Reads
only sealed publications; NEVER starts model work. `intelligence.get` resolves (or reuses) a Shared-compatible
child context bound to the publication's knowledge view (`evidence_boundary = exclusive_stock_endpoint`, internal
temporal-policy sidecar) so the frozen drill-through operations on that context use the same `demo-held-day-v1`
availability as the result. Earlier views are served from the publication log (as reported then); a view with no
publication returns HISTORY_UNAVAILABLE, never a backfilled later diagnosis.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ..canonical import random_id
from . import temporal
from .publish import POINTER

SCHEMA = json.loads((Path(__file__).parent / "schemas" / "intelligence-v1.schema.json").read_text(encoding="utf-8"))
COMPANION_OPERATIONS = ("intelligence.get", "case.memory.get")


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]}, format_checker=FormatChecker())


VALIDATORS = {n: _validator(n) for n in ("intelligence.get.request", "case.memory.get.request", "ok_response",
                                         "error_response")}


def errors(name: str, doc) -> list[str]:
    return [e.message for e in VALIDATORS[name].iter_errors(doc)]


def publications(rt) -> list[dict]:
    """Every intel publication in pointer history order (as-reported log)."""
    rows = rt.db.connect().execute("SELECT object_id, body FROM objects WHERE kind='publication_event'").fetchall()
    evs = sorted((json.loads(r["body"]) for r in rows), key=lambda e: (e["published_at"], e["generation"]))
    out = []
    for e in evs:
        if e["pointer_key"] == POINTER:
            pub = rt.db.get_object("intel_publication", e["target_id"])
            if pub:
                out.append({**pub, "_event": e})
    return out


def publication_for(rt, view_date: str | None) -> dict | None:
    from .publish import current
    cur = current(rt)
    if view_date is None or cur is None or cur["closure"]["view_date"] == view_date:
        return cur
    # latest publication made FOR that view (as reported for that knowledge view)
    hits = [p for p in publications(rt) if p["closure"]["view_date"] == view_date]
    return hits[-1] if hits else None


def _coverage_counts(cov: dict) -> dict:
    """V01: the publication summary travels with every response, so it carries counts only — case-level lists
    are served by the population-guarded `coverage` section."""
    return {k: (len(v) if isinstance(v, list) else v) for k, v in cov.items()} | {"detail": "section=coverage"}


def summary(pub: dict, current_id: str | None) -> dict:
    c = pub["closure"]
    return {"publication_id": pub["publication_id"], "publication_policy": pub["publication_policy"],
            "temporal_policy": c["temporal_policy"], "view_date": c["view_date"],
            "knowledge_end_exclusive": c["knowledge_end_exclusive"], "watermarks": pub["watermarks"],
            "coverage": _coverage_counts(pub["coverage"]), "model_assessed": pub["model_assessed"],
            "human_review": pub["human_review"],
            "analysis_set_version": pub["analysis_set_version"], "stale": pub["publication_id"] != current_id}


def day_precision_map(rt, release_id: str) -> dict[str, str]:
    from .intake import availability_sidecar, crosswalk_for
    side = availability_sidecar(rt, release_id)
    out = {sid: v["date"] for sid, v in side.items()}
    for r in (crosswalk_for(rt, release_id) or {"rows": []})["rows"]:
        if r.get("asset_version_id") and any(s in side for s in r["source_ids"]):
            out[r["asset_version_id"]] = side[next(s for s in r["source_ids"] if s in side)]["date"]
    return out


def _clean_refs(refs) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "passage_handle"} | ({"passage_handle": r["passage_handle"]}
            if r.get("passage_handle") else {}) for r in refs or []]


def _strip_contributors(obj, population: set):
    """Capability restriction (no `contributors`): every case/member identifier is removed — lists become empty
    with an explicit `*_withheld` marker, dict keys naming cases are dropped — while values stay unchanged."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in population or (isinstance(v, str) and v in population):
                out.setdefault("case_keyed_detail_withheld", True)
                continue
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v) and set(v) & population:
                out[k], out[k + "_withheld"] = [], len(v)
                continue
            if k == "contributors" and isinstance(v, list):
                out[k], out[k + "_withheld"] = [], len(v)
                continue
            out[k] = _strip_contributors(v, population)
        return out
    if isinstance(obj, list):
        return [_strip_contributors(x, population) for x in obj if not (isinstance(x, str) and x in population)]
    return obj


class Companion:
    def __init__(self, ops):
        self.ops, self.rt = ops, ops.rt

    def handle(self, principal_id: str, body: dict) -> dict:
        from ..api.ops import OpError
        op = body.get("operation")
        request_id = random_id("REQ", 8)
        errs = errors(f"{op}.request", body)
        if errs:
            return self._checked(self._error(op, request_id, OpError("INVALID_REQUEST", "Request does not match "
                                                                     f"intelligence-v1 {op}: " + errs[0][:200]), None))
        try:
            ctx, payload = (self.intelligence(principal_id, body) if op == "intelligence.get"
                            else self.memory(principal_id, body))
            resp = {"operation": op, "request_id": request_id, "status": "ok", "context": ctx, "payload": payload}
        except OpError as exc:
            resp = self._error(op, request_id, exc, exc.context)
        except Exception:  # noqa: BLE001 - never leak internals
            import logging
            corr = random_id("CORR", 8)
            logging.getLogger("sdbackend.intel").exception("companion %s failed correlation=%s", op, corr)
            resp = self._error(op, request_id, OpError("INTERNAL_FAILURE", "Internal failure.", True), None, corr)
        return self._checked(resp)

    def _error(self, op, request_id, exc, context, corr=None) -> dict:
        return {"operation": op, "request_id": request_id, "status": "unavailable", "context": context,
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable,
                          "correlation_id": corr or random_id("CORR", 8)}}

    def _checked(self, resp: dict) -> dict:
        name = "ok_response" if resp.get("status") == "ok" else "error_response"
        errs = errors(name, resp)
        if errs:
            return {"operation": resp.get("operation", "?"), "request_id": resp["request_id"], "status": "unavailable",
                    "context": None, "error": {"code": "INTERNAL_FAILURE", "message": "Response withheld: failed "
                                               "intelligence-v1 validation.", "retryable": False,
                                               "correlation_id": random_id("CORR", 8)}}
        return resp

    # ---------------------------------------------------------------- contexts
    def _child_context(self, principal_id: str, pub: dict) -> dict:
        from ..api.ops import _iso
        from ..api.repo import served_release
        auth = self.ops._authority(principal_id)
        rv = served_release(self.rt)
        if rv is None or rv.release_id != pub["closure"]["release_id"]:
            from ..api.ops import OpError
            raise OpError("NOT_INGESTED", "The publication's release is not the served release.")
        aset = self.rt.db.get_object("analysis_set", pub["analysis_set_version"])
        now = datetime.now(timezone.utc)
        ctx = {"context_id": random_id("CTX", 12), "contract_version": "1.0.0", "release_id": rv.release_id,
               "scope_token": auth.scope_token, "view_id": auth.view_id, "permission_revision": auth.permission_revision,
               "capabilities": sorted(auth.capabilities), "timezone": "Australia/Melbourne",
               "evidence_as_of": pub["closure"]["knowledge_end_exclusive"],
               "evidence_boundary": "exclusive_stock_endpoint", "origin_population_reference": None,
               "analysis_mode": "reconstructed", "analysis_as_of": None,
               "source_set_version": rv.control["source_set_version"],
               "analysis_set_version": aset["analysis_set_version"], "taxonomy_version": aset.get("taxonomy_version"),
               "supported_definitions": aset.get("supported_definitions", []),
               "supported_response_dimensions": aset.get("supported_response_dimensions", []),
               "resolved_at": _iso(now), "expires_at": _iso(now + timedelta(seconds=self.rt.settings.context_ttl_seconds)),
               "synthetic_demo": True}
        self.rt.contracts.check("context", ctx)
        internal = {"release_status": rv.control["status"], "release_limitations": rv.control["limitations"],
                    "temporal_policy": temporal.POLICY_ID, "intel_publication_id": pub["publication_id"]}
        self.rt.state.put_context({"context_id": ctx["context_id"], "session_principal": principal_id,
                                   "body": json.dumps(ctx), "internal": json.dumps(internal),
                                   "expires_at": ctx["expires_at"]})
        return ctx

    def _context(self, principal_id: str, context_id: str | None, pub: dict) -> dict:
        if context_id is None:
            return self._child_context(principal_id, pub)
        ctx, internal, auth, repo = self.ops._load_context(principal_id, context_id)
        if internal.get("intel_publication_id") != pub["publication_id"]:
            from ..api.ops import OpError
            raise OpError("CONTEXT_EXPIRED", "Context belongs to another publication/view; resolve with context_id null.")
        return ctx

    # ---------------------------------------------------------------- intelligence.get
    def intelligence(self, principal_id: str, req: dict):
        from ..api.ops import OpError
        from .publish import current
        auth = self.ops._authority(principal_id)
        if not auth.can("aggregate"):
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.")
        sel = req["selection"] or {"temporal_policy": temporal.POLICY_ID}
        cur = current(self.rt)
        if cur is None:
            raise OpError("NOT_CALCULATED", "No intelligence publication exists yet for this store.")
        pub = publication_for(self.rt, sel.get("view_date"))
        if pub is None:
            raise OpError("HISTORY_UNAVAILABLE", "No publication was made for the requested knowledge view.")
        ctx = self._context(principal_id, req["context_id"], pub)
        res = pub["results"]
        section = sel.get("section") or "overview"
        grain = sel.get("grain") or "month"
        iid = sel.get("issue_id")
        detail = None
        if section == "overview":
            items = [{"overview": res["overview"], "issue_count": len(res["issues"]),
                      "alerts": [{k: a[k] for k in ("issue_id", "state")} for a in res["alerts"]],
                      "calendar_year_note": res["calendar_year_note"]}]
        elif section == "issues":
            items = [{"issue_id": i["issue_id"], "definition": next((x for x in pub["issues"] if x["issue_id"] == i["issue_id"]), None),
                      "membership": i["membership"], "affected_members": i["affected_members"],
                      "open_included": i["open_included"]} for i in res["issues"]]
        elif section == "issue":
            v = next((i for i in res["issues"] if i["issue_id"] == iid), None)
            if v is None:
                raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.")
            series = res["issue_series"][iid][grain] if grain in res["issue_series"][iid] else []
            items = series
            detail = {"issue": v, "definition": next((x for x in pub["issues"] if x["issue_id"] == iid), None),
                      "alert": next((a for a in res["alerts"] if a["issue_id"] == iid), None), "grain": grain,
                      "frozen_drill_through": {
                          "taxonomy_node_id": iid,
                          "analysis_query_template": {"operation": "analysis.query", "context_id": ctx["context_id"],
                                                      "definition": {"registry_id": "runtime-measures-0.2.0",
                                                                     "measure_id": "issues.linked_cases",
                                                                     "definition_version": "0.2.0"},
                                                      "filters": "see reports/intel examples: issue_ids=[node], "
                                                                 "selection_operator=any"}}}
        elif section == "alerts":
            items = res["alerts"]
        elif section == "priorities":
            items = res["priorities"]
        elif section == "arrivals":
            items = res["arrivals"].get(grain, [])
        elif section == "burden":
            items = res["burden_series"].get(grain, []) if grain in res["burden_series"] else []
            detail = {"total": res["overview"]["burden_total"],
                      "unallocated_to_any_issue": res["overview"]["burden_unallocated_to_any_issue"]}
        elif section == "experience":
            items = [res["experience"]]
        elif section == "afca":
            if "afca" not in res:
                raise OpError("NOT_CALCULATED", "This publication predates AFCA procedure coverage.", context=ctx)
            items = res["afca"]["matters"]
            detail = {k: v for k, v in res["afca"].items() if k != "matters"}
        elif section == "continuity":
            if "continuity" not in res:
                raise OpError("NOT_CALCULATED", "This publication predates continuity coverage.", context=ctx)
            items = res["continuity"]["repeat_case_links"]
            detail = {"summary": res["continuity"]["summary"], "cases": res["continuity"]["cases"],
                      "note": res["continuity"]["note"]}
        elif section == "coverage":
            items = [pub["coverage"] | {"checks": pub["checks"]}]
        else:                                                   # publications (as-reported log)
            items = [summary(p, cur["publication_id"]) for p in publications(self.rt)]
        if section != "publications":
            # V01: every aggregate section was computed over the publication's whole sealed population; if any
            # represented case is outside this principal's authority the sealed result is incompatible and is
            # withheld as a whole (never silently shortened while keeping the original value)
            self._population_permitted(auth, pub, ctx)
        if not auth.can("contributors"):
            pop = set(pub["case_views"]) | set(res["overview"]["members"]["contributors"])
            items, detail = _strip_contributors(items, pop), _strip_contributors(detail, pop)
        page, nxt = self._page(ctx, f"intel:{section}:{iid}:{grain}", items, req["cursor"], req["page_size"])
        payload = {"publication": summary(pub, cur["publication_id"]), "section": section, "items": page,
                   "next_cursor": nxt, "total_items": len(items)}
        if detail is not None:
            payload["detail"] = detail
        return ctx, payload

    def _population_permitted(self, auth, pub, ctx) -> None:
        from ..api.ops import OpError
        population = set(pub["case_views"]) | set(pub["memory_by_case"])
        if auth.case_scope == "none" or any(not auth.case_permitted(c) for c in population):
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.",
                          context=ctx)

    def _permitted(self, auth, items):
        if auth.case_scope == "none":
            from ..api.ops import OpError
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.")
        if not auth.denied_case_ids:
            return items
        text = json.dumps(items)
        if any(c in text for c in auth.denied_case_ids):     # any denied case in a contributor set: neutral denial
            from ..api.ops import OpError
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.")
        return items

    def _page(self, ctx, stream, items, cursor, page_size):
        page, nxt, _ = self.ops._page(ctx, stream, stream, items, cursor, page_size)
        return page, nxt

    # ---------------------------------------------------------------- case.memory.get
    def memory(self, principal_id: str, req: dict):
        from ..api.ops import OpError
        from .publish import current
        ctx, internal, auth, repo = self.ops._load_context(principal_id, req["context_id"])
        pub = self.rt.db.get_object("intel_publication", internal.get("intel_publication_id") or "")
        if pub is None:
            raise OpError("NOT_CALCULATED", "This context is not bound to an intelligence publication; resolve one "
                                            "with intelligence.get.")
        if not auth.can("cases") or not auth.case_permitted(req["case_id"]):
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.", context=ctx)
        mid = req["memory_revision_id"] or pub["memory_by_case"].get(req["case_id"])
        mem = self.rt.db.get_object("case_memory", mid) if mid else None
        if mem is None or mem["case_id"] != req["case_id"]:
            raise OpError("NOT_CALCULATED" if req["case_id"] in pub["case_views"] else "RESOURCE_UNAVAILABLE",
                          "No case memory at this view (unassessed or not eligible)." if req["case_id"] in
                          pub["case_views"] else "The requested resource is not available in this context.",
                          context=ctx)
        if datetime.fromisoformat(mem["knowledge_end_exclusive"]) > datetime.fromisoformat(ctx["evidence_as_of"]):
            raise OpError("NOT_CALCULATED", "That memory revision uses knowledge later than this context; a later "
                                            "understanding never labels an earlier view.", context=ctx)
        # V01: the narrative, factors, service, commitments, contacts and memberships are all derived from the
        # memory's evidence; if ANY cited/consumed source (claims, retrieved passages, table sources) is outside
        # this principal's authority, the whole interpretation is withheld neutrally rather than returned with
        # its restricted dependencies hidden
        memberships = self._memberships(pub, mem)
        deps = ({c["source_id"] for c in mem["claims"]} | set(mem["inputs"].get("table_sources") or []) |
                {r["source_id"] for m_ in memberships for r in m_["retrieved_support_refs"] +
                 m_["retrieved_contrary_refs"]})
        if any(repo.source_record(sid) is None for sid in sorted(deps)):
            raise OpError("RESOURCE_UNAVAILABLE", "The requested resource is not available in this context.",
                          context=ctx)
        claims = list(mem["claims"])
        withheld = 0
        page, nxt = self._page(ctx, f"memory:{mem['memory_id']}", claims, req["cursor"], req["page_size"])
        chain, cur_mem = [], mem
        while cur_mem is not None:
            chain.append({"memory_id": cur_mem["memory_id"], "revision": cur_mem["revision"],
                          "reason": cur_mem["reason"], "view_date": cur_mem["view_date"]})
            cur_mem = self.rt.db.get_object("case_memory", cur_mem["predecessor_id"]) if cur_mem["predecessor_id"] else None
        detail = {k: mem[k] for k in ("memory_id", "case_id", "member_references", "revision", "predecessor_id",
                                      "reason", "temporal_policy", "knowledge_end_exclusive", "view_date",
                                      "native_case", "narrative", "factors", "service", "commitments", "contacts",
                                      "member_effort", "responses", "handoffs", "contradictions",
                                      "unresolved_questions", "lifecycle", "money_work_refs", "coverage", "critique",
                                      "producer", "model_assessed", "human_review")}
        detail["revision_history"] = chain
        detail["claims_withheld_outside_context"] = withheld
        detail["memberships"] = memberships
        payload = {"publication": summary(pub, (current(self.rt) or {}).get("publication_id")), "section": "memory",
                   "items": page, "next_cursor": nxt, "total_items": len(claims), "detail": detail}
        return ctx, payload

    def _memberships(self, pub, mem) -> list[dict]:
        from .discovery import ASSESS_PROMPT
        from ..canonical import derived_id
        out = []
        for iss in pub["issues"]:
            if iss["status"] != "active":
                continue
            a = self.rt.db.get_object("membership_assessment", derived_id("MBA", mem["memory_id"], iss["definition_id"],
                                                                           ASSESS_PROMPT))
            out.append({"issue_id": iss["issue_id"], "label": iss["label"],
                        "state": a["state"] if a else "unassessed",
                        "factor_id": a.get("factor_id") if a else None,
                        "support_claim_ids": a["support_claim_ids"] if a else [],
                        "contrary_claim_ids": a["contrary_claim_ids"] if a else [],
                        # V02: retrieved original passages, kept on their own side, each a citation for source.get
                        "retrieved_support_refs": _clean_refs((a or {}).get("retrieved_support_refs") if a and
                                                              "retrieved_support_refs" in a else
                                                              (a or {}).get("retrieved_evidence_refs")),
                        "retrieved_contrary_refs": _clean_refs((a or {}).get("retrieved_contrary_refs")),
                        "evidence_basis": None if not a else
                        ("memory_claims+retrieved_passages" if a["support_claim_ids"] and
                         (a.get("retrieved_support_refs") or a.get("retrieved_evidence_refs")) else
                         "retrieved_passages" if (a.get("retrieved_support_refs") or a.get("retrieved_evidence_refs"))
                         else "memory_claims"),
                        "claims_shown": (a or {}).get("claims_shown"), "claims_total": (a or {}).get("claims_total"),
                        "retrieval": (a or {}).get("retrieval"),
                        "reason": a["reason"] if a else None})
        return out
