"""R10: S04 ledger resolver → published measures, S05 collective guards, targeted invalidation, isolated evaluator.

All facts, findings, gold rows and decisions here are isolated TEST SPECIMENS in temporary stores (specimens.py).
The scripted model is a test double, never inference; nothing here is published on the served release.
"""
import json
from datetime import datetime

import pytest

from sdbackend.citations import text_ref
from sdbackend.models.base import GenResult, Usage

from specimens import approve_originals, case_row, decide, mini_bundle, publish_native, specimen_bundle
from test_ingest_and_api import FILTERS

CASE = "C-e1da28538652e263887a"
ONE = dict(FILTERS, granularity="none")


def _fact(src, entity, field, value, *, day=None, kind="documented_in_source", span=(0, 5)):
    return {"data": {"fact_id": f"F-{entity}-{field}-{src['source_id'][-6:]}", "entity_id": entity,
                     "field_key": field, "event_date": day, "assertion_kind": kind,
                     "value": {"value": value, "state": "known", "evidence_refs": [text_ref(src, *span)]}}}


@pytest.fixture()
def ledger(rt, rehearsal_bundle, tmp_path):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    bundle = specimen_bundle(tmp_path, rehearsal_bundle, [case_row(CASE, "2026-01-10")])
    d = receive(bundle, rt=rt)
    approve_originals(rt, d)
    decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                 "subject_sha256": n.record_sha256} for n in d.natives])
    summary = ingest(rt, receive(bundle, rt=rt))
    srcs = sorted((s for s in rt.db.list_objects("source") if CASE in s["data"]["case_ids"]),
                  key=lambda s: s["data"]["source_id"])
    a, b, c = (x["data"] for x in srcs[:3])
    facts1 = [
        _fact(a, "COMP", "financial_amount_minor", 50000, day="2026-02-03"),
        _fact(a, "COMP", "financial_kind", "payment"), _fact(a, "COMP", "financial_category", "compensation"),
        _fact(a, "OFFER", "financial_amount_minor", 99999, day="2026-02-01"),
        _fact(a, "OFFER", "financial_kind", "offer"), _fact(a, "OFFER", "financial_category", "compensation"),
        _fact(a, "LOOSE", "financial_amount_minor", 4242),                                  # no kind/category
        _fact(a, "CALL", "work_duration_seconds", 3600, day="2026-02-04", kind="documented_native"),  # model label
        _fact(a, "CALL", "work_phase", "member_contact"),
        _fact(a, "PROSE", "work_duration_seconds", 7200, day="2026-02-05"),               # reported in prose
    ]
    facts2 = [
        _fact(b, "COMP", "financial_amount_minor", 20000, day="2026-02-10"),
        _fact(b, "COMP", "financial_kind", "reversal"), _fact(b, "COMP", "financial_category", "compensation"),
        _fact(c, "COMP", "financial_amount_minor", 50000, day="2026-01-30"),
        _fact(c, "COMP", "financial_kind", "agreed"), _fact(c, "COMP", "financial_category", "compensation"),
    ]
    rt.db.put_object("stage_s02", "STAGE-S02-A", {"facts": facts1})
    rt.db.put_object("stage_s02", "STAGE-S02-B", {"facts": facts2})
    rt.db.put_object("stage_s02", "STAGE-S02-A-REPEAT", {"facts": facts1[:3]})              # repeated mention
    return rt, summary


