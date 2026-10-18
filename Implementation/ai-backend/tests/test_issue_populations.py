"""Baseline R07/R10: issue reach (Any/All/Exact, parent reach), temporal memberships with endpoint lineage, and
issue/AFCA-matter-linked burden crosswalks with union de-duplication. Specimen records only (isolated)."""
from datetime import datetime

import pytest

from sdbackend.engine import results
from sdbackend.engine.core import MELBOURNE
from sdbackend.engine.issues import interval_at, interval_in_window
from test_analytical_r10 import three_cases  # noqa: F401

FILTERS = {**results.DEFAULT_FILTERS, "activity_start": "2026-01-01T00:00:00+11:00",
           "activity_end_exclusive": "2026-04-01T00:00:00+11:00", "receipt_basis": "complaints",
           "granularity": "none", "issue_population_mode": "all_linked_to_date"}
REF = {"source_id": "SRC-1", "content_sha256": "a" * 64, "anchor": {"kind": "text", "start_codepoint": 0,
                                                                    "end_codepoint": 4}}


def _t(s):
    return datetime.fromisoformat(s).astimezone(MELBOURNE)


def _env(rid, available="2026-01-10T00:00:00+11:00"):
    return {"record_id": rid, "logical_id": rid, "supersedes_record_id": None, "available_at": available,
            "evidence_refs": [REF]}


def _finding(fid, members, *, state="supported", available="2026-01-10T00:00:00+11:00"):
    return {"kind": "finding", "envelope": _env(fid, available),
            "data": {"finding_id": fid, "evidence_state": state,
                     "memberships": [{"case_id": c, "state": st, "evidence_refs": [REF]} for c, st in members]}}


TAX = {"taxonomy_version": "TAXV-1", "nodes": [
    {"node_id": "FAM", "level": "family", "parent_node_id": None, "permitted_finding_ids": []},
    {"node_id": "A", "level": "issue", "parent_node_id": "FAM", "permitted_finding_ids": ["F-A"]},
    {"node_id": "B", "level": "issue", "parent_node_id": "FAM", "permitted_finding_ids": ["F-B"]},
    {"node_id": "C", "level": "issue", "parent_node_id": None, "permitted_finding_ids": ["F-C", "F-C-WEAK"]}]}


class _DB:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, kind, oid):
        return self.objects.get((kind, oid))


class _RT:
    def __init__(self, objects):
        self.db = _DB(objects)


class FakeInputs(results.Inputs):
    """Real `Inputs` issue/time logic over fixed accepted records (no release/authority plumbing)."""

    def __init__(self, recs, *, cutoff="2026-04-01T00:00:00+11:00", boundary="inclusive_cutoff", intervals=(),
                 transitions=None):
        objects = {("taxonomy", "TAXV-1"): TAX}
        for iv in intervals:
            objects[("membership_interval", iv["membership_version_id"])] = iv
        aset = {"taxonomy_version": "TAXV-1", "membership_interval_ids": [iv["membership_version_id"] for iv in intervals]}
        super().__init__(_RT(objects), aset, None, _t(cutoff), boundary, None)
        self.recs, self._transitions = recs, transitions or {}

    def heads(self, ids, kind, key, *, restricted=True):
        out = {}
        for r in self.recs:
            avail = self.assessment_available if kind == "finding" else self._available
            if r["kind"] == kind and avail(r["envelope"]["available_at"]):
                out[r["data"][key]] = r
                self.used_ids.add(r["envelope"]["record_id"])
        return out, set()

    def records(self, kind, key, *, restricted=True):
        return self.heads([], kind, key)

    def cases(self):
        return self.heads([], "case", "case_id")

    def transitions(self, case_id):
        return self._transitions.get(case_id, [])


def _sel(measure_id, **f):
    m = next(v for k, v in results.MEASURES.items() if k[1] == measure_id)
    return results.Sel(f={**FILTERS, **f}, measure=m, restriction=None)


def _case(cid, received="2026-01-05"):
    return {"kind": "case", "envelope": _env("NR-" + cid, "2026-01-05T00:00:00+11:00"),
            "data": {"case_id": cid, "complaints_receipt_date": received, "original_receipt_date": received}}


