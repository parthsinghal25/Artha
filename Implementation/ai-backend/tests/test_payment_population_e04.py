"""E04: a selected financial-event population keeps exactly its events when re-queried (isolated specimens only).

The Architect's preserved reproducer (`Architecture/integration-reviews/2026-10-15-payment-population/`) covers the
A$300 parent → child case. These add the branches it cannot: a selected subset of one case's payments must not widen
to every payment in that case, case-less AFCA expense contributors survive, further case/matter filters intersect,
an empty population stays empty, and a different measure over the same unit intersects by entity. The amounts are
specimen values, not case 11's figures.
"""
from __future__ import annotations

import copy

import pytest

from conftest import data_dir, rt, rehearsal_bundle  # noqa: F401  (pytest fixtures)
from test_analytical_r10 import CASE, ledger  # noqa: F401
from test_ingest_and_api import FILTERS
from specimens import publish_native

MATTER = "AFCA-SPECIMEN-E04"


def _extra(rec, kind, rid, data):
    out = copy.deepcopy(rec)
    out["kind"] = kind
    out["envelope"].update(record_id=rid, logical_id=rid, supersedes_record_id=None)
    out["data"] = data
    return out


@pytest.fixture
def payments(ledger):  # noqa: F811
    from sdbackend.engine.resolvers import resolve
    from sdbackend.api.ops import Operations
    runtime, summary = ledger
    body = resolve(runtime, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"], persist=False)
    base_ob = next(r for r in body["analysis_records"] if r["kind"] == "obligation")
    base_ev = next(r for r in body["analysis_records"] if r["kind"] == "financial_event")
    refs = base_ev["data"]["evidence_refs"]
    ev = lambda eid, oid, amount, day: _extra(base_ev, "financial_event", eid, {  # noqa: E731
        **base_ev["data"], "financial_event_id": eid, "obligation_id": oid, "kind": "payment", "amount_minor": amount,
        "effective_at": None, "effective_date": day, "evidence_refs": refs})
    body["analysis_records"] += [
        # a second remedy obligation in the SAME case, paid in March: never part of a February selection
        _extra(base_ob, "obligation", "OBL-E04-SAMECASE", {**base_ob["data"], "obligation_id": "OBL-E04-SAMECASE"}),
        ev("FEV-E04-SAMECASE", "OBL-E04-SAMECASE", 7000, "2026-03-05"),
        # an AFCA fee scoped to a matter only (no case IDs): a non-case contributor
        _extra(base_ob, "obligation", "OBL-E04-AFCA", {**base_ob["data"], "obligation_id": "OBL-E04-AFCA",
                                                         "category": "afca_fee", "case_ids": [],
                                                         "afca_matter_ids": [MATTER]}),
        ev("FEV-E04-AFCA", "OBL-E04-AFCA", 250000, "2026-02-12"),
    ]
    for r in body["analysis_records"]:
        runtime.contracts.check("analysis_record", r, document="runtime")
    body["stage_id"] = "STAGE-S04-E04-SPECIMEN"
    runtime.db.put_object("stage_s04", body["stage_id"], body)
    publish_native(runtime, summary["release_id"], stages=[body["stage_id"]])
    ops = Operations(runtime)
    root = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                          release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                          analysis_mode="reconstructed", analysis_as_of=None, population_reference=None))
    return ops, summary, root["context"]


def _q(ops, ctx, measure, ref=None, **filters):
    got = ops.handle("leader.demo", dict(
        operation="analysis.query", context_id=ctx["context_id"], query_id="E04",
        definition={"registry_id": "runtime-measures-0.2.0", "measure_id": measure, "definition_version": "0.2.0"},
        filters={**FILTERS, **filters}, population_reference=ref))
    assert got["status"] == "ok", got
    return got["payload"]


def _ref(result, key):
    cell = next(c for c in result["cells"] if c["key"] == key)
    return cell, {"result_id": result["result_id"], "cell_key": key, "population_id": cell["population_id"],
                  "predicate_sha256": cell["population"]["predicate_sha256"]}


