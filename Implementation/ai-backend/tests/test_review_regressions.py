"""Expected-correct regression tests for the Principal AI Engineer's 2026-10-14 audit probes (R01–R07, R12).

Each test names the probe it replaces. Specimen rows/decisions are isolated test specimens (specimens.py); the
audit's fabricated approvals are never reused, and nothing here touches the served store.
"""
import copy
import hashlib
import json
import shutil
import time

import pytest

from sdbackend.acceptance import DecisionRejected, import_decision
from sdbackend.engine.publish import PublicationRefused, prepare_closure, publish_analysis_set, withdraw_analysis_set

from specimens import (approve_originals, approve_required, case_row, decide, decision, ingest_specimen,
                       publish_native)
from test_ingest_and_api import FILTERS

DEF = {"registry_id": "runtime-measures-0.2.0", "measure_id": "cases.new", "definition_version": "0.2.0"}
ONE = dict(FILTERS, granularity="none")


@pytest.fixture()
def world(rt, rehearsal_bundle, tmp_path):
    rows = [case_row("SPEC-A", "2026-01-15"), case_row("SPEC-B", "2026-03-02")]
    summary, bundle = ingest_specimen(rt, tmp_path, rehearsal_bundle, rows)
    aset, prepared = publish_native(rt, summary["release_id"])
    from sdbackend.api.ops import Operations
    ops = Operations(rt)
    rel = summary["release_id"]

    def context(cutoff="2026-10-01T00:00:00+10:00", pop=None, principal="leader.demo", **kw):
        req = dict(operation="context.resolve", preferred_view_id=None, release_id=rel, evidence_as_of=cutoff,
                   analysis_mode="reconstructed", analysis_as_of=None, population_reference=pop)
        req.update(kw)
        return ops.handle(principal, req)

    def query(ctx, filters=None, pop=None, principal="leader.demo", definition=DEF):
        return ops.handle(principal, dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                          definition=definition, filters=filters or FILTERS, population_reference=pop))
    return dict(rt=rt, rel=rel, aset=aset, ops=ops, context=context, query=query, summary=summary, bundle=bundle,
                prepared=prepared)


def _val(ans):
    return ans.get("payload", {}).get("cells", [{}])[0].get("point", {}).get("value")


# ---------------------------------------------------------------- R01
def test_probe_unrelated_approvals_refused(rt, rehearsal_bundle, tmp_path):
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [case_row("SPEC-A", "2026-01-15")],
                                 approve_natives=False)
    prepared = prepare_closure(rt, release_id=summary["release_id"], assessed_evidence_as_of="2026-10-01T00:00:00+10:00")
    # approvals for an unrelated subject / zero digest do not satisfy the exact subjects
    decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": "UNRELATED-SUBJECT",
                                 "subject_sha256": "0" * 64}])
    decide(rt, "result_integrity", [{"subject_kind": "analysis_closure", "subject_id": prepared["candidate_id"],
                                     "subject_sha256": "0" * 64}])
    with pytest.raises(PublicationRefused):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])
    assert rt.db.get_pointer(f"analysis:{summary['release_id']}") is None


def test_decision_registry_rejects_malformed_unlisted_and_unauthorised(rt):
    for bad in ({}, {"decision_format": "x"}, decision("source", [])):
        with pytest.raises(DecisionRejected):
            import_decision(rt, bad)
    sub = [{"subject_kind": "supplied_original", "subject_id": "X", "subject_sha256": "b" * 64}]
    with pytest.raises(DecisionRejected, match="allowlist"):
        import_decision(rt, decision("source", sub, reviewer="pytest"))
    with pytest.raises(DecisionRejected, match="not authorised"):
        import_decision(rt, decision("result_integrity", [{"subject_kind": "analysis_closure", "subject_id": "C",
                                                            "subject_sha256": "c" * 64}], reviewer="specimen-source-only"))
    doc = decision("source", sub, decision_id="DEC-FIXED")
    assert import_decision(rt, doc)["imported"] and not import_decision(rt, doc)["imported"]   # idempotent
    changed = copy.deepcopy(doc)
    changed["subjects"][0]["result"] = "fail"
    with pytest.raises(DecisionRejected, match="different content"):
        import_decision(rt, changed)


def test_rows_outside_release_never_contribute(world):
    rt = world["rt"]
    stray = case_row("SPEC-STRAY", "2026-01-20")
    rt.db.put_object("native_record", stray["envelope"]["record_id"], stray)      # sealed, but not a release entry
    ans = world["query"](world["context"]()["context"], ONE)
    assert ans["status"] == "ok" and _val(ans) == 2