FINDINGS = [
    _finding("F-A", [("C1", "included"), ("C2", "included"), ("C3", "uncertain"), ("C4", "excluded")]),
    _finding("F-B", [("C1", "included"), ("C3", "included"), ("C5", "included")]),
    _finding("F-C", [("C5", "included")]),
    _finding("F-C-WEAK", [("C6", "included")], state="insufficient"),
]
CASES = [_case(c) for c in ("C1", "C2", "C3", "C4", "C5", "C6")]


def _pop(**f):
    inp = FakeInputs(FINDINGS + CASES)
    inp.window, inp.at = (_t(FILTERS["activity_start"]), inp.cutoff), inp.cutoff
    items, unc, miss, notes = results.collect_issue_linked_cases(inp, _sel("issues.linked_cases", **f), inp.cutoff)
    return sorted(i.entity_id for i in items), sorted(unc)


def test_issue_operators_parent_reach_and_exact_combinations():
    assert _pop(selection_operator="any", issue_ids=["A"]) == (["C1", "C2"], ["C3"])
    assert _pop(selection_operator="any", issue_ids=["A", "B"]) == (["C1", "C2", "C3", "C5"], [])
    assert _pop(selection_operator="all", issue_ids=["A", "B"]) == (["C1"], ["C3"])      # C3: A uncertain, B incl.
    assert _pop(selection_operator="any", issue_ids=["FAM"]) == (["C1", "C2", "C3", "C5"], [])   # parent reach
    # none = every node; the insufficient finding's included link is uncertain, the excluded link is nothing
    assert _pop() == (["C1", "C2", "C3", "C5"], ["C6"])
    # exact at level "issue": C1 = {A,B}; C3 = {B} + uncertain A; C5 = {B,C}
    assert _pop(selection_operator="exact", taxonomy_level="issue", issue_ids=["A", "B"]) == (["C1"], ["C3"])
    assert _pop(selection_operator="exact", taxonomy_level="issue", issue_ids=["B"]) == ([], ["C3"])
    assert _pop(selection_operator="exact", taxonomy_level="issue", issue_ids=["A"]) == (["C2"], [])


def test_issue_selection_validation_is_explicit():
    from sdbackend.engine.issues import IssueSelectionError, Taxonomy, validate
    tax = Taxonomy(TAX)
    base = dict(FILTERS)
    for bad in (dict(selection_operator="any", issue_ids=[]), dict(selection_operator="none", issue_ids=["A"]),
                dict(selection_operator="any", issue_ids=["NOPE"]),
                dict(selection_operator="exact", issue_ids=["A"], taxonomy_level=None),
                dict(selection_operator="any", issue_ids=["FAM"], taxonomy_level="issue"),
                dict(issue_population_mode="whenever")):
        with pytest.raises(IssueSelectionError):
            validate({**base, **bad}, tax, issue_measure=True)
    validate({**base, "selection_operator": "any", "issue_ids": ["A", "B"], "taxonomy_level": "issue"}, tax,
             issue_measure=True)                                                  # the UI lens request shape


def _iv(fid, case, start, end=None, ongoing=None, assessed="2026-01-20T00:00:00+11:00", state="included"):
    return {"membership_version_id": f"MIV-{fid}-{case}", "finding_id": fid, "finding_version": "v1",
            "taxonomy_node_id": "A", "taxonomy_version": "TAXV-1", "case_id": case, "episode_id": None,
            "state": state, "start": start, "end": end, "ongoing_at": ongoing, "endpoint_state": "unknown",
            "supporting_refs": [REF], "contradictory_refs": [], "assessed_evidence_as_of": assessed,
            "published_at": assessed}


