"""The twelve frozen Shared 1.0.0 operations.

Every request is validated against its exact frozen request definition and every response against its frozen
response definition before it leaves the service (fail closed → INTERNAL_FAILURE). Denied and nonexistent
resources both return a neutral RESOURCE_UNAVAILABLE. Read operations never start model work.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..canonical import canonical_sha256, derived_id, random_id
from ..citations import check_citation, map_citation
from ..policy import Authority, resolve_authority
from ..runtime import Runtime
from . import analysis_read
from .repo import Repo, ReleaseView, parse_dt, served_release

log = logging.getLogger("sdbackend.ops")

OPERATIONS = ("context.resolve", "analysis.query", "findings.list", "contributors.list", "taxonomy.get", "case.get",
              "case.sources.list", "case.files.list", "entity.get", "source.get", "asset.get", "location.resolve")
MAX_PAGE = 100


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class OpError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, context: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.context = code, message, retryable, context


UNAVAILABLE = "RESOURCE_UNAVAILABLE"


def _neutral() -> OpError:
    return OpError(UNAVAILABLE, "The requested resource is not available in this context.")


class Operations:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.c = rt.contracts

    # ------------------------------------------------------------------ entry
    def handle(self, principal_id: str, body: Any) -> dict:
        request_id = random_id("REQ", 8)
        op = body.get("operation") if isinstance(body, dict) else None
        if op in ("intelligence.get", "case.memory.get"):
            from ..intel.api import Companion          # backend-owned additive companion (intelligence-v1)
            return Companion(self).handle(principal_id, body)
        if op not in OPERATIONS:
            return {"_http_status": 400, "error": "unknown operation"}
        errors = self.c.errors(f"{op}.request", body)
        if errors:
            resp = self._error(op, request_id, OpError("INVALID_REQUEST", "Request does not match the frozen "
                                                       f"{op} request definition: " + errors[0][:200]), None)
            return self._checked(op, resp)
        try:
            handler: Callable = getattr(self, "op_" + op.replace(".", "_"))
            context, payload = handler(principal_id, body)
            resp = {"operation": op, "request_id": request_id, "status": "ok", "context": context, "payload": payload}
        except OpError as exc:
            resp = self._error(op, request_id, exc, exc.context)
        except Exception:  # noqa: BLE001 - never leak internals
            corr = random_id("CORR", 8)
            log.exception("operation %s failed correlation=%s", op, corr)
            resp = self._error(op, request_id, OpError("INTERNAL_FAILURE", "Internal failure.", True), None, corr)
        return self._checked(op, resp)

    def _error(self, op, request_id, exc: OpError, context, corr=None) -> dict:
        return {"operation": op, "request_id": request_id, "status": "unavailable", "context": context,
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable,
                          "correlation_id": corr or random_id("CORR", 8)}}

    def _checked(self, op: str, resp: dict) -> dict:
        errors = self.c.errors(f"{op}.response", resp)
        if errors:
            corr = random_id("CORR", 8)
            log.error("response for %s failed frozen schema (%s) correlation=%s: %s", op, resp.get("status"), corr, errors[:3])
            return {"operation": op, "request_id": resp["request_id"], "status": "unavailable", "context": None,
                    "error": {"code": "INTERNAL_FAILURE", "message": "Response withheld: failed contract validation.",
                              "retryable": False, "correlation_id": corr}}
        return resp

    # ------------------------------------------------------------- contexts
    def _authority(self, principal_id: str) -> Authority:
        auth = resolve_authority(principal_id)
        if auth is None:
            raise _neutral()
        return auth

    def _load_context(self, principal_id: str, context_id: str) -> tuple[dict, dict, Authority, Repo]:
        row = self.rt.state.get_context(context_id)
        if row is None or row["session_principal"] != principal_id:
            raise OpError("CONTEXT_EXPIRED", "Context is not available; resolve a new context.")
        ctx, internal = json.loads(row["body"]), json.loads(row["internal"])
        auth = self._authority(principal_id)
        if parse_dt(row["expires_at"]) <= datetime.now(timezone.utc):
            raise OpError("CONTEXT_EXPIRED", "Context expired; resolve it again.")
        if auth.permission_revision != ctx["permission_revision"]:
            raise OpError("CONTEXT_EXPIRED", "Authority changed; resolve a new context.")
        rv = served_release(self.rt)
        if rv is None or rv.release_id != ctx["release_id"]:
            raise OpError("CONTEXT_INCOMPATIBLE", "The context's release is no longer served.")
        day = None
        if internal.get("temporal_policy") == "demo-held-day-v1":
            from ..intel.api import day_precision_map
            day = day_precision_map(self.rt, rv.release_id)
        repo = Repo(self.rt, rv, auth, ctx["evidence_as_of"], ctx["evidence_boundary"], day_precision=day)
        return ctx, internal, auth, repo

    def op_context_resolve(self, principal_id: str, req: dict):
        auth = self._authority(principal_id)
        rv = served_release(self.rt)
        if rv is None or rv.release_id != req["release_id"]:
            raise _neutral()
        boundary = "inclusive_cutoff"
        evidence_as_of = req["evidence_as_of"]
        mode, analysis_as_of = req["analysis_mode"], req["analysis_as_of"] if req["analysis_mode"] == "as_reported" else None
        if mode == "as_reported":
            aset = analysis_read.as_reported_available(self.rt, rv.release_id, analysis_as_of)
            if aset is None:
                raise OpError("HISTORY_UNAVAILABLE", "No analysis was published by the requested as-reported time.")
        else:
            aset = analysis_read.analysis_set_for(self.rt, rv.release_id)
        origin = None
        if req["population_reference"] is not None:
            found = analysis_read.child_origin(self.rt, req["population_reference"], auth)
            if found is None:
                raise OpError("NOT_CALCULATED" if aset is None else UNAVAILABLE,
                              "No published population matches this reference.")
            result, cell, binding = found
            pinned = self.rt.db.get_object("analysis_set", result["result_context"]["versions"]["analysis_set_version"])
            stored = result["result_context"]["context"]
            if pinned is None or pinned["release_id"] != rv.release_id or \
                    analysis_read.pinned_set(self.rt, {**stored, "release_id": rv.release_id}) is None:
                raise _neutral()
            aset, origin = pinned, req["population_reference"]
            # the child inherits the binding's exact evidence/analysis lineage; request preferences cannot widen it
            evidence_as_of, boundary = binding["effective_evidence_as_of"], binding["evidence_boundary"]
            mode, analysis_as_of = stored["analysis_mode"], stored["analysis_as_of"]
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.rt.settings.context_ttl_seconds)
        ctx = {
            "context_id": random_id("CTX", 12), "contract_version": "1.0.0", "release_id": rv.release_id,
            "scope_token": auth.scope_token, "view_id": auth.view_id, "permission_revision": auth.permission_revision,
            "capabilities": sorted(auth.capabilities), "timezone": "Australia/Melbourne",
            "evidence_as_of": evidence_as_of, "evidence_boundary": boundary, "origin_population_reference": origin,
            "analysis_mode": mode, "analysis_as_of": analysis_as_of,
            "source_set_version": rv.control["source_set_version"],
            "analysis_set_version": aset["analysis_set_version"] if aset else None,
            "taxonomy_version": aset.get("taxonomy_version") if aset else None,
            "supported_definitions": aset.get("supported_definitions", []) if aset else [],
            "supported_response_dimensions": aset.get("supported_response_dimensions", []) if aset else [],
            "resolved_at": _iso(now), "expires_at": _iso(expires), "synthetic_demo": True}
        internal = {"release_status": rv.control["status"], "release_limitations": rv.control["limitations"]}
        self.rt.state.put_context({"context_id": ctx["context_id"], "session_principal": principal_id,
                                   "body": json.dumps(ctx), "internal": json.dumps(internal),
                                   "expires_at": ctx["expires_at"]})
        return ctx, ctx

    # -------------------------------------------------------------- cursors
    def _page(self, ctx: dict, stream: str, binding: Any, items: list, cursor: str | None, page_size: int):
        binding_sha = canonical_sha256({"stream": stream, "binding": binding, "ctx": ctx["context_id"],
                                        "perm": ctx["permission_revision"]})
        offset = 0
        if cursor is not None:
            row = self.rt.state.get_cursor(cursor)
            if row is None or row["context_id"] != ctx["context_id"] or row["binding"] != binding_sha:
                raise OpError("INVALID_REQUEST", "Cursor does not belong to this stream/context.")
            offset = row["offset_value"]
        page_size = min(page_size, MAX_PAGE)
        page = items[offset:offset + page_size]
        nxt = None
        if offset + page_size < len(items):
            nxt = "CUR-" + secrets.token_urlsafe(18)
            self.rt.state.put_cursor(nxt, {"context_id": ctx["context_id"], "stream": stream, "binding": binding_sha,
                                           "offset_value": offset + page_size, "expires_at": ctx["expires_at"]})
        return page, nxt, derived_id("STREAM", stream, binding, ctx["context_id"])

    # ---------------------------------------------------- source inventory
    def op_case_sources_list(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        ids = repo.case_source_ids(req["case_id"])
        if not ids:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        self._case_in_population(ctx, repo, req["case_id"])
        fams = set(req["source_families"])
        rows = []
        for sid in ids:
            rec = repo.source_record(sid)
            data, env = rec["data"], rec["envelope"]
            if fams and data["source_family"] not in fams:
                continue
            assets = sorted(a for a in self.rt.db.linked("asset", "source", sid) if repo.asset(a) is not None)
            occ = ({"precision": "instant", "instant": env["occurred_at"]} if env["occurred_at"] else
                   {"precision": "day", "date": env["occurrence_date"]} if env["occurrence_date"] else
                   {"precision": "unknown", "reason": "no evidenced occurrence time"})
            rows.append({"source_id": sid, "content_sha256": data["content_sha256"], "source_family": data["source_family"],
                         "subtype": data["subtype"], "scenario_available_at": env["available_at"], "occurrence": occ,
                         "permitted_asset_version_ids": assets})
        page, nxt, stream = self._page(ctx, "case.sources", [req["case_id"], sorted(fams)], rows, req["cursor"], req["page_size"])
        limitations = list(internal["release_limitations"])
        if internal["release_status"] != "accepted":
            limitations.insert(0, "Sources are from an unreviewed candidate release; independent source review pending.")
        limitations.append("Inventory is the permitted selected-corpus stream, not whole-institution completeness.")
        return ctx, {"case_id": req["case_id"], "stream_reference": stream, "total_permitted_count": len(rows),
                     "completeness": "partial", "items": page, "next_cursor": nxt, "limitations": limitations}

    # ------------------------------------------------------- file inventory
    def _public_asset(self, repo: Repo, a: dict) -> dict:
        parents = sorted({p for p in a["derived_from_asset_version_ids"] if repo.asset(p) is not None})
        actions = []
        if self._representation_allowed(repo, a, "inline"):
            actions.append("inline")
        if self._representation_allowed(repo, a, "download"):
            actions.append("download")
        return {"asset_version_id": a["asset_version_id"], "logical_document_id": a["logical_document_id"],
                "version": a["version"], "representation_role": a["representation_role"], "filename": a["filename"],
                "media_type": a["media_type"], "byte_size": a["byte_size"], "bytes_sha256": a["bytes_sha256"],
                "scenario_document_time": a["scenario_document_time"], "scenario_available_at": a["scenario_available_at"],
                "permitted_source_ids": sorted(s for s in a["source_ids"] if repo.source_record(s) is not None),
                "permitted_parent_asset_version_ids": parents,
                "lineage_disclosure": "complete_permitted" if len(parents) == len(a["derived_from_asset_version_ids"]) else "limited",
                "permitted_actions": actions}

    def _representation_allowed(self, repo: Repo, a: dict, action: str) -> bool:
        need = "previews" if a["representation_role"] == "preview" else "originals"
        if not repo.auth.can(need):
            return False
        if action == "download" and not repo.auth.can("download"):
            return False
        return repo.container_fully_permitted(a)

    def op_case_files_list(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        ids = repo.case_asset_ids(req["case_id"])
        if not ids:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        self._case_in_population(ctx, repo, req["case_id"])
        mts = set(req["media_types"])
        rows = []
        for aid in ids:
            a = repo.asset(aid)
            if mts and a["media_type"] not in mts:
                continue
            ext_state, map_state = "not_applicable", "not_applicable"
            if a["extraction_ids"]:
                ext = repo.extraction(a["extraction_ids"][0])
                ext_state = ext["state"] if ext else "pending"
                locs = [m for s in a["source_ids"] for m in repo.locators_for_source(s) if m["asset_version_id"] == aid]
                if locs:
                    states = {m["state"] for m in locs}
                    map_state = "invalid" if "invalid" in states else "unmapped" if "unmapped" in states else "verified"
                # no published citation bound to this file yet: mapping is not applicable (not "unmapped")
            parent_sources, occs = set(), []
            for att in repo.attachments_of_child(aid):
                if repo.attachment_permitted(att):
                    parent_sources.add(att["parent_source_id"])
                    occs.append(att["attachment_occurrence_id"])
            rows.append({"asset": self._public_asset(repo, a), "permitted_parent_source_ids": sorted(parent_sources),
                         "permitted_attachment_occurrence_ids": sorted(occs), "extraction_state": ext_state,
                         "mapping_state": map_state})
        page, nxt, _ = self._page(ctx, "case.files", [req["case_id"], sorted(mts)], rows, req["cursor"], req["page_size"])
        return ctx, {"case_id": req["case_id"], "total_permitted_count": len(rows), "items": page, "next_cursor": nxt}

    # ------------------------------------------------------------ source.get
    def op_source_get(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        rec = repo.source_record(req["source_id"])
        if rec is None:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        data = rec["data"]
        if req["content_sha256"] is not None and req["content_sha256"] != data["content_sha256"]:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        citation, state = req["citation"], "not_requested"
        if citation is not None:
            state = "valid" if check_citation(data, citation) is None else "invalid"
        extractions, actions, mappings = [], [], []
        for aid in self.rt.db.linked("asset", "source", data["source_id"]):
            a = repo.asset(aid)
            for eid in (a["extraction_ids"] if a else []):
                ext = repo.extraction(eid)
                if ext is None:
                    continue
                extractions.append({
                    "extraction_id": ext["extraction_id"], "version": ext["version"],
                    "permitted_asset_version_id": a["asset_version_id"], "permitted_asset_bytes_sha256": a["bytes_sha256"],
                    "extractor": ext["extractor"], "extractor_version": ext["extractor_version"],
                    "processed_at": ext["processed_at"], "state": ext["state"], "omissions": ext["omissions"],
                    "permitted_emitted_sources": [b for b in ext["emitted_sources"] if repo.source_record(b["source_id"])],
                    "lineage_disclosure": "complete_permitted"})
                if citation is not None and state == "valid":
                    mappings.append(map_citation(data, repo.layout(eid), ext, a, citation))
            if a:
                for role_asset in [a] + [repo.asset(p) for p in a["preview_ids"]]:
                    if role_asset is None:
                        continue
                    for action in ("inline", "download"):
                        if self._representation_allowed(repo, role_asset, action):
                            actions.append(self._issue_action(principal_id, ctx, role_asset, action))
        mappings += [m for m in repo.locators_for_source(data["source_id"])
                     if citation is not None and m["citation"] == citation and m not in mappings]
        atts = []
        for att in repo.attachments_of_parent(data["source_id"]):
            if not repo.attachment_permitted(att):
                continue
            atts.append({"attachment_occurrence_id": att["attachment_occurrence_id"], "parent_source_id": att["parent_source_id"],
                         "parent_source_sha256": att["parent_source_sha256"], "child_asset_version_id": att["child_asset_version_id"],
                         "relation": att["relation"], "mime_part_path": att["mime_part_path"],
                         "advertised_filename": att["advertised_filename"], "scenario_available_at": att["scenario_available_at"],
                         "permitted_copy_of_attachment_occurrence_id": None})
        return ctx, {"record": rec, "source_binding": {"source_id": data["source_id"], "content_sha256": data["content_sha256"]},
                     "citation": citation, "citation_state": state, "extractions": extractions,
                     "permitted_asset_actions": actions, "permitted_attachments": atts, "mappings": mappings}

    # ------------------------------------------------------------- assets
    def _issue_action(self, principal_id: str, ctx: dict, a: dict, action: str) -> dict:
        token = "TOK-" + secrets.token_urlsafe(24)
        expires = min(datetime.now(timezone.utc) + timedelta(seconds=self.rt.settings.asset_token_ttl_seconds),
                      parse_dt(ctx["expires_at"]))
        representation = {"supplied_original": "original", "derived_redacted": "derived_redacted",
                          "preview": "preview"}[a["representation_role"]]
        disposition = "inline" if action == "inline" else "attachment"
        self.rt.state.put_token(token, {"principal_id": principal_id, "context_id": ctx["context_id"],
                                        "asset_version_id": a["asset_version_id"], "representation": representation,
                                        "disposition": disposition, "permission_revision": ctx["permission_revision"],
                                        "expires_at": _iso(expires)})
        return {"action_id": derived_id("ACT", token), "asset_version_id": a["asset_version_id"],
                "representation": representation, "disposition": disposition, "retrieval_token": token,
                "expires_at": _iso(expires), "media_type": a["media_type"], "bytes_sha256": a["bytes_sha256"]}

    def op_asset_get(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        a = repo.asset(req["asset_version_id"])
        if a is None or not self._representation_allowed(repo, a, req["action"]):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        return ctx, self._issue_action(principal_id, ctx, a, req["action"])

    def redeem(self, principal_id: str, token: str) -> tuple[dict, bytes] | None:
        """Revalidate an opaque asset action at use: owner, expiry, authority revision, context and container."""
        row = self.rt.state.get_token(token)
        if row is None or row["principal_id"] != principal_id:
            return None
        if parse_dt(row["expires_at"]) <= datetime.now(timezone.utc):
            raise OpError("CONTEXT_EXPIRED", "expired")
        try:
            ctx, _, auth, repo = self._load_context(principal_id, row["context_id"])
        except OpError:
            # a context expiring does not by itself revoke a still-valid token; recheck authority directly
            auth = self._authority(principal_id)
            if auth.permission_revision != row["permission_revision"]:
                return None
            rv = served_release(self.rt)
            ctxrow = self.rt.state.get_context(row["context_id"])
            if rv is None or ctxrow is None:
                return None
            ctx = json.loads(ctxrow["body"])
            # the fallback never bypasses an incompatible served release, view or principal
            if rv.release_id != ctx["release_id"] or auth.view_id != ctx["view_id"] or \
                    ctx["permission_revision"] != row["permission_revision"]:
                return None
            repo = Repo(self.rt, rv, auth, ctx["evidence_as_of"], ctx["evidence_boundary"])
        if auth.permission_revision != row["permission_revision"]:
            return None
        a = repo.asset(row["asset_version_id"])
        action = "inline" if row["disposition"] == "inline" else "download"
        if a is None or not self._representation_allowed(repo, a, action):
            return None
        data = self.rt.blobs.get(a["bytes_sha256"])
        return {"asset": a, "disposition": row["disposition"]}, data

    # --------------------------------------------------------- location
    def op_location_resolve(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        citation = req["citation"]
        rec = repo.source_record(citation["source_id"])
        a = repo.asset(req["asset_version_id"])
        ext = repo.extraction(req["extraction_id"])
        if rec is None or a is None or ext is None or not auth.can("originals"):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        return ctx, map_citation(rec["data"], repo.layout(ext["extraction_id"]), ext, a, citation)

    # -------------------------------------------------------------- case
    def op_case_get(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        if not repo.case_source_ids(req["case_id"]):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        self._case_in_population(ctx, repo, req["case_id"])
        aset = analysis_read.pinned_set(self.rt, ctx)
        found = None if aset is None else analysis_read.case_payload(self.rt, aset, repo, ctx, req)
        if found is None:
            raise OpError("NOT_CALCULATED", "No accepted case register record and state projection are published "
                          "for this case yet; its sources and files remain available.", context=ctx)
        events, base = found
        page, nxt, stream = self._page(ctx, "case.timeline", [req["case_id"]], events, req["cursor"], req["page_size"])
        return ctx, {**base, "events": page, "stream_reference": stream, "total_permitted_event_count": len(events),
                     "next_cursor": nxt}

    # ---------------------------------------------------------- analysis
    def _aset_or_raise(self, ctx):
        """The context's pinned immutable set; a withdrawn/incompatible set is refused, never silently replaced."""
        aset = analysis_read.pinned_set(self.rt, ctx)
        if aset is None:
            raise OpError("NOT_CALCULATED", "No accepted analysis set is available for this context.", context=ctx)
        return aset

    def _population(self, ctx, aset, repo, ref=None) -> list[str] | None:
        try:
            restriction, _ = analysis_read.population_restriction(self.rt, aset, repo, ctx, ref)
        except LookupError:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        return restriction

    def _case_in_population(self, ctx, repo, case_id: str) -> None:
        """Child contexts navigate only within their frozen origin population."""
        if ctx.get("origin_population_reference") is None:
            return
        aset = analysis_read.pinned_set(self.rt, ctx)
        if aset is None or case_id not in (self._population(ctx, aset, repo) or []):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)

    def op_analysis_query(self, principal_id, req):
        from ..engine.results import HistoryUnavailable, PopulationDenied, Unsupported
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        if not auth.can("aggregate"):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        d = req["definition"]
        if not analysis_read.registered(self.c, d):
            raise OpError("DEFINITION_UNAVAILABLE", "Definition is not registered at this version.", context=ctx)
        aset = self._aset_or_raise(ctx)
        try:
            result = analysis_read.find_result(self.rt, aset, repo, ctx, req)
        except (LookupError, PopulationDenied) as exc:
            if isinstance(exc, HistoryUnavailable):
                raise OpError("HISTORY_UNAVAILABLE", "No compatible result was published by the as-reported time.",
                              context=ctx)
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        except Unsupported as exc:
            raise OpError("INVALID_REQUEST", f"Unsupported parameter for this definition: {exc}", context=ctx)
        if result is None:
            raise OpError("NOT_CALCULATED", "This definition/selection has not been calculated for the context.", context=ctx)
        return ctx, result

    def op_findings_list(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        if not auth.can("cases"):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        aset = self._aset_or_raise(ctx)
        population = self._population(ctx, aset, repo, req["population_reference"])
        if req["result_id"] is not None:
            res = self.rt.db.get_object("result", req["result_id"])
            if res is None or res["result_context"]["versions"]["analysis_set_version"] != aset["analysis_set_version"] \
                    or not analysis_read.compatible(res["result_context"]["context"], ctx):
                raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
            cases = set()
            for cell in res["cells"]:
                cset = self.rt.db.get_object("contributor_set", cell["contributor_set_id"] or "")
                if cset and analysis_read.members(repo, cset) is not None:
                    cases |= set(analysis_read.members(repo, cset))
                elif cset:
                    raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
            population = sorted(cases if population is None else cases & set(population))
        items = analysis_read.findings(self.rt, aset, repo, req, population)
        page, nxt, stream = self._page(ctx, "findings", {k: req[k] for k in ("result_id", "case_id", "record_kinds",
                                       "membership_dispositions", "population_reference")}, items, req["cursor"], req["page_size"])
        return ctx, {"items": page, "population_reference": req["population_reference"],
                     "total_permitted_count": len(items), "stream_reference": stream, "next_cursor": nxt}

    def op_contributors_list(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        if not auth.can("contributors") or not auth.can("aggregate"):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        aset = self._aset_or_raise(ctx)
        found = analysis_read.contributors(self.rt, aset, repo, ctx, req)
        if found is None:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        items, header = found
        page, nxt, _ = self._page(ctx, "contributors", [req["result_id"], req["cell_key"]], items, req["cursor"], req["page_size"])
        return ctx, {**header, "items": page, "next_cursor": nxt}

    def op_taxonomy_get(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        aset = self._aset_or_raise(ctx)
        tax = analysis_read.taxonomy(self.rt, aset, req["taxonomy_version"])
        if tax is None:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        return ctx, tax

    def op_entity_get(self, principal_id, req):
        ctx, internal, auth, repo = self._load_context(principal_id, req["context_id"])
        if not auth.can("cases"):
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        aset = self._aset_or_raise(ctx)
        found = analysis_read.entity(self.rt, aset, repo, req, self._population(ctx, aset, repo))
        if found is None:
            raise OpError(UNAVAILABLE, "The requested resource is not available in this context.", context=ctx)
        related, header = found
        page, nxt, stream = self._page(ctx, "entity.links", [req["entity_id"], req["entity_kind"]], related,
                                       req["cursor"], req["page_size"])
        return ctx, {**header, "related_records": page, "next_cursor": nxt,
                     "total_permitted_related_count": len(related), "stream_reference": stream}