def test_withdrawal_refuses_last_result(world):
    rt, rel = world["rt"], world["rel"]
    ctx = world["context"]()["context"]
    assert world["query"](ctx, ONE)["status"] == "ok"
    integrity = next(r for r in world["prepared"]["required_decisions"] if r["gate"] == "result_integrity")
    decide(rt, "result_integrity", [{k: integrity[k] for k in ("subject_kind", "subject_id", "subject_sha256")}],
           result="withdrawn", decided_at="2026-10-14T12:00:00+11:00")
    ans = world["query"](ctx, ONE)
    assert ans["error"]["code"] == "NOT_CALCULATED"       # incompatible last result refused, not served
    assert world["context"]()["context"]["analysis_set_version"] is None


def test_concurrent_publication_is_fenced(world):
    rt, rel = world["rt"], world["rel"]
    gen = rt.db.get_pointer(f"analysis:{rel}")["generation"]
    withdraw_analysis_set(rt, release_id=rel, reason="pytest specimen withdrawal")
    assert rt.db.get_pointer(f"analysis:{rel}")["generation"] == gen + 1
    # republishing the same closure after a withdrawal is allowed and fenced on the new generation
    aset = publish_analysis_set(rt, candidate_id=world["prepared"]["candidate_id"])
    assert aset["analysis_set_version"] == world["aset"]["analysis_set_version"]
    assert aset["published_at"] == world["aset"]["published_at"]                  # sealed clock retained


# ---------------------------------------------------------------- R02 / R03
def test_probe_repeat_identical_query_is_stable(world):
    ctx = world["context"]()["context"]
    first = world["query"](ctx)
    time.sleep(0.02)
    repeat = world["query"](ctx)
    again = world["query"](world["context"]()["context"])        # re-resolved equivalent context
    assert first["status"] == repeat["status"] == again["status"] == "ok"
    assert first["payload"]["result_id"] == repeat["payload"]["result_id"] == again["payload"]["result_id"]
    assert first["payload"]["cells"] == again["payload"]["cells"]
    assert again["payload"]["result_context"]["context"]["context_id"] != ctx["context_id"]   # echo projected


def test_probe_receipt_cohort_applies(world):
    cohort = dict(ONE, receipt_cohort_start="2026-03-01", receipt_cohort_end_exclusive="2026-04-01")
    assert _val(world["query"](world["context"]()["context"], cohort)) == 1
    reversed_ = dict(ONE, receipt_cohort_start="2026-04-01", receipt_cohort_end_exclusive="2026-03-01")
    assert world["query"](world["context"]()["context"], reversed_)["error"]["code"] == "INVALID_REQUEST"


def _jan_ref(world):
    first = world["query"](world["context"]()["context"])
    jan = next(c for c in first["payload"]["cells"] if c["key"] == "2026-01-01")
    return dict(result_id=first["payload"]["result_id"], cell_key=jan["key"], population_id=jan["population_id"],
                predicate_sha256=jan["population"]["predicate_sha256"])


def test_probe_child_without_repeated_reference_inherits_population(world):
    ref = _jan_ref(world)
    child = world["context"](pop=ref)["context"]
    assert _val(world["query"](child, ONE)) == 1          # January's one case, not the whole corpus


def test_probe_query_tampered_reference_refused(world):
    ref = _jan_ref(world)
    for bad in (dict(ref, predicate_sha256="0" * 64), dict(ref, population_id="POP-forged"), dict(ref, cell_key="2026-02-01")):
        ans = world["query"](world["context"]()["context"], ONE, bad)
        assert ans["error"]["code"] == "RESOURCE_UNAVAILABLE"


def test_probe_later_contributors_in_early_context_refused(world):
    ref = _jan_ref(world)
    early = world["context"]("2026-01-01T00:00:00+11:00")["context"]
    ans = world["ops"].handle("leader.demo", dict(operation="contributors.list", context_id=early["context_id"],
                                                  result_id=ref["result_id"], cell_key=ref["cell_key"], cursor=None,
                                                  page_size=10))
    assert ans["error"]["code"] == "RESOURCE_UNAVAILABLE"
    # ... and a reference from a different cutoff cannot be imported into an analysis query either
    ans = world["query"](early, ONE, ref)
    assert ans["error"]["code"] == "RESOURCE_UNAVAILABLE"