def test_resolver_builds_exact_ledger_and_lists_unresolved(ledger):
    rt, _ = ledger
    from sdbackend.engine.resolvers import resolve
    st = resolve(rt, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"])
    evs = [r["data"] for r in st["analysis_records"] if r["kind"] == "financial_event"]
    obs = [r for r in st["analysis_records"] if r["kind"] == "obligation"]
    assert sorted((e["kind"], e["amount_minor"]) for e in evs) == [
        ("agreed", 50000), ("offered", 99999), ("payment", 50000), ("payment_reversal", -20000)]
    assert len(obs) == 2                                          # COMP across three sources = one obligation
    work = [r["data"] for r in st["analysis_records"] if r["kind"] == "work_entry"]
    assert work == []           # a model's `documented_native` label on prose is not a trusted native work log
    reasons = " | ".join(u["reason"] for u in st["unresolved"])
    assert "lacks an exact amount" in reasons and reasons.count("model-read duration") == 2
    assert st["consumed_sources"] and st["draft_status"] == "staged_not_accepted"
    assert resolve(rt, ["STAGE-S02-A-REPEAT", "STAGE-S02-B", "STAGE-S02-A"])["stage_id"] == st["stage_id"]


def test_resolved_ledger_publishes_cash_work_and_obligation_measures(ledger):
    rt, summary = ledger
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import resolve
    st = resolve(rt, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"])
    aset, prepared = publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    assert any(r["subject_kind"] == "stage_s04" for r in prepared["required_decisions"])
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]

    def val(measure):
        ans = ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                             definition={"registry_id": "runtime-measures-0.2.0", "measure_id": measure,
                                                         "definition_version": "0.2.0"},
                                             filters=ONE, population_reference=None))
        assert ans["status"] == "ok", ans
        return ans["payload"]["cells"][0]["point"]["value"]
    assert val("cash.remedies_paid") == 30000                     # payment − reversal; offers/agreements excluded
    assert val("cash.expenses_paid") == 0
    assert val("obligations.outstanding") == 20000                # agreed 50000 − net paid 30000
    assert not any(d["measure_id"] == "work.recorded_hours" for d in aset["supported_definitions"])  # no native log


def test_stage_refused_without_independent_diagnostic_pass(ledger):
    rt, summary = ledger
    from sdbackend.engine.publish import PublicationRefused, prepare_closure, publish_analysis_set
    from sdbackend.engine.resolvers import resolve
    from specimens import approve_required
    st = resolve(rt, ["STAGE-S02-A"])
    prepared = prepare_closure(rt, release_id=summary["release_id"],
                               assessed_evidence_as_of="2026-10-01T00:00:00+10:00", stage_ids=[st["stage_id"]])
    approve_required(rt, prepared, skip=("diagnostic_evaluation",))
    with pytest.raises(PublicationRefused, match="diagnostic_evaluation decision for stage_s04"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])


# ------------------------------------------------------------------ cases.open endpoints
class _Inputs:
    def __init__(self, transitions):
        self._t = transitions

    def cases(self):
        rec = case_row("C-X", "2026-01-05")
        return {"C-X": rec}, set()

    def transitions(self, cid):
        return self._t


class _Sel:
    f = dict(FILTERS)


def test_cases_open_two_endpoints_and_contradictory_later_evidence():
    from sdbackend.engine.results import collect_cases_open
    t = [{"state": "open", "effective_at": None, "effective_date": "2026-01-05",
          "available_at": "2026-01-05T10:00:00+11:00", "evidence_refs": []},
         {"state": "closed", "effective_at": None, "effective_date": "2026-03-01",
          "available_at": "2026-03-01T10:00:00+11:00", "evidence_refs": []},
         {"state": "reopened", "effective_at": None, "effective_date": "2026-03-01",          # contradicts the close
          "available_at": "2026-05-01T10:00:00+10:00", "evidence_refs": []}]
    feb = datetime.fromisoformat("2026-02-01T00:00:00+11:00")
    apr = datetime.fromisoformat("2026-04-01T00:00:00+11:00")
    jun = datetime.fromisoformat("2026-06-01T00:00:00+10:00")
    items, unc, _, _ = collect_cases_open(_Inputs(t), _Sel, feb)
    assert [i.entity_id for i in items] == ["C-X"] and unc == []
    items, unc, _, _ = collect_cases_open(_Inputs(t), _Sel, apr)       # contrary evidence not yet available
    assert items == [] and unc == []
    items, unc, _, _ = collect_cases_open(_Inputs(t), _Sel, jun)       # now available: conflicting → uncertain
    assert items == [] and unc == ["C-X"]


