"""Atomic, immutable portfolio publication under the explicit `synthetic-demo-v1` policy (M4).

One publication binds an exact set: release + source set, head case memories at the view, the issue registry,
membership assessments, deterministic table projections and the computed results, with input / assessment /
publication watermarks. Two pointers move in ONE transaction:

* `intel:current` → `intel_publication` (read by the companion operations `intelligence.get`, `case.memory.get`);
* `analysis:<release>` → a Shared-compatible `analysis_set` (case views, finding records, taxonomy, membership
  intervals, ledger/work records) so the twelve frozen operations (`analysis.query`, `contributors.list`,
  `case.get`, `findings.list`, `taxonomy.get`, …) serve the same publication.

No independent approval is minted. Acceptance records written here are mechanical (`independent: false`), name the
policy and the checks performed, and every published model output carries `model_assessed=true,
human_review=not_performed`. Superseded memories/memberships are replaced by the successor publication; earlier
publications stay replayable through the publication log (as-reported history).
"""
from __future__ import annotations

from datetime import date

from ..acceptance import mechanical
from ..api.analysis_read import analysis_pointer_key
from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..engine.core import parse_instant
from ..engine.publish import _supported
from ..policy import permission_revision, record_access
from . import measures, temporal
from .discovery import ASSESS_PROMPT, active_issues, registry_head

PUBLISHER_VERSION = "sdb-intel-publish-1.0.0"
POINTER = "intel:current"
EVENT_TYPE = {"complaint": "contact", "request": "request", "submission": "contact", "acknowledgement": "receipt",
              "receipt": "receipt", "assessment": "assessment", "decision": "decision", "approval": "approval",
              "instruction": "request", "execution": "execution", "confirmation": "execution", "handoff": "request",
              "waiting": "other", "promise": "other", "contact_attempt": "attempt", "contact_completed": "contact",
              "chase": "contact", "explanation": "other", "response_position": "contact", "payment": "financial",
              "closure": "closure", "reopening": "reopening", "afca": "afca", "error": "other", "other": "other"}
DIMENSION = {"service": "original_problem", "external": "original_problem", "handling": "handling_factor",
             "communication": "handling_factor", "dependency": "current_dependency"}


class PublicationRefused(RuntimeError):
    pass


def _mech(rt, kind: str, sid: str, sha: str, gate: str, limitations: list[str]) -> str:
    return mechanical(rt, subject_kind=kind, subject_id=sid, subject_sha256=sha, gate=gate, result="pass",
                      checker=PUBLISHER_VERSION, limitations=limitations)


