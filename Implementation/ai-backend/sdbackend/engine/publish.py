"""Analysis-set publication (AI-PA-01, R01/R10): closure → exact independent decisions → fenced CAS publication.

1. `prepare_closure` computes the exact input closure of an analysis set from the SOURCE RELEASE ITSELF: native
   case/party/procedure rows that entered through reviewed intake (never a side-door argument), staged S03/S05
   outputs proposed for publication, the analysis records they carry, the algorithm version and the definitions
   actually supported by those inputs. It seals an `analysis_closure` candidate and lists the decisions required.
2. Independent reviewers decide on those exact subjects (imported via `acceptance.import_decision`):
   * every native row: `native_input` pass bound to its canonical record digest;
   * every staged diagnostic output: `diagnostic_evaluation` pass bound to the stage object digest, and every
     source it consumed must sit in the release under an effective independent `source` pass;
   * the closure itself: `result_integrity` pass bound to the closure digest (deterministic/result gate; it never
     approves model diagnoses — purely native arithmetic needs no diagnostic gold).
3. `publish_analysis_set` recomputes the closure (detecting any change), re-checks every decision is still
   effective, refuses unresolved revisions, seals case views/records/set, then CAS-swaps the pointer with the
   generation read BEFORE the checks (a concurrent publication refuses this one) and appends a publication event.
   Refusal leaves the last accepted pointer untouched. Re-publishing the same closure is idempotent.
"""
from __future__ import annotations

from ..acceptance import effective, is_pass, mechanical, still_effective
from ..api.analysis_read import analysis_pointer_key
from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..contracts import EXPECTED_MANIFEST_SHA256
from ..policy import permission_revision
from .results import ALGORITHM_VERSION, MEASURES, REG

PUBLISHER_VERSION = "sdb-publish-2.1.0"


class PublicationRefused(RuntimeError):
    pass


def _release_entries(rt, release_id: str) -> dict[tuple[str, str], dict]:
    release = rt.db.get_object("release", release_id)
    if release is None:
        raise PublicationRefused("unknown release")
    return {(e["kind"], e["immutable_id"]): e for e in release["entries"]}


def _native_rows(rt, entries) -> list[dict]:
    out = []
    for (kind, oid) in sorted(entries):
        if kind != "input":
            continue
        rec = rt.db.get_object("native_record", oid)
        if rec is not None:
            out.append(rec)
    return out


def _supported(natives: list[dict], views: list[dict], records: list[dict], mx_kinds=frozenset()) -> list[dict]:
    kinds = {r["kind"] for r in records}
    have_cases = any(n["kind"] == "case" for n in natives)
    have_transitions = any(v.get("administrative_transitions") for v in views)
    want = set()
    if have_cases:
        want.add("cases.new")
    if have_cases and have_transitions:
        want |= {"cases.open", "cases.age_distribution"}
    if {"financial_event", "obligation"} <= kinds:
        want |= {"cash.remedies_paid", "cash.expenses_paid", "cash.expenses_and_remedies_paid",
                 "obligations.outstanding"}
    if "work_entry" in kinds:
        want.add("work.recorded_hours")
    if "afca_matter" in kinds:
        want |= {"afca.new", "afca.open"}
    if "contact_link" in kinds:
        want.add("contacts.repeat")
        if have_transitions:
            want.add("contacts.post_closure_return")
    if have_cases and "finding" in kinds:
        want.add("issues.linked_cases")
        if have_transitions:
            want.add("issues.open_linked_cases")
    out = [{"registry_id": REG, "measure_id": m, "definition_version": "0.2.0"}
           for m in sorted(want) if (REG, m, "0.2.0") in MEASURES]
    mx_want = {"commitment": "mx.commitments.follow_through",
               "member_effort_event": "mx.effort.repeat_valid_evidence_requests",
               "member_outcome_response": "mx.responses.expressed_positions"}
    return out + [{"registry_id": "shared-measures-1.0.0", "measure_id": mx_want[k], "definition_version": "1.0.0"}
                  for k in sorted(mx_kinds) if k in mx_want]