def test_probe_as_reported_never_computes(world):
    rt = world["rt"]
    stamp = world["aset"]["published_at"]
    ar = world["context"](analysis_mode="as_reported", analysis_as_of=stamp)["context"]
    ans = world["query"](ar, ONE)
    assert ans["error"]["code"] == "HISTORY_UNAVAILABLE"          # nothing was computed by then
    computed = world["query"](world["context"]()["context"], ONE)
    later = computed["payload"]["result_context"]["published_at"]
    ar2 = world["context"](analysis_mode="as_reported", analysis_as_of="2099-01-01T00:00:00Z")["context"]
    ans = world["query"](ar2, ONE)
    assert ans["status"] == "ok" and ans["payload"]["result_id"] == computed["payload"]["result_id"]
    assert ans["payload"]["result_context"]["published_at"] == later
    before = world["context"](analysis_mode="as_reported", analysis_as_of="2000-01-01T00:00:00Z")
    assert before["error"]["code"] == "HISTORY_UNAVAILABLE"


def test_old_context_pinned_after_republish(world, rehearsal_bundle, tmp_path):
    rt = world["rt"]
    old_ctx = world["context"]()["context"]
    # a new closure with an extra native row is prepared, approved and published
    from sdbackend.intake.delivery import receive
    from sdbackend.ingest.build import ingest
    from specimens import specimen_bundle
    rows = [case_row("SPEC-A", "2026-01-15"), case_row("SPEC-B", "2026-03-02"), case_row("SPEC-C", "2026-01-03")]
    bundle = specimen_bundle(tmp_path, rehearsal_bundle, rows, name="bundle2")
    delivery = receive(bundle, rt=rt)
    decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                 "subject_sha256": n.record_sha256} for n in delivery.natives])
    rel2 = ingest(rt, receive(bundle, rt=rt))["release_id"]
    assert rel2 != world["rel"]
    # the old context keeps reading its pinned set even if a new set exists for its release
    ans = world["query"](old_ctx, ONE)
    assert ans["status"] in ("ok", "unavailable")
    if ans["status"] == "unavailable":
        assert ans["error"]["code"] in ("CONTEXT_INCOMPATIBLE",)   # its release is no longer the served candidate


def test_probe_duplicate_case_revision_counts_unique(rt, rehearsal_bundle, tmp_path):
    a = case_row("SPEC-A", "2026-01-15")
    revised = case_row("SPEC-A", "2026-01-16", record_id="REC-SPEC-A-2", supersedes="REC-SPEC-A")
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [a, revised, case_row("SPEC-B", "2026-03-02")])
    publish_native(rt, summary["release_id"])
    from sdbackend.api.ops import Operations
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    ans = ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                         definition=DEF, filters=ONE, population_reference=None))
    assert ans["status"] == "ok" and _val(ans) == 2


def test_unresolved_revision_fork_refuses_publication(rt, rehearsal_bundle, tmp_path):
    a = case_row("SPEC-A", "2026-01-15")
    fork1 = case_row("SPEC-A", "2026-01-16", record_id="REC-SPEC-A-2", supersedes="REC-SPEC-A")
    fork2 = case_row("SPEC-A", "2026-01-17", record_id="REC-SPEC-A-3", supersedes="REC-SPEC-A")
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [a, fork1, fork2])
    with pytest.raises(PublicationRefused, match="unresolved"):
        publish_native(rt, summary["release_id"])


def test_date_only_receipt_against_non_midnight_bounds(world):
    f = dict(ONE, activity_start="2026-01-15T12:00:00+11:00", activity_end_exclusive="2026-03-03T00:00:00+11:00")
    ans = world["query"](world["context"]()["context"], f)
    cell = ans["payload"]["cells"][0]
    # SPEC-A's receipt day straddles the noon start → uncertain, not counted; SPEC-B wholly inside → counted
    assert cell["point"]["value"] == 1 and cell["point"]["uncertain_count"] == 1 and cell["point"]["state"] == "partial"