def test_interval_rules_missing_completion_is_not_open_ended():
    day = lambda d: {"precision": "day", "date": d}
    jan, feb = (_t("2026-01-01T00:00:00+11:00"), _t("2026-02-01T00:00:00+11:00")), \
        (_t("2026-02-01T00:00:00+11:00"), _t("2026-03-01T00:00:00+11:00"))
    closed = _iv("F", "C", day("2026-01-10"), day("2026-01-20"))
    assert interval_in_window(closed, *jan) == "supported" and interval_in_window(closed, *feb) == "none"
    no_end = _iv("F", "C", day("2026-01-10"))
    assert interval_in_window(no_end, *jan) == "uncertain"          # unknown completion never supports
    ongoing = _iv("F", "C", day("2026-01-10"), ongoing="2026-02-15T00:00:00+11:00")
    assert interval_in_window(ongoing, *feb) == "supported"
    assert interval_at(ongoing, _t("2026-02-10T00:00:00+11:00")) == "supported"
    assert interval_at(ongoing, _t("2026-03-01T00:00:00+11:00")) == "uncertain"   # beyond what was observed
    assert interval_at(closed, _t("2026-03-01T00:00:00+11:00")) == "none"
    unknown_start = _iv("F", "C", {"precision": "unknown", "reason": "not evidenced"}, day("2026-03-01"))
    assert interval_at(unknown_start, _t("2026-02-10T00:00:00+11:00")) == "uncertain"


def test_temporal_modes_and_each_endpoint_uses_its_own_assessment():
    day = lambda d: {"precision": "day", "date": d}
    recs = [_finding("F-A", [("C1", "included"), ("C2", "included")]), _case("C1"), _case("C2")]
    ivs = [_iv("F-A", "C1", day("2026-01-10"), day("2026-01-25")),                 # active in January only
           _iv("F-A", "C2", day("2026-02-05"), ongoing="2026-03-20T00:00:00+11:00",
               assessed="2026-03-20T00:00:00+11:00")]                           # learned in late March
    def run(mode, window, endpoint):
        inp = FakeInputs(recs, cutoff=endpoint, boundary="exclusive_stock_endpoint", intervals=ivs)
        inp.window, inp.at = (_t(window[0]), _t(window[1])), _t(endpoint)
        items, unc, _, _ = results.collect_issue_linked_cases(
            inp, _sel("issues.linked_cases", selection_operator="any", issue_ids=["A"], issue_population_mode=mode),
            _t(endpoint))
        return sorted(i.entity_id for i in items), sorted(unc)
    jan = ("2026-01-01T00:00:00+11:00", "2026-02-01T00:00:00+11:00")
    mar = ("2026-03-01T00:00:00+11:00", "2026-04-01T00:00:00+11:00")
    assert run("active_in_window", jan, jan[1]) == (["C1"], ["C2"])        # C2 interval not yet assessed at Feb 1
    assert run("active_in_window", mar, mar[1]) == (["C2"], [])
    assert run("active_at_endpoint", mar, "2026-03-10T00:00:00+11:00") == ([], ["C2"])   # later diagnosis unseen
    assert run("active_at_endpoint", mar, mar[1]) == ([], ["C2"])          # observed only to 20 March
    assert run("all_linked_to_date", jan, jan[1]) == (["C1", "C2"], [])     # historical links kept
    # without any accepted interval an active mode is uncertain, never included because the case is open
    inp = FakeInputs(recs)
    inp.window, inp.at = (_t(jan[0]), _t(jan[1])), _t(jan[1])
    items, unc, _, notes = results.collect_issue_linked_cases(
        inp, _sel("issues.linked_cases", selection_operator="any", issue_ids=["A"],
                  issue_population_mode="active_at_endpoint"), _t(jan[1]))
    assert items == [] and unc == ["C1", "C2"] and any("no accepted membership intervals" in n for n in notes)


def _ledger():
    def ob(oid, cases, matters=(), cat="compensation"):
        return {"kind": "obligation", "envelope": _env(oid),
                "data": {"obligation_id": oid, "category": cat, "case_ids": list(cases),
                         "afca_matter_ids": list(matters), "duplicate_of_obligation_id": None}}

    def pay(eid, oid, amount, kind="payment"):
        return {"kind": "financial_event", "envelope": _env(eid),
                "data": {"financial_event_id": eid, "obligation_id": oid, "kind": kind, "amount_minor": amount,
                         "effective_at": None, "effective_date": "2026-02-01", "evidence_refs": [REF]}}

    def work(wid, cases, secs, matters=()):
        return {"kind": "work_entry", "envelope": _env(wid),
                "data": {"work_id": wid, "case_ids": list(cases), "afca_matter_ids": list(matters),
                         "duplicate_of_work_id": None, "duration_basis": "recorded", "phase": "follow_up",
                         "event_at": None, "event_date": "2026-02-02", "duration_seconds": secs,
                         "evidence_refs": [REF]}}
    return [ob("O-12", ["C1", "C2"], ["AFCA-1"]), pay("E-1", "O-12", 10000), pay("E-1R", "O-12", -2500, "payment_reversal"),
            ob("O-3", ["C3"]), pay("E-3", "O-3", 7000), ob("O-4", ["C4"], ["AFCA-2"]), pay("E-4", "O-4", 900),
            work("W-12", ["C1", "C2"], 3600, ["AFCA-1"]), work("W-3", ["C3"], 1800), work("W-4", ["C4"], 600)]