# ------------------------------------------------------------------ S05 collective
TEXT_A = b"Member A: the rollover request form was lost after the scanning queue backlog in March.\n"
TEXT_B = b"Member B: our rollover request form was lost in the scanning queue backlog again.\n"
TEXT_C = b"Member C: the death benefit claim was delayed by a missing certificate.\n"


@pytest.fixture()
def three_cases(rt, tmp_path):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    bundle = mini_bundle(tmp_path, [
        {"original_id": "ORIG-A", "rel": "originals/a.txt", "data": TEXT_A, "media": "text/plain", "case": "C-A",
         "available": "2026-03-10T09:00:00+11:00"},
        {"original_id": "ORIG-B", "rel": "originals/b.txt", "data": TEXT_B, "media": "text/plain", "case": "C-B",
         "available": "2026-04-10T09:00:00+10:00"},
        {"original_id": "ORIG-C", "rel": "originals/c.txt", "data": TEXT_C, "media": "text/plain", "case": "C-C",
         "available": "2026-04-12T09:00:00+10:00"}])
    summary = ingest(rt, receive(bundle, rt=rt))
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.pipeline.tools import ToolHost, build_index
    from sdbackend.policy import pipeline_authority
    rv = served_release(rt)
    repo = Repo(rt, rv, pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    host = ToolHost(rt, repo, run_id="RUN-s05", index_version=build_index(rt, rv.release_id), case_scope=None)
    src = {s["data"]["case_ids"][0]: s["data"] for s in rt.db.list_objects("source")}
    return rt, summary, host, src, bundle


def _new_host(rt):
    """A fresh tool host per investigation (a host refuses identical repeated searches within one run)."""
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.pipeline.tools import ToolHost, build_index
    from sdbackend.policy import pipeline_authority
    rv = served_release(rt)
    repo = Repo(rt, rv, pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    return ToolHost(rt, repo, run_id="RUN-s05", index_version=build_index(rt, rv.release_id), case_scope=None)


class _Gateway:
    """Scripted TEST DOUBLE for the gateway: returns queued DTOs; never a model."""
    class provider:
        name = "test-double"
    model = "scripted"

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def call(self, req, *, task_id, attempt):
        self.requests.append(req)
        data = self.responses.pop(0)
        return GenResult(provider="test-double", model="scripted", text=json.dumps(data), data=data,
                         usage=Usage(10, 0, 0, 5, 15, "provider"), latency_ms=1)


def _seed_stage(src, case):
    return {"timeline_events": [{"proposition": "rollover request form lost in scanning queue backlog",
                                 "source_refs": [text_ref(src[case], 0, 10)]}],
            "missing_facts": [], "contrary_evidence": []}


def _handle(shown, case, src):
    return next(h for h in shown if h.split("~")[0] == src[case]["source_id"])


def _finding(members, contrary=()):
    return {"findings": [{"title": "Lost rollover forms", "claim": "Rollover request forms were lost in a scanning backlog.",
                          "taxonomy_label": "Scanning backlog loss", "dimension": "original_problem",
                          "members": members, "contrary_citations": list(contrary), "alternatives": [],
                          "missing_facts": []}]}


def test_collective_supported_only_with_each_members_own_evidence(three_cases):
    rt, summary, host, src, _ = three_cases
    from sdbackend.pipeline.collective import discover, investigate
    shown = [p["passage_handle"] for p in discover(host, [_seed_stage(src, "C-A")])[0]]
    ha, hb = _handle(shown, "C-A", src), _handle(shown, "C-B", src)       # discovery reached another case
    good = _finding([
        {"case_id": "C-A", "state": "included", "association": "historical", "reason": "own passage",
         "citations": [{"passage_handle": ha, "quote": "rollover request form was lost"}]},
        {"case_id": "C-B", "state": "included", "association": "active", "reason": "own passage",
         "citations": [{"passage_handle": hb, "quote": "rollover request form was lost"}]},
        {"case_id": "C-C", "state": "included", "association": "unknown", "reason": "borrowed evidence",
         "citations": [{"passage_handle": ha, "quote": "scanning queue backlog"}]}])           # another case's quote
    gw = _Gateway({"findings": "not-a-list"}, good)                   # one schema repair, then valid
    out = investigate(gw, _new_host(rt), [_seed_stage(src, "C-A")], task_id="T-s05", contracts=rt.contracts,
                      assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    assert out["requests"] == 2
    f = out["analysis_records"][0]["data"]
    states = {m["case_id"]: m["state"] for m in f["memberships"]}
    assert states == {"C-A": "included", "C-B": "included", "C-C": "uncertain"}   # lookalike kept, not dropped
    assert f["evidence_state"] == "supported" and any("another case" in d for d in out["dropped_citations"])
    assert out["taxonomy_nodes"][0]["permitted_finding_ids"] == [f["finding_id"]]
    assert out["search_scopes"] and all(s["case_scope"] is None for s in out["search_scopes"])


def test_collective_single_case_or_unshown_citations_are_insufficient(three_cases):
    rt, summary, host, src, _ = three_cases
    from sdbackend.pipeline.collective import discover, investigate
    shown = [p["passage_handle"] for p in discover(host, [_seed_stage(src, "C-A")])[0]]
    ha = _handle(shown, "C-A", src)
    dto = _finding([
        {"case_id": "C-A", "state": "included", "association": "active", "reason": "",
         "citations": [{"passage_handle": ha, "quote": "rollover request form was lost"}]},
        {"case_id": "C-B", "state": "included", "association": "active", "reason": "",
         "citations": [{"passage_handle": "SRC-NOT-SHOWN~P1", "quote": "lost"},
                       {"passage_handle": ha, "quote": "text that is not in the passage"}]}],
        contrary=[{"passage_handle": ha, "quote": "in March"}])
    out = investigate(_Gateway(dto), _new_host(rt), [_seed_stage(src, "C-A")], task_id="T-s05b", contracts=rt.contracts,
                      assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    f = out["analysis_records"][0]["data"]
    assert f["evidence_state"] == "insufficient" and f["contradictory_refs"]      # contrary evidence retained
    assert {m["case_id"]: m["state"] for m in f["memberships"]}["C-B"] == "uncertain"
    assert len(out["dropped_citations"]) >= 2


def test_s05_publishes_finding_and_taxonomy_after_independent_pass(three_cases):
    rt, summary, host, src, _ = three_cases
    from sdbackend.api.ops import Operations
    from sdbackend.pipeline.collective import discover, stage
    shown = [p["passage_handle"] for p in discover(host, [_seed_stage(src, "C-A")])[0]]
    ha, hb = _handle(shown, "C-A", src), _handle(shown, "C-B", src)
    dto = _finding([{"case_id": c, "state": "included", "association": "active", "reason": "",
                     "citations": [{"passage_handle": h, "quote": "rollover request form was lost"}]}
                    for c, h in (("C-A", ha), ("C-B", hb))])
    rt.db.put_object("stage_s03", "STAGE-S03-A", _seed_stage(src, "C-A"))
    st = stage(rt, _Gateway(dto), _new_host(rt), ["STAGE-S03-A"], run_id="RUN-s05", task_id="T-s05c",
               assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    from specimens import approve_required
    from sdbackend.engine.publish import PublicationRefused, prepare_closure, publish_analysis_set
    prepared = prepare_closure(rt, release_id=summary["release_id"],
                               assessed_evidence_as_of="2026-10-01T00:00:00+10:00", stage_ids=[st["stage_id"]])
    approve_required(rt, prepared)
    with pytest.raises(PublicationRefused, match="without an effective independent source pass"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])        # unreviewed sources refused
    # independent source review arrives; the reviewed release is re-assembled (no rewrite of the old one)
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    _, _, _, _, bundle = three_cases
    approve_originals(rt, receive(bundle, rt=rt))
    reviewed = ingest(rt, receive(bundle, rt=rt))
    aset, prepared = publish_native(rt, reviewed["release_id"], stages=[st["stage_id"]])
    assert aset["taxonomy_version"] and len(aset["record_ids"]) == 1
    tax = rt.db.get_object("taxonomy", aset["taxonomy_version"])
    assert tax["nodes"][0]["label"] == "Scanning backlog loss" and tax["acceptance_id"] in aset["acceptance_ids"]
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=reviewed["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    got = ops.handle("leader.demo", dict(operation="taxonomy.get", context_id=ctx["context_id"],
                                         taxonomy_version=aset["taxonomy_version"]))
    assert got["status"] == "ok" and got["payload"]["nodes"][0]["permitted_finding_ids"]


def test_invalidation_new_evidence_reaches_older_case(three_cases, tmp_path):
    rt, summary, host, src, bundle = three_cases
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    from sdbackend.pipeline.collective import stage
    from sdbackend.pipeline.invalidation import affected, source_changes
    shown = [p["passage_handle"] for p in __import__("sdbackend.pipeline.collective", fromlist=["discover"])
             .discover(host, [_seed_stage(src, "C-A")])[0]]
    ha, hb = _handle(shown, "C-A", src), _handle(shown, "C-B", src)
    dto = _finding([{"case_id": c, "state": "included", "association": "historical", "reason": "",
                     "citations": [{"passage_handle": h, "quote": "rollover request form was lost"}]}
                    for c, h in (("C-A", ha), ("C-B", hb))])
    rt.db.put_object("stage_s03", "STAGE-S03-A", _seed_stage(src, "C-A"))
    st = stage(rt, _Gateway(dto), _new_host(rt), ["STAGE-S03-A"], run_id="RUN-s05", task_id="T-s05d",
               assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    old = {s["source_id"]: s["object_sha256"] for s in st["consumed_sources"]}
    old.update({s["data"]["source_id"]: rt.db.object_sha("source", s["data"]["source_id"])
                for s in rt.db.list_objects("source")})
    # a new email arrives for an OLD, already-closed case C-OLD mentioning the same mechanism
    new_text = b"Member D (2025 matter, closed): my rollover request form was also lost in the scanning queue.\n"
    m = json.loads((bundle / "delivery-manifest.json").read_text())
    orig = [{"original_id": o["original_id"], "rel": o["file"], "data": (bundle / o["file"]).read_bytes(),
             "media": o["declared_media_type"], "case": o["references"]["case"],
             "available": o["scenario_time"]["available_at"]["value"]} for o in m["originals"]]
    orig.append({"original_id": "ORIG-D", "rel": "originals/d.txt", "data": new_text, "media": "text/plain",
                 "case": "C-OLD", "available": "2026-06-01T09:00:00+10:00"})
    ingest(rt, receive(mini_bundle(tmp_path, orig, name="mini2"), rt=rt))
    changes = source_changes(rt, old, [s["data"]["source_id"] for s in rt.db.list_objects("source")])
    assert len(changes["added"]) == 1 and changes["changed"] == [] and changes["removed"] == []
    out = affected(rt, changes)
    assert [s["stage_id"] for s in out["affected_stages"]] == [st["stage_id"]]
    assert "search scope" in out["affected_stages"][0]["reasons"][0]
    assert {"C-OLD", "C-A", "C-B"} <= set(out["affected_cases"])
    unrelated = source_changes(rt, {**old, **{k: rt.db.object_sha("source", k) for k in changes["added"]}},
                               [s["data"]["source_id"] for s in rt.db.list_objects("source")])
    assert affected(rt, unrelated)["affected_stages"] == []                 # nothing changed → nothing invalidated


# ------------------------------------------------------------------ evaluator
def test_evaluator_scores_precision_recall_abstention_in_isolation(ledger, tmp_path):
    rt, _ = ledger
    from sdbackend.evaluation.adapter import evaluate, score_metrics
    s02 = [dict(rt.db.get_object("stage_s02", "STAGE-S02-A"), stage_id="STAGE-S02-A")]
    ref = s02[0]["facts"][0]["data"]["value"]["evidence_refs"][0]

    def gold(tid, ents, state, values, proof):
        return {"schema_version": "0.2.0", "kind": "evidence_gold", "data": {
            "target_id": tid, "target_type": "atomic_fact", "entity_ids": ents,
            "context": {"view_id": "complaints-leadership", "snapshot": "2026-10-01T00:00:00+10:00",
                        "source_version": "SSV-x", "clarification_request_ids": []},
            "expected_state": state, "acceptable_values": values, "acceptable_claims": [],
            "acceptable_proof_sets": proof, "contradictory_refs": [], "rationale": "pytest specimen gold",
            "reviewer_reference": "pytest-specimen", "review_state": "draft"}}
    rows = [gold("T1", ["COMP"], "known", [50000, "payment", "compensation"], [[ref]]),
            gold("T2", ["NOBODY"], "unknown", [], []),
            gold("T3", ["LOOSE"], "known", [1], [[ref]]),
            gold("T4", ["MISSING"], "known", ["x"], [])]
    path = tmp_path / "gold.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out_path = tmp_path / "eval" / "report.json"
    rep = evaluate(rt, path, s02=s02, out_path=out_path)
    ev = rep["evidence"]
    assert {t["target_id"]: t["outcome"] for t in ev["targets"]} == {
        "T1": "correct", "T2": "abstained_correct", "T3": "wrong", "T4": "abstained"}
    # three correct typed assertions on T1 (amount, kind, category), one wrong on T3: assertion precision 3/4;
    # target-hit rate (1 of 2 asserted targets) is reported separately, never as precision (F04)
    assert ev["assertion_precision"] == 0.75 and ev["target_hit_rate"] == 0.5
    assert ev["recall"] == round(1 / 3, 4) and ev["correct_abstention_rate"] == 1.0
    assert ev["unscored_predictions"] > 0 and rep["status"].startswith("evidence_for_independent_review")
    assert out_path.is_file() and rt.db.list_objects("evidence_gold") == []      # gold never enters the store
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"schema_version": "0.2.0", "kind": "evidence_gold", "data": {}}) + "\n")
    with pytest.raises(ValueError, match="not a valid evaluation row"):
        evaluate(rt, bad)
    oracle = [{"kind": "metric_oracle", "data": {"fixture_id": "F1", "query": {}, "included_ids": ["a"],
                                                 "excluded_ids": [], "uncertain_ids": [],
                                                 "expected_result": {"point": {"state": "known", "value": 3}}}}]
    m = score_metrics(oracle, lambda q: {"result": {"point": {"state": "known", "value": 3}},
                                         "contributors": {"included": ["a"]}})
    assert m["matched"] == 1
    m = score_metrics(oracle, lambda q: {"result": {"point": {"state": "known", "value": 4}},
                                         "contributors": {"included": ["a"]}})
    assert m["fixtures"][0]["outcome"] == "mismatch" and not m["fixtures"][0]["checks"]["value"]


def test_extraction_rejected_or_missing_refuses_stage_publication(ledger):
    rt, summary = ledger
    from sdbackend.engine.publish import PublicationRefused, prepare_closure, publish_analysis_set
    from sdbackend.engine.resolvers import resolve
    from specimens import approve_required
    st = resolve(rt, ["STAGE-S02-A", "STAGE-S02-B"])
    prepared = prepare_closure(rt, release_id=summary["release_id"],
                               assessed_evidence_as_of="2026-10-01T00:00:00+10:00", stage_ids=[st["stage_id"]])
    ext = [r for r in prepared["required_decisions"] if r["gate"] == "extraction"]
    assert ext                                           # every consumed source's extraction is a required subject
    approve_required(rt, prepared, skip=("extraction",))
    with pytest.raises(PublicationRefused, match="extraction decision"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])         # missing
    for r in ext:
        decide(rt, "extraction", [{k: r[k] for k in ("subject_kind", "subject_id", "subject_sha256")}],
               result="repair_required")
    with pytest.raises(PublicationRefused, match="is repair_required"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])         # rejected
    for r in ext:
        decide(rt, "extraction", [{k: r[k] for k in ("subject_kind", "subject_id", "subject_sha256")}],
               decided_at="2026-10-14T13:00:00+11:00")
    assert publish_analysis_set(rt, candidate_id=prepared["candidate_id"])["analysis_set_version"]


