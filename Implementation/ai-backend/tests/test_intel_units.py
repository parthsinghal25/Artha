"""Deterministic unit rules of the intel layer: grounding, host critique downgrades, temporal policy."""
from datetime import date

from sdbackend.intel import memory, temporal


def _claim(i, kind, modality="recorded", actor="member", quote=None):
    return {"claim_id": f"CLM-{i}", "kind": kind, "modality": modality, "actor": actor,
            "quote": quote or f"quote {i}", "business_date": None, "source_id": f"S{i}"}


def _crit(raw, claims, prior=None):
    alias = {f"C{i}": c["claim_id"] for i, c in enumerate(claims, start=1)}
    return memory.critique(raw, alias, claims, prior, "CASE")


def test_ground_is_exact_modulo_whitespace():
    text = "The adjustment was\n  posted on 3 March to the account."
    s = memory.ground(text, "adjustment was posted on 3 March")
    assert s and text[s[0]:s[1]].split() == "adjustment was posted on 3 March".split()
    assert memory.ground(text, "adjustment was approved on 3 March") is None
    assert memory.ground(text, "ok") is None


def test_service_completion_requires_recorded_execution():
    claims = [_claim(1, "approval"), _claim(2, "closure")]
    parts, flags = _crit({"service": {"state": "confirmed_complete", "claims": ["C1", "C2"]}}, claims)
    # generic target: identity with the approval cannot be established, so not even "attempted" is claimed
    assert parts["service"]["state"] == "unknown" and any("completion" in f for f in flags)
    claims = [_claim(1, "execution", quote="The rollover to Fund B was processed on 3 May.")]
    item = {"service_items": [{"action": "Rollover to Fund B", "state": "confirmed_complete", "claims": ["C1"]}]}
    parts, _ = _crit(item, claims)
    assert parts["service"]["state"] == "confirmed_complete"
    assert parts["service"]["items"][0]["completion_claim_ids"] == ["CLM-1"]
    claims = [_claim(1, "execution", modality="promised", quote="The rollover to Fund B will be processed.")]
    parts, _ = _crit(item, claims)
    assert parts["service"]["state"] != "confirmed_complete"
    # a generic legacy "service" target cannot be identified with any completion claim: stays unconfirmed
    parts, _ = _crit({"service": {"state": "confirmed_complete", "claims": ["C1"]}},
                     [_claim(1, "execution", quote="quote 1")])
    assert parts["service"]["state"] != "confirmed_complete"


def test_commitment_rules():
    claims = [_claim(1, "commitment", actor="staff"), _claim(2, "other", modality="uncertain")]
    raw = {"commitments": [{"claim": "C1", "status": "fulfilled", "evidence": []},
                           {"claim": "C1", "status": "unfulfilled", "evidence": ["C2"]}]}
    parts, flags = _crit(raw, claims)
    assert [c["status"] for c in parts["commitments"]] == ["unknown", "unknown"]
    assert any("missing confirmation is not breach" in f for f in flags)


def test_thanks_is_not_acceptance_and_staff_forward_is_not_member_effort():
    claims = [_claim(1, "response", quote="Thanks for letting me know."), _claim(2, "handoff", actor="complaints officer")]
    raw = {"responses": [{"target": "decision", "dimension": "decision", "position": "accepts", "claims": ["C1"]}],
           "member_effort": [{"kind": next(iter(memory.ENUMS["effort"])), "claims": ["C2"]}],
           "handoffs": [{"claims": ["C2"], "acknowledged": "yes", "from": "a", "to": "b"}]}
    parts, flags = _crit(raw, claims)
    assert parts["responses"][0]["position"] == "unclear"
    assert parts["member_effort"] == []
    assert parts["handoffs"][0]["acknowledged"] == "unknown"


