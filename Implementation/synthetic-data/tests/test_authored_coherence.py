"""Acceptance 2 and the v2 defect patterns: incoherent author input is rejected with an actionable finding and nothing
is published; deliberately uncertain evidence stays uncertain."""
import json

import pytest

from authored_helpers import EXAMPLE, Input, codes
from sdgen.authored.export import build


def test_canonical_example_is_clean():
    res = build(EXAMPLE, dry_run=True)
    assert res["ok"], res["findings"]
    assert not [f for f in res["findings"] if f["severity"] == "error"]


def _rows(inp, sid):
    return next(r for r in inp.rows("runtime/sources.jsonl") if r["source_id"] == sid)["content"]["rows"]


@pytest.mark.parametrize("mutate,code", [
    # v2 F02: a dated export already reporting a later completed payment
    (lambda i: i.source("FX-SRC-0406", lambda r: r["clocks"].update(created_at="2026-09-20T17:00:00+10:00", available_at="2026-09-20T17:00:00+10:00")), "future_row"),
    # the same for recorded work
    (lambda i: i.source("FX-SRC-0407", lambda r: r["clocks"].update(created_at="2026-07-20T09:00:00+10:00", available_at="2026-07-20T09:00:00+10:00")), "future_row"),
    # a supplied finished email whose Date header disagrees with the record
    (lambda i: i.source("FX-SRC-0501", lambda r: r["clocks"].update(event_at="2026-04-28T14:47:00+10:00", available_at="2026-04-28T15:05:00+10:00")), "inconsistent_clock"),
    # created after it was held
    (lambda i: i.source("FX-SRC-0302", lambda r: r["clocks"].update(created_at="2026-02-15T09:00:00+11:00")), "inconsistent_clock"),
    # AEST offset during daylight saving
    (lambda i: i.source("FX-SRC-0301", lambda r: r["clocks"].update(event_at="2026-01-20T10:05:00+10:00")), "clock_wrong_offset"),
    # a ledger row naming an event that was never authored
    (lambda i: i.source("FX-SRC-0406", lambda r: r["content"]["rows"][1].__setitem__(0, "FX-FIN-9999")), "dangling_event_ref"),
    # a source linked to a case that does not exist
    (lambda i: i.source("FX-SRC-0105", lambda r: r.update(case_ids=["FX-C99"])), "dangling_case_ref"),
    # a payment prerequisite that does not exist
    (lambda i: i.event("FX-FIN-0302", lambda r: r.update(prerequisite_event_ids=["FX-FIN-0399"])), "missing_prerequisite"),
    # v2 F03: a reversal with no earlier payment on its obligation (B075)
    (lambda i: i.event("FX-FIN-0503", lambda r: r.update(obligation_id="FX-OBL-99")), "unmatched_reversal"),
    # reversing more than was paid
    (lambda i: i.event("FX-FIN-0503", lambda r: r.update(amount_minor=-30000)), "over_reversal"),
    # a payment with no approval on its obligation
    (lambda i: i.event("FX-FIN-0501", lambda r: r.update(obligation_id="FX-OBL-98")), "unmatched_payment"),
    # an approval/invoice labelled as a payment
    (lambda i: i.source("FX-SRC-0406", lambda r: r["content"]["rows"][0].__setitem__(5, "payment")), "row_event_mismatch"),
    # 'remedy_payment' as a state is not a lifecycle state
    (lambda i: i.event("FX-FIN-0302", lambda r: r.update(state="remedy_payment")), "financial_state"),
    # a later event clamped into the window is refused, not moved
    (lambda i: i.event("FX-EV-0203", lambda r: r.update(occurred="2026-10-02T09:00:00+10:00")), "event_outside_window"),
    (lambda i: i.source("FX-SRC-0203", lambda r: r["clocks"].update(available_at="2026-10-01T09:00:00+10:00")), "available_outside_window"),
    # closing a case that was never received
    (lambda i: i.event("FX-EV-0305", lambda r: r.update(occurred="2026-01-10T09:00:00+11:00")), "lifecycle_order"),
    # a source asserting an event that has not happened yet
    (lambda i: i.source("FX-SRC-0302", lambda r: r["reports_events"].append("FX-EV-0306")), "future_knowledge"),
    # v2 F04: an early notice that names a complaint received later
    (lambda i: i.source("FX-SRC-0302", lambda r: r["content"].update(text=r["content"]["text"] + "See also FX-C06.\n")), "case_id_foreknowledge"),
    # an attachment the sender never had
    (lambda i: i.source("FX-SRC-0404", lambda r: r["attachments"].append({"source_id": "FX-SRC-0999", "disposition": "supplied"})), "missing_attachment"),
    # the combined v2 ledger shape (entry_id/status, no obligation link)
    (lambda i: i.source("FX-SRC-0406", lambda r: r["content"].update(columns=["entry_id", "case_id", "date", "status", "amount_minor", "category"],
                                                                     rows=[["E1", "FX-C04", "2026-08-20", "invoiced", 310000, "afca_fee"]])), "ledger_profile"),
    # incomplete input is reported, never filled in
    (lambda i: i.source("FX-SRC-0203", lambda r: r.pop("recipients")), "missing_field"),
    (lambda i: i.source("FX-SRC-0105", lambda r: r["clocks"].pop("available_at")), "missing_clock"),
])
def test_incoherent_input_is_rejected(tmp_path, mutate, code):
    inp = Input(tmp_path)
    mutate(inp)
    res = inp.check()
    assert not res["ok"]
    assert code in codes(res), res["findings"]
    hit = next(f for f in res["findings"] if f["code"] == code)
    assert hit["message"] and hit["subject"]