def _burden(measure, **f):
    inp = FakeInputs(FINDINGS + CASES + _ledger())
    inp.window, inp.at = (_t(FILTERS["activity_start"]), inp.cutoff), inp.cutoff
    sel = _sel(measure, **f)
    items, unc, miss, notes = sel.measure.collect(inp, sel, inp.cutoff) if sel.measure.basis != "flow" else \
        sel.measure.collect(inp, sel)
    return {i.entity_id: i.amount for i in items}, sorted(unc)


def test_issue_and_matter_linked_burden_is_an_exact_deduplicated_crosswalk():
    # Issue A: C1, C2 included (C3 uncertain). E-1/E-1R link two included cases → once each.
    cash, unc = _burden("cash.remedies_paid", selection_operator="any", issue_ids=["A"])
    assert cash == {"E-1": 10000, "E-1R": -2500} and unc == ["E-3"]           # C3-only event disclosed uncertain
    hours, unc = _burden("work.recorded_hours", selection_operator="any", issue_ids=["A"])
    assert hours == {"W-12": 3600} and unc == ["W-3"]
    owed, unc = _burden("obligations.outstanding", selection_operator="any", issue_ids=["A"])
    assert owed == {} and unc == ["O-3"]                                       # no amount kinds: not obligations
    # AFCA matter crosswalk follows each record's own matter links (events through their obligation)
    cash, _ = _burden("cash.remedies_paid", afca_matter_ids=["AFCA-2"])
    assert cash == {"E-4": 900}
    hours, _ = _burden("work.recorded_hours", afca_matter_ids=["AFCA-1"])
    assert hours == {"W-12": 3600}
    # Issue AND matter narrow together; the union total is never the sum of overlapping issue totals
    cash, _ = _burden("cash.remedies_paid", selection_operator="any", issue_ids=["A"], afca_matter_ids=["AFCA-2"])
    assert cash == {}
    a, _ = _burden("cash.remedies_paid", selection_operator="any", issue_ids=["A"])
    b, _ = _burden("cash.remedies_paid", selection_operator="any", issue_ids=["B"])
    ab, _ = _burden("cash.remedies_paid", selection_operator="any", issue_ids=["A", "B"])
    assert sum(ab.values()) < sum(a.values()) + sum(b.values()) and set(ab) == set(a) | set(b)


def test_open_linked_cases_intersects_reconstructed_open_state_at_endpoint():
    tr = lambda st, d, avail="2026-01-06T00:00:00+11:00": {"state": st, "effective_at": None, "effective_date": d,
                                                           "available_at": avail, "evidence_refs": [REF]}
    transitions = {"C1": [tr("open", "2026-01-05")], "C2": [tr("open", "2026-01-05"), tr("closed", "2026-01-15")],
                   "C3": [tr("open", "2026-01-05")], "C5": []}
    inp = FakeInputs(FINDINGS + CASES, cutoff="2026-02-01T00:00:00+11:00", boundary="exclusive_stock_endpoint",
                     transitions=transitions)
    inp.window, inp.at = (_t("2026-01-01T00:00:00+11:00"), inp.cutoff), inp.cutoff
    sel = _sel("issues.open_linked_cases", selection_operator="any", issue_ids=["A", "B"])
    items, unc, _, _ = results.collect_cases_open(inp, sel, inp.cutoff)
    # C1 open+included; C2 closed; C3 open+included(B); C5 included but no sourced state → uncertain
    assert sorted(i.entity_id for i in items) == ["C1", "C3"] and "C5" in unc and "C2" not in unc
    # linked cases segmented by state at the endpoint (UI status segments)
    sel = _sel("issues.linked_cases", selection_operator="any", issue_ids=["A", "B"], case_statuses=["closed"])
    items, _, _, _ = results.collect_issue_linked_cases(inp, sel, inp.cutoff)
    assert [i.entity_id for i in items] == ["C2"]
    sel = _sel("issues.linked_cases", selection_operator="any", issue_ids=["A", "B"], case_statuses=["unknown"])
    items, _, _, _ = results.collect_issue_linked_cases(inp, sel, inp.cutoff)
    assert [i.entity_id for i in items] == ["C5"]