def test_unsupported_and_unrestated_factors():
    claims = [_claim(1, "error")]
    raw = {"factors": [{"key": "N1", "label": "no support", "support": []},
                       {"key": "N2", "label": "queue stalled", "support": ["C1"], "status": "ceased"}]}
    parts, flags = _crit(raw, claims)
    assert [f["label"] for f in parts["factors"]] == ["queue stalled"]
    assert parts["factors"][0]["status"] == "unknown"                 # ceased needs a ceasing claim
    prior = {"factors": [dict(parts["factors"][0], label="old factor", factor_id="FAC-old")]}
    parts2, _ = _crit({"factors": []}, claims, prior)
    assert parts2["factors"][0]["retention"] == "carried_forward_not_reassessed"   # never silently dropped


def test_unknown_claim_citations_are_flagged_not_trusted():
    parts, flags = _crit({"narrative": [{"text": "x", "claims": ["C9"]}]}, [_claim(1, "other")])
    assert parts["narrative"] == [] and any("unknown claim C9" in f for f in flags)


def test_temporal_buckets_and_views():
    assert temporal.bucket(date(2026, 3, 31), "week")[1] == date(2026, 3, 30)          # Monday weeks
    assert temporal.bucket(date(2025, 12, 31), "quarter") == ("2025-Q4", date(2025, 10, 1), date(2026, 1, 1))
    assert temporal.bucket(date(2026, 9, 30), "observation_year")[0] == "OY2025-26"
    assert temporal.bucket(date(2026, 10, 1), "observation_year")[0] == "OY2026-27"
    assert temporal.bucket(date(2026, 9, 30), "calendar_year")[0] == "2026"
    eod = temporal.end_of_day("2026-03-31")
    assert eod.isoformat() == "2026-04-01T00:00:00+11:00"                  # half-open Melbourne end of day (AEDT)
    assert temporal.end_of_day("2026-06-30").isoformat() == "2026-07-01T00:00:00+10:00"
    assert temporal.view_date(eod) == date(2026, 3, 31)
    # an item available at the boundary instant is NOT in the view (exclusive)
    assert not temporal.available("2026-04-01T00:00:00+11:00", None, eod)
    assert temporal.available("2026-03-31T23:59:59+11:00", None, eod)
    labels = [b[0] for b in temporal.buckets(date(2025, 10, 1), date(2026, 10, 1), "quarter")]
    assert labels == ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]


def test_service_items_negation_and_overall():
    claims = [_claim(1, "execution", quote="Cash-only transfer completed 6 January, transaction PT-377."),
              _claim(2, "execution", quote="No consolidation has been executed and no claim of completion was sent."),
              _claim(3, "request", modality="requested")]
    raw = {"service_items": [{"action": "cash transfer", "state": "confirmed_complete", "claims": ["C1"]},
                             {"action": "consolidation", "state": "confirmed_complete", "claims": ["C2"]},
                             {"action": "statement", "state": "pending", "claims": ["C3"]}]}
    parts, flags = _crit(raw, claims)
    st = {i["action"]: i["state"] for i in parts["service"]["items"]}
    assert st["cash transfer"] == "confirmed_complete"
    assert st["consolidation"] != "confirmed_complete"           # a negated execution is not completion
    assert parts["service"]["state"] == "partially_complete"
    legacy, _ = _crit({"service": {"state": "confirmed_complete", "action": "cash transfer", "claims": ["C1"]}},
                      claims)
    assert legacy["service"]["state"] == "confirmed_complete" and len(legacy["service"]["items"]) == 1


def test_unterminated_array_salvage_keeps_only_complete_elements():
    from sdbackend.intel.discovery import _bare_array
    t = '{"assessments": [ {"case": "K1", "issue": "I1", "state": "x"}, {"case": "K2", "reason": "a } b"} endez}'
    assert [x["case"] for x in _bare_array(t)] == ["K1", "K2"]
    assert _bare_array('{"claims": [{"handle": "S1"}, {"handle": "S2", "quo', "claims") == [{"handle": "S1"}]
    assert _bare_array('{"other": 1}') is None