def _stage(rt, stage_id: str) -> tuple[str, dict]:
    for kind in ("stage_s03", "stage_s04", "stage_s05"):
        body = rt.db.get_object(kind, stage_id)
        if body is not None:
            return kind, body
    raise PublicationRefused(f"staged output {stage_id} not found")


def _closure(rt, release_id: str, assessed_evidence_as_of: str, stage_ids: list[str]) -> dict:
    entries = _release_entries(rt, release_id)
    natives = _native_rows(rt, entries)
    stages, views, records, mx_kinds = [], [], [], set()
    for sid in sorted(set(stage_ids)):
        kind, body = _stage(rt, sid)
        mx_kinds |= {m["kind"] for m in body.get("mx_records", [])}
        if "mx.commitments.follow_through" in body.get("assessment_coverage", {}):
            mx_kinds.add("commitment")
        stages.append({"stage_id": sid, "kind": kind, "sha256": rt.db.object_sha(kind, sid)})
        if kind == "stage_s03":
            views.append(body)
        records += body.get("analysis_records", [])
    consumed = sorted({src["source_id"] for sid in sorted(set(stage_ids))
                       for src in _stage(rt, sid)[1].get("consumed_sources", [])})
    extractions = []
    for (kind, eid) in sorted(entries):
        if kind != "extraction":
            continue
        ext = rt.db.get_object("extraction", eid)
        if ext and any(s["source_id"] in consumed for s in ext.get("emitted_sources", [])):
            extractions.append({"extraction_id": eid, "sha256": rt.db.object_sha("extraction", eid)})
    for r in records:
        errs = rt.contracts.errors("analysis_record", r, document="runtime")
        if errs:
            raise PublicationRefused(f"analysis record {r['envelope']['record_id']} invalid: {errs[0][:160]}")
    return {"release_id": release_id, "release_sha256": rt.db.object_sha("release", release_id),
            "assessed_evidence_as_of": assessed_evidence_as_of, "contract_baseline": EXPECTED_MANIFEST_SHA256,
            "native": [{"record_id": n["envelope"]["record_id"], "sha256": canonical_sha256(n)} for n in natives],
            "extractions": extractions,
            "stages": stages,
            "records": sorted(({"record_id": r["envelope"]["record_id"], "sha256": canonical_sha256(r)} for r in records),
                              key=lambda x: x["record_id"]),
            "algorithm_version": ALGORITHM_VERSION, "publisher_version": PUBLISHER_VERSION,
            "supported_definitions": _supported(natives, views, records,
                                                frozenset(mx_kinds))}


def prepare_closure(rt, *, release_id: str, assessed_evidence_as_of: str, stage_ids: list[str] = ()) -> dict:
    closure = _closure(rt, release_id, assessed_evidence_as_of, list(stage_ids))
    sha = canonical_sha256(closure)
    cid = derived_id("CLOSURE", sha)
    rt.db.put_object("analysis_closure", cid, {"candidate_id": cid, "closure_sha256": sha, "closure": closure})
    required = [{"gate": "native_input", "subject_kind": "native_record", "subject_id": n["record_id"],
                 "subject_sha256": n["sha256"]} for n in closure["native"]]
    required += [{"gate": "extraction", "subject_kind": "extraction", "subject_id": e["extraction_id"],
                  "subject_sha256": e["sha256"]} for e in closure["extractions"]]
    required += [{"gate": "diagnostic_evaluation", "subject_kind": s["kind"], "subject_id": s["stage_id"],
                  "subject_sha256": s["sha256"]} for s in closure["stages"]]
    required.append({"gate": "result_integrity", "subject_kind": "analysis_closure", "subject_id": cid,
                     "subject_sha256": sha})
    return {"candidate_id": cid, "closure_sha256": sha, "closure": closure, "required_decisions": required}


