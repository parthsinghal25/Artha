"""Acceptance 3 and 4: checkpoints exclude later events and later-available evidence; five grains reconcile exact
contributor sets; copies/re-exports never multiply totals; a shared charge keeps one identity and all its links."""
import json

import pytest

from authored_helpers import EXAMPLE, Input
from sdgen.authored.export import build


def built(root, out, name=None):
    res = build(root, out, name=name)
    assert res["ok"], res["findings"]
    c = out / (name or json.loads((root / "BATCH.json").read_text())["batch_id"] + "-p1")
    return c, json.loads((c / "private/EXPECTED-METRICS.json").read_text())


@pytest.fixture(scope="module")
def exp(tmp_path_factory):
    return built(EXAMPLE, tmp_path_factory.mktemp("c"))


def v(snap, k):
    return snap[k]["value"], snap[k]["contributors"]


def test_checkpoint_31_may_excludes_later_payments_work_and_evidence(exp):
    s = exp[1]["checkpoints"]["2026-05-31"]
    assert v(s, "cases_arrived") == (3, ["FX-C03", "FX-C04", "FX-C05"])
    assert v(s, "paid_cash_gross_minor") == (65000, ["FX-FIN-0302", "FX-FIN-0502", "FX-FIN-0504"])
    assert v(s, "payment_reversals_minor") == (-20000, ["FX-FIN-0503"])
    assert v(s, "paid_cash_net_minor")[0] == 45000
    # the work rows are dated Jan/Mar but their only export became available on 5 June
    assert v(s, "work_recorded_seconds") == (0, [])
    # C03's closure is visible (12 March export); C04/C05 have no visible lifecycle rows: unknown, never "open"
    assert v(s, "open_backlog_confirmed") == (0, [])
    assert v(s, "closed_confirmed") == (1, ["FX-C03"])
    assert v(s, "status_unknown") == (2, ["FX-C04", "FX-C05"])
    assert v(s, "received_without_evidenced_closure") == (2, ["FX-C04", "FX-C05"])


def test_checkpoint_28_september_excludes_29_september_payment(exp):
    s = exp[1]["checkpoints"]["2026-09-28"]
    assert v(s, "cases_arrived")[0] == 4 and v(s, "members_with_arrived_cases")[0] == 3
    assert "FX-FIN-0402" not in v(s, "paid_cash_gross_minor")[1]
    assert v(s, "invoiced_minor") == (0, [])                  # the fee export only arrives on 30 September
    assert v(s, "work_recorded_seconds") == (6000, ["FX-WRK-0301", "FX-WRK-0302", "FX-WRK-0303"])
    assert v(s, "closed_confirmed") == (1, ["FX-C03"])                   # C03 reopening not yet visible
    assert v(s, "status_unknown") == (3, ["FX-C04", "FX-C05", "FX-C06"])


def test_checkpoint_30_september_final_state(exp):
    s = exp[1]["checkpoints"]["2026-09-30"]
    assert v(s, "cases_arrived")[0] == 6 and v(s, "members_with_arrived_cases")[0] == 5
    assert v(s, "open_backlog_confirmed") == (4, ["FX-C01", "FX-C02", "FX-C03", "FX-C06"])
    assert v(s, "closed_confirmed") == (2, ["FX-C04", "FX-C05"]) and v(s, "status_unknown") == (0, [])
    assert v(s, "paid_cash_gross_minor")[0] == 375000 and v(s, "paid_cash_net_minor")[0] == 355000
    assert v(s, "invoiced_minor") == (310000, ["FX-FIN-0401"])
    assert v(s, "requested_minor") == (1500, ["FX-FIN-0101"])
    assert v(s, "approved_obligations_minor")[0] == 45000      # approvals are not cash and not invoices
    assert v(s, "work_recorded_seconds")[0] == 11400
    assert v(s, "afca_matters_referenced") == (1, ["FX-AFCA-01"])


def test_five_grains_reconcile_exact_contributors(exp):
    e = exp[1]
    assert set(e["grains"]) == {"daily", "weekly", "monthly", "quarterly", "annual"}
    assert e["reconciled"] and all(r["ok"] for r in e["reconciliation"])
    assert e["grains"]["monthly"]["paid_cash_gross_minor"] == {
        "2026-03": {"value": 25000, "contributors": ["FX-FIN-0302"]},
        "2026-05": {"value": 40000, "contributors": ["FX-FIN-0502", "FX-FIN-0504"]},
        "2026-09": {"value": 310000, "contributors": ["FX-FIN-0402"]}}
    assert e["grains"]["quarterly"]["cases_arrived"]["2026-Q3"]["contributors"] == ["FX-C01", "FX-C02", "FX-C06"]
    assert e["grains"]["weekly"]["paid_cash_gross_minor"]["2026-W40"]["contributors"] == ["FX-FIN-0402"]
    (annual,) = e["grains"]["annual"]["work_recorded_seconds"].values()
    assert annual["value"] == 11400


def test_copies_and_reexports_do_not_multiply(tmp_path, exp):
    inp = Input(tmp_path)
    src = next(r for r in inp.rows("runtime/sources.jsonl") if r["source_id"] == "FX-SRC-0406")
    copy = json.loads(json.dumps(src))
    copy.update(source_id="FX-SRC-0408", filename="fin-export-FX-AFCA-01-resent.csv", copy_of_source_id="FX-SRC-0406")
    inp.add("runtime/sources.jsonl", copy)
    _, e = built(inp.root, tmp_path / "out")
    for cp in ("2026-05-31", "2026-09-28", "2026-09-30"):
        a, b = exp[1]["checkpoints"][cp], e["checkpoints"][cp]
        for k in ("paid_cash_gross_minor", "invoiced_minor", "work_recorded_seconds", "approved_obligations_minor"):
            assert a[k] == b[k]


def test_shared_charge_is_one_event_with_all_links(exp):
    cand, e = exp
    s = e["checkpoints"]["2026-09-30"]
    assert ["FX-FIN-0402", ["FX-C04", "FX-C06"]] in s["shared_events_counted_once"]
    assert s["paid_cash_gross_minor"]["contributors"].count("FX-FIN-0402") == 1
    index = [json.loads(x) for x in (cand / "runtime/SOURCE-INDEX.jsonl").read_text().splitlines()]
    inv = next(r for r in index if r["source_id"] == "FX-SRC-0403")
    assert inv["case_ids"] == ["FX-C04", "FX-C06"] and inv["afca_matter_ids"] == ["FX-AFCA-01"]
    assert sorted(inv["attached_to_source_ids"]) == ["FX-SRC-0404", "FX-SRC-0405"]
    m = json.loads((cand / "runtime/intake-bundle/delivery-manifest.json").read_text())
    e406 = next(o for o in m["originals"] if o["original_id"] == "FX-SRC-0406")
    assert e406["references"]["case_ids"] == ["FX-C04", "FX-C06"] and "first listed" in e406["association_note"]
