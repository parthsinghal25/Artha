"""Intel pipeline MECHANICS on the real P4 corpus with a deterministic mock model (tests/intel_mock.py).

These establish queueing, grounding, reuse, versioning, publication, temporal views and API mechanics only. They are
not evidence of diagnostic quality — that comes only from the persisted real-model pilot (reports/intel/)."""
import json
from pathlib import Path

import pytest

from intel_mock import MockProvider

BACKEND = Path(__file__).resolve().parents[1]
CORPUS = BACKEND / "config" / "demo" / "p4-pilot-v1.corpus.json"


def _make_demo(root, mp):
    mp.setenv("SDB_DATA_DIR", str(root / "var" / "demo" / "pytest"))
    mp.setenv("SDB_POLICY_PATH", str(BACKEND / "config" / "demo" / "policy.json"))
    mp.setenv("SDB_PUBLICATION_POLICY", "synthetic-demo-v1")
    mp.setenv("SDB_DEMO_CORPUS", str(CORPUS))
    mp.setenv("SDB_REPORT_DIR", str(root / "runs"))
    mp.setenv("SDB_INTEL_REPORT_DIR", str(root / "reports"))
    for k in ("SDB_ALLOW_UNREVIEWED_SOURCES", "SDB_ALLOW_SPECIMEN_ACCEPTANCE", "SDB_REVIEWER_REGISTRY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        mp.delenv(k, raising=False)
    from sdbackend.intel.corpus import load_corpus
    from sdbackend.intel.intake import run_intake
    from sdbackend.runtime import make_runtime
    try:
        corpus = load_corpus(CORPUS)
    except (FileNotFoundError, SystemExit) as exc:
        pytest.skip(f"P4 corpus not present: {exc}")
    rt = make_runtime()
    intake = run_intake(rt, corpus)
    prov = MockProvider()
    import sdbackend.intel.worker as worker
    import sdbackend.models.gateway as gw
    mp.setattr(gw, "make_provider", lambda name, settings: prov)
    mp.setattr(worker, "choose_provider", lambda settings, pref, db=None, **k: ("mock", "pytest mock"))
    mp.setattr(gw.ModelGateway, "fingerprint",
               lambda self: {"provider": "mock", "model": prov.model, "sha256": "0" * 64})
    return {"rt": rt, "corpus": corpus, "intake": intake, "prov": prov, "steps": {}}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield _make_demo(tmp_path_factory.mktemp("intel"), mp)
    finally:
        mp.undo()


def _step(demo, view, **kw):
    from sdbackend.intel.worker import run_step
    before = len(demo["prov"].calls)
    rep = run_step(demo["rt"], demo["corpus"], view, provider="mock", log=lambda *a: None, **kw)
    rep["_calls"] = demo["prov"].calls[before:]
    demo["steps"].setdefault(view, []).append(rep)
    return rep


def _ops(demo):
    from sdbackend.api.ops import Operations
    return Operations(demo["rt"])


def test_intake_population_and_worklist(demo):
    i = demo["intake"]
    assert i["served"] and i["release"]["status"] == "synthetic_demo"
    assert i["population"] == {"cases": 25, "members": 20, "eligible_cases": 20, "not_eligible_cases": 5}
    assert sum(i["originals_by_extraction_state"].values()) == 253
    assert i["unresolved_originals"] == []


def test_initial_view_builds_memories_and_publication(demo):
    rep = _step(demo, "2026-03-31")
    assert rep["state"] == "complete", rep.get("stopped")
    pl = rep["plan"]
    assert pl["counts"]["update"] == 0 and pl["counts"]["unchanged"] == 0
    assert rep["memory"]["failed"] == []
    roles = set(rep["_calls"])
    assert {"case_reader", "case_factor_analyst", "case_synthesiser", "collective_discovery"} <= roles
    pub = rep["publication"]
    assert pub["publication_id"].startswith("IPUB-")
    from sdbackend.intel.publish import current
    assert current(demo["rt"])["publication_id"] == pub["publication_id"]


def test_memory_is_structured_grounded_and_critiqued(demo):
    from sdbackend.intel.worker import heads
    rt = demo["rt"]
    hs = heads(rt)
    assert hs
    for cid, h in hs.items():
        mem = rt.db.get_object("case_memory", h["memory_id"])
        assert mem["model_assessed"] is True and mem["human_review"] == "not_performed"
        # a narrative is never the only representation: structured claims/factors exist beside it
        assert isinstance(mem["claims"], list) and isinstance(mem["factors"], list)
        srcs = {s["data"]["source_id"]: s for s in rt.db.list_objects("source") if s["data"]["source_id"] in
                {c["source_id"] for c in mem["claims"]}}
        for c in mem["claims"]:
            text = " ".join(srcs[c["source_id"]]["data"]["text"].split())
            assert " ".join(c["quote"].split()) in text          # every claim is an exact grounded quote
        # the mock always asserts confirmed_complete; the host critic must downgrade without a recorded execution
        if mem["service"]["state"] == "confirmed_complete":
            kinds = {c["kind"] for c in mem["claims"] if c["claim_id"] in set(mem["service"].get("claims", []))}
            assert kinds & {"execution", "confirmation", "payment"}


def test_rerun_same_view_makes_zero_model_calls(demo):
    from sdbackend.intel.publish import current
    before = current(demo["rt"])["publication_id"]
    rep = _step(demo, "2026-03-31")
    assert rep["state"] == "complete"
    assert set(rep["plan"]["counts"]) and rep["plan"]["counts"]["new"] == 0 and rep["plan"]["counts"]["update"] == 0
    assert [r for r in rep["_calls"] if r.startswith("case_")] == []
    # collective is keyed on (memories, registry): identical inputs reuse discovery and assessments
    assert rep["_calls"] == [], rep["_calls"]
    assert current(demo["rt"])["publication_id"] == before


def test_later_view_revises_only_changed_cases(demo):
    from sdbackend.intel.worker import heads
    rt = demo["rt"]
    prior = {c: h["memory_id"] for c, h in heads(rt).items()}
    demo["prov"].propose_more = True             # a newly discovered issue must revisit older/unchanged memories
    rep = _step(demo, "2026-06-30")
    assert rep["state"] == "complete", rep.get("stopped")
    pl = rep["plan"]
    upd = [c for c in pl["cases"] if c["action"] == "update"]
    unchanged = [c for c in pl["cases"] if c["action"] == "unchanged"]
    assert upd, "the replay clock should reveal new evidence for some case"
    hs = heads(rt)
    for c in upd:
        mem = rt.db.get_object("case_memory", hs[c["case_id"]]["memory_id"])
        old = rt.db.get_object("case_memory", prior[c["case_id"]])
        assert mem["revision"] == old["revision"] + 1 and mem["predecessor_id"] == old["memory_id"]
        # the update read only the changed sources; previously read claims are retained, not re-read
        old_ids = {cl["claim_id"] for cl in old["claims"]}
        assert old_ids <= {cl["claim_id"] for cl in mem["claims"]}
        fresh = {cl["source_id"] for cl in mem["claims"] if cl["claim_id"] not in old_ids}
        assert fresh <= set(c["changed_sources"])
        assert rt.db.get_object("case_memory", old["memory_id"]) == old      # predecessor immutable
    for c in unchanged:
        assert hs[c["case_id"]]["memory_id"] == prior[c["case_id"]]
    from sdbackend.intel.discovery import ASSESS_PROMPT, active_issues, registry_head
    from sdbackend.canonical import derived_id
    issues = active_issues(registry_head(rt))
    assert len(issues) == 2
    new_def = [i for i in issues if i["label"] == "mock repeated document request"][0]["definition_id"]
    for c in unchanged:                      # older, unchanged (incl. closed) memories assessed for the new issue
        assert rt.db.get_object("membership_assessment",
                                derived_id("MBA", hs[c["case_id"]]["memory_id"], new_def, ASSESS_PROMPT)) is not None
    # every eligible memory was (re)assessed against the active registry at this view (older/closed included)
    from sdbackend.intel.publish import current
    pub = rt.db.get_object("intel_publication", current(rt)["publication_id"])
    cov = pub["coverage"]
    assert cov["unassessed_pairs"] == [] and cov["pending_memories"] == [], cov


def test_api_reads_make_zero_model_calls_and_validate(demo):
    ops = _ops(demo)
    n = len(demo["prov"].calls)
    r = ops.handle("leader.demo", {"operation": "intelligence.get", "context_id": None,
                                   "selection": {"temporal_policy": "demo-held-day-v1", "section": "overview"},
                                   "cursor": None, "page_size": 50})
    assert r["status"] == "ok", r
    ctx = r["context"]["context_id"]
    for section in ("issues", "alerts", "priorities", "arrivals", "burden", "experience", "coverage", "publications"):
        for grain in ("day", "week", "month", "quarter", "observation_year"):
            q = ops.handle("leader.demo", {"operation": "intelligence.get", "context_id": ctx,
                                           "selection": {"temporal_policy": "demo-held-day-v1", "section": section,
                                                         "grain": grain}, "cursor": None, "page_size": 50})
            assert q["status"] == "ok", (section, grain, q)
    pub = demo["rt"].db.get_object("intel_publication", r["payload"]["publication"]["publication_id"])
    cid = sorted(pub["memory_by_case"])[0]
    m = ops.handle("leader.demo", {"operation": "case.memory.get", "context_id": ctx, "case_id": cid,
                                   "memory_revision_id": None, "cursor": None, "page_size": 20})
    assert m["status"] == "ok", m
    # restricted principal is refused, not leaked
    d = ops.handle("restricted.demo", {"operation": "intelligence.get", "context_id": None,
                                       "selection": {"temporal_policy": "demo-held-day-v1"}, "cursor": None,
                                       "page_size": 10})
    assert d["status"] == "unavailable"
    assert len(demo["prov"].calls) == n


def test_evidence_drill_through_to_original(demo):
    """issue → included contributor → case memory claim → source.get (citation valid) → location.resolve → case.get"""
    ops = _ops(demo)
    sel = {"temporal_policy": "demo-held-day-v1"}
    ov = ops.handle("leader.demo", {"operation": "intelligence.get", "context_id": None,
                                    "selection": {**sel, "section": "issues"}, "cursor": None, "page_size": 50})
    ctx = ov["context"]["context_id"]
    iss = [i for i in ov["payload"]["items"] if (i["membership"].get("included") or {}).get("contributors")][0]
    cid = iss["membership"]["included"]["contributors"][0]
    mem = ops.handle("leader.demo", {"operation": "case.memory.get", "context_id": ctx, "case_id": cid,
                                     "memory_revision_id": None, "cursor": None, "page_size": 50})
    ms = {m["issue_id"]: m for m in mem["payload"]["detail"]["memberships"]}
    assert ms[iss["issue_id"]]["state"] == "included" and ms[iss["issue_id"]]["support_claim_ids"]
    claims = {c["claim_id"]: c for c in mem["payload"]["items"]}
    cl = claims[ms[iss["issue_id"]]["support_claim_ids"][0]]
    src = ops.handle("leader.demo", {"operation": "source.get", "context_id": ctx, "source_id": cl["source_id"],
                                     "content_sha256": cl["content_sha256"], "citation": cl["ref"]})
    assert src["status"] == "ok", src
    assert src["payload"]["citation_state"] == "valid"
    ex = src["payload"]["extractions"][0]
    loc = ops.handle("leader.demo", {"operation": "location.resolve", "context_id": ctx, "citation": cl["ref"],
                                     "asset_version_id": ex["permitted_asset_version_id"],
                                     "extraction_id": ex["extraction_id"]})
    assert loc["status"] == "ok", loc
    case = ops.handle("leader.demo", {"operation": "case.get", "context_id": ctx, "case_id": cid, "cursor": None,
                                      "page_size": 20})
    assert case["status"] == "ok", case


def test_final_view_publishes_tables_held_at_year_end(demo):
    rep = _step(demo, "2026-09-30")
    assert rep["state"] == "complete", rep.get("stopped")
    assert rep["plan"]["counts"]["new"] + rep["plan"]["counts"]["update"] + rep["plan"]["counts"]["table_refresh"] > 0


def test_burden_totals_are_nonduplicated(demo):
    """Hours and money equal sums over DISTINCT ledger/work records in view, whatever the issue/case overlap."""
    from sdbackend.intel.publish import current, portfolio
    from sdbackend.intel.worker import Context
    rt = demo["rt"]
    pub = rt.db.get_object("intel_publication", current(rt)["publication_id"])
    p, _ = portfolio(rt, Context(rt, demo["corpus"], pub["closure"]["view_date"]))
    work = {r["data"]["work_id"]: r for r in p.records if r["kind"] == "work_entry"}
    assert len(work) == sum(1 for r in p.records if r["kind"] == "work_entry")      # no duplicate copies
    shared = [r for r in work.values() if len(p.event_cases(r)) > 1]
    assert shared, "corpus holds shared work entries spanning several cases"
    tot = pub["results"]["overview"]["burden_total"]
    secs = sum(r["data"]["duration_seconds"] or 0 for r in work.values())
    assert tot["recorded_work"]["value"] == secs and tot["recorded_work"]["unit"] == "seconds"
    assert tot["recorded_work"]["hours"] == round(secs / 3600, 4)
    assert sorted(tot["recorded_work"]["contributors"]) == sorted(work)
    # issue-attributed burden never exceeds the portfolio total and each shared entry counts once per issue
    for v in pub["results"]["issues"]:
        ab = v["associated_burden"]["recorded_work"]
        assert ab["value"] <= secs and len(ab["contributors"]) == len(set(ab["contributors"]))
    for k in ("remedies_paid", "complaint_expenses_paid", "principal_or_entitlement_paid"):
        assert len(tot[k]["contributors"]) == len(set(tot[k]["contributors"]))
    unalloc = pub["results"]["overview"]["burden_unallocated_to_any_issue"]["recorded_work"]["value"]
    assert unalloc <= secs
    blob = json.dumps(pub["results"]).lower()
    assert "salary" not in blob and "hourly_rate" not in blob and "payroll" not in blob


def test_restart_resume_preserves_results(demo):
    """A fresh runtime over the same store sees the same heads, publication and zero work for the same view."""
    from sdbackend.intel.publish import current
    from sdbackend.intel.worker import Context, heads, plan
    from sdbackend.runtime import make_runtime
    rt2 = make_runtime()
    assert current(rt2)["publication_id"] == current(demo["rt"])["publication_id"]
    assert heads(rt2).keys() == heads(demo["rt"]).keys()
    pl = plan(Context(rt2, demo["corpus"], "2026-09-30"))
    assert pl["counts"]["new"] == pl["counts"]["update"] == pl["counts"]["withdrawal"] == 0
    # an earlier view never re-investigates or moves heads backwards; it is served from its sealed publication
    old = plan(Context(rt2, demo["corpus"], "2026-06-30"))
    assert old["counts"]["update"] == 0 and old["estimate"]["memory_model_calls"] == 0
    from sdbackend.intel.api import publication_for
    assert publication_for(rt2, "2026-06-30")["closure"]["view_date"] == "2026-06-30"


def test_interpretive_prompt_change_resynthesises_without_rereading(demo, monkeypatch):
    from sdbackend.intel import memory
    from sdbackend.intel.worker import heads
    rt = demo["rt"]
    before = {c: h["memory_id"] for c, h in heads(rt).items()}
    monkeypatch.setattr(memory, "FACTOR_PROMPT", "intel-case-factors-test-bump")
    rep = _step(demo, "2026-09-30")
    assert rep["state"] == "complete", rep.get("stopped")
    assert rep["plan"]["counts"]["resynthesis"] == len(before)
    assert "case_reader" not in rep["_calls"] and "case_read_repair" not in rep["_calls"]
    for cid, h in heads(rt).items():
        mem = rt.db.get_object("case_memory", h["memory_id"])
        old = rt.db.get_object("case_memory", before[cid])
        assert mem["reason"] == "interpretation_revised" and mem["predecessor_id"] == old["memory_id"]
        assert {c["claim_id"] for c in mem["claims"]} == {c["claim_id"] for c in old["claims"]}
        assert mem["producer"]["factor_prompt"] == "intel-case-factors-test-bump"


def test_budget_exhaustion_resumes_and_incremental_equals_fresh(tmp_path, monkeypatch):
    """A bounded run stops cleanly, the same command resumes without redoing sealed work, and the incremental
    (view-by-view) memories hold the same grounded claims as a fresh single pass at the final view."""
    from sdbackend.intel.worker import heads
    inc = _make_demo(tmp_path / "inc", monkeypatch)
    r1 = _step(inc, "2026-03-31", max_requests=7, collective=False, publish=False)
    assert r1["state"] == "budget_exhausted"
    sealed = dict(heads(inc["rt"]))
    assert 0 < len(sealed) < len(r1["plan"]["cases"])
    r2 = _step(inc, "2026-03-31", collective=False, publish=False)
    assert r2["state"] == "complete"
    assert r2["memory"]["processed"] == len(r1["plan"]["cases"]) - len(sealed)
    assert all(heads(inc["rt"])[c]["memory_id"] == h["memory_id"] for c, h in sealed.items())
    _step(inc, "2026-06-30", collective=False, publish=False)
    inc_claims = {c: {x["claim_id"] for x in inc["rt"].db.get_object("case_memory", h["memory_id"])["claims"]}
                  for c, h in heads(inc["rt"]).items()}
    fresh = _make_demo(tmp_path / "fresh", monkeypatch)
    _step(fresh, "2026-06-30", collective=False, publish=False)
    fresh_claims = {c: {x["claim_id"] for x in fresh["rt"].db.get_object("case_memory", h["memory_id"])["claims"]}
                    for c, h in heads(fresh["rt"]).items()}
    assert inc_claims == fresh_claims


def test_dead_lease_on_this_host_is_recovered_but_live_lease_is_not(demo):
    import socket
    import subprocess
    from sdbackend.pipeline import control
    rt = demo["rt"]
    rid = rt.db.connect().execute("SELECT run_id FROM runs WHERE state='active' ORDER BY created_at DESC "
                                  "LIMIT 1").fetchone()["run_id"]
    task = control.ensure_task(rt, control.load_run(rt, rid), "intel_test_lease", "x", ["lease"])
    dead = subprocess.Popen(["true"])
    dead.wait()
    with rt.db.tx() as c:
        c.execute("UPDATE tasks SET state='running', lease_owner=?, lease_expires_at='2999-01-01T00:00:00Z' "
                  "WHERE task_id=?", (f"{socket.gethostname()}:{dead.pid}", task["task_id"]))
    assert control.claim(rt, task["task_id"]) is None              # unexpired lease blocks a normal claim
    assert control.recover_dead_lease(rt, task["task_id"]) is True
    assert control.claim(rt, task["task_id"]) is not None
    with rt.db.tx() as c:                                          # a live owner (this process's parent) is kept
        c.execute("UPDATE tasks SET state='running', lease_owner=? WHERE task_id=?",
                  (f"{socket.gethostname()}:{__import__('os').getppid()}", task["task_id"]))
    assert control.recover_dead_lease(rt, task["task_id"]) is False
    with rt.db.tx() as c:                                          # another host is never touched
        c.execute("UPDATE tasks SET lease_owner='other-host:1' WHERE task_id=?", (task["task_id"],))
    assert control.recover_dead_lease(rt, task["task_id"]) is False


def test_refused_assessment_persist_is_reported_unassessed_then_resumed(tmp_path, monkeypatch):
    from sdbackend.pipeline import control
    d = _make_demo(tmp_path / "lease", monkeypatch)
    orig, state = control.finish, {"refused": 0}

    def flaky(rt, task_id, token, st, **kw):
        row = rt.db.connect().execute("SELECT task_kind FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row["task_kind"] == "intel_membership" and state["refused"] == 0:
            state["refused"] += 1
            return False                                    # e.g. the lease was lost during a long batch
        return orig(rt, task_id, token, st, **kw)
    monkeypatch.setattr(control, "finish", flaky)
    r1 = _step(d, "2026-03-31")
    a1 = r1["collective"]["assessment"]
    assert a1["assessed"] < a1["to_assess"]
    assert any("persist refused" in u["reason"] for u in a1["unassessed"])
    pub1 = d["rt"].db.get_object("intel_publication", r1["publication"]["publication_id"])
    assert pub1["coverage"]["unassessed_pairs"]                 # disclosed, never silently treated as assessed
    r2 = _step(d, "2026-03-31")
    a2 = r2["collective"]["assessment"]
    assert a2["to_assess"] == len(a1["unassessed"]) and a2["assessed"] == a2["to_assess"]
    pub2 = d["rt"].db.get_object("intel_publication", r2["publication"]["publication_id"])
    assert pub2["coverage"]["unassessed_pairs"] == []
