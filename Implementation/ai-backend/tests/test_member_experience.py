"""V11/V12 baseline services over accepted records, using MEMBER-EXPERIENCE.md's explicit negative examples.
Specimen objects are validated against the frozen Shared schema; nothing here is evaluation gold."""
import copy

import pytest

from sdbackend.engine import mx, results
from test_issue_populations import FILTERS, REF, FakeInputs, _sel, _t

SRC = {"source_id": "SRC-1", "content_sha256": "a" * 64}
WIN = dict(activity_start="2026-05-01T00:00:00+10:00", activity_end_exclusive="2026-06-01T00:00:00+10:00")


def _p(role="member"):
    return {"party_reference": None, "role": role, "represented_member_reference": None,
            "authority_state": "not_applicable", "evidence_refs": []}


def _common(aid, cases=("C1",), when=None, state="supported"):
    return {"analysis_id": aid, "logical_occurrence_id": aid, "version": "v1", "supersedes_analysis_id": None,
            "case_ids": list(cases), "member_reference": "M1", "member_identity_state": "known",
            "member_life_state": "living", "problem_reference": None, "problem_link_state": "supported",
            "event_time": when or {"precision": "day", "date": "2026-05-10"}, "source_bindings": [SRC],
            "supporting_refs": [REF], "contradictory_refs": [], "evidence_state": state,
            "attribution": "direct_words", "context_id": "CTX-1", "assessed_evidence_as_of": "2026-05-01T00:00:00+10:00",
            "computed_at": "2026-05-01T00:00:00+10:00", "published_at": "2026-05-01T00:00:00+10:00",
            "policy": {"access": {"view_ids": ["complaints-leadership"], "classification": "standard",
                                  "redaction_state": "none"},
                       "entitlement_policy_id": "POL-1", "permission_revision": "REV-1"},
            "review_state": "not_reviewed"}


def _due(day=None, instant=None, vague=False):
    if vague:
        return {"wording": "soon", "evidence_refs": [REF], "interpretation_state": "unknown", "boundary": None,
                "boundary_kind": "unknown", "cohort_anchor": None, "timezone": "Australia/Melbourne",
                "precision": "unknown", "calendar_id": None, "calendar_version": None,
                "interpretation_basis": "vague wording"}
    if day:
        nxt = mx.bounds({"precision": "day", "date": day})[1].isoformat()
        return {"wording": f"by {day}", "evidence_refs": [REF], "interpretation_state": "interpreted",
                "boundary": nxt, "boundary_kind": "exclusive_next_local_day",
                "cohort_anchor": {"precision": "day", "date": day}, "timezone": "Australia/Melbourne",
                "precision": "day", "calendar_id": None, "calendar_version": None, "interpretation_basis": "date"}
    return {"wording": f"by {instant}", "evidence_refs": [REF], "interpretation_state": "interpreted",
            "boundary": instant, "boundary_kind": "inclusive_instant",
            "cohort_anchor": {"precision": "instant", "instant": instant}, "timezone": "Australia/Melbourne",
            "precision": "instant", "calendar_id": None, "calendar_version": None, "interpretation_basis": "instant"}


def _done(when, criteria="supported", refs=(REF,)):
    return {"observation_id": "OBS-" + (when.get("date") or when.get("instant", "x")).replace(":", "-").replace("+", "p"),
            "action_target_reference": "TGT-1",
            "fulfils_criteria": criteria, "completion_time": when, "proposition": "done",
            "supporting_refs": list(refs), "contradictory_refs": []}


def commitment(cid, due, *, obs=(), app="applicable", cancels=(), scope="member_facing", basis="direct"):
    return {"kind": "commitment", "common": _common(cid), "commitment_id": cid, "promisor": _p("institution"),
            "beneficiary": _p(), "scope": scope, "action": "send the statement", "action_target_reference": "TGT-1",
            "action_target_state": "identified", "action_target_reason": "", "fulfilment_criteria": "statement sent",
            "undertaking_basis": basis, "original_due": due, "conditions": [],
            "original_opportunity_applicability": app, "applicability_refs": [], "cancellations": list(cancels),
            "revisions": [], "fulfilment_observations": list(obs), "original_due_outcome": "unknown",
            "classification_reason": "", "classification_refs": [], "revised_due_outcome": None}


def _item(state="eligible_repeat", validity="recognised_valid_before_request", order="supported_later", item="DC-11"):
    return {"item_reference": item, "item_version_reference": "v1", "requirement_reference": "REQ-ID",
            "prior_occurrence_reference": "SUB-1", "receipt_state": "received", "validity_state": validity,
            "comparison_state": state, "order_state": order, "supporting_refs": [REF], "contradictory_refs": [],
            "reason": ""}