def test_conflicting_duplicate_event_across_exports(tmp_path):
    inp = Input(tmp_path)
    src = next(r for r in inp.rows("runtime/sources.jsonl") if r["source_id"] == "FX-SRC-0406")
    dup = json.loads(json.dumps(src))
    dup.update(source_id="FX-SRC-0408", filename="fin-export-FX-AFCA-01-copy.csv")
    dup["content"]["rows"][1][6] = 300000
    inp.add("runtime/sources.jsonl", dup)
    assert "conflicting_duplicate" in codes(inp.check())


def test_shared_charge_recorded_as_two_invoices_is_rejected(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0401", lambda r: r.update(case_ids=["FX-C04"]))
    inp.add("private/events.jsonl", {"event_id": "FX-FIN-0405", "kind": "financial", "case_ids": ["FX-C06"], "occurred": "2026-08-20",
                                     "state": "invoiced", "obligation_id": "FX-OBL-04", "amount_minor": 310000, "category": "afca_fee",
                                     "invoice_reference": "FX-INV-7781"})
    assert "shared_charge_duplicated" in codes(inp.check())


def test_fee_before_referral_is_rejected_when_the_dependency_is_declared(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0401", lambda r: r.update(prerequisite_event_ids=["FX-EV-0402"]))
    assert inp.check()["ok"]
    inp.event("FX-EV-0402", lambda r: r.update(occurred="2026-08-31"))   # referral after the fee
    assert "prerequisite_after_event" in codes(inp.check())


def test_uncertain_evidence_stays_uncertain(tmp_path):
    res = build(EXAMPLE, tmp_path)
    assert res["ok"]
    checks = json.loads((tmp_path / "fx-five-members-p1/private/JUDGEMENT-ANCHOR-CHECKS.json").read_text())
    j5 = next(j for j in checks["judgements"] if j["judgement_id"] == "FX-J05")
    assert j5["judgement"] == "uncertain"
    assert {a["result"] for a in j5["anchors"]} == {"resolved"}
    assert j5["review_status"] == "author_proposed_pending_independent_review"
    assert not {"verdict", "supported", "confirmed"} & set(j5)
    assert "no judgement is supported" in checks["statement"]


def test_future_dated_mentions_need_author_acknowledgement(tmp_path):
    inp = Input(tmp_path)
    inp.source("FX-SRC-0103", lambda r: r.pop("future_dates_acknowledged"))
    res = inp.check()
    assert res["ok"]          # a promise is legitimate: warning, not error
    assert any(f["code"] == "future_date_mention" and f["severity"] == "warning" for f in res["findings"])


def test_explicit_unknown_prerequisite_is_preserved(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0501", lambda r: r.update(obligation_id="FX-OBL-98"))
    assert "unmatched_payment" in codes(inp.check())
    for e in ("FX-FIN-0502", "FX-FIN-0504"):
        inp.event(e, lambda r: r.update(prerequisite_unknown="approval record is held by the fees team, not the complaints file"))
    assert "unmatched_payment" not in codes(inp.check())
