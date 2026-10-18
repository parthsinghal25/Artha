"""Regression coverage for the Principal implementation review (V01–V06), on the real P4 corpus with the
deterministic mock model. Mechanics only — no claim about diagnostic quality."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from intel_mock import MockProvider
from test_intel_mechanics import BACKEND, _make_demo, _ops, _step, demo  # noqa: F401  (fixture re-export)

POLICY = BACKEND / "config" / "demo" / "policy.json"
SEL = {"temporal_policy": "demo-held-day-v1"}


@pytest.fixture(scope="module")
def built(demo):
    assert _step(demo, "2026-09-30")["state"] == "complete"
    return demo


def _policy(tmp_path, monkeypatch, edit):
    p = json.loads(POLICY.read_text())
    edit(p)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(p))
    monkeypatch.setenv("SDB_POLICY_PATH", str(path))


def _intel(ops, principal, ctx=None, **sel):
    return ops.handle(principal, {"operation": "intelligence.get", "context_id": ctx, "selection": {**SEL, **sel},
                                  "cursor": None, "page_size": 100})


def _memory(ops, principal, ctx, cid):
    return ops.handle(principal, {"operation": "case.memory.get", "context_id": ctx, "case_id": cid,
                                  "memory_revision_id": None, "cursor": None, "page_size": 100})


# ------------------------------------------------------------------ V01 permissions over the whole envelope
def test_v01_capability_restriction_strips_every_identifier_but_keeps_values(built, tmp_path, monkeypatch):
    from sdbackend.intel.publish import current
    pub = current(built["rt"])
    population = set(pub["case_views"]) | set(pub["results"]["overview"]["members"]["contributors"])
    iid = pub["results"]["issues"][0]["issue_id"]
    full = _intel(_ops(built), "leader.demo", section="issue", issue_id=iid)
    assert full["status"] == "ok" and population & set(json.dumps(full["payload"]).replace('"', " ").split())

    def no_contributors(p):
        p["principals"]["analyst.demo"]["capabilities"] = [c for c in p["principals"]["analyst.demo"]["capabilities"]
                                                           if c != "contributors"]
    _policy(tmp_path, monkeypatch, no_contributors)
    for section in ("overview", "issues", "issue", "alerts", "priorities", "burden", "experience", "coverage"):
        r = _intel(_ops(built), "analyst.demo", section=section, issue_id=iid)
        assert r["status"] == "ok", (section, r)
        text = json.dumps(r["payload"])
        assert not [x for x in population if x in text], section          # no case/member identifier anywhere
    r = _intel(_ops(built), "analyst.demo", section="issue", issue_id=iid)
    assert r["payload"]["detail"]["issue"]["membership"]["included"]["value"] == \
        full["payload"]["detail"]["issue"]["membership"]["included"]["value"]   # values unchanged, ids withheld


def test_v01_denied_case_withholds_every_aggregate_section_neutrally(built, tmp_path, monkeypatch):
    from sdbackend.intel.publish import current
    pub = current(built["rt"])
    cid = sorted(pub["memory_by_case"])[0]
    _policy(tmp_path, monkeypatch, lambda p: p["views"]["complaints-leadership"].update(denied_case_ids=[cid]))
    ops = _ops(built)
    for section in ("overview", "issues", "alerts", "priorities", "burden", "arrivals", "experience", "coverage"):
        r = _intel(ops, "leader.demo", section=section)
        assert r["status"] == "unavailable" and r["error"]["code"] == "RESOURCE_UNAVAILABLE", section
        assert cid not in json.dumps(r)
    log = _intel(ops, "leader.demo", section="publications")            # no population dependency
    assert log["status"] == "ok" and cid not in json.dumps(log)


def test_v01_denied_table_source_withholds_dependent_memory(built, tmp_path, monkeypatch):
    from sdbackend.intel.worker import heads
    rt = built["rt"]
    mem = next(m for m in heads(rt).values() if m["inputs"]["table_sources"] and m["narrative"])
    sid = mem["inputs"]["table_sources"][0]
    _policy(tmp_path, monkeypatch, lambda p: p["views"]["complaints-leadership"].update(denied_source_ids=[sid]))
    ops = _ops(built)
    ctx = _intel(ops, "leader.demo")["context"]["context_id"]
    r = _memory(ops, "leader.demo", ctx, mem["case_id"])
    assert r["status"] == "unavailable" and sid not in json.dumps(r)
    assert mem["narrative"][0]["text"] not in json.dumps(r)


def test_v01_full_access_drill_through_still_completes(built):
    ops = _ops(built)
    iss = _intel(ops, "leader.demo", section="issues")
    ctx = iss["context"]["context_id"]
    item = next(i for i in iss["payload"]["items"] if (i["membership"].get("included") or {}).get("contributors"))
    cid = item["membership"]["included"]["contributors"][0]
    mem = _memory(ops, "leader.demo", ctx, cid)
    assert mem["status"] == "ok"
    ms = {m["issue_id"]: m for m in mem["payload"]["detail"]["memberships"]}[item["issue_id"]]
    cl = {c["claim_id"]: c for c in mem["payload"]["items"]}[ms["support_claim_ids"][0]]
    src = ops.handle("leader.demo", {"operation": "source.get", "context_id": ctx, "source_id": cl["source_id"],
                                     "content_sha256": cl["content_sha256"], "citation": cl["ref"]})
    assert src["status"] == "ok" and src["payload"]["citation_state"] == "valid"


# ------------------------------------------------------------------ V02 retrieved evidence identity and side
def _fake_search(rt, ctx):
    from sdbackend.citations import text_ref

    def search(case_id, terms, limit):
        srcs = ctx.inputs(case_id).narrative
        if not srcs:
            return []
        # different issues (terms) get different passages, so a handle collision would mis-resolve
        start = int(hashlib.sha256(terms.encode()).hexdigest(), 16) % len(srcs)
        out = []
        for s in (srcs[start:] + srcs[:start])[:limit]:
            d = s["data"]
            end = min(len(d["text"]), 160)
            out.append({"source_id": d["source_id"], "text": d["text"][:end], "ref": text_ref(d, 0, end)})
        return out
    return search


def test_v02_retrieval_only_and_conflicting_memberships_keep_exact_evidence(tmp_path, monkeypatch):
    from sdbackend.intel import discovery
    from sdbackend.intel.publish import current
    d = _make_demo(tmp_path / "v02", monkeypatch)
    monkeypatch.setattr(discovery, "make_search", _fake_search)
    assert _step(d, "2026-03-31", collective=False, publish=False)["state"] == "complete"
    d["prov"].weak = True
    rep = _step(d, "2026-03-31")
    assert rep["state"] == "complete", rep.get("stopped")
    rt = d["rt"]
    pub = current(rt)
    stored = [rt.db.get_object("membership_assessment", a) for a in pub["closure"]["assessment_ids"]]
    inc = [a for a in stored if a["state"] == "included"]
    con = [a for a in stored if a["state"] == "conflicting"]
    assert inc and con
    for a in inc:                                       # retrieval-only inclusion: no memory claim, exact passage
        assert a["support_claim_ids"] == [] and a["retrieved_support_refs"]
        assert not ({json.dumps(r, sort_keys=True) for r in a["retrieved_support_refs"]} &
                    {json.dumps(r, sort_keys=True) for r in a["retrieved_contrary_refs"]})
        assert a["retrieval"]["limit"] and "truncated" in a["retrieval"]
    for a in con:                                       # contrary evidence never becomes support
        assert a["retrieved_support_refs"] and a["retrieved_contrary_refs"]
    # the bridged finding keeps the sides apart
    for f in rt.db.list_objects("membership_interval"):
        if f["case_id"] == con[0]["case_id"] and f["taxonomy_node_id"] == con[0]["issue_id"]:
            sup = {r["source_id"] + str(r.get("start")) for r in f["supporting_refs"]}
            contra = {r["source_id"] + str(r.get("start")) for r in f["contradictory_refs"]}
            assert contra and not (sup & contra)
    # API: every included membership has a resolvable path to its original evidence
    ops = _ops(d)
    ctx = _intel(ops, "leader.demo")["context"]["context_id"]
    for a in inc[:3]:
        m = _memory(ops, "leader.demo", ctx, a["case_id"])
        ms = {x["issue_id"]: x for x in m["payload"]["detail"]["memberships"]}[a["issue_id"]]
        assert ms["evidence_basis"] == "retrieved_passages" and ms["retrieved_support_refs"]
        ref = {k: v for k, v in ms["retrieved_support_refs"][0].items() if k != "passage_handle"}
        src = rt.db.get_object("source", ref["source_id"])
        r = ops.handle("leader.demo", {"operation": "source.get", "context_id": ctx, "source_id": ref["source_id"],
                                       "content_sha256": src["data"]["content_sha256"], "citation": ref})
        assert r["status"] == "ok" and r["payload"]["citation_state"] == "valid"


def test_v02_two_issues_same_case_retrieval_handles_resolve_to_their_own_passages(tmp_path, monkeypatch):
    from sdbackend.intel import discovery
    d = _make_demo(tmp_path / "v02b", monkeypatch)
    monkeypatch.setattr(discovery, "make_search", _fake_search)
    _step(d, "2026-03-31")
    d["prov"].weak, d["prov"].propose_more = True, True
    rep = _step(d, "2026-06-30")
    assert rep["state"] == "complete", rep.get("stopped")
    rt = d["rt"]
    by_case = {}
    for a in rt.db.list_objects("membership_assessment"):
        if a.get("retrieved_support_refs") and a["assessed_view"] == "2026-06-30":
            by_case.setdefault(a["case_id"], []).append(a)
    multi = [v for v in by_case.values() if len({a["issue_id"] for a in v}) >= 2]
    assert multi, "expected one case retrieved for two issues in one request"
    for v in multi:
        handles = [r["passage_handle"] for a in v for r in a["retrieved_support_refs"] + a["retrieved_contrary_refs"]]
        refs = {r["passage_handle"]: json.dumps({k: x for k, x in r.items() if k != "passage_handle"}, sort_keys=True)
                for a in v for r in a["retrieved_support_refs"] + a["retrieved_contrary_refs"]}
        assert len(refs) == len(set(handles))          # one handle → one passage, never reused for another issue


def test_v02_subset_shown_to_assessor_is_recorded_and_omission_is_not_exclusion():
    from sdbackend.intel import discovery
    mem = {"native_case": {}, "factors": [], "claims": [
        {"claim_id": f"CLM-{i}", "kind": "other", "modality": "recorded", "actor": "m", "quote": f"q{i}",
         "source_day": "2026-01-01"} for i in range(30)]}
    txt, alias = discovery._case_block("K1", mem)
    assert len(alias) == discovery.MAX_CLAIMS_SHOWN and "claims shown 24 of 30" in txt and "SUBSET" in txt
    iss = {"issue_id": "ISS-1", "definition_id": "DEF-1"}
    v = discovery._validate({"case": "K1", "issue": "I1", "state": "excluded", "support": [], "contrary": []},
                            alias, {"K1": "C"}, {"I1": iss}, {}, {"C": (24, 30)})
    assert v["state"] == "uncertain" and v["needs_evidence"] and v["claims_shown"] == 24 and v["claims_total"] == 30
    v = discovery._validate({"case": "K1", "issue": "I1", "state": "excluded", "support": [], "contrary": ["K1.C2"]},
                            alias, {"K1": "C"}, {"I1": iss}, {}, {"C": (24, 30)})
    assert v["state"] == "excluded"                     # an explicit contrary claim may still exclude


# ------------------------------------------------------------------ V03 withdrawal / replacement / last source
class _Gw:
    def __init__(self, prov):
        self.prov = prov

    def call(self, req, *, task_id, attempt=1):
        return self.prov.generate(req)


def _changed(src, text):
    from sdbackend.canonical import source_content_sha256
    s = copy.deepcopy(src)
    s["data"]["text"] = text
    s["data"]["content_sha256"] = source_content_sha256(s["data"])
    return s


def test_v03_add_change_withdraw_last_source_incremental_equals_fresh(built):
    from dataclasses import replace
    from sdbackend.intel import memory
    from sdbackend.intel.publish import dangling_refs
    from sdbackend.intel.worker import Context
    rt = built["rt"]
    ctx = Context(rt, built["corpus"], "2026-09-30")
    cid = next(c for c in ctx.eligible if len(ctx.inputs(c).narrative) >= 3)
    full = ctx.inputs(cid)
    gw = _Gw(MockProvider())
    prov = {"provider": "mock", "model": "m", "fingerprint_sha256": "0" * 64}

    def run(inp, prior):
        body = memory.investigate(gw, inp, prior, task_id="T")
        return memory.seal_memory(rt, inp, body, prior, reason="t", provider=prov, run_id="R")[1]

    def claims(m):
        return {c["claim_id"] for c in m["claims"]}

    steps = {
        "add": replace(full, narrative=full.narrative),
        "change": replace(full, narrative=[_changed(full.narrative[0], full.narrative[0]["data"]["text"] +
                                                    "\nThe replacement statement records a different outcome entirely.")]
                          + full.narrative[1:]),
        "withdraw": replace(full, narrative=full.narrative[1:-1]),
    }
    prior = run(replace(full, narrative=full.narrative[:-1]), None)
    for name, inp in steps.items():
        inc = run(inp, prior)
        fresh = run(inp, None)
        assert claims(inc) == claims(fresh), name                 # incremental agrees with a fresh pass
        assert not dangling_refs(inc), (name, dangling_refs(inc))
        held = {s["data"]["source_id"]: s["data"]["content_sha256"] for s in inp.narrative}
        assert all(held.get(c["source_id"]) == c["content_sha256"] for c in inc["claims"]), name
        prior = inc
    last = run(replace(full, narrative=[]), prior)                # final usable narrative source withdrawn
    assert last["claims"] == [] and last["factors"] == [] and last["service"]["state"] == "unknown"
    assert last["coverage"]["state"] == "no_grounded_claims" and last["retracted_factors"]
    assert last["predecessor_id"] == prior["memory_id"] and not dangling_refs(last)


def test_v03_published_interpretation_has_no_dangling_references(built):
    from sdbackend.intel.publish import current, dangling_refs
    rt = built["rt"]
    pub = current(rt)
    assert pub["checks"]["no_dangling_claim_refs"] is True
    for mid in pub["memory_by_case"].values():
        assert not dangling_refs(rt.db.get_object("case_memory", mid))


# ------------------------------------------------------------------ V05 partial extraction reaches the case
def test_v05_partial_ocr_is_carried_to_case_memory_view_and_api(built):
    from sdbackend.intel.publish import current
    rt = built["rt"]
    pub = current(rt)
    xw = next(iter(rt.db.list_objects("intel_crosswalk")))
    partial = {r["original_id"]: r for r in xw["rows"] if r["extraction_state"] == "partial"}
    assert partial
    hits = 0
    for cid, mid in pub["memory_by_case"].items():
        mem = rt.db.get_object("case_memory", mid)
        mine = [o for o, r in partial.items() if cid in r["case_ids"] and
                set(r["source_ids"]) & {s["source_id"] for s in mem["inputs"]["narrative_sources"]}]
        if not mine:
            continue
        hits += 1
        cov = mem["coverage"]
        assert cov["extraction"] == "partial" and cov["state"] == "partial"
        assert [o["original_id"] for o in cov["partial_extraction_originals"]] == mine
        assert cov["reading"] == "all_available_text_read"          # the OCR text stays usable
        view = rt.db.get_object("case_view", pub["case_views"][cid])
        assert view["completeness"] == "partial" and any(mine[0] in x for x in view["limitations"])
        ops = _ops(built)
        ctx = _intel(ops, "leader.demo")["context"]["context_id"]
        r = _memory(ops, "leader.demo", ctx, cid)
        assert r["payload"]["detail"]["coverage"]["partial_extraction_originals"][0]["original_id"] == mine[0]
    assert hits >= 1


# ------------------------------------------------------------------ V06 budget at every provider attempt
def test_v06_retrieval_and_retries_cannot_exceed_the_request_cap_and_resume_preserves_work(tmp_path, monkeypatch):
    from sdbackend.intel import discovery
    from sdbackend.intel.worker import heads
    d = _make_demo(tmp_path / "v06", monkeypatch)
    monkeypatch.setattr(discovery, "make_search", _fake_search)
    _step(d, "2026-03-31", collective=False, publish=False)
    sealed = {c: h["memory_id"] for c, h in heads(d["rt"]).items()}
    d["prov"].weak = True
    r1 = _step(d, "2026-03-31", max_requests=1)              # discovery takes the only request
    assert r1["state"] == "budget_exhausted" and len(r1["_calls"]) == 1
    r2 = _step(d, "2026-03-31", max_requests=1)              # assessor gets it; retrieval must not run
    assert r2["state"] == "budget_exhausted" and r2["_calls"] == ["membership_assessor"]
    r3 = _step(d, "2026-03-31")
    assert r3["state"] == "complete" and r3["collective"]["assessment"]["unassessed"] == []
    assert {c: h["memory_id"] for c, h in heads(d["rt"]).items()} == sealed      # sealed memories preserved
    assert r3["usage"]["calls"] == len(r3["_calls"])          # every attempt is in the ledger for this step


# ------------------------------------------------------------------ V07 A10 AFCA procedure / A09 continuity
def test_v07_afca_matters_are_associated_cited_and_holdout_separated(built):
    from sdbackend.intel.publish import current
    rt = built["rt"]
    pub = current(rt)
    afca = pub["results"]["afca"]
    assert afca["matters"], "P4 carries factual AFCA associations"
    assessed = set(pub["memory_by_case"])
    for m in afca["matters"]:
        assert m["case_ids"] and set(m["case_ids"]) <= assessed                       # eligible, assessed cases only
        assert m["association_refs"]                                                  # exists only via association
        for e in m["stage_history"]:
            text = rt.db.get_object("source", e["source_id"])["data"]["text"]
            assert e["quote"] and e["ref"] and e["quote"] in text                     # every stage is a cited span
        if m["closed_date"] is None:
            assert m["closure_route"] in ("not_closed", "unknown")
        else:
            assert m["current_stage"] == "closed" or m["closure_route"] != "not_closed"
    served = {m["afca_matter_id"] for m in afca["matters"]}
    for mid in afca["holdout_or_ineligible_matters_unassessed"]:                      # holdout never assessed
        m = next((x for x in afca["matters"] if x["afca_matter_id"] == mid), None)
        assert m is None or set(m["case_ids"]) <= assessed
    assert served


def test_v07_withdrawal_email_is_a_notice_not_a_closure_and_route_needs_the_closing_source():
    from sdbackend.intel import procedures as P
    email = "Dear team, I am withdrawing this matter from the ombudsman as you have now fixed it."
    assert P.WITHDRAWAL_NOTICE.search(email)
    assert not any(rx.search(email) for st, rx in P.STAGE_RULES if st == "closed")
    closure = "Our file is closed. This is a closure following withdrawal by the complainant."
    assert dict(P.STAGE_RULES)["closed"].search(closure) and P.ROUTE_WITHDRAWN.search(closure)
    assert P.NOT_DETERMINATION.search("The matter is closed. This is not a determination.")
    assert not any(rx.search("I will take this to AFCA if you do not respond.") for _, rx in P.STAGE_RULES)


def _case(eligible=True, members=("M1",), transitions=(), receipt="2026-01-01"):
    return {"eligible": eligible, "members": list(members), "receipt_date": receipt,
            "transitions": [{"state": s, "effective_date": d, "available_at": d + "T00:00:00Z", "evidence_refs": []}
                            for s, d in transitions]}


def _mem(claims=(), items=(), factors=(), narrative=()):
    return {"claims": [{"claim_id": i, "business_date": d, "source_day": d, "quote": q} for i, d, q in claims],
            "service": {"items": list(items)}, "factors": list(factors),
            "inputs": {"narrative_sources": [{"source_id": s} for s in narrative]}}


def test_v07_continuity_completion_reopen_and_repeat_case_bases():
    from sdbackend.intel.procedures import continuity
    cases = {
        "C-a": _case(transitions=[("open", "2026-01-01"), ("closed", "2026-02-01"), ("reopened", "2026-03-01"),
                                  ("closed", "2026-04-01")]),
        "C-b": _case(transitions=[("open", "2026-05-01"), ("closed", "2026-05-20")], receipt="2026-05-01"),
        "C-c": _case(transitions=[("open", "2026-06-01")], receipt="2026-06-01"),
        "C-h": _case(eligible=False, transitions=[("open", "2026-01-01"), ("closed", "2026-01-05")]),
    }
    mems = {
        "C-a": _mem(claims=[("CL1", "2026-01-10", "fee charged"), ("CL2", "2026-03-10", "fee charged again"),
                            ("CL3", "2026-03-20", "refund paid")],
                    items=[{"action": "refund the fee", "completion_claim_ids": ["CL3"]}],
                    factors=[{"factor_id": "F1", "label": "duplicate fee", "support_claim_ids": ["CL1", "CL2"]}],
                    narrative=["S-shared"]),
        "C-b": _mem(claims=[("CL4", "2026-05-02", "same as case C-a")], narrative=["S-b"]),
        "C-c": _mem(claims=[("CL5", "2026-06-02", "new address")]),
    }
    xw = {"rows": [{"original_id": "O1", "case_ids": ["C-a", "C-c"], "source_ids": ["S-shared"],
                    "source_family": "S02", "semantic_type": "email"},
                   {"original_id": "O2", "case_ids": ["C-a", "C-b", "C-c"], "source_ids": ["S-x"],
                    "source_family": "S10", "semantic_type": "work_export"}]}
    out = continuity(cases, mems, xw, [{"afca_matter_id": "AM1", "case_ids": ["C-b", "C-c"]}])
    assert "C-h" not in out["cases"] and "C-h" not in json.dumps(out["repeat_case_links"])       # holdout excluded
    a = out["cases"]["C-a"]
    assert [i["completion"] for i in a["intervals"]] == ["closure_without_completion_evidence", "recorded_completion"]
    assert a["reopens"][0]["continuity"] == "same_problem_continued"
    assert out["cases"]["C-c"]["intervals"][0]["completion"] == "open_or_unknown"
    links = {tuple(l["case_ids"]): l for l in out["repeat_case_links"]}
    assert [b["basis"] for b in links[("C-a", "C-b")]["basis"]] == ["claim_names_other_case"]
    assert [b["basis"] for b in links[("C-a", "C-c")]["basis"]] == ["original_associated_with_both"]  # S10 ignored
    assert [b["basis"] for b in links[("C-b", "C-c")]["basis"]] == ["shared_external_matter"]
    # without any runtime evidence a repeat case is uncertain, never a new problem
    out2 = continuity({k: v for k, v in cases.items() if k in ("C-a", "C-c")}, {"C-a": _mem(), "C-c": _mem()},
                      {"rows": []}, [])
    assert out2["repeat_case_links"][0]["state"] == "uncertain"
    assert out2["cases"]["C-a"]["reopens"][0]["continuity"] == "uncertain"


def test_v07_api_serves_afca_and_continuity_and_strips_ids_without_contributors(built, tmp_path, monkeypatch):
    from sdbackend.intel.publish import current
    pub = current(built["rt"])
    population = set(pub["case_views"]) | set(pub["results"]["overview"]["members"]["contributors"])
    for section in ("afca", "continuity"):
        r = _intel(_ops(built), "leader.demo", section=section)
        assert r["status"] == "ok", (section, r)

    def no_contributors(p):
        p["principals"]["analyst.demo"]["capabilities"] = [c for c in p["principals"]["analyst.demo"]["capabilities"]
                                                           if c != "contributors"]
    _policy(tmp_path, monkeypatch, no_contributors)
    for section in ("afca", "continuity"):
        r = _intel(_ops(built), "analyst.demo", section=section)
        assert r["status"] == "ok", (section, r)
        text = json.dumps(r["payload"])
        assert not [x for x in population if x in text], section


# ------------------------------------------------------------------ V07 M5 offline evaluator interface
def test_v07_offline_evaluator_export_adapter_and_honest_outcomes(built, tmp_path):
    """Synthetic in-test judgements only — no private authoring, gold or reviewer files are read."""
    from sdbackend.intel import evaluate as E
    from sdbackend.intel.intake import crosswalk_for
    from sdbackend.intel.publish import current
    rt, corpus = built["rt"], built["corpus"]
    with pytest.raises(ValueError):
        E.export_frozen(rt, corpus, Path(rt.settings.data_dir) / "export")              # never inside the store
    with pytest.raises(PermissionError):
        E.export_frozen(rt, corpus, tmp_path / "early", subset="early_evaluation")      # needs authorisation
    with pytest.raises(ValueError):
        E.export_frozen(rt, corpus, tmp_path / "h", subset="holdout")
    man = E.export_frozen(rt, corpus, tmp_path / "dev")
    pub = current(rt)
    assert man["publication_id"] == pub["publication_id"] and man["versions"]["prompts"]["assessment"]
    manifest, preds = E.load_export(tmp_path / "dev")
    holdout = set(pub["case_views"]) - set(pub["memory_by_case"])
    assert holdout and not holdout & set(preds)                                         # holdout never exported
    early = set(corpus.early_evaluation_members)
    assert all(not early & set(rt.db.get_object("case_memory", p["memory_id"])["member_references"])
               for p in preds.values())
    xw = crosswalk_for(rt, pub["closure"]["release_id"])
    # no judgements at all → not_performed, every target unmeasured
    none = E.evaluate_offline(tmp_path / "dev", None, xw)
    assert none["diagnostic_evaluation"] == "not_performed"
    assert all(v["agreement"] == "unmeasured" for v in none["by_target"].values())
    cid, p = next((c, p) for c, p in sorted(preds.items()) if p["memberships"] and p["memberships"][0]["support"])
    ship = p["memberships"][0]
    ref = ship["support"][0]
    review = {"kind": "independent_review", "reviewer": "test-reviewer", "adjudicated": True}
    rows = [
        {"judgement_id": "J1", "case_id": cid, "target": "membership", "issue_id": ship["issue_id"],
         "expected": ship["state"], "provenance": review,
         "evidence": [{"filename": next(r["filename"] for r in xw["rows"] if r["original_id"] == ref["original_id"]),
                       "quote": ref["quote"][:40]}]},
        {"judgement_id": "J2", "case_id": cid, "target": "service_state", "expected": "definitely-not-a-state",
         "provenance": review},
        {"judgement_id": "J3", "case_id": cid, "target": "factor_present", "expected": "present", "provenance": review},
        {"judgement_id": "J4", "case_id": cid, "target": "membership", "issue_id": ship["issue_id"],
         "expected": "excluded", "provenance": {"kind": "independent_review", "adjudicated": False}},
        {"judgement_id": "J5", "case_id": cid, "target": "membership", "expected": "included",
         "provenance": {"kind": "author_label"}},                                       # planted label → refused
        {"judgement_id": "J6", "case_id": sorted(holdout)[0], "target": "service_state", "expected": "unknown",
         "provenance": review},                                                         # holdout → refused
        {"judgement_id": "J7", "case_id": cid, "target": "commitment", "expected": {"status": "unknown"},
         "dates": {"promised": "31/13/2026"}, "provenance": review},                    # bad date → refused
    ]
    jp = tmp_path / "judgements.jsonl"
    jp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rep = E.evaluate_offline(tmp_path / "dev", jp, xw, out_path=tmp_path / "report.json")
    by = {r["judgement_id"]: r for r in rep["rows"]}
    assert by["J1"]["outcome"] == "agree" and by["J1"]["evidence_found_in_prediction"] is True
    assert by["J2"]["outcome"] == "disagree"
    assert by["J3"]["outcome"] == "needs_semantic_adjudication"                         # no label string-matching
    assert by["J4"]["outcome"] == "unscored"
    assert {r["judgement_id"] for r in rep["refused"]} == {"J5", "J6", "J7"}
    assert rep["by_target"]["membership"]["agreement"] == 1.0
    assert rep["by_target"]["commitment"]["agreement"] == "unmeasured"
    assert rep["diagnostic_evaluation"] == "performed_on_adjudicated_subset"
    # the frozen export is tamper-evident
    (tmp_path / "dev" / "predictions.jsonl").write_text("{}\n")
    with pytest.raises(ValueError):
        E.load_export(tmp_path / "dev")


def test_v07_unit_and_temporal_crosswalk():
    from sdbackend.intel.evaluate import norm_date, norm_money
    assert norm_date("17/10/2026") == norm_date("17 Oct 2026") == norm_date("2026-10-17") == "2026-10-17"
    assert norm_money("A$25") == 2500 and norm_money("AUD 1,234.50") == 123450 and norm_money(12.5) == 1250
    with pytest.raises(ValueError):
        norm_money("about twenty dollars")
