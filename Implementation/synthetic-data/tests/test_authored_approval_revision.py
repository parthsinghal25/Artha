"""R1 completion (2026-10-17): the payment ceiling is the latest approval/revision in effect at each payment, by known
time — never the maximum historical approval. Pure-function cases reproduce the reviewed example exactly; the FX
example cases run the complete checker/build path (FX-SRC-0504 is a snapshot table rendered from the authored
events, so its rows agree with them by construction and are asserted)."""
import csv
import io
import json
from pathlib import Path

from authored_helpers import Input, codes
from sdgen.authored.events import check_obligations
from sdgen.authored.export import build
from sdgen.authored.model import Clock, Report

TABLE = "runtime/intake-bundle/originals/tables/fin-export-FX-C05-2026-05-26.csv"


def ev(eid, when, state, amount):
    clock = Clock("day", when) if len(when) == 10 else Clock("second", when)
    return {"event_id": eid, "obligation_id": "OBL", "clock": clock, "state": state, "amount": amount,
            "category": "compensation", "invoice": "", "prerequisite_unknown": False, "where": f"event:{eid}"}


def run(*events):
    rep = Report()
    amb = check_obligations(list(events), rep, "event")
    return rep, amb


def errs(rep):
    return [f for f in rep.findings if f.severity == "error"]


def t(hh):
    return f"2026-05-08T{hh}:00:00+10:00"


# ---------------------------------------------------------------- pure function (reviewed reproduction)
def test_payment_above_reduced_ceiling_is_rejected():
    rep, amb = run(ev("A", t("09"), "approved", 10000), ev("R", t("10"), "amount_revision", 5000),
                   ev("P", t("11"), "payment", 9000))
    assert [f.code for f in errs(rep)] == ["payment_exceeds_obligation"]
    assert "5000" in errs(rep)[0].message and "['R']" in errs(rep)[0].message
    assert amb == []


def test_payment_within_revised_ceiling_passes():
    rep, amb = run(ev("A", t("09"), "approved", 10000), ev("R", t("10"), "amount_revision", 5000),
                   ev("P", t("11"), "payment", 5000))
    assert errs(rep) == [] and amb == []


def test_later_known_increase_is_respected():
    rep, _ = run(ev("A", t("09"), "approved", 5000), ev("R", t("10"), "amount_revision", 10000),
                 ev("P", t("11"), "payment", 9000))
    assert errs(rep) == []
    # the same payment before the increase exceeded what was then authorised
    rep, _ = run(ev("A", t("09"), "approved", 5000), ev("P", t("10"), "payment", 9000),
                 ev("R", t("11"), "amount_revision", 10000))
    assert [f.code for f in errs(rep)] == ["payment_exceeds_obligation"]


def test_revision_after_payment_does_not_change_what_was_authorised_then():
    rep, amb = run(ev("A", t("09"), "approved", 10000), ev("P", t("11"), "payment", 9000),
                   ev("R", t("15"), "amount_revision", 5000))
    assert errs(rep) == [] and amb == []


def test_cumulative_payments_and_reversals_measured_against_current_ceiling():
    base = [ev("A", "2026-05-01", "approved", 10000), ev("P1", "2026-05-02", "payment", 6000),
            ev("X", "2026-05-03", "payment_reversal", -6000), ev("R", "2026-05-04", "amount_revision", 5000)]
    rep, _ = run(*base, ev("P2", "2026-05-05", "payment", 5000))        # net 5000 within the revised 5000
    assert errs(rep) == []
    rep, _ = run(*base[:2], base[3], ev("P2", "2026-05-05", "payment", 1000))   # no reversal: net 7000 > 5000
    assert [f.code for f in errs(rep)] == ["payment_exceeds_obligation"]


def test_date_only_uncertain_order_is_ambiguous_not_invented():
    # approval and reduction share a date with no times: either may be the one in effect at the later payment
    rep, amb = run(ev("A", "2026-05-08", "approved", 10000), ev("R", "2026-05-08", "amount_revision", 5000),
                   ev("P", "2026-05-12", "payment", 9000))
    assert errs(rep) == []
    assert len(amb) == 1 and set(amb[0][0]) == {"A", "R", "P"} and "unknown" in amb[0][1]
    assert any(f.code == "ordering_ambiguous" for f in rep.findings)
    # above every candidate ceiling it is certain under any order
    rep, _ = run(ev("A", "2026-05-08", "approved", 10000), ev("R", "2026-05-08", "amount_revision", 5000),
                 ev("P", "2026-05-12", "payment", 11000))
    assert [f.code for f in errs(rep)] == ["payment_exceeds_obligation"]
    # a date-only revision on the payment's own day may fall either side of a timed payment
    rep, amb = run(ev("A", t("09"), "approved", 10000), ev("R", "2026-05-08", "amount_revision", 5000),
                   ev("P", t("11"), "payment", 9000))
    assert errs(rep) == [] and len(amb) == 1