def request(aid, occasion, items, *, when=None, repetition="supported"):
    return {"kind": "member_effort_event", "common": _common(aid, when=when), "effort_kind": "repeat_valid_evidence_request",
            "occasion_id": occasion, "occasion_type": "institution_request", "initiator": _p("institution"),
            "prior_occurrence_ids": ["SUB-1"], "continuity_state": "supported", "repetition_state": repetition,
            "receipt_state": "not_applicable", "repeated_concern": None, "item_comparisons": list(items),
            "qualifiers": []}


def response(aid, occasion, target, dimension, position, *, identified=True, link="supported"):
    return {"kind": "member_outcome_response", "common": _common(aid), "response_relationship_id": aid,
            "response_occasion_id": occasion, "respondent": _p(), "target_id": target if identified else None,
            "target_version": "v1" if identified else None, "target_link_state": link,
            "target_identity_state": "identified" if identified else "unidentified", "target_link_reason": "",
            "dimension": dimension, "expressed_position": position, "stated_reason": None,
            "supersedes_position_relationship_id": None}


class MXInputs(FakeInputs):
    def __init__(self, mx_records, recs=(), **kw):
        super().__init__(list(recs), **kw)
        self.mx_records = mx_records

    def mx(self, kind):
        return {r["common"]["logical_occurrence_id"]: r for r in self.mx_records if r["kind"] == kind}, set()


def _run(measure, objs, recs=(), cutoff="2026-06-15T00:00:00+10:00", transitions=None):
    inp = MXInputs(objs, recs, cutoff=cutoff, transitions=transitions)
    inp.window, inp.at = (_t(WIN["activity_start"]), _t(WIN["activity_end_exclusive"])), inp.cutoff
    sel = _sel(measure, **WIN)
    items, unc, miss, notes = sel.measure.collect(inp, sel)
    return {i.entity_id: i.state for i in items}, unc, miss


def test_specimens_match_the_frozen_shared_schema(rt):
    for obj in (commitment("CM-1", _due(day="2026-05-31"), obs=[_done({"precision": "day", "date": "2026-05-31"})]),
                request("RQ-1", "OCC-1", [_item()]), response("RS-1", "OCC-R", "TGT-1", "service", "accept"),
                response("RS-2", "OCC-R", None, "unknown", "unknown", identified=False, link="uncertain")):
        rt.contracts.check(obj["kind"], obj)


def test_original_due_six_states_and_negative_examples():
    cut = _t("2026-06-15T00:00:00+10:00")
    day = lambda d: {"precision": "day", "date": d}
    by_31 = _due(day="2026-05-31")
    cls = lambda c: mx.classify_original_due(c, cut)[0]
    assert cls(commitment("A", by_31, obs=[_done(day("2026-05-31"))])) == "on_time"     # whole due day is on time
    assert cls(commitment("B", by_31, obs=[_done(day("2026-06-01"))])) == "late"
    at5 = _due(instant="2026-05-31T17:00:00+10:00")
    assert cls(commitment("C", at5, obs=[_done(day("2026-05-31"))])) == "unknown"       # date-only straddles 17:00
    assert cls(commitment("D", _due(vague=True))) == "ineligible"                        # vague due
    later = [{"time": day("2026-06-05"), "cancelled_by": _p("institution"), "evidence_refs": [REF]}]
    assert cls(commitment("E", by_31, app="cancelled_before_opportunity", cancels=later)) == "unknown"  # later cancel
    early = [{"time": day("2026-05-20"), "cancelled_by": _p("institution"), "evidence_refs": [REF]}]
    assert cls(commitment("F", by_31, app="cancelled_before_opportunity", cancels=early)) == "not_applicable"
    assert cls(commitment("G", by_31)) == "unknown"                       # missing confirmation is not failure
    assert cls(commitment("H", by_31, obs=[_done(day("2026-06-02"), criteria="excluded")])) == "explicitly_unmet"
    assert cls(commitment("I", _due(day="2026-06-30"))) == "not_yet_due"
    assert cls(commitment("J", by_31, obs=[_done(day("2026-05-30"), refs=())])) == "unknown"   # uncited is not proof


