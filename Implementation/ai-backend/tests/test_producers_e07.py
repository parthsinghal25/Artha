"""E07 producers: isolated specimen sources/facts (no model, no served store).

Each branch is a test specimen; none is a case-11 expectation. Real case-11 outputs come from the actual staged run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sdbackend.canonical import sha256_text
from sdbackend.engine import producers
from sdbackend.engine.core import parse_instant
from sdbackend.engine.mx import classify_original_due

CASE = "C-SPEC-E07"


def _src(sid, text, *, author="STAFF-CM03", role="complaints case manager", at="2026-01-12T16:00:00+11:00",
         occurred=None, subtype="email", case=CASE):
    return {"envelope": {"available_at": at, "occurred_at": occurred or at},
            "data": {"source_id": sid, "text": text, "author_reference": author, "author_role": role,
                     "subtype": subtype, "case_ids": [case], "content_sha256": sha256_text(text),
                     "copy_of_source_id": None}}


def _fact(src, key, value, *, day=None, state="known"):
    start = src["data"]["text"].find("\n\n") + 2 if key == "commitment" else 0
    end = len(src["data"]["text"]) if key == "commitment" else 5
    ref = {"source_id": src["data"]["source_id"], "content_sha256": src["data"]["content_sha256"],
           "anchor": {"kind": "text", "start_codepoint": max(0, start), "end_codepoint": end}}
    return {"data": {"field_key": key, "event_date": day, "entity_id": f"ENT-{key}",
                     "value": {"state": state, "value": value, "evidence_refs": [ref]}}}


def _groups(*items):
    out = {}
    for src, entity, facts in items:
        g = out.setdefault((src["data"]["source_id"], entity), {
            "facts": {}, "case_ids": src["data"]["case_ids"], "available_at": src["envelope"]["available_at"],
            "author_role": src["data"]["author_role"]})
        for f in facts:
            g["facts"].setdefault(f["data"]["field_key"], []).append(f)
    return out


TO_MEMBER = "From: A <a@inst.example>\nTo: Member <m@member-mail.example>\nSubject: x\n\nI will send the next update by 12 January 2026."
TO_STAFF = "From: A <a@inst.example>\nTo: B <b@inst.example>\nSubject: x\n\nI will send you the file by 13 January 2026."
FROM_MEMBER = "From: Member <m@member-mail.example>\nTo: A <a@inst.example>\nSubject: x\n\nYou promised an update by 12 January 2026."


def test_commitments_scope_basis_due_and_dedup():
    s1 = _src("SRC-C1", TO_MEMBER, at="2026-01-07T16:00:00+11:00")
    s2 = _src("SRC-C2", TO_STAFF, at="2026-01-08T16:00:00+11:00")
    s3 = _src("SRC-C3", FROM_MEMBER, author=None, role="claimant", at="2026-01-09T16:00:00+11:00")
    s4 = _src("SRC-C4", "From: A <a@inst.example>\nTo: Member <m@member-mail.example>\n\nWe will look into it.",
              at="2026-01-10T16:00:00+11:00")
    srcs = {s["data"]["source_id"]: s for s in (s1, s2, s3, s4)}
    groups = _groups(
        (s1, "E1", [_fact(s1, "commitment", "send the next update"), _fact(s1, "commitment_due_date", "2026-01-12")]),
        (s2, "E2", [_fact(s2, "commitment", "send you the file"), _fact(s2, "commitment_due_date", "2026-01-13")]),
        (s3, "E3", [_fact(s3, "commitment", "an update"), _fact(s3, "commitment_due_date", "2026-01-12")]),
        (s4, "E4", [_fact(s4, "commitment", "look into it")]))
    out = producers.commitments(groups, srcs, producers.institution_domains(list(srcs.values())), [])
    by_due = {(c["original_due"]["cohort_anchor"] or {}).get("date"): c for c in out}
    assert len(out) == 3                                  # the member's account merges into the staff promise
    m = by_due["2026-01-12"]
    assert (m["scope"], m["undertaking_basis"], m["common"]["evidence_state"]) == ("member_facing", "direct", "supported")
    assert {b["source_id"] for b in m["common"]["source_bindings"]} == {"SRC-C1", "SRC-C3"}
    assert m["original_due"]["boundary"] == "2026-01-13T00:00:00+11:00"          # exclusive next local midnight
    assert by_due["2026-01-13"]["scope"] == "internal"                           # every addressee internal
    vague = by_due[None]
    assert vague["original_due"]["interpretation_state"] == "unknown"            # vague wording stays unknown
    # outcomes are re-derived at query time; no fulfilment evidence is never failure
    assert classify_original_due(m, parse_instant("2026-01-12T12:00:00+11:00"))[0] == "not_yet_due"
    assert classify_original_due(m, parse_instant("2026-02-01T00:00:00+11:00"))[0] == "unknown"
    assert classify_original_due(vague, parse_instant("2026-02-01T00:00:00+11:00"))[0] == "ineligible"


def test_member_report_alone_is_report_only():
    s3 = _src("SRC-C3", FROM_MEMBER, author=None, role="claimant")
    groups = _groups((s3, "E3", [_fact(s3, "commitment", "an update"), _fact(s3, "commitment_due_date", "2026-01-12")]))
    (c,) = producers.commitments(groups, {"SRC-C3": s3}, set(), [])
    assert c["undertaking_basis"] == "reported_only" and c["common"]["evidence_state"] == "uncertain"


def test_afca_versions_by_availability_and_reference_alone_is_not_a_matter():
    reg = _src("SRC-A1", "Registered on 14 January 2026", author=None, role="external dispute correspondence",
               at="2026-01-14T16:05:00+11:00")
    close = _src("SRC-A2", "closed as of 27 March 2026", author=None, role="external dispute correspondence",
                 at="2026-03-27T16:00:00+11:00")
    lone = _src("SRC-A3", "AF-OTHER mentioned", at="2026-02-01T16:00:00+11:00")
    srcs = {s["data"]["source_id"]: s for s in (reg, close, lone)}
    groups = _groups(
        (reg, "M", [_fact(reg, "afca_reference", "AF-1"), _fact(reg, "afca_stage", "registration_referral",
                                                                 day="2026-01-14")]),
        (close, "M", [_fact(close, "afca_reference", "AF-1"), _fact(close, "afca_stage", "closed", day="2026-03-27"),
                      _fact(close, "afca_closure_route", "negotiation_agreement", day="2026-03-27")]),
        (lone, "M", [_fact(lone, "afca_reference", "AF-OTHER")]))
    recs = producers.afca(groups, srcs, [])
    matters = [r for r in recs if r["kind"] == "afca_matter"]
    assert {r["data"]["afca_matter_id"] for r in matters} == {"AF-1"}           # reference alone: no matter
    v1, v2 = sorted(matters, key=lambda r: r["envelope"]["available_at"])
    assert v1["data"]["received_date"] == "2026-01-14" and v1["data"]["closed_date"] is None
    assert v1["data"]["closure_route"] == "not_closed"
    assert v2["envelope"]["supersedes_record_id"] == v1["envelope"]["record_id"]
    assert (v2["data"]["closed_date"], v2["data"]["closure_route"]) == ("2026-03-27", "negotiation_agreement")
    links = [r for r in recs if r["kind"] == "afca_link"]
    assert [(l["data"]["case_id"], l["data"]["link_state"]) for l in links] == [(CASE, "supported")]


def _s03(transitions, service="unknown"):
    return {"case_id": CASE, "administrative_transitions": transitions,
            "state_projection_candidate": {"service": [{"value": service}]}}


def _t(state, day, at):
    return {"state": state, "effective_at": None, "effective_date": day, "available_at": at,
            "evidence_refs": [{"source_id": "SRC-S03", "content_sha256": "0" * 64,
                               "anchor": {"kind": "text", "start_codepoint": 0, "end_codepoint": 3}}]}


def test_episodes_and_contact_continuity():
    eps, spans = producers.episodes([_s03([_t("open", "2025-12-31", "2025-12-31T16:00:00+11:00"),
                                           _t("closed", "2026-03-13", "2026-03-13T16:00:00+11:00")])])
    (ep,) = eps
    assert ep["data"]["end_at_exclusive"] == "2026-03-14T00:00:00+11:00"
    assert ep["data"]["completion_state"] == "closure_without_completion_evidence"   # closure ≠ service completion
    member = dict(author=None, role="claimant and verified correspondence contact")
    srcs = {s["data"]["source_id"]: s for s in (
        _src("SRC-K0", "pre", occurred="2025-12-21T09:30:00+11:00", **member),        # before the episode
        _src("SRC-K1", "a", occurred="2026-01-02T08:45:00+11:00", **member),
        _src("SRC-K2", "b", occurred="2026-01-09T09:30:00+11:00", **member),
        _src("SRC-K3", "c", occurred="2026-03-20T09:00:00+11:00", **member),          # after the closure
        _src("SRC-K4", "staff", occurred="2026-01-05T09:00:00+11:00"),                # not a member contact
        _src("SRC-K5", "doc", occurred="2026-01-02T09:00:00+11:00", subtype="certificate_copy", **member))}
    recs = producers.contacts([], srcs, spans)
    state = {r["data"]["source_ids"][0]: (r["data"]["repeat_state"], r["data"]["episode_id"]) for r in recs}
    assert set(state) == {"SRC-K0", "SRC-K1", "SRC-K2", "SRC-K3"}
    assert state["SRC-K0"] == ("uncertain", None)
    assert state["SRC-K1"][0] == "first" and state["SRC-K2"][0] == "repeat"
    assert state["SRC-K3"] == ("repeat", ep["data"]["episode_id"])               # post-closure return candidate


def test_contacts_without_s03_history_stay_uncertain():
    member = dict(author=None, role="claimant")
    recs = producers.contacts([], {"SRC-K1": _src("SRC-K1", "a", **member)}, {})
    assert [r["data"]["repeat_state"] for r in recs] == ["uncertain"]


def test_repeat_request_needs_prior_recognised_validity():
    inst = dict(at="2026-01-20T16:00:00+11:00")
    rec1 = _src("SRC-R1", "received DC-11", at="2025-12-31T16:00:00+11:00")
    val1 = _src("SRC-R2", "DC-11 valid", at="2026-01-02T16:00:00+11:00")
    req = _src("SRC-R3", "please resend DC-11 for REQ-1", **inst)
    srcs = {s["data"]["source_id"]: s for s in (rec1, val1, req)}
    base = [(rec1, "DC", [_fact(rec1, "evidence_received", "DC-11", day="2025-12-31")]),
            (req, "DC", [_fact(req, "dependency", "DC-11", day="2026-01-20"),
                         _fact(req, "routing_reference", "REQ-1")])]
    (uncertain,) = producers.effort(_groups(*base), srcs, [])
    child = uncertain["item_comparisons"][0]
    assert (child["comparison_state"], child["validity_state"]) == ("uncertain", "not_established")
    from sdbackend.engine.mx import request_qualifies
    assert request_qualifies(uncertain) == "uncertain"
    (eligible,) = producers.effort(_groups(*base, (val1, "DC", [_fact(val1, "evidence_validity", "valid",
                                                                       day="2026-01-02")])), srcs, [])
    assert eligible["item_comparisons"][0]["comparison_state"] == "eligible_repeat"
    assert request_qualifies(eligible) == "supported"
    # a first request (no earlier receipt) is not a repeat occasion at all
    assert producers.effort(_groups(base[1]), srcs, []) == []


def test_membership_interval_start_from_evidence_and_no_invented_end():
    finding = {"kind": "finding", "data": {"finding_id": "FND-1", "version": "v1", "memberships": [
        {"case_id": CASE, "episode_id": None, "state": "included", "association": "unknown",
         "evidence_refs": [{"source_id": "SRC-1"}, {"source_id": "SRC-2"}]},
        {"case_id": "C-X", "episode_id": None, "state": "uncertain", "association": "active", "evidence_refs": []}]}}
    days = {"SRC-1": ("2026-01-07", "2026-01-07T16:00:00+11:00"), "SRC-2": ("2026-01-02", "2026-01-02T16:00:00+11:00")}
    now = datetime(2026, 10, 1, tzinfo=timezone.utc).isoformat()
    (iv,) = producers.intervals([finding], {"FND-1": "TAX-1"}, days.get, assessed=now, now=now)
    assert iv["start"] == {"precision": "day", "date": "2026-01-02"} and iv["end"] is None
    assert iv["ongoing_at"] is None and iv["endpoint_state"] == "unknown"
    from sdbackend.engine.issues import interval_in_window
    assert interval_in_window(iv, parse_instant("2026-02-01T00:00:00+11:00"),
                              parse_instant("2026-03-01T00:00:00+11:00")) == "uncertain"   # unknown end: never active