def _revision_problems(natives: list[dict]) -> list[str]:
    problems, by_case = [], {}
    for n in natives:
        if n["kind"] == "case":
            by_case.setdefault(n["data"]["case_id"], []).append(n)
    for cid, recs in by_case.items():
        ids = {r["envelope"]["record_id"] for r in recs}
        targets = [r["envelope"]["supersedes_record_id"] for r in recs if r["envelope"]["supersedes_record_id"]]
        if any(t not in ids for t in targets):
            problems.append(f"case {cid}: revision supersedes a record outside the accepted release")
        if len(targets) != len(set(targets)):
            problems.append(f"case {cid}: two revisions supersede the same record (unresolved fork)")
        heads = [r for r in recs if r["envelope"]["record_id"] not in set(targets)]
        if len(heads) != 1:
            problems.append(f"case {cid}: {len(heads)} unsuperseded revisions (unresolved)")
        if len({r["envelope"]["logical_id"] for r in recs}) != 1:
            problems.append(f"case {cid}: revisions disagree on logical_id")
    return problems


def _decision(rt, gate, kind, sid, sha, problems: list[str]) -> str | None:
    rec = effective(rt, gate, kind, sid, sha)
    if not is_pass(rec):
        state = "missing" if rec is None else rec["result"]
        problems.append(f"{gate} decision for {kind} {sid} is {state} (needs an independent pass bound to {sha[:12]}…)")
        return None
    return rec["acceptance_id"]


def _case_view(rt, stage: dict, case_record: dict, version: str, published_at: str) -> dict:
    projection = {**stage["state_projection_candidate"], "analysis_set_version": version, "published_at": published_at}
    projection["projection_id"] = derived_id("PROJ", version, projection["case_id"])
    rt.contracts.check("state_projection", projection)
    limitations = ["accepted case view: model draft independently evaluated before publication"]
    if stage.get("read_coverage", {}).get("unread_passages"):
        limitations.append(f"{stage['read_coverage']['unread_passages']} passage(s) not read by the investigator")
    return {"case_record": case_record, "state_projection": projection, "events": stage["timeline_events"],
            "completeness": "partial" if stage.get("read_coverage", {}).get("unread_passages") else "complete_permitted",
            "limitations": limitations, "administrative_transitions": stage.get("administrative_transitions", []),
            "stage_id": stage["stage_id"]}


def _discovery_coverage(s05_bodies: list[dict]) -> dict | None:
    """E09: the bounded collective investigation's own limits, carried into every issue-selected result. Units are
    stated: candidate passages (shown / omitted / failed), truncated searches, failed model batches and permitted
    cases with no examined passage. `None` when no collective stage is part of the set."""
    if not s05_bodies:
        return None
    covs = [b.get("coverage") or {} for b in s05_bodies]
    out = {"unit_notes": "passages = candidate source passages; cases = permitted cases",
           "candidate_passages": sum(c.get("candidate_count", 0) for c in covs),
           "examined_passages": sum(c.get("shown_count", 0) for c in covs),
           "omitted_passages": sum(len(c.get("omitted_handles", [])) for c in covs),
           "failed_passages": sum(len(c.get("failed_handles", [])) for c in covs),
           "truncated_searches": sum(c.get("searches_truncated", 0) for c in covs),
           "failed_batches": sum(len(c.get("failed_batches", [])) for c in covs),
           "unassessed_case_ids": sorted({x for c in covs for x in c.get("unassessed_case_ids", [])}),
           "incomplete_reasons": sorted({r for c in covs for r in c.get("incomplete_reasons", [])}),
           "exhaustive": False}
    return out