WORK_LOG = (b"entry_id,case_id,date,duration_minutes,phase,basis,afca_matter_id\r\n"
            b"W1,C-LOG,2026-02-04,60,member_contact,recorded,\r\n"
            b"W2,C-LOG,2026-02-05,30,follow_up,actual,\r\n"
            b"W2,C-LOG,2026-02-05,30,follow_up,actual,\r\n"          # exact repeat of W2: union, counted once
            b"W3,C-LOG,2026-02-06,90,approval,estimate,\r\n"         # estimate: never recorded
            b"W4,C-OTHER,2026-02-06,15,other,recorded,\r\n"          # not this source's case: unresolved
            b"W5,C-LOG,2026-02-07,abc,other,recorded,\r\n"           # unparseable duration: unresolved
            b"W6,C-LOG,2026-02-08,20,other,recorded,\r\n"
            b"W6,C-LOG,2026-02-08,25,other,recorded,\r\n")           # conflicting repeat: neither counted
PROSE = b"Call note: I spent about 2 hours (120 minutes) on the member call for C-LOG on 4 Feb 2026.\n"


@pytest.fixture()
def native_log(rt, tmp_path):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    base = mini_bundle(tmp_path, [
        {"original_id": "ORIG-LOG", "rel": "originals/time-log.csv", "data": WORK_LOG, "media": "text/csv",
         "case": "C-LOG", "available": "2026-02-09T09:00:00+11:00"},
        {"original_id": "ORIG-NOTE", "rel": "originals/call-note.txt", "data": PROSE, "media": "text/plain",
         "case": "C-LOG", "available": "2026-02-09T09:00:00+11:00"}], name="worklog")
    bundle = specimen_bundle(tmp_path, base, [case_row("C-LOG", "2026-02-01")], name="worklog-natives")
    d = receive(bundle, rt=rt)
    approve_originals(rt, d)
    decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                 "subject_sha256": n.record_sha256} for n in d.natives])
    summary = ingest(rt, receive(bundle, rt=rt))
    srcs = {s["data"]["text"][:5]: s["data"] for s in rt.db.list_objects("source")}
    log, note = srcs["entry"], srcs["Call "]
    # S02 stages exactly as a model would leave them: the log's durations read as facts, the prose as reported
    rt.db.put_object("stage_s02", "STAGE-S02-LOG", {
        "source_binding": {"source_id": log["source_id"], "content_sha256": log["content_sha256"]},
        "facts": [_fact(log, "W1", "work_duration_seconds", 3600, day="2026-02-04", kind="documented_native")]})
    rt.db.put_object("stage_s02", "STAGE-S02-NOTE", {
        "source_binding": {"source_id": note["source_id"], "content_sha256": note["content_sha256"]},
        "facts": [_fact(note, "CALL", "work_duration_seconds", 7200, day="2026-02-04", kind="documented_native")]})
    return rt, summary, log