def test_carried_forward_factor_merges_into_restated_factor_on_same_evidence():
    claims = [_claim(1, "error"), _claim(2, "waiting"), _claim(3, "chase")]
    prior = {"factors": [{"factor_id": "FAC-old", "label": "fee not reflected", "support_claim_ids": ["CLM-1", "CLM-2"],
                          "level": "symptom", "prominence": "primary", "status": "active", "began_claim_ids": [],
                          "ceased_claim_ids": [], "contrary_claim_ids": [], "first_revision": 1}]}
    raw = {"factors": [{"key": "N1", "label": "credit queued without owner", "support": ["C1", "C2", "C3"]}]}
    parts, _ = _crit(raw, claims, prior)
    assert len(parts["factors"]) == 1 and parts["factors"][0]["merged_prior_factor_ids"] == ["FAC-old"]
    raw = {"factors": [{"key": "N1", "label": "member chased", "support": ["C3"]}]}
    parts, _ = _crit(raw, claims, prior)                       # different evidence: carried forward, not dropped
    assert [f["retention"] for f in parts["factors"]] == ["restated", "carried_forward_not_reassessed"]



def test_completion_must_name_the_same_action_and_multi_actions_stay_distinct():
    claims = [_claim(1, "payment", quote="Compensation of A$25 was paid on 2 June."),
              _claim(2, "execution", quote="The rollover to Fund B was processed on 3 May."),
              _claim(3, "approval", quote="The hardship release was approved by the trustee.")]
    raw = {"service_items": [
        {"action": "Transfer retirement account", "state": "confirmed_complete", "claims": ["C1"]},
        {"action": "Pay compensation", "state": "confirmed_complete", "claims": ["C1"]},
        {"action": "Rollover to Fund B", "state": "confirmed_complete", "claims": ["C2"]},
        {"action": "Release hardship benefit", "state": "confirmed_complete", "claims": ["C3", "C2"]}]}
    parts, flags = _crit(raw, claims)
    st = {i["action"]: i["state"] for i in parts["service"]["items"]}
    assert st["Transfer retirement account"] == "unknown"           # unrelated payment does not complete it
    assert st["Pay compensation"] == "confirmed_complete"
    assert st["Rollover to Fund B"] == "confirmed_complete"
    assert st["Release hardship benefit"] == "attempted"            # approval of THIS action, no execution
    assert any("different action" in f for f in flags)
    # commitment fulfilment also needs evidence of the promised action, not of something else
    claims += [_claim(4, "commitment", actor="staff", quote="We will send the revised benefit statement by Friday.")]
    raw = {"commitments": [{"claim": "C4", "action": "send revised benefit statement", "status": "fulfilled",
                            "evidence": ["C1"]}]}
    parts, flags = _crit(raw, claims)
    assert parts["commitments"][0]["status"] == "unknown"


def test_withdrawn_evidence_never_survives_in_derived_interpretation():
    prior = {"factors": [
        {"factor_id": "F-gone", "label": "queue stalled", "support_claim_ids": ["CLM-removed"], "level": "mechanism",
         "status": "active", "prominence": "primary", "began_claim_ids": ["CLM-removed"], "ceased_claim_ids": [],
         "contrary_claim_ids": [], "first_revision": 1},
        {"factor_id": "F-part", "label": "member chased", "support_claim_ids": ["CLM-1", "CLM-removed"],
         "level": "handling", "status": "ceased", "prominence": "secondary", "began_claim_ids": [],
         "ceased_claim_ids": ["CLM-removed"], "contrary_claim_ids": ["CLM-removed"], "first_revision": 1}]}
    parts, flags = _crit({"factors": []}, [_claim(1, "chase")], prior)
    refs = {i for f in parts["factors"] for k in ("support_claim_ids", "contrary_claim_ids", "began_claim_ids",
                                                    "ceased_claim_ids") for i in f[k]}
    assert refs <= {"CLM-1"}
    assert [r["factor_id"] for r in parts["retracted_factors"]] == ["F-gone"]
    kept = parts["factors"][0]
    assert kept["status"] == "unknown"                    # its ceasing claim was withdrawn