def test_finding_published_after_endpoint_does_not_classify_earlier_stock():
    late = [_finding("F-A", [("C1", "included")], available="2026-03-01T00:00:00+11:00"), _case("C1")]
    inp = FakeInputs(late, cutoff="2026-02-01T00:00:00+11:00", boundary="exclusive_stock_endpoint")
    inp.window, inp.at = (_t("2026-01-01T00:00:00+11:00"), inp.cutoff), inp.cutoff
    items, unc, _, _ = results.collect_issue_linked_cases(inp, _sel("issues.linked_cases"), inp.cutoff)
    assert items == [] and unc == []
    at_endpoint = [_finding("F-A", [("C1", "included")], available="2026-02-01T00:00:00+11:00"), _case("C1")]
    inp = FakeInputs(at_endpoint, cutoff="2026-02-01T00:00:00+11:00", boundary="exclusive_stock_endpoint")
    inp.window, inp.at = (_t("2026-01-01T00:00:00+11:00"), inp.cutoff), inp.cutoff
    items, _, _, _ = results.collect_issue_linked_cases(inp, _sel("issues.linked_cases"), inp.cutoff)
    assert [i.entity_id for i in items] == ["C1"]                 # the endpoint's own assessment counts


def test_issue_reach_through_the_api_on_published_findings(three_cases, tmp_path):
    """End-to-end over the published (specimen-accepted) S05 finding + taxonomy: supported definition, Any
    reach, exact drill-through contributors, typed refusal of unknown nodes and of AFCA filters on case counts."""
    from sdbackend.api.ops import Operations
    from test_followup_f01_f06 import _ctx, _publish_findings
    from test_ingest_and_api import FILTERS as API_FILTERS
    from specimens import case_row
    rows = [case_row(c, "2026-03-01") for c in ("C-A", "C-B", "C-C")]
    rt, reviewed, aset = _publish_findings(three_cases, second_label="Second mechanism", natives=rows, tmp=tmp_path)
    assert {"registry_id": results.REG, "measure_id": "issues.linked_cases", "definition_version": "0.2.0"} \
        in aset["supported_definitions"]
    ops = Operations(rt)
    ctx = _ctx(ops, reviewed["release_id"])
    tax = rt.db.get_object("taxonomy", aset["taxonomy_version"])
    node = next(n["node_id"] for n in tax["nodes"] if n["label"] == "Scanning backlog loss")

    def q(**f):
        return ops.handle("leader.demo", dict(
            operation="analysis.query", context_id=ctx["context_id"], query_id="ISSUE-Q",
            definition=dict(registry_id=results.REG, measure_id="issues.linked_cases", definition_version="0.2.0"),
            filters=dict(API_FILTERS, granularity="none", issue_population_mode="all_linked_to_date", **f),
            population_reference=None))
    out = q(selection_operator="any", issue_ids=[node])
    assert out["status"] == "ok", out
    out = out["payload"] if "payload" in out else out
    cell = out["cells"][0]
    assert cell["point"]["value"] == 2 and cell["point"]["included_count"] == 2
    pred = cell["population"]["predicate"]
    assert pred["issue_ids"] == [node] and pred["selection_operator"] == "any" and pred["taxonomy_version"]
    contrib = ops.handle("leader.demo", dict(operation="contributors.list", context_id=ctx["context_id"],
                                             result_id=out["result_id"], cell_key=cell["key"],
                                             cursor=None, page_size=50))
    assert contrib["status"] == "ok", contrib
    assert sorted(i["entity_id"] for i in contrib["payload"]["items"]) == ["C-A", "C-B"]   # C-C excluded member
    assert q(selection_operator="any", issue_ids=["TAX-NOT-ACCEPTED"])["error"]["code"] == "INVALID_REQUEST"
    assert q(selection_operator="all", issue_ids=[])["error"]["code"] == "INVALID_REQUEST"