# ---------------------------------------------------------------- complete checker/build path on the FX example
def revised(root, approve, revise, revise_at, pay, pay_at, approve_at=t("09")):
    """FX-OBL-05: approval FX-FIN-0501, an added amount_revision FX-FIN-0500 and payment FX-FIN-0502. The later
    payment FX-FIN-0504 and reversal FX-FIN-0503 are lowered so they never decide the outcome."""
    inp = Input(root)
    inp.event("FX-FIN-0501", lambda r: r.update(occurred=approve_at, amount_minor=approve))
    inp.event("FX-FIN-0502", lambda r: r.update(occurred=pay_at, amount_minor=pay))
    inp.event("FX-FIN-0503", lambda r: r.update(amount_minor=-pay))
    inp.event("FX-FIN-0504", lambda r: r.update(amount_minor=min(pay, revise, approve)))
    inp.add("private/events.jsonl", {"event_id": "FX-FIN-0500", "kind": "financial", "case_ids": ["FX-C05"],
                                     "occurred": revise_at, "state": "amount_revision", "obligation_id": "FX-OBL-05",
                                     "amount_minor": revise, "category": "refund_entitlement"})
    return inp


def errors_for(res, code):
    return [f for f in res["findings"] if f["severity"] == "error" and f["code"] == code]


def test_full_path_reduction_then_overpayment_is_rejected(tmp_path):
    res = revised(tmp_path, 10000, 5000, t("10"), 9000, t("11")).check()
    assert not res["ok"] and "payment_exceeds_obligation" in codes(res)
    hits = errors_for(res, "payment_exceeds_obligation")
    assert any("FX-FIN-0502" in f["message"] and "approved 5000" in f["message"] for f in hits)


def test_full_path_within_revised_ceiling_builds_and_table_agrees(tmp_path):
    res = build(revised(tmp_path, 10000, 5000, t("10"), 5000, t("11")).root, tmp_path / "out")
    assert res["ok"], [f for f in res["findings"] if f["severity"] == "error"]
    c = Path(res["candidate_dir"])
    rows = {r["event_id"]: r for r in csv.DictReader(io.StringIO((c / TABLE).read_text()))}
    authored = {json.loads(x)["event_id"]: json.loads(x)
                for x in (tmp_path / "input/private/events.jsonl").read_text().splitlines() if x.strip()}
    for eid in ("FX-FIN-0500", "FX-FIN-0501", "FX-FIN-0502"):
        assert int(rows[eid]["amount_minor"]) == authored[eid]["amount_minor"]
        assert rows[eid]["event_time"] == authored[eid]["occurred"]
    m = json.loads((c / "private/EXPECTED-METRICS.json").read_text())["checkpoints"]["2026-05-31"]
    a = m["approved_obligations_minor"]
    assert "FX-FIN-0500" in a["events"] and "FX-FIN-0501" not in a["events"] and a["value"] == 25000 + 5000 and "FX-OBL-05" not in (a.get("excluded_order_unknown") or [])


def test_full_path_later_increase_is_respected(tmp_path):
    res = revised(tmp_path, 5000, 9000, t("10"), 9000, t("11")).check()
    assert res["ok"], [f for f in res["findings"] if f["severity"] == "error"]


def test_full_path_revision_after_payment_does_not_reject_it(tmp_path):
    inp = revised(tmp_path, 10000, 5000, t("15"), 9000, t("11"))
    inp.event("FX-FIN-0504", lambda r: r.update(amount_minor=5000))
    res = inp.check()
    assert not errors_for(res, "payment_exceeds_obligation"), errors_for(res, "payment_exceeds_obligation")


def test_full_path_date_only_order_stays_ambiguous_in_check_and_expectations(tmp_path):
    inp = revised(tmp_path, 10000, 5000, "2026-05-08", 9000, "2026-05-12", approve_at="2026-05-08")
    res = build(inp.root, tmp_path / "out")
    assert res["ok"], [f for f in res["findings"] if f["severity"] == "error"]
    amb = [f for f in res["findings"] if f["code"] == "ordering_ambiguous" and "ceiling is unknown" in f["message"]]
    assert amb and "FX-FIN-0500" in amb[0]["message"] and "FX-FIN-0501" in amb[0]["message"]
    m = json.loads((Path(res["candidate_dir"]) / "private/EXPECTED-METRICS.json").read_text())
    assert "FX-OBL-05" in m["checkpoints"]["2026-05-31"]["approved_obligations_minor"]["excluded_order_unknown"]