def portfolio(rt, ctx) -> tuple[measures.Portfolio, dict]:
    from .worker import heads
    E = ctx.knowledge_end
    reg = registry_head(rt)
    issues = active_issues(reg)
    mems = {cid: m for cid, m in heads(rt).items() if cid in set(ctx.eligible) and ctx.in_view(cid)
            and parse_instant(m["knowledge_end_exclusive"]) <= E}
    cases = {}
    for cid, n in ctx.natives.items():
        if not ctx.in_view(cid):
            continue
        trans = []
        for t in ctx.tables["lifecycle"].get(cid, []):
            sc = ctx.sidecar.get(t["source_id"])
            trans.append({**{k: t[k] for k in ("state", "effective_at", "effective_date", "available_at",
                                               "evidence_refs", "lifecycle_event_id", "source_id")},
                          "available_precision": sc})
        ev = [s["envelope"]["available_at"] for s in ctx.inputs(cid).narrative]
        cases[cid] = {"receipt_date": n["data"].get("complaints_receipt_date"), "members": ctx.members.get(cid, []),
                      "eligible": cid in set(ctx.eligible), "transitions": trans, "evidence_available": ev,
                      "native_record_id": n["envelope"]["record_id"]}
    ships, assessment_ids, unassessed = {}, [], []
    for cid, m in mems.items():
        by_claim = {c["claim_id"]: c for c in m["claims"]}
        for iss in issues:
            aid = derived_id("MBA", m["memory_id"], iss["definition_id"], ASSESS_PROMPT)
            a = rt.db.get_object("membership_assessment", aid)
            if a is None:
                unassessed.append((cid, iss["issue_id"]))
                continue
            days = sorted(by_claim[c]["business_date"] or by_claim[c]["source_day"] for c in a["support_claim_ids"]
                          if c in by_claim)
            fac = next((f for f in m["factors"] if f["factor_id"] == a.get("factor_id")), None)
            ships[(cid, iss["issue_id"])] = {**a, "first_evidenced": days[0] if days else None,
                                             "association": ("historical" if fac and fac["status"] == "ceased" else
                                                             "active" if fac and fac["status"] == "active" else
                                                             "unknown")}
            assessment_ids.append(aid)
    recs = []
    for r in ctx.tables["s04"]["analysis_records"]:
        if r["kind"] not in ("work_entry", "financial_event", "obligation"):
            continue
        sid = r["envelope"]["evidence_refs"][0]["source_id"] if r["envelope"]["evidence_refs"] else None
        if temporal.available(r["envelope"]["available_at"], ctx.sidecar.get(sid), E):
            recs.append(r)
    from . import procedures
    p = measures.Portfolio(view_date=ctx.view_date, knowledge_end=E, cases=cases, issues=issues, memberships=ships,
                           records=recs, memories=mems)
    p.afca = procedures.afca_matters(ctx, recs)                       # V07/A10: deterministic, cited
    p.continuity = procedures.continuity(cases, mems, ctx.crosswalk, p.afca["matters"])   # V07/A09
    return p, {"registry": reg, "assessment_ids": sorted(assessment_ids), "unassessed_pairs": unassessed}


def _ref_of(m: dict, claim_ids: list[str]) -> list[dict]:
    by = {c["claim_id"]: c for c in m["claims"]}
    return [by[c]["ref"] for c in claim_ids if c in by]