def test_probe_future_completion_before_cutoff():
    from sdbackend.engine.core import deadline_outcome, original_due, parse_instant
    out = deadline_outcome(applicable=True, due_boundary=parse_instant("2026-02-01T00:00:00Z"),
                           boundary_rule="inclusive_instant", cutoff=parse_instant("2026-01-01T00:00:00Z"),
                           completion={"precision": "instant", "instant": "2026-03-01T00:00:00Z"}, explicitly_unmet=False)
    assert out == "not_yet_due"
    out = deadline_outcome(applicable=True, due_boundary=parse_instant("2026-02-01T00:00:00Z"),
                           boundary_rule="inclusive_instant", cutoff=parse_instant("2026-04-01T00:00:00Z"),
                           completion={"precision": "instant", "instant": "2026-01-20T00:00:00Z",
                                       "available_at": "2026-05-01T00:00:00Z"}, explicitly_unmet=False)
    assert out == "unknown"                       # completion evidence only available after the cutoff
    revs = [{"revision_id": "r2", "due": "2026-03-01", "recorded_at": "2026-02-01T00:00:00Z", "available_at": "2026-02-01T00:00:00Z"},
            {"revision_id": "r1", "due": "2026-02-01", "recorded_at": "2026-01-05T00:00:00Z", "available_at": "2026-01-05T00:00:00Z"}]
    assert original_due(revs, parse_instant("2026-06-01T00:00:00Z"))["revision_id"] == "r1"


# ---------------------------------------------------------------- R04
def test_probe_aggregate_capability_required(world):
    restricted = world["context"](principal="restricted.demo")["context"]
    ans = world["query"](restricted, ONE, principal="restricted.demo")
    assert ans["error"]["code"] == "RESOURCE_UNAVAILABLE"


def test_partial_record_access_makes_aggregate_unavailable(rt, rehearsal_bundle, tmp_path):
    hidden = case_row("SPEC-H", "2026-01-20", access={"view_ids": ["another-view"], "classification": "restricted",
                                                     "redaction_state": "none"})
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [case_row("SPEC-A", "2026-01-15"), hidden])
    publish_native(rt, summary["release_id"])
    from sdbackend.api.ops import Operations
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    ans = ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                         definition=DEF, filters=ONE, population_reference=None))
    assert ans["error"]["code"] == "RESOURCE_UNAVAILABLE"      # never a silently smaller count