def test_follow_through_cohort_partition_and_scope():
    day = lambda d: {"precision": "day", "date": d}
    objs = [commitment("A", _due(day="2026-05-31"), obs=[_done(day("2026-05-31"))]),
            commitment("B", _due(day="2026-05-31"), obs=[_done(day("2026-06-01"))]),
            commitment("JUN", _due(instant="2026-06-01T00:00:00+10:00")),      # instant at 1 June midnight → June
            commitment("V", _due(vague=True)),
            commitment("INT", _due(day="2026-05-20"), scope="internal"),
            commitment("REP", _due(day="2026-05-20"), basis="reported_only")]
    got, unc, miss = _run("mx.commitments.follow_through", objs)
    assert got == {"A": "on_time", "B": "late"} and miss == ["V"] and unc == []


def test_repeat_valid_evidence_requests_count_occasions_once():
    objs = [request("R1", "OCC-1", [_item(), _item(state="legitimate_new_or_changed", validity="not_applicable",
                                                   item="NEW-1")]),               # valid plus new item → once
            request("R1-COPY", "OCC-1", [_item()]),                               # same occasion via a copy
            request("R2", "OCC-2", [_item(validity="not_established", state="uncertain")]),   # absent prior validity
            request("R3", "OCC-3", [_item(state="legitimate_new_or_changed", validity="changed_requirement")]),
            request("R4", "OCC-4", [_item()], when={"precision": "day", "date": "2026-06-03"})]   # outside window
    got, unc, miss = _run("mx.effort.repeat_valid_evidence_requests", objs)
    assert set(got) == {"OCC-1"} and unc == ["OCC-2"]


def test_expressed_positions_split_targets_conflicts_and_silence():
    objs = [response("P1", "OCC-A", "TGT-PAY", "service", "accept"),
            response("P2", "OCC-A", "TGT-COMP", "decision", "disagree"),           # split target positions
            response("P3", "OCC-B", "TGT-COMP", "decision", "conflicting"),        # same-target conflict, once
            response("P3-COPY", "OCC-B", "TGT-COMP", "decision", "conflicting"),
            response("P4", "OCC-C", "TGT-PAY", "service", "update_only"),          # "thanks for the update"
            response("P5", "OCC-D", None, "unknown", "unknown", identified=False, link="uncertain")]
    got, unc, _ = _run("mx.responses.expressed_positions", objs)
    assert got == {"P1": "accept:service", "P2": "disagree:decision", "P3": "conflicting:decision",
                   "P4": "update_only:service"}
    assert unc == ["P5"]                                    # unidentified candidate disclosed, never confirmed


def _contact(cid, day, *, repeat="repeat", initiator="complainant", episode="EP-1", dup=None):
    return {"kind": "contact_link", "envelope": {"record_id": cid, "logical_id": cid, "supersedes_record_id": None,
                                                 "available_at": "2026-05-01T00:00:00+10:00", "evidence_refs": [REF]},
            "data": {"contact_id": cid, "case_ids": ["C1"], "episode_id": episode, "event_at": None,
                     "event_date": day, "initiator": initiator, "repeat_state": repeat, "duplicate_of_contact_id": dup,
                     "source_ids": ["SRC-1"], "evidence_refs": [REF]}}


def _episode(eid, link="supported"):
    return {"kind": "episode", "envelope": {"record_id": eid, "logical_id": eid, "supersedes_record_id": None,
                                            "available_at": "2026-05-01T00:00:00+10:00", "evidence_refs": [REF]},
            "data": {"episode_id": eid, "case_ids": ["C1"], "link_state": link}}


def test_repeat_and_post_closure_return_contacts():
    recs = [_contact("K1", "2026-05-05"), _contact("K1-COPY", "2026-05-05", dup="K1"),
            _contact("K2", "2026-05-06", repeat="uncertain"), _contact("K3", "2026-05-07", initiator="staff"),
            _contact("K4", "2026-05-08", repeat="first"), _contact("K5", "2026-05-20", episode=None),
            _episode("EP-1")]
    got, unc, _ = _run("contacts.repeat", [], recs)
    assert set(got) == {"K1"} and unc == ["K2", "K5"]
    closed = {"C1": [{"state": "closed", "effective_at": None, "effective_date": "2026-05-06",
                      "available_at": "2026-05-06T12:00:00+10:00", "evidence_refs": [REF]}]}
    got, unc, _ = _run("contacts.post_closure_return", [], recs, transitions=closed)
    assert set(got) == {"K4"}                       # strictly after the evidenced closure, supported episode
    assert {"K2", "K5"} <= set(unc)                 # K2 same day as closure: order uncertain; K5 no episode link
    assert "K1" not in unc and "K3" not in unc      # K1 before closure; K3 staff-initiated