def test_native_work_log_is_the_only_recorded_hours_path(native_log):
    rt, summary, log = native_log
    from sdbackend.api.ops import Operations
    from sdbackend.citations import check_citation
    from sdbackend.engine import native_work
    from sdbackend.engine.resolvers import resolve
    st = resolve(rt, ["STAGE-S02-LOG", "STAGE-S02-NOTE"])
    work = sorted((r["data"] for r in st["analysis_records"] if r["kind"] == "work_entry"),
                  key=lambda w: w["event_date"])
    assert [(w["event_date"], w["duration_seconds"], w["phase"], w["duration_basis"]) for w in work] == [
        ("2026-02-04", 3600, "member_contact", "recorded"), ("2026-02-05", 1800, "follow_up", "recorded")]
    assert st["native_work_logs"] == [{"source_id": log["source_id"], "profile_id": native_work.PROFILE_ID}]
    for w in work:                                     # every recorded duration cites its exact native cell
        dur_ref = w["evidence_refs"][0]
        a = dur_ref["anchor"]
        assert check_citation(log, dur_ref) is None
        assert native_work.parse_seconds("minutes", log["text"][a["start_codepoint"]:a["end_codepoint"]]) \
            == w["duration_seconds"]
    reasons = " | ".join(u["reason"] for u in st["unresolved"])
    for needle in ("estimated/planned", "not one of this source's linked cases", "not in a registered format",
                   "repeated with different values", "model-read duration"):
        assert needle in reasons, needle
    assert not any(u["source_id"] == log["source_id"] and "model-read" in u["reason"] for u in st["unresolved"])
    aset, _ = publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    ans = ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                         definition={"registry_id": "runtime-measures-0.2.0",
                                                     "measure_id": "work.recorded_hours", "definition_version": "0.2.0"},
                                         filters=ONE, population_reference=None))
    assert ans["status"] == "ok", ans
    assert ans["payload"]["cells"][0]["point"]["value"] == 1.5      # 60 + 30 min; prose 2 h and estimate excluded