def _bridge(rt, ctx, p: measures.Portfolio, meta: dict, version: str, published_at: str, source_acc: str) -> dict:
    """Shared-compatible objects for the frozen operations, all bound to this publication."""
    c = rt.contracts
    E_iso = ctx.knowledge_end.isoformat()
    stage_id = derived_id("DEMOSTAGE", version)
    views, records, intervals, nodes = {}, [], {}, {}
    for cid, case in sorted(p.cases.items()):
        native = ctx.natives[cid]
        m = p.memories.get(cid)
        st, used = p.state_at(cid, ctx.knowledge_end)
        events = []
        if m:
            for cl in m["claims"]:
                ev = {"event_id": derived_id("EV", cid, cl["claim_id"]),
                      "occurrence": {"precision": "day", "date": cl["business_date"] or cl["source_day"]},
                      "event_type": EVENT_TYPE.get(cl["kind"], "other"), "proposition": cl["quote"][:500],
                      "source_refs": [cl["ref"]], "observation_reference": None, "state": "supported"}
                if not c.errors("timeline_event", ev):
                    events.append(ev)
        svc = (m or {}).get("service") or {"state": "unknown", "claim_ids": [], "reason": "not assessed"}
        projection = {"projection_id": derived_id("PROJ", version, cid), "case_id": cid,
                      "assessed_evidence_as_of": E_iso, "computed_at": published_at, "published_at": published_at,
                      "source_set_version": ctx.rv.control["source_set_version"], "analysis_set_version": version,
                      "administrative": {"value": st if st in ("open", "awaiting", "closed", "reopened", "conflicting")
                                         else "unknown", "evidence_refs": [r for t in used for r in t["evidence_refs"]],
                                         "reason": "latest held lifecycle transition at the view (deterministic table "
                                                   "projection)" if used else "no lifecycle transition held at the view"},
                      "merits": [], "service": [{"target_reference": None,
                                                 "value": {"pending": "not_started"}.get(it["state"], it["state"]),
                                                 "assertion_refs": _ref_of(m, it["claim_ids"]),
                                                 "confirmation_refs": [], "contradictory_refs": [],
                                                 "reason": (it["action"] + (": " + it["reason"] if it.get("reason")
                                                                            else ""))[:480]}
                                                for it in (svc.get("items") or [dict(svc, action="service")])]
                      if m else [],
                      "responses": [], "coverage": [], "resolver_version": PUBLISHER_VERSION}
        c.check("state_projection", projection)
        lim = ["synthetic-demo-v1: model_assessed case memory, human review not performed"] if m else \
            ["not assessed by a model: case outside the explicit eligible worklist (reserved)" if not case["eligible"]
             else "no case memory at this view"]
        cov = (m or {}).get("coverage") or {}
        for o in cov.get("partial_extraction_originals", []):      # V05: carry partial extraction to the case
            lim.append(f"original {o['original_id']} only partially extracted ("
                       + "; ".join(o.get("omissions") or ["partial"])[:200] + "); its usable text was read")
        for oid in cov.get("failed_or_unextracted_originals", []):
            lim.append(f"original {oid} could not be extracted; its content is not represented")
        if cov.get("state") == "no_grounded_claims":
            lim.append("no grounded claims remain at this view (evidence withdrawn or unreadable)")
        views[cid] = {"case_record": native, "state_projection": projection, "events": events,
                      "completeness": "complete_permitted" if m and m["coverage"]["state"] == "complete_read" else "partial",
                      "limitations": lim, "administrative_transitions": case["transitions"], "stage_id": stage_id,
                      "memory_id": m["memory_id"] if m else None}
    for r in p.records:
        records.append(r)
    taxonomy_version = derived_id("TAXV", version)
    for iss in p.issues:
        fid = derived_id("FND", iss["issue_id"], iss["definition_id"], ctx.view_date.isoformat(), version)
        members, sup, con = [], [], []
        for cid in sorted(p.memories):
            a = p.memberships.get((cid, iss["issue_id"]))
            if a is None:
                continue
            m = p.memories[cid]
            strip = lambda rs: [{k: v for k, v in r.items() if k != "passage_handle"} for r in rs or []]
            # V02: retrieved passages keep their side — support stays support, contrary stays contrary
            refs = _ref_of(m, a["support_claim_ids"]) + strip(a.get("retrieved_support_refs") if "retrieved_support_refs"
                                                               in a else a.get("retrieved_evidence_refs"))
            cref = _ref_of(m, a["contrary_claim_ids"]) + strip(a.get("retrieved_contrary_refs"))
            state = {"conflicting": "uncertain"}.get(a["state"], a["state"])
            members.append({"case_id": cid, "episode_id": None, "state": state,
                            "reason": ((a["reason"] or "") + (" [conflicting evidence]" if a["state"] == "conflicting"
                                                               else ""))[:300] or a["state"],
                            "association": a["association"], "evidence_refs": refs})
            if state == "included":
                sup += refs
                con += cref
            if state in ("included", "uncertain") and refs:
                start = {"precision": "day", "date": a["first_evidenced"]} if a.get("first_evidenced") else \
                    {"precision": "unknown", "reason": "first evidenced date not established"}
                ivid = derived_id("MIV", fid, cid, version)
                iv = {"membership_version_id": ivid, "finding_id": fid, "finding_version": "v1",
                      "taxonomy_node_id": iss["issue_id"], "taxonomy_version": taxonomy_version, "case_id": cid,
                      "episode_id": None, "state": state, "start": start, "end": None,
                      "ongoing_at": E_iso if a["association"] == "active" else None,
                      "endpoint_state": a["association"], "supporting_refs": refs, "contradictory_refs": cref,
                      "assessed_evidence_as_of": E_iso, "published_at": published_at}
                if not c.errors("membership_interval", iv):
                    intervals[ivid] = iv
        included = [x for x in members if x["state"] == "included"]
        unass = [cid for (cid, i) in meta["unassessed_pairs"] if i == iss["issue_id"]]
        missing = ["synthetic-demo-v1: membership model_assessed; human review not performed"]
        if unass:
            missing.append(f"{len(unass)} eligible case memories not assessed for this definition")
        if p.cases and any(not v["eligible"] for v in p.cases.values()):
            missing.append("cases outside the explicit eligible worklist are unassessed, not excluded")
        rec = {"schema_version": "0.2.0", "kind": "finding",
               "envelope": {"record_id": fid, "logical_id": iss["issue_id"], "supersedes_record_id": None,
                            "recorded_at": published_at, "available_at": E_iso, "occurred_at": None,
                            "occurrence_date": None, "access": record_access(), "evidence_refs": sup[:20]},
               "data": {"finding_id": fid, "version": "v1", "assessed_view_id": "complaints-leadership",
                        "assessed_snapshot": E_iso, "source_version": ctx.rv.control["source_set_version"],
                        "published_at": published_at, "title": iss["label"][:200], "claim": iss["definition"][:1000],
                        "original_failure": None, "handling_amplification": None, "current_dependency": None,
                        "evidence_state": ("conflicting" if con and len(included) >= 2 else
                                           "supported" if len(included) >= 2 else "insufficient"),
                        "supporting_refs": sup, "contradictory_refs": con,
                        "alternatives": [iss["alternatives"]] if iss.get("alternatives") else [],
                        "missing_facts": missing, "memberships": members, "human_review_state": "not_reviewed",
                        "temporal_flags": [], "temporal_rule_version": temporal.POLICY_ID,
                        "serious_review_concern": False}}
        errs = c.errors("analysis_record", rec, document="runtime")
        if errs:
            raise PublicationRefused(f"finding {fid} invalid: {errs[0][:200]}")
        records.append(rec)
        nodes[iss["issue_id"]] = {"node_id": iss["issue_id"], "level": iss["level"], "label": iss["label"][:200],
                                  "definition": iss["definition"][:500],
                                  "dimension": DIMENSION.get(iss["dimension"], "original_problem"),
                                  "parent_node_id": None, "aliases": [], "permitted_finding_ids": [fid]}
    taxonomy = None
    if nodes:
        taxonomy = {"taxonomy_version": taxonomy_version, "published_at": published_at, "acceptance_id": source_acc,
                    "nodes": [nodes[k] for k in sorted(nodes)],
                    "lineage_notes": ["open-vocabulary issues proposed by the collective investigator "
                                      "(model_assessed; human review not performed)"]
                    + list((meta["registry"] or {}).get("lineage_notes", []))}
        c.check("taxonomy", taxonomy)
    for r in records:
        errs = c.errors("analysis_record", r, document="runtime")
        if errs:
            raise PublicationRefused(f"record {r['envelope']['record_id']} invalid: {errs[0][:200]}")
    day_prec = {}
    for r in records:
        if r["kind"] == "finding" or not r["envelope"]["evidence_refs"]:
            continue
        sc = ctx.sidecar.get(r["envelope"]["evidence_refs"][0]["source_id"])
        if sc:
            day_prec[r["envelope"]["record_id"]] = sc["date"]
    return {"views": views, "records": records, "intervals": intervals, "taxonomy": taxonomy, "stage_id": stage_id,
            "day_precision_available": day_prec}