def _child(ops, summary, ctx, ref):
    got = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of=ctx["evidence_as_of"],
                                         analysis_mode="reconstructed", analysis_as_of=None, population_reference=ref))
    assert got["status"] == "ok", got
    return got["context"]


def _ids(ops, ctx, result, key="all"):
    got = ops.handle("leader.demo", dict(operation="contributors.list", context_id=ctx["context_id"],
                                         result_id=result["result_id"], cell_key=key, cursor=None, page_size=50))
    assert got["status"] == "ok", got
    return {i["entity_id"] for i in got["payload"]["items"]}


def _total(result):
    return sum(c["point"]["value"] or 0 for c in result["cells"])


def test_selected_payments_do_not_widen_to_other_payments_of_the_same_case(payments):
    ops, summary, root = payments
    whole = _q(ops, root, "cash.remedies_paid", granularity="none")
    assert whole["cells"][0]["point"]["value"] == 37000             # Feb +500 −200 and March +70, specimen values
    monthly = _q(ops, root, "cash.remedies_paid")
    feb, ref = _ref(monthly, "2026-02-01")
    assert feb["point"]["value"] == 30000
    parent_ids = _ids(ops, root, monthly, "2026-02-01")
    assert "FEV-E04-SAMECASE" not in parent_ids and len(parent_ids) == 2
    child = _child(ops, summary, root, ref)
    again = _q(ops, child, "cash.remedies_paid", ref, granularity="none")
    assert again["cells"][0]["point"]["value"] == 30000             # not 37000: no widening to the March payment
    assert _ids(ops, child, again) == parent_ids
    assert any("frozen financial_event_id set of 2" in l for l in again["cells"][0]["coverage"]["limitations"])
    # further restrictions intersect; a non-overlapping case filter is empty, not the parent value
    assert _q(ops, child, "cash.remedies_paid", ref, granularity="none", case_ids=[CASE])["cells"][0]["point"]["value"] == 30000
    other = _q(ops, child, "cash.remedies_paid", ref, granularity="none", case_ids=["C-not-in-population"])
    assert other["cells"][0]["point"]["value"] == 0 and other["cells"][0]["point"]["included_count"] == 0
    # same unit, different definition: the frozen remedy events are not AFCA expenses
    assert _q(ops, child, "cash.expenses_paid", ref, granularity="none")["cells"][0]["point"]["value"] == 0


def test_case_less_afca_expense_population_survives_requery(payments):
    ops, summary, root = payments
    parent = _q(ops, root, "cash.expenses_paid", granularity="none")
    cell, ref = _ref(parent, "all")
    assert cell["point"]["value"] == 250000
    ids = _ids(ops, root, parent)
    assert ids == {"FEV-E04-AFCA"}
    child = _child(ops, summary, root, ref)
    again = _q(ops, child, "cash.expenses_paid", ref, granularity="none")
    assert again["cells"][0]["point"]["value"] == 250000 and _ids(ops, child, again) == ids
    assert _q(ops, child, "cash.expenses_paid", ref, granularity="none",
              afca_matter_ids=[MATTER])["cells"][0]["point"]["value"] == 250000
    assert _q(ops, child, "cash.expenses_paid", ref, granularity="none",
              afca_matter_ids=["AFCA-OTHER"])["cells"][0]["point"]["value"] == 0
    # the combined measure over the same frozen events keeps only the AFCA payment, not the case's remedies
    assert _q(ops, child, "cash.expenses_and_remedies_paid", ref, granularity="none")["cells"][0]["point"]["value"] == 250000


def test_empty_payment_population_stays_empty(payments):
    ops, summary, root = payments
    monthly = _q(ops, root, "cash.remedies_paid")
    jan, ref = _ref(monthly, "2026-01-01")
    assert jan["point"]["value"] == 0 and jan["point"]["included_count"] == 0
    child = _child(ops, summary, root, ref)
    again = _q(ops, child, "cash.expenses_and_remedies_paid", ref, granularity="none")
    assert again["cells"][0]["point"]["value"] == 0 and again["cells"][0]["point"]["included_count"] == 0
    assert _ids(ops, child, again) == set()