def test_native_work_profile_rejects_untrusted_tables():
    from sdbackend.engine.native_work import header_map, parse_seconds
    assert header_map(["request", "as_of", "item", "status"]) is None            # v3 checklist: not a work log
    assert header_map(["case_id", "duration", "duration"]) is None               # ambiguous header
    assert header_map(["Case ID", "Effort"]) is None                             # unregistered duration column
    assert header_map(["Case ID", "Duration Minutes"]) == {"case": 0, "minutes": 1}
    assert [parse_seconds("duration", v) for v in ("1:30", "0:45:30", "1h 30m", "90", "")] == \
        [5400, 2730, 5400, None, None]


def test_s04_contact_identity_collapses_copies_and_never_infers_continuity(ledger):
    rt, _ = ledger
    from sdbackend.engine.resolvers import resolve
    srcs = sorted((s["data"] for s in rt.db.list_objects("source") if CASE in s["data"]["case_ids"]),
                  key=lambda s: s["source_id"])
    a, b = srcs[0], srcs[1]
    rt.db.put_object("stage_s02", "STAGE-S02-K1", {"facts": [
        _fact(a, "CALL", "contact_reference", "CALL-0042", day="2026-02-04"),
        _fact(a, "AFCA", "afca_reference", "AFCA-123")]})
    rt.db.put_object("stage_s02", "STAGE-S02-K2", {"facts": [
        _fact(b, "CALL", "contact_reference", "call-0042 ", day="2026-02-04")]})          # same contact, copy
    st = resolve(rt, ["STAGE-S02-K1", "STAGE-S02-K2"])
    contacts = [r["data"] for r in st["analysis_records"] if r["kind"] == "contact_link"]
    assert len(contacts) == 1 and contacts[0]["source_ids"] == sorted([a["source_id"], b["source_id"]])
    c = contacts[0]
    assert c["repeat_state"] == "uncertain" and c["episode_id"] is None and c["event_date"] == "2026-02-04"
    assert not any(r["kind"] == "afca_matter" for r in st["analysis_records"])
    assert any("AFCA reference without receipt" in u["reason"] for u in st["unresolved"])