def dangling_refs(mem: dict) -> list[str]:
    """Claim ids referenced by any derived part of a memory that are not among its own claims."""
    held = {c["claim_id"] for c in mem["claims"]}
    refs = set()
    for f in mem.get("factors", []):
        for k in ("support_claim_ids", "contrary_claim_ids", "began_claim_ids", "ceased_claim_ids"):
            refs |= set(f.get(k) or [])
    for n in mem.get("narrative", []):
        refs |= set(n.get("claim_ids") or [])
    svc = mem.get("service") or {}
    refs |= set(svc.get("claim_ids") or [])
    for i in svc.get("items", []):
        refs |= set(i.get("claim_ids") or []) | set(i.get("completion_claim_ids") or [])
    for c in mem.get("commitments", []):
        refs |= {c["promise_claim_id"]} | set(c.get("evidence_claim_ids") or [])
    for key in ("member_effort", "responses", "handoffs", "contradictions"):
        for x in mem.get(key, []):
            refs |= set(x.get("claim_ids") or [])
    return sorted(refs - held)


def publish_view(rt, ctx, corpus, *, step_id: str) -> dict:
    p, meta = portfolio(rt, ctx)
    results = measures.compute(p)
    published_at = utc_now_iso()
    mem_ids = sorted(m["memory_id"] for m in p.memories.values())
    closure = {"release_id": ctx.rv.release_id, "release_sha256": rt.db.object_sha("release", ctx.rv.release_id),
               "view_date": ctx.view_date.isoformat(), "knowledge_end_exclusive": ctx.knowledge_end.isoformat(),
               "temporal_policy": temporal.POLICY_ID, "memory_ids": mem_ids,
               "registry_id": (meta["registry"] or {}).get("registry_id"), "assessment_ids": meta["assessment_ids"],
               "tables_digest": ctx.tables["summary"]["digest"], "results_digest": results["results_digest"],
               "publisher_version": PUBLISHER_VERSION, "measures_version": measures.MEASURES_VERSION}
    closure_sha = canonical_sha256(closure)
    pub_id = derived_id("IPUB", closure_sha)
    existing = rt.db.get_object("intel_publication", pub_id)
    cur = rt.db.get_pointer(POINTER)
    if existing is not None and cur is not None and cur["target_id"] == pub_id:
        return {"publication_id": pub_id, "state": "unchanged_reused", "analysis_set_version":
                existing["analysis_set_version"]}
    version = derived_id("ASET", "demo", closure_sha)
    # ---- mechanical checks (the only gate under this policy; no independent decision is created)
    checks = {"memories_bound_to_view": all(parse_instant(m["knowledge_end_exclusive"]) <= ctx.knowledge_end
                                            for m in p.memories.values()),
              "memberships_bound_to_head_memories": all(a["memory_id"] == p.memories[c]["memory_id"]
                                                        for (c, _), a in p.memberships.items()),
              "contributors_within_view": all(c in p.cases for v in results["issues"]
                                              for c in v["membership"].get("included", {"contributors": []})["contributors"]),
              "no_withdrawn_sources_in_memories": all(
                  rt.db.get_object("source", cl["source_id"]) is not None and any(
                      s["source_id"] == cl["source_id"] for s in p.memories[cid]["inputs"]["narrative_sources"])
                  for cid in p.memories for cl in p.memories[cid]["claims"]),
              "event_union_dedup": len({r["envelope"]["record_id"] for r in p.records}) == len(p.records),
              # V03: no current interpretation may reference a claim that is not held in its own memory
              "no_dangling_claim_refs": all(not dangling_refs(m) for m in p.memories.values())}
    if not all(checks.values()):
        raise PublicationRefused(f"mechanical checks failed: {[k for k, v in checks.items() if not v]}")
    source_acc = _mech(rt, "demo_source_basis", corpus.corpus_id, corpus.demo_binding()["source_quality_basis"]["sha256"],
                       "demo_source_basis", [f"synthetic-demo-v1: source quality per {corpus.demo_binding()['source_quality_basis']['path']}",
                                             "per-item independent source review not performed"])
    integrity = _mech(rt, "intel_publication_closure", pub_id, closure_sha, "demo_publication_integrity",
                      ["synthetic-demo-v1 mechanical checks: " + ", ".join(sorted(checks)),
                       "not an independent result-integrity review"])
    b = _bridge(rt, ctx, p, meta, version, published_at, source_acc)
    consumed = sorted({cl["source_id"] for m in p.memories.values() for cl in m["claims"]} |
                      {r["source_id"] for rec in b["records"] for r in rec["envelope"]["evidence_refs"]} |
                      {r["source_id"] for v in b["views"].values() for t in v["administrative_transitions"]
                       for r in t["evidence_refs"]})
    deps = {"natives": {v["case_record"]["envelope"]["record_id"]: source_acc for v in b["views"].values()},
            "extractions": {}, "sources": {s: source_acc for s in consumed},
            "stages": {b["stage_id"]: {"acceptance_id": integrity, "sources": consumed, "extractions": []}},
            "records": {r["envelope"]["record_id"]: b["stage_id"] for r in b["records"]},
            "case_stages": {}, "result_integrity": integrity}
    natives = [v["case_record"] for v in b["views"].values()]
    supported = _supported(natives, list(b["views"].values()), b["records"])
    aset = {"analysis_set_version": version, "assessment_id": derived_id("ASSESS", version),
            "published_at": published_at, "release_id": ctx.rv.release_id, "closure_candidate_id": pub_id,
            "closure_sha256": closure_sha,
            "case_record_ids": sorted(n["envelope"]["record_id"] for n in natives),
            "native_record_ids": sorted(n["envelope"]["record_id"] for n in natives),
            "assessed_evidence_as_of": ctx.knowledge_end.isoformat(),
            "taxonomy_version": b["taxonomy"]["taxonomy_version"] if b["taxonomy"] else None,
            "supported_definitions": supported, "supported_response_dimensions": [],
            "record_ids": sorted(r["envelope"]["record_id"] for r in b["records"]),
            "cases": {cid: derived_id("CVIEW", version, cid) for cid in b["views"]},
            "membership_interval_ids": sorted(b["intervals"]), "mx_record_ids": [],
            "discovery_coverage": ((meta["registry"] or {}).get("discovery") or None) and {
                "unit_notes": "memories = eligible case memories scanned by the collective investigator",
                "candidate_passages": 0, "examined_passages": 0, "omitted_passages": 0, "failed_passages": 0,
                "truncated_searches": 0,
                "failed_batches": len(meta["registry"]["discovery"].get("failed_pages", [])),
                "unassessed_case_ids": sorted({c for c, _ in meta["unassessed_pairs"]} |
                                              {c for c, v in p.cases.items() if not v["eligible"]}),
                "incomplete_reasons": ["discovery recall not established; assessment covers eligible memories only"],
                "exhaustive": False},
            "assessment_coverage": {}, "financial_history_uncertainties": [],
            "entities": {f"{r['kind']}:{r['envelope']['logical_id']}": r["envelope"]["record_id"] for r in b["records"]},
            "related": {}, "result_ids": [], "acceptance_ids": sorted({source_acc, integrity}),
            "integrity_acceptance_id": integrity, "dependency_closure": deps,
            "day_precision_available": b["day_precision_available"],
            "publication_policy": "synthetic-demo-v1", "human_review": "not_performed",
            "notes": ["synthetic-demo-v1: mechanical checks only; model outputs are model_assessed and not "
                      "independently reviewed", f"intel publication {pub_id}"]}
    prev = rt.db.get_object("intel_publication", cur["target_id"]) if cur else None
    watermarks = {"input_watermark": max((s for c in p.cases.values() for s in c["evidence_available"]),
                                         key=lambda x: parse_instant(x), default=None),
                  "knowledge_end_exclusive": ctx.knowledge_end.isoformat(),
                  "last_memory_assessment": max((m["produced_at"] for m in p.memories.values()), default=None),
                  "last_registry": (meta["registry"] or {}).get("produced_at"),
                  "published_at": published_at}
    pending = [c for c in ctx.eligible if ctx.in_view(c) and c not in p.memories]
    pub = {"publication_id": pub_id, "publisher_version": PUBLISHER_VERSION, "closure": closure,
           "closure_sha256": closure_sha, "step_id": step_id, "corpus_id": corpus.corpus_id,
           "predecessor_id": prev["publication_id"] if prev else None, "analysis_set_version": version,
           "taxonomy_version": aset["taxonomy_version"], "source_set_version": ctx.rv.control["source_set_version"],
           "watermarks": watermarks, "checks": checks, "integrity_acceptance_id": integrity,
           "coverage": {"cases_in_view": len(p.cases), "eligible_in_view": sum(v["eligible"] for v in p.cases.values()),
                        "memories": len(p.memories), "pending_memories": pending,
                        "not_eligible_unassessed": sorted(c for c, v in p.cases.items() if not v["eligible"]),
                        "unassessed_pairs": [list(x) for x in meta["unassessed_pairs"]],
                        "extraction_partial_or_failed": [r["original_id"] for r in ctx.crosswalk["rows"]
                                                         if r["extraction_state"] != "complete"]},
           "issues": [{k: i[k] for k in ("issue_id", "definition_id", "definition_version", "label", "level",
                                          "dimension", "definition", "include_if", "exclude_if", "lineage", "status")}
                      | {"alternatives": i.get("alternatives")} for i in (meta["registry"] or {}).get("issues", [])],
           "results": results, "case_views": aset["cases"], "memory_by_case": {c: m["memory_id"] for c, m in
                                                                             p.memories.items()},
           "publication_policy": "synthetic-demo-v1", "model_assessed": True, "human_review": "not_performed",
           "published_at": published_at}
    akey = analysis_pointer_key(ctx.rv.release_id)
    with rt.db.tx() as c:
        for r in b["records"]:
            rt.db.put_object("analysis_record", r["envelope"]["record_id"], r, conn=c,
                             links={"case": sorted(set(r["data"].get("case_ids") or [])
                                                   | ({r["data"]["case_id"]} if r["data"].get("case_id") else set())
                                                   | {m["case_id"] for m in r["data"].get("memberships", [])})})
        for cid, v in b["views"].items():
            rt.db.put_object("case_view", aset["cases"][cid], v, conn=c, links={"case": [cid]})
        if b["taxonomy"]:
            if c.execute("SELECT 1 FROM objects WHERE kind='taxonomy' AND object_id=?",
                         (b["taxonomy"]["taxonomy_version"],)).fetchone() is None:
                rt.db.put_object("taxonomy", b["taxonomy"]["taxonomy_version"], b["taxonomy"], conn=c)
        for iid, iv in b["intervals"].items():
            rt.db.put_object("membership_interval", iid, iv, conn=c, links={"case": [iv["case_id"]]})
        rt.db.put_object("analysis_set", version, aset, conn=c, links={"release": [ctx.rv.release_id]})
        rt.db.put_object("intel_publication", pub_id, pub, conn=c, links={"release": [ctx.rv.release_id]})
        perm = permission_revision()
        for key, kind, tid in ((akey, "analysis_set", version), (POINTER, "intel_publication", pub_id)):
            row = c.execute("SELECT generation FROM publication_pointers WHERE pointer_key=?", (key,)).fetchone()
            gen = 1 if row is None else row["generation"] + 1
            sha = c.execute("SELECT sha256 FROM objects WHERE kind=? AND object_id=?", (kind, tid)).fetchone()["sha256"]
            c.execute("INSERT OR REPLACE INTO publication_pointers VALUES (?,?,?,?,?,?,?,?)",
                      (key, kind, tid, sha, gen, integrity, perm, published_at))
            ev = derived_id("PUBEV", key, gen, tid)
            rt.db.put_object("publication_event", ev, {"event_id": ev, "pointer_key": key, "generation": gen,
                                                        "target_kind": kind, "target_id": tid,
                                                        "release_id": ctx.rv.release_id, "published_at": published_at,
                                                        "acceptance_id": integrity}, conn=c,
                             links={"release": [ctx.rv.release_id]})
    return {"publication_id": pub_id, "state": "published", "analysis_set_version": version,
            "taxonomy_version": aset["taxonomy_version"], "issues": len(p.issues), "memories": len(p.memories),
            "supported_definitions": [d["measure_id"] for d in supported], "watermarks": watermarks}


def current(rt) -> dict | None:
    row = rt.db.get_pointer(POINTER)
    if row is None:
        return None
    pub = rt.db.get_object("intel_publication", row["target_id"])
    if pub is None or rt.db.object_sha("intel_publication", row["target_id"]) != row["target_sha256"]:
        return None
    return pub