def publish_analysis_set(rt, *, candidate_id: str, notes: list[str] | None = None) -> dict:
    cand = rt.db.get_object("analysis_closure", candidate_id)
    if cand is None:
        raise PublicationRefused("unknown closure candidate")
    closure = cand["closure"]
    release_id = closure["release_id"]
    key = analysis_pointer_key(release_id)
    pointer = rt.db.get_pointer(key)
    expected_generation = None if pointer is None else pointer["generation"]     # fence read BEFORE checks
    problems: list[str] = []
    try:
        fresh = _closure(rt, release_id, closure["assessed_evidence_as_of"], [s["stage_id"] for s in closure["stages"]])
    except PublicationRefused as exc:
        raise PublicationRefused(f"closure no longer reproducible: {exc}") from exc
    if canonical_sha256(fresh) != cand["closure_sha256"]:
        raise PublicationRefused("closure changed since it was prepared (inputs, release or stages differ)")
    entries = _release_entries(rt, release_id)
    natives = _native_rows(rt, entries)
    problems += _revision_problems(natives)
    acceptance_ids = []
    deps = {"natives": {}, "extractions": {}, "sources": {}, "stages": {}, "records": {}, "case_stages": {}}
    for n in closure["native"]:
        a = _decision(rt, "native_input", "native_record", n["record_id"], n["sha256"], problems)
        acceptance_ids.append(a)
        deps["natives"][n["record_id"]] = a
    for e in closure["extractions"]:              # model analyses rest on extracted text: its own independent gate
        a = _decision(rt, "extraction", "extraction", e["extraction_id"], e["sha256"], problems)
        acceptance_ids.append(a)
        deps["extractions"][e["extraction_id"]] = a
    emitted = {}
    for eid in deps["extractions"]:
        for x in (rt.db.get_object("extraction", eid) or {}).get("emitted_sources", []):
            emitted.setdefault(x["source_id"], []).append(eid)
    stages = {}
    for s in closure["stages"]:
        a = _decision(rt, "diagnostic_evaluation", s["kind"], s["stage_id"], s["sha256"], problems)
        acceptance_ids.append(a)
        _, body = _stage(rt, s["stage_id"])
        stages[s["stage_id"]] = body
        consumed = sorted({src["source_id"] for src in body.get("consumed_sources", [])})
        deps["stages"][s["stage_id"]] = {"acceptance_id": a, "sources": consumed,
                                         "extractions": sorted({e for x in consumed for e in emitted.get(x, [])})}
        for r in body.get("analysis_records", []):
            deps["records"][r["envelope"]["record_id"]] = s["stage_id"]
        for m in body.get("mx_records", []):
            deps["records"][m["common"]["analysis_id"]] = s["stage_id"]
        if body.get("kind") == "s03_case_assessment":
            deps["case_stages"][body["case_id"]] = s["stage_id"]
        for src in body.get("consumed_sources", []):
            entry = entries.get(("input", src["source_id"]))
            if entry is not None:
                deps["sources"][src["source_id"]] = entry["acceptance_id"]
            if entry is None:
                problems.append(f"stage {s['stage_id']} consumed source {src['source_id']} outside this release")
            elif src.get("object_sha256") and rt.db.object_sha("source", src["source_id"]) != src["object_sha256"]:
                problems.append(f"stage {s['stage_id']} consumed a different version of {src['source_id']} (stale)")
            elif not still_effective(rt, entry["acceptance_id"]) or \
                    not (rt.db.get_object("acceptance", entry["acceptance_id"]) or {}).get("independent"):
                problems.append(f"stage {s['stage_id']} consumed source {src['source_id']} without an effective "
                                "independent source pass")
    deps["result_integrity"] = _decision(rt, "result_integrity", "analysis_closure", candidate_id,
                                         cand["closure_sha256"], problems)
    acceptance_ids.append(deps["result_integrity"])
    if problems:
        raise PublicationRefused("; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""))

    version = derived_id("ASET", cand["closure_sha256"])
    existing = rt.db.get_object("analysis_set", version)
    if existing is not None:
        if pointer is not None and pointer["target_id"] == version:
            return existing                                   # idempotent: sealed clocks retained
        # re-publication of an already sealed set (e.g. after a withdrawal): never rebuilt, only re-pointed
        return _point(rt, key, expected_generation, version, existing["integrity_acceptance_id"], release_id,
                      utc_now_iso()) or existing
    published_at = utc_now_iso()
    heads = {}
    superseded = {n["envelope"]["supersedes_record_id"] for n in natives if n["envelope"]["supersedes_record_id"]}
    for n in natives:
        if n["kind"] == "case" and n["envelope"]["record_id"] not in superseded:
            heads[n["data"]["case_id"]] = n
    cases, view_objects, records = {}, [], []
    for sid, body in sorted(stages.items()):
        if body.get("kind") == "s03_case_assessment":
            head = heads.get(body["case_id"])
            if head is None:
                raise PublicationRefused(f"case {body['case_id']} has no accepted native register row")
            view_id = derived_id("CVIEW", version, body["case_id"])
            view_objects.append((view_id, _case_view(rt, body, head, version, published_at)))
            cases[body["case_id"]] = view_id
        records += body.get("analysis_records", [])
    nodes: dict[str, dict] = {}
    for sid, body in sorted(stages.items()):
        for n in body.get("taxonomy_nodes", []):
            cur = nodes.setdefault(n["node_id"], {**n, "permitted_finding_ids": []})
            cur["permitted_finding_ids"] = sorted(set(cur["permitted_finding_ids"]) | set(n["permitted_finding_ids"]))
    mx_records = {}
    for sid, body in sorted(stages.items()):
        for m in body.get("mx_records", []):
            rt.contracts.check(m["kind"], m)
            mx_records[m["common"]["analysis_id"]] = m
    intervals = {}
    for sid, body in sorted(stages.items()):
        for iv in body.get("membership_intervals", []):
            rt.contracts.check("membership_interval", iv)
            if iv["finding_id"] not in {r["envelope"]["record_id"] for r in records if r["kind"] == "finding"}:
                raise PublicationRefused(f"membership interval {iv['membership_version_id']} names a finding "
                                         "outside this closure")
            intervals[iv["membership_version_id"]] = iv
    discovery = _discovery_coverage([body for _, body in sorted(stages.items()) if body.get("kind") == "s05_collective"])
    taxonomy = None
    if nodes:
        offset = len(closure["native"]) + len(closure["extractions"])
        s05 = [a for a, s in zip(acceptance_ids[offset:], closure["stages"]) if s["kind"] == "stage_s05"]
        taxonomy = {"taxonomy_version": derived_id("TAXV", version), "published_at": published_at,
                    "acceptance_id": s05[0], "nodes": [nodes[k] for k in sorted(nodes)],
                    "lineage_notes": ["mechanism labels proposed by S05 and independently evaluated with their stage"]}
        rt.contracts.check("taxonomy", taxonomy)
    # staged intervals carry a placeholder taxonomy version; bind them to the taxonomy published with them
    intervals = {k: {**iv, "taxonomy_version": taxonomy["taxonomy_version"]} for k, iv in intervals.items()} \
        if taxonomy else {}
    integrity_id = mechanical(rt, subject_kind="analysis_set_closure", subject_id=candidate_id,
                              subject_sha256=cand["closure_sha256"], gate="analysis_integrity", result="pass",
                              checker=PUBLISHER_VERSION,
                              limitations=["mechanical: closure reproduced, decisions effective, revisions resolved, "
                                           "schemas valid"])
    body = {"release_id": release_id, "closure_candidate_id": candidate_id, "closure_sha256": cand["closure_sha256"],
            "case_record_ids": sorted(n["envelope"]["record_id"] for n in natives if n["kind"] == "case"),
            "native_record_ids": sorted(n["envelope"]["record_id"] for n in natives),
            "assessed_evidence_as_of": closure["assessed_evidence_as_of"],
            "taxonomy_version": taxonomy["taxonomy_version"] if taxonomy else None,
            "supported_definitions": closure["supported_definitions"],
            # capability, not completeness: the response service classifies these four dimensions whenever
            # accepted member_outcome_response records are part of the set
            "supported_response_dimensions": ["decision", "explanation", "handling", "service"]
            if any(m["kind"] == "member_outcome_response" for m in mx_records.values()) else [],
            "record_ids": sorted(r["envelope"]["record_id"] for r in records), "cases": cases,
            "membership_interval_ids": sorted(intervals), "mx_record_ids": sorted(mx_records),
            "discovery_coverage": discovery,
            "assessment_coverage": {k: {**v, "stage_id": body["stage_id"]} for body in stages.values()
                                    for k, v in body.get("assessment_coverage", {}).items()},
            "financial_history_uncertainties": [{**u, "stage_id": body["stage_id"]} for body in stages.values()
                                                 for u in body.get("financial_history_uncertainties", [])],
            "entities": {f"{r['kind']}:{r['envelope']['logical_id']}": r["envelope"]["record_id"] for r in records},
            "related": {}, "result_ids": [], "acceptance_ids": sorted(set(a for a in acceptance_ids if a)),
            "integrity_acceptance_id": integrity_id, "dependency_closure": deps, "notes": notes or []}
    assessment_id = derived_id("ASSESS", version)
    aset = {"analysis_set_version": version, "assessment_id": assessment_id, "published_at": published_at, **body}
    with rt.db.tx() as c:
        for r in records:
            rt.db.put_object("analysis_record", r["envelope"]["record_id"], r, conn=c,
                             links={"case": sorted(set(r["data"].get("case_ids") or [])
                                                   | ({r["data"]["case_id"]} if r["data"].get("case_id") else set()))})
        for vid, view in view_objects:
            rt.db.put_object("case_view", vid, view, conn=c, links={"case": [view["state_projection"]["case_id"]]})
        if taxonomy:
            rt.db.put_object("taxonomy", taxonomy["taxonomy_version"], taxonomy, conn=c)
        for mid, m in sorted(mx_records.items()):
            if c.execute("SELECT 1 FROM objects WHERE kind='mx_record' AND object_id=?", (mid,)).fetchone() is None:
                rt.db.put_object("mx_record", mid, m, conn=c, links={"case": sorted(m["common"]["case_ids"])})
        for iid, iv in sorted(intervals.items()):
            if c.execute("SELECT 1 FROM objects WHERE kind='membership_interval' AND object_id=?", (iid,)).fetchone() is None:
                rt.db.put_object("membership_interval", iid, iv, conn=c, links={"case": [iv["case_id"]]})
        rt.db.put_object("analysis_set", version, aset, conn=c, links={"release": [release_id]})
        rt.db.put_object("assessment_manifest", assessment_id, {
            "assessment_id": assessment_id, "analysis_set_version": version, "release_id": release_id,
            "assessed_evidence_as_of": closure["assessed_evidence_as_of"], "analysis_mode": "reconstructed",
            "eligible_input_sha256": cand["closure_sha256"], "algorithm_version": ALGORITHM_VERSION,
            "acceptance_ids": aset["acceptance_ids"], "computed_at": published_at}, conn=c)
    _point(rt, key, expected_generation, version, integrity_id, release_id, published_at)
    return aset


def _point(rt, key, expected_generation, version, integrity_id, release_id, at: str) -> None:
    """Fenced CAS + immutable publication event stamped with the publication instant (as-reported history)."""
    generation = rt.db.swap_pointer(key, expected_generation=expected_generation, target_kind="analysis_set",
                                    target_id=version, acceptance_id=integrity_id,
                                    authority_revision=permission_revision())
    event_id = derived_id("PUBEV", key, generation, version)
    rt.db.put_object("publication_event", event_id, {
        "event_id": event_id, "pointer_key": key, "generation": generation, "target_kind": "analysis_set",
        "target_id": version, "release_id": release_id, "published_at": at,
        "acceptance_id": integrity_id}, links={"release": [release_id]})


def withdraw_analysis_set(rt, *, release_id: str, reason: str) -> dict:
    """Move the pointer to an explicit withdrawal marker (incompatible last result is refused, never served)."""
    key = analysis_pointer_key(release_id)
    pointer = rt.db.get_pointer(key)
    if pointer is None:
        raise PublicationRefused("nothing published")
    marker_id = derived_id("WDRAW", key, pointer["generation"], reason)
    rt.db.put_object("withdrawal", marker_id, {"withdrawal_id": marker_id, "release_id": release_id,
                                               "withdrawn_target": pointer["target_id"], "reason": reason},
                     links={"withdrawn": [pointer["target_id"]]})
    acc = mechanical(rt, subject_kind="withdrawal", subject_id=marker_id, subject_sha256=canonical_sha256(reason),
                     gate="analysis_integrity", result="pass", checker=PUBLISHER_VERSION, limitations=[reason])
    generation = rt.db.swap_pointer(key, expected_generation=pointer["generation"], target_kind="withdrawal",
                                    target_id=marker_id, acceptance_id=acc, authority_revision=permission_revision())
    event_id = derived_id("PUBEV", key, generation, marker_id)
    rt.db.put_object("publication_event", event_id, {
        "event_id": event_id, "pointer_key": key, "generation": generation, "target_kind": "withdrawal",
        "target_id": marker_id, "release_id": release_id, "published_at": utc_now_iso(), "acceptance_id": acc},
        links={"release": [release_id]})
    return {"withdrawal_id": marker_id, "generation": generation}
