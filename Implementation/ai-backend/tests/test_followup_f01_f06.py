"""Expected-correct regressions for the Principal AI Engineer's 15 October follow-up (F01–F06).

The first seven tests are the Principal's independent probes (`Architecture/AI/reviews/2026-10-15/
test_independent_followup.py`), folded into this suite unchanged in substance; the rest extend each item. Every
approval, finding and gold row here is an isolated TEST SPECIMEN in a temporary store; the scripted gateway is a
test double, never inference.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sdbackend.citations import text_ref
from sdbackend.models.base import GenResult, Usage

from specimens import approve_originals, approve_required, decide, mini_bundle, publish_native
from test_analytical_r10 import _Gateway, _new_host, ledger, three_cases  # noqa: F401
from test_ingest_and_api import FILTERS

BACKEND = Path(__file__).resolve().parents[1]
ONE = dict(FILTERS, granularity="none")


# =================================================================== Principal's probes (expected-correct)
def _ctx(ops, release_id):
    return ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None, release_id=release_id,
                                          evidence_as_of="2026-10-01T00:00:00+10:00", analysis_mode="reconstructed",
                                          analysis_as_of=None, population_reference=None))["context"]


def _query(ctx, measure):
    return dict(operation="analysis.query", context_id=ctx["context_id"], query_id="AUDIT-Q",
                definition=dict(registry_id="runtime-measures-0.2.0", measure_id=measure, definition_version="0.2.0"),
                filters=ONE, population_reference=None)


def _withdraw_source(rt, release_id, sid, at="2026-10-15T12:00:00+11:00"):
    release = rt.db.get_object("release", release_id)
    entry = next(e for e in release["entries"] if e["kind"] == "input" and e["immutable_id"] == sid)
    old = rt.db.get_object("acceptance", entry["acceptance_id"])
    decide(rt, "source", [{k: old[k] for k in ("subject_kind", "subject_id", "subject_sha256")}],
           result="withdrawn", decided_at=at)


def test_source_withdrawal_invalidates_published_and_cached_ledger(ledger):
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import resolve
    runtime, summary = ledger
    st = resolve(runtime, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"])
    publish_native(runtime, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(runtime)
    ctx = _ctx(ops, summary["release_id"])
    req = _query(ctx, "cash.remedies_paid")
    assert ops.handle("leader.demo", req)["status"] == "ok"
    _withdraw_source(runtime, summary["release_id"], st["consumed_sources"][0]["source_id"])
    after = ops.handle("leader.demo", req)
    assert after["status"] == "unavailable", "A withdrawn consumed source still supports an ok cached cash result"


def _finding_store(three_cases):
    from sdbackend.pipeline.collective import materialise
    from sdbackend.pipeline.passages import passages
    runtime, _, host, sources, _ = three_cases
    shown, members = {}, []
    for case in ("C-A", "C-B"):
        s = sources[case]
        p = passages(s["text"])[0]
        h = s["source_id"] + "~P1"
        shown[h] = host._passage(h)
        members.append(dict(case_id=case, state="included", association="active", reason="specimen",
                            citations=[dict(passage_handle=h, quote=p["text"])]))
    dto = {"findings": [dict(title="Specimen", claim="Shared scanning problem", taxonomy_label="Scanning",
                             dimension="handling_factor", members=members, contrary_citations=[], alternatives=[],
                             missing_facts=[])]}
    out = materialise(dto, host, shown, runtime.contracts, assessed_snapshot="2026-10-01T00:00:00+10:00",
                      source_version="SSV-SPECIMEN")
    assert len(out["analysis_records"]) == 1
    rec = out["analysis_records"][0]
    rid = rec["envelope"]["record_id"]
    runtime.db.put_object("analysis_record", rid, rec)
    return runtime, host.repo, {"record_ids": [rid]}


def test_collective_finding_visible_on_its_own_case(three_cases):
    from sdbackend.api.analysis_read import findings
    runtime, repo, aset = _finding_store(three_cases)
    req = dict(record_kinds=["finding"], case_id="C-A", membership_dispositions=[])
    assert len(findings(runtime, aset, repo, req, None)) == 1, "Case membership is nested, not data.case_ids"


def test_collective_finding_visible_in_included_population(three_cases):
    from sdbackend.api.analysis_read import findings
    runtime, repo, aset = _finding_store(three_cases)
    req = dict(record_kinds=["finding"], case_id=None, membership_dispositions=["included"])
    assert len(findings(runtime, aset, repo, req, ["C-A"])) == 1, "Finding/membership filtering loses an included case"


def test_s04_cancelled_worker_seals_no_output(ingested, monkeypatch):
    from sdbackend.engine import resolvers
    from sdbackend.pipeline import control, runner
    runtime, summary = ingested
    run = control.seal_runspec(runtime, purpose="release_analysis", release_id=summary["release_id"],
                               source_set_version=summary["source_set_version"], boundary={}, worklist=[], provider={},
                               versions={}, budget={}, parent_run_id=None,
                               selection_reason="isolated independent cancellation probe")
    original = resolvers.resolve

    def cancel_before_completion(*a, **kw):
        result = original(*a, **kw)
        control.cancel_run(runtime, run["run_id"], "specimen cancellation")
        return result
    monkeypatch.setattr(resolvers, "resolve", cancel_before_completion)
    stats = {"s04": runner._new_stats(), "s05": runner._new_stats()}
    runner._derived_tasks(runtime, None, run, None, ("ledger",), stats)
    assert runtime.db.list_objects("stage_s04") == [], "S04 seals its output before fenced completion"


def test_evaluator_wrong_source_digest_is_not_proof():
    from sdbackend.evaluation.adapter import _covers
    ref = dict(source_id="SRC-SPEC", content_sha256="a" * 64, anchor=dict(kind="text", start_codepoint=0, end_codepoint=10))
    assert not _covers([dict(ref, content_sha256="b" * 64)], [[ref]]), "Evaluator ignores source digest"


def test_evaluator_does_not_hide_wrong_assertion_beside_correct_one():
    from sdbackend.evaluation.adapter import score_evidence
    ref = dict(source_id="SRC-SPEC", content_sha256="a" * 64, anchor=dict(kind="text", start_codepoint=0, end_codepoint=10))
    gold = [dict(kind="evidence_gold", data=dict(entity_ids=["E"], expected_state="known", acceptable_values=["right"],
                                                 acceptable_proof_sets=[[ref]], contradictory_refs=[], target_id="T",
                                                 target_type="field"))]
    base = dict(subjects={"E"}, field="reported_problem", state="known", refs=[ref], contrary=[])
    report = score_evidence(gold, [dict(base, value="right"), dict(base, value="wrong")])
    assert report["precision"] != 1.0, "One correct value masks an additional wrong assertion on the same target"


def test_s05_discovery_cap_is_explicit_in_coverage():
    from sdbackend.pipeline.collective import discover

    class Host:
        search_scopes = []

        def execute(self, name, args):
            return {"items": [dict(passage_handle=h, text="synthetic") for h in args["passage_handles"]]}
    stages = [dict(timeline_events=[], missing_facts=[], contrary_evidence=[])]
    stages[0]["timeline_events"] = [dict(proposition="", source_refs=[{"source_id": f"S{i}", "anchor": {}}])
                                    for i in range(40)]
    import sdbackend.pipeline.collective as collective
    old = collective._handle_for
    try:
        collective._handle_for = lambda host, ref: ref["source_id"] + "~P1"
        shown, metadata = discover(Host(), stages)
    finally:
        collective._handle_for = old
    assert len(shown) == 36
    assert metadata, "36 of 40 passages shown, but no partial/truncation/omitted coverage is returned"
    assert len(metadata["omitted_handles"]) == 4 and metadata["assessment_complete"] is False


# =================================================================== F01 extensions
def test_f01_withdrawal_spares_unrelated_results_and_refuses_contributors_and_sources(ledger):
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import resolve
    rt, summary = ledger
    st = resolve(rt, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"])
    publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(rt)
    ctx = _ctx(ops, summary["release_id"])
    cash = ops.handle("leader.demo", _query(ctx, "cash.remedies_paid"))
    new = ops.handle("leader.demo", _query(ctx, "cases.new"))
    assert cash["status"] == new["status"] == "ok"
    sid = st["consumed_sources"][0]["source_id"]
    _withdraw_source(rt, summary["release_id"], sid)
    ctx2 = _ctx(ops, summary["release_id"])
    assert ops.handle("leader.demo", _query(ctx2, "cash.remedies_paid"))["status"] == "unavailable"   # fresh calc too
    assert ops.handle("leader.demo", _query(ctx2, "cases.new"))["status"] == "ok"   # native-only result unaffected
    cell = cash["payload"]["cells"][0]
    got = ops.handle("leader.demo", dict(operation="contributors.list", context_id=ctx["context_id"],
                                         result_id=cash["payload"]["result_id"], cell_key=cell["key"],
                                         cursor=None, page_size=50))
    assert got["status"] == "unavailable"            # former contributors never paged under the withdrawn input
    src = ops.handle("leader.demo", dict(operation="source.get", context_id=ctx2["context_id"], source_id=sid,
                                         citation=None))
    assert src["status"] == "unavailable"            # withdrawn binding no longer served as reviewed evidence


def test_f01_transitive_extraction_withdrawal_refuses_dependent_stage_output(ledger):
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import resolve
    rt, summary = ledger
    st = resolve(rt, ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"])
    aset, prepared = publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(rt)
    ctx = _ctx(ops, summary["release_id"])
    assert ops.handle("leader.demo", _query(ctx, "obligations.outstanding"))["status"] == "ok"
    ext = next(r for r in prepared["required_decisions"] if r["gate"] == "extraction")
    decide(rt, "extraction", [{k: ext[k] for k in ("subject_kind", "subject_id", "subject_sha256")}],
           result="repair_required", decided_at="2026-10-15T13:00:00+11:00")
    assert ops.handle("leader.demo", _query(ctx, "obligations.outstanding"))["status"] == "unavailable"
    assert ops.handle("leader.demo", _query(ctx, "cases.new"))["status"] == "ok"
    deps = aset["dependency_closure"]
    assert deps["stages"][st["stage_id"]]["extractions"] and deps["result_integrity"]


# =================================================================== F02 extensions
def _publish_findings(three_cases, members_extra=(), second_label=None, natives=(), tmp=None):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    from sdbackend.pipeline.collective import discover, stage
    rt, summary, host, src, bundle = three_cases
    shown = [p["passage_handle"] for p in discover(host, [{"timeline_events": [
        {"proposition": "rollover request form lost scanning queue backlog",
         "source_refs": [text_ref(src["C-A"], 0, 10)]}], "missing_facts": [], "contrary_evidence": []}])[0]]
    h = {c: next(x for x in shown if x.split("~")[0] == src[c]["source_id"]) for c in ("C-A", "C-B")}
    members = [{"case_id": c, "state": "included", "association": "active", "reason": "own passage",
                "citations": [{"passage_handle": h[c], "quote": "rollover request form was lost"}]}
               for c in ("C-A", "C-B")]
    members.append({"case_id": "C-C", "state": "excluded", "association": "unknown",
                    "reason": "different mechanism (missing certificate)", "citations": []})
    findings = [{"title": "Lost rollover forms", "claim": "Forms were lost in a scanning backlog.",
                 "taxonomy_label": "Scanning backlog loss", "dimension": "original_problem", "members": members,
                 "contrary_citations": [], "alternatives": [], "missing_facts": []}]
    if second_label:
        findings.append(dict(findings[0], title="Second", claim="A second mechanism.", taxonomy_label=second_label))
    seed = {"timeline_events": [{"proposition": "rollover request form lost scanning queue backlog",
                                 "source_refs": [text_ref(src["C-A"], 0, 10)]}], "missing_facts": [],
            "contrary_evidence": []}
    rt.db.put_object("stage_s03", "STAGE-S03-F02", seed)
    st = stage(rt, _Gateway({"findings": findings}), _new_host(rt), ["STAGE-S03-F02"], run_id="RUN-f02",
               task_id="T-f02", assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    if natives:                                            # specimen register rows (isolated copy of the bundle)
        from specimens import specimen_bundle
        bundle = specimen_bundle(tmp, bundle, list(natives), name="with-natives")
        delivery = receive(bundle, rt=rt)
        decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                     "subject_sha256": n.record_sha256} for n in delivery.natives])
    approve_originals(rt, receive(bundle, rt=rt))
    reviewed = ingest(rt, receive(bundle, rt=rt))
    aset, _ = publish_native(rt, reviewed["release_id"], stages=[st["stage_id"]])
    return rt, reviewed, aset


def _findings(ops, ctx, *, case_id=None, dispositions=(), cursor=None, page_size=50):
    return ops.handle("leader.demo", dict(operation="findings.list", context_id=ctx["context_id"], result_id=None,
                                          case_id=case_id, record_kinds=["finding"],
                                          membership_dispositions=list(dispositions), population_reference=None,
                                          cursor=cursor, page_size=page_size))


def test_f02_membership_dispositions_and_paging_through_the_api(three_cases):
    from sdbackend.api.ops import Operations
    rt, reviewed, aset = _publish_findings(three_cases, second_label="Second mechanism")
    ops = Operations(rt)
    ctx = _ctx(ops, reviewed["release_id"])
    assert _findings(ops, ctx, case_id="C-A")["payload"]["total_permitted_count"] == 2
    assert _findings(ops, ctx, case_id="C-A", dispositions=["included"])["payload"]["total_permitted_count"] == 2
    assert _findings(ops, ctx, case_id="C-A", dispositions=["excluded"])["payload"]["total_permitted_count"] == 0
    assert _findings(ops, ctx, case_id="C-C", dispositions=["excluded"])["payload"]["total_permitted_count"] == 2
    assert _findings(ops, ctx, case_id="C-C", dispositions=["included"])["payload"]["total_permitted_count"] == 0
    assert _findings(ops, ctx, case_id="C-C", dispositions=["uncertain"])["payload"]["total_permitted_count"] == 0
    first = _findings(ops, ctx, case_id="C-B", page_size=1)["payload"]
    assert len(first["items"]) == 1 and first["next_cursor"] and first["total_permitted_count"] == 2
    second = _findings(ops, ctx, case_id="C-B", cursor=first["next_cursor"], page_size=1)["payload"]
    assert len(second["items"]) == 1 and second["items"][0] != first["items"][0]
    fid = first["items"][0]["data"]["finding_id"]
    ent = ops.handle("leader.demo", dict(operation="entity.get", context_id=ctx["context_id"], entity_kind="finding",
                                         entity_id=fid, immutable_record_id=None, cursor=None, page_size=10))
    assert ent["status"] == "ok"


def test_f02_record_matching_rules():
    from sdbackend.api.analysis_read import record_matches
    rec = {"kind": "finding", "data": {"memberships": [{"case_id": "A", "state": "included"},
                                                       {"case_id": "B", "state": "excluded"},
                                                       {"case_id": "C", "state": "uncertain"}]}}
    assert record_matches(rec, "A", set(), None) and not record_matches(rec, "Z", set(), None)
    assert record_matches(rec, None, {"included"}, ["A", "B"]) and not record_matches(rec, None, {"included"}, ["B"])
    assert record_matches(rec, None, {"uncertain"}, ["C"]) and not record_matches(rec, "B", {"included"}, None)
    flat = {"kind": "obligation", "data": {"case_ids": ["A"]}}
    assert record_matches(flat, "A", set(), ["A"]) and not record_matches(flat, "A", {"included"}, None)


# =================================================================== F03 extensions
def _s04_run(rt, summary):
    from sdbackend.pipeline import control
    return control.seal_runspec(rt, purpose="release_analysis", release_id=summary["release_id"],
                                source_set_version=summary["source_set_version"], boundary={}, worklist=[],
                                provider={}, versions={}, budget={}, parent_run_id=None, selection_reason="f03 test")


def test_f03_lease_expiry_refuses_then_restart_seals_once(ingested, monkeypatch):
    from sdbackend.engine import resolvers
    from sdbackend.pipeline import control, runner
    rt, summary = ingested
    run = _s04_run(rt, summary)
    original = resolvers.resolve

    def expire(*a, **kw):
        out = original(*a, **kw)
        rt.db.connect().execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00Z' WHERE run_id=?",
                                (run["run_id"],))
        return out
    monkeypatch.setattr(resolvers, "resolve", expire)
    stats = {"s04": runner._new_stats(), "s05": runner._new_stats()}
    runner._derived_tasks(rt, None, run, None, ("ledger",), stats)
    assert stats["s04"]["revoked"] == 1 and rt.db.list_objects("stage_s04") == []
    edges = rt.db.connect().execute("SELECT COUNT(*) FROM dependency_edges WHERE dependent_kind='stage_s04'").fetchone()[0]
    assert edges == 0                                # dependencies are sealed only with the output
    monkeypatch.setattr(resolvers, "resolve", original)          # "restart": a new worker invocation, same run
    stats = {"s04": runner._new_stats(), "s05": runner._new_stats()}
    runner._derived_tasks(rt, None, run, None, ("ledger",), stats)
    assert stats["s04"]["processed"] == 1 and len(rt.db.list_objects("stage_s04")) == 1
    assert control.task_counts(rt, run["run_id"]) == {"staged": 1}


RACE_WORKER = r'''
import json, sys, time
from sdbackend.runtime import make_runtime
from sdbackend.pipeline import control, runner
from sdbackend.engine import resolvers
rt = make_runtime()
run = control.load_run(rt, sys.argv[1])
delay = float(sys.argv[2])
orig = resolvers.resolve
def slow(*a, **kw):
    out = orig(*a, **kw)
    time.sleep(delay)
    return out
resolvers.resolve = slow
stats = {"s04": runner._new_stats(), "s05": runner._new_stats()}
runner._derived_tasks(rt, None, run, None, ("ledger",), stats)
print(json.dumps({k: v for k, v in stats["s04"].items() if k in ("processed", "reused", "skipped_held", "revoked")}))
'''


def test_f03_separate_process_race_seals_exactly_once(ingested, tmp_path):
    from sdbackend.pipeline import control
    rt, summary = ingested
    run = _s04_run(rt, summary)
    script = tmp_path / "race_worker.py"
    script.write_text(RACE_WORKER)
    env = {**os.environ, "PYTHONPATH": f"{BACKEND}:{os.environ.get('PYTHONPATH', '')}".rstrip(":")}
    slow = subprocess.Popen([sys.executable, str(script), run["run_id"], "3"], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:            # wait until the slow worker holds the lease
            row = rt.db.connect().execute("SELECT state FROM tasks WHERE run_id=?", (run["run_id"],)).fetchone()
            if row and row["state"] == "running":
                break
            time.sleep(0.05)
        fast = subprocess.run([sys.executable, str(script), run["run_id"], "0"], cwd=BACKEND, env=env,
                              capture_output=True, text=True, timeout=60)
        out_slow, err_slow = slow.communicate(timeout=60)
    finally:
        if slow.poll() is None:                       # only the PID this test started
            slow.kill()
    assert fast.returncode == 0 and slow.returncode == 0, (fast.stderr[-500:], err_slow[-500:])
    a, b = json.loads(out_slow.strip().splitlines()[-1]), json.loads(fast.stdout.strip().splitlines()[-1])
    assert a["processed"] == 1 and b["skipped_held"] == 1 and b["processed"] == 0
    assert len(rt.db.list_objects("stage_s04")) == 1 and control.task_counts(rt, run["run_id"]) == {"staged": 1}


# =================================================================== F05 extensions
N_CASES = 112                                          # > 3 × 36 passages and > 20 members per model finding


@pytest.fixture()
def many_cases(rt, tmp_path):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    originals = []
    for i in range(N_CASES):
        text = (f"Member {i:03d}: my rollover request form was lost in the scanning queue backlog.\n" if i != 50 else
                f"Member {i:03d}: my rollover form was NOT lost; the scanning queue processed it the same day.\n")
        originals.append({"original_id": f"ORIG-{i:03d}", "rel": f"originals/m{i:03d}.txt", "data": text.encode(),
                          "media": "text/plain", "case": f"C-{i:03d}", "available": "2026-03-10T09:00:00+11:00"})
    ingest(rt, receive(mini_bundle(tmp_path, originals, name="many"), rt=rt))
    src = {s["data"]["case_ids"][0]: s["data"] for s in rt.db.list_objects("source")}
    stage = {"timeline_events": [{"proposition": "rollover request form lost scanning queue backlog",
                                  "source_refs": [text_ref(src[f"C-{i:03d}"], 0, 10)]} for i in range(N_CASES)],
             "missing_facts": [], "contrary_evidence": []}
    return rt, src, stage


class _BatchGateway:
    """TEST DOUBLE: proposes the shared mechanism for the first 20 cases of each batch it is shown (the DTO bound)
    and cites the contrary passage whenever that passage is in the batch."""
    class provider:
        name = "test-double"
    model = "scripted"

    def __init__(self):
        self.batches = 0

    def call(self, req, *, task_id, attempt):
        self.batches += 1
        packet = req.messages[0].parts[0].text
        shown = re.findall(r"\[(\S+~P\d+)\] cases=(\S+) source=\S+ \([^)]*\)\n([^\n]+)", packet)
        members, contrary = [], []
        for handle, case, text in shown:
            if "NOT lost" in text:
                contrary.append({"passage_handle": handle, "quote": "was NOT lost"})
            elif len(members) < 20:
                members.append({"case_id": case, "state": "included", "association": "historical", "reason": "own",
                                "citations": [{"passage_handle": handle, "quote": "rollover request form was lost"}]})
        data = {"findings": [{"title": "Lost rollover forms", "claim": "Rollover forms lost in a scanning backlog.",
                              "taxonomy_label": "Scanning backlog loss", "dimension": "original_problem",
                              "members": members, "contrary_citations": contrary, "alternatives": [],
                              "missing_facts": []}]}
        return GenResult(provider="test-double", model="scripted", text="{}", data=data,
                         usage=Usage(10, 0, 0, 5, 15, "provider"), latency_ms=1)


def test_f05_bounded_continuation_aggregates_beyond_caps_and_reports_omissions(many_cases):
    from sdbackend.pipeline.collective import MAX_BATCHES, MAX_PASSAGES, investigate
    rt, src, stage = many_cases
    gw = _BatchGateway()
    out = investigate(gw, _new_host(rt), [stage], task_id="T-f05", contracts=rt.contracts,
                      assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    cov = out["coverage"]
    assert gw.batches == MAX_BATCHES and cov["batches_run"] == MAX_BATCHES
    assert cov["shown_count"] == MAX_PASSAGES * MAX_BATCHES and cov["candidate_count"] == N_CASES
    assert len(cov["omitted_handles"]) == N_CASES - MAX_PASSAGES * MAX_BATCHES and cov["assessment_complete"] is False
    assert cov["searches_truncated"] >= 1                         # 112 matches against a 20-hit search limit
    omitted_cases = {src_id_case for h in cov["omitted_handles"]
                     for src_id_case in [next(c for c, d in src.items() if d["source_id"] == h.split("~")[0])]}
    assert set(cov["unassessed_case_ids"]) == omitted_cases       # not shown → unassessed, never excluded
    f = out["analysis_records"][0]["data"]
    included = [m for m in f["memberships"] if m["state"] == "included"]
    assert len(included) > 20                                     # union across batches beyond the per-DTO bound
    assert not ({m["case_id"] for m in f["memberships"]} & omitted_cases)
    assert f["contradictory_refs"] and f["evidence_state"] == "conflicting"   # contrary evidence after batch 1
    assert any("coverage incomplete" in m for m in f["missing_facts"])


def test_f05_failed_batch_is_reported_not_silently_discarded(many_cases):
    from sdbackend.pipeline.collective import investigate
    rt, src, stage = many_cases
    good = _BatchGateway()

    class Flaky:
        provider, model = _BatchGateway.provider, "scripted"
        calls = 0

        def call(self, req, *, task_id, attempt):
            Flaky.calls += 1
            if attempt // 10 == 2:                    # every attempt of batch 2 is invalid
                return GenResult(provider="test-double", model="scripted", text="{}", data={"findings": "x"},
                                 usage=Usage(10, 0, 0, 5, 15, "provider"), latency_ms=1)
            return good.call(req, task_id=task_id, attempt=attempt)
    out = investigate(Flaky(), _new_host(rt), [stage], task_id="T-f05b", contracts=rt.contracts,
                      assessed_snapshot="2026-10-01T00:00:00+10:00", source_version="SSV-x")
    assert [fb["batch"] for fb in out["coverage"]["failed_batches"]] == [2]
    assert any("batch(es) produced no valid model output" in r for r in out["coverage"]["incomplete_reasons"])


# =================================================================== F06
def test_f06_inventory_shows_failed_ocr_asset_when_ocr_unavailable(rt, rehearsal_bundle, monkeypatch):
    """Forces the unsupported-runtime path on any machine: the full ingest → files inventory shows the receipt as an
    explicit failed extraction with the runtime diagnostic (the supported path is asserted where OCR works)."""
    from sdbackend.api.ops import Operations
    from sdbackend.extract import image
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive

    def unavailable(_):
        raise image.OCRUnavailable("Vision request failed without NSError (test: neural-engine service blocked)")
    monkeypatch.setattr(image, "_ocr_lines", unavailable)
    summary = ingest(rt, receive(rehearsal_bundle, rt=rt))
    assert summary["sources"] == 11 and summary["extractions"]["failed"] == 1
    ops = Operations(rt)
    ctx = _ctx(ops, summary["release_id"])
    files = ops.handle("leader.demo", dict(operation="case.files.list", context_id=ctx["context_id"],
                                           case_id="C-e1da28538652e263887a", media_types=[], cursor=None,
                                           page_size=100))["payload"]["items"]
    jpg = next(i for i in files if i["asset"]["filename"] == "11-05-portal-upload-receipt.jpg")
    assert jpg["extraction_state"] == "failed"
    ext = next(e for e in rt.db.list_objects("extraction") if e["state"] == "failed")
    assert any("OCR unavailable" in o and "NSError" in o for o in ext["omissions"])


def test_incremental_schedule_targets_only_affected_work_and_matches_full_rebuild(ledger):
    """Controlled change: one consumed source (reversal evidence) leaves the inputs. The schedule lists the S04
    rebuild and exactly the dependent result; the incremental S04 equals a full deterministic rebuild."""
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import dependency_edges, resolve
    from sdbackend.pipeline import control
    from sdbackend.pipeline.incremental import compare, schedule
    rt, summary = ledger
    ids = ["STAGE-S02-A", "STAGE-S02-B", "STAGE-S02-A-REPEAT"]
    st = resolve(rt, ids)
    control.record_dependencies(rt, "stage_s04", st["stage_id"], dependency_edges(st))
    aset, _ = publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(rt)
    ctx = _ctx(ops, summary["release_id"])
    cash = ops.handle("leader.demo", _query(ctx, "cash.remedies_paid"))
    new = ops.handle("leader.demo", _query(ctx, "cases.new"))
    assert cash["status"] == new["status"] == "ok"
    b_src = rt.db.get_object("stage_s02", "STAGE-S02-B")["facts"][0]["data"]["value"]["evidence_refs"][0]["source_id"]
    plan = schedule(rt, {"added": [], "changed": [], "removed": [b_src]}, aset)
    assert plan["s04_rebuild"] and plan["s02_sources"] == [] and st["stage_id"] in plan["affected_stages"]
    assert plan["results"] == [cash["payload"]["result_id"]]          # cases.new (native only) is reused
    kept = [i for i in ids if i != "STAGE-S02-B"]
    incremental = resolve(rt, kept, persist=False)
    full = resolve(rt, list(reversed(kept)), persist=False)           # independent full rebuild, other order
    verdict = compare(incremental, full)
    assert verdict["equal"], verdict["differences"]
    pays = sorted(r["data"]["amount_minor"] for r in incremental["analysis_records"] if r["kind"] == "financial_event")
    assert -20000 not in pays and incremental["stage_id"] != st["stage_id"]
    tampered = dict(full, analysis_records=full["analysis_records"][1:])
    assert not compare(incremental, tampered)["equal"]                # a real difference is reported