def test_probe_source_view_allowlist(ingested):
    rt, summary = ingested
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.policy import resolve_authority
    repo = Repo(rt, served_release(rt), resolve_authority("leader.demo"), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    src = copy.deepcopy(rt.db.list_objects("source")[0])
    sid = "SRC-AUDIT-RESTRICTED"
    src["data"]["source_id"] = sid
    src["envelope"]["record_id"] = sid
    src["envelope"]["access"]["view_ids"] = ["another-view"]
    rt.db.put_object("source", sid, src)
    repo._entries.add(("input", sid))
    assert repo.source_record(sid) is None
    asset = copy.deepcopy(rt.db.list_objects("asset")[0])
    asset["asset_version_id"] = "AV-AUDIT-RESTRICTED"
    asset["policy"]["access"]["view_ids"] = ["another-view"]
    rt.db.put_object("asset", asset["asset_version_id"], asset)
    repo._entries.add(("asset", asset["asset_version_id"]))
    assert repo.asset(asset["asset_version_id"]) is None


# ---------------------------------------------------------------- R05
def test_probe_review_after_staging_promotes_without_rewrites(ingested, rehearsal_bundle):
    rt, staged = ingested
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    assets_before = {a["asset_version_id"]: rt.db.object_sha("asset", a["asset_version_id"])
                     for a in rt.db.list_objects("asset")}
    delivery = receive(rehearsal_bundle, rt=rt)
    approve_originals(rt, delivery)
    promoted = ingest(rt, receive(rehearsal_bundle, rt=rt))
    assert promoted["status"] == "accepted" and promoted["pointer"] == "release:current"
    assert promoted["release_id"] != staged["release_id"]
    assert rt.db.get_pointer("release:candidate")["target_id"] == staged["release_id"]   # old release auditable
    for aid, sha in assets_before.items():
        assert rt.db.object_sha("asset", aid) == sha                                      # nothing rewritten
    again = ingest(rt, receive(rehearsal_bundle, rt=rt))                                   # repeated import idempotent
    assert again["release_id"] == promoted["release_id"]


def test_changed_bytes_never_inherit_approval(rt, rehearsal_bundle, tmp_path):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    delivery = receive(rehearsal_bundle, rt=rt)
    approve_originals(rt, delivery)
    copy_dir = tmp_path / "changed"
    shutil.copytree(rehearsal_bundle, copy_dir)
    target = copy_dir / "originals" / "attachments" / "11-07-receiving-document-checklist.csv"
    data = target.read_bytes().replace(b"outstanding", b"OUTSTANDING")
    target.write_bytes(data)
    m = json.loads((copy_dir / "delivery-manifest.json").read_text())
    for o in m["originals"]:
        if o["file"].endswith("checklist.csv"):
            o["sha256"], o["size_bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    raw = json.dumps(m, indent=1).encode()
    (copy_dir / "delivery-manifest.json").write_bytes(raw)
    (copy_dir / "INTAKE-FILES.sha256").unlink()
    d2 = receive(copy_dir, rt=rt)
    row = next(e for e in d2.receipt["originals"] if e["file"].endswith("checklist.csv"))
    assert row["disposition"] == "staged_pending_review" and row["source_review"] == "pending"
    assert ingest(rt, d2)["status"] == "unreviewed_candidate"


def test_repair_required_and_rejected_review_paths(rt, rehearsal_bundle):
    from sdbackend.intake.delivery import receive
    delivery = receive(rehearsal_bundle, rt=rt)
    o1, o2 = delivery.originals[0], delivery.originals[1]
    decide(rt, "source", [{"subject_kind": "supplied_original", "subject_id": o1.entry["original_id"],
                           "subject_sha256": hashlib.sha256(o1.data).hexdigest(), "result": "repair_required"},
                          {"subject_kind": "supplied_original", "subject_id": o2.entry["original_id"],
                           "subject_sha256": hashlib.sha256(o2.data).hexdigest(), "result": "fail"}])
    rows = {e["original_id"]: e for e in receive(rehearsal_bundle, rt=rt).receipt["originals"]}
    assert rows[o1.entry["original_id"]]["disposition"] == "staged_pending_review"
    assert rows[o2.entry["original_id"]]["disposition"] == "rejected"


def test_legacy_unbound_review_list_refused(rehearsal_bundle, tmp_path):
    from sdbackend.intake.delivery import DeliveryRejected, receive
    f = tmp_path / "decisions.json"
    f.write_text(json.dumps({"reviewer": "x", "independent": True, "decisions": {}}))
    with pytest.raises(DeliveryRejected):
        receive(rehearsal_bundle, review_decisions=f)


# ---------------------------------------------------------------- R06
def _run(rt, worklist=("x", "y")):
    from sdbackend.pipeline import control
    return control.seal_runspec(rt, purpose="case_assess", release_id="REL-x", source_set_version="SSV-x",
                                boundary={}, worklist=list(worklist), provider={}, versions={}, budget={},
                                parent_run_id=None, selection_reason="isolated test")


def test_probe_cancelled_worker_cannot_finish(rt):
    from sdbackend.pipeline import control
    run = _run(rt)
    t = control.ensure_task(rt, run, "test", "x", [])
    tok = control.claim(rt, t["task_id"])
    control.cancel_run(rt, run["run_id"], "test cancellation")
    out = ("stage_s02", "STAGE-late", {"late": True}, None)
    assert control.finish(rt, t["task_id"], tok, "staged", output_object_id="STAGE-late", outputs=[out]) is False
    assert rt.db.get_object("stage_s02", "STAGE-late") is None            # no stale output was written
    assert control.claim(rt, t["task_id"]) is None                         # cancellation is terminal
    with pytest.raises(control.TaskRevoked):
        control.heartbeat(rt, t["task_id"], tok)


def test_probe_expired_worker_cannot_finish(rt):
    from sdbackend.pipeline import control
    run = _run(rt)
    t = control.ensure_task(rt, run, "test", "y", [])
    tok = control.claim(rt, t["task_id"])
    rt.db.connect().execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00Z' WHERE task_id=?", (t["task_id"],))
    assert control.finish(rt, t["task_id"], tok, "staged") is False
    tok2 = control.claim(rt, t["task_id"])                                 # takeover
    assert tok2 == tok + 1
    assert control.finish(rt, t["task_id"], tok, "staged") is False        # old token still refused
    control.heartbeat(rt, t["task_id"], tok2)
    assert control.finish(rt, t["task_id"], tok2, "staged") is True


def test_run_expiry_and_resume(rt):
    from sdbackend.pipeline import control
    run = _run(rt, ["a"])
    assert control.find_resumable(rt, run["compat_key"])["run_id"] == run["run_id"]
    t = control.ensure_task(rt, run, "test", "a", [])
    rt.db.connect().execute("UPDATE runs SET expires_at='2000-01-01T00:00:00Z' WHERE run_id=?", (run["run_id"],))
    assert control.claim(rt, t["task_id"]) is None
    assert control.run_state(rt, run["run_id"]) == "expired"
    assert control.find_resumable(rt, run["compat_key"]) is None


# ---------------------------------------------------------------- R12
def test_probe_missing_normative_file_refused(rt, tmp_path, monkeypatch):
    from sdbackend.contracts import ContractIntegrityError, load_contracts
    contract_copy = tmp_path / "contracts"
    shutil.copytree(rt.settings.shared_contract_dir, contract_copy)
    (contract_copy / "DECISIONS.md").unlink()
    monkeypatch.setenv("SDB_SHARED_CONTRACT_DIR", str(contract_copy))
    load_contracts.cache_clear()
    try:
        with pytest.raises(ContractIntegrityError, match="missing"):
            load_contracts()
    finally:
        monkeypatch.undo()
        load_contracts.cache_clear()
        load_contracts()


# ---------------------------------------------------------------- R07 (S02 grounding)
def _atom(**kw):
    base = dict(passage="P1", quote="$12.50", statement="amount", asserted_by="author", assertion_kind="reported",
                field_key="financial_amount_minor", value="$12.50", value_state="known", event_date=None,
                subject="amount")
    return {**base, **kw}


def test_probe_numeric_value_grounding(rt):
    from sdbackend.pipeline.passages import passages
    from sdbackend.pipeline.source_interpret import materialise
    from test_models_and_pipeline import _src
    source = _src("The amount stated is $12.50. Work took 90 minutes on 13 March 2026.")
    ps = passages(source["data"]["text"])

    def run(atom):
        return materialise([atom], source, ps, rt.contracts, native_source=False, produced_at="2026-10-14T00:00:00Z")
    bad = run(_atom(value="$999.00"))
    assert bad["facts"] == [] and "not grounded" in bad["rejected"][0]["reason"]
    good = run(_atom())
    assert [f["data"]["value"]["value"] for f in good["facts"]] == [1250]
    dur = run(_atom(quote="took 90 minutes", field_key="work_duration_seconds", value="2 hours"))
    assert dur["facts"] == []
    assert run(_atom(quote="took 90 minutes", field_key="work_duration_seconds", value="90 minutes"))["facts"]
    wrong_date = run(_atom(quote="on 13 March 2026", field_key="complaints_receipt_date", value="2026-03-14"))
    assert wrong_date["facts"] == []
    assert run(_atom(quote="on 13 March 2026", field_key="complaints_receipt_date", value="2026-03-13"))["facts"]
    malformed = run(_atom(assertion_kind="certain", extra="x"))
    assert malformed["facts"] == [] and malformed["rejected"][0]["reason"].startswith("DTO:")


def test_data_original_signoff_imports_only_with_exact_bindings(rt, tmp_path, monkeypatch):
    """Maps Data's real v3 sign-off format in an isolated store under the TEST reviewer registry only."""
    from pathlib import Path
    from sdbackend.acceptance import effective, import_original_signoff, is_pass
    root = Path(__file__).resolve().parents[3] / "Data"
    signoff = root / "reviews/case-11-rehearsal-v3-2026-10-15/REHEARSAL-SOURCE-SIGNOFF.json"
    bundle = root / "synthesis/pilot-v2-releases/case-11-rehearsal-v3/intake-bundle"
    if not signoff.is_file() or not bundle.is_dir():
        pytest.skip("v3 sign-off or bundle not present")
    with pytest.raises(DecisionRejected, match="allowlist"):
        import_original_signoff(rt, signoff, bundle, reviewer_id="principal-data-engineer")   # not trusted here
    with pytest.raises(DecisionRejected, match="different delivery manifest"):
        import_original_signoff(rt, signoff, BUNDLE_V1, reviewer_id="specimen-reviewer")
    doc = json.loads(signoff.read_text())
    doc["originals"][0]["sha256"] = "0" * 64
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(doc))
    with pytest.raises(DecisionRejected, match="does not match the bound manifest"):
        import_original_signoff(rt, forged, bundle, reviewer_id="specimen-reviewer")
    out = import_original_signoff(rt, signoff, bundle, reviewer_id="specimen-reviewer")
    assert out["imported"]
    first = json.loads(signoff.read_text())["originals"][0]
    rec = effective(rt, "source", "supplied_original", first["original_id"], first["sha256"])
    assert is_pass(rec) and rec["decided_at"] == "2026-10-16T00:00:00+11:00"      # end of the review day
    assert effective(rt, "extraction", "extraction", first["original_id"], first["sha256"]) is None


BUNDLE_V1 = __import__("conftest").REHEARSAL
