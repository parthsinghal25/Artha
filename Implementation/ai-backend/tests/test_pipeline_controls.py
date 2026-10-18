"""R06/R07/R08 control tests with an isolated scripted provider (a TEST DOUBLE, not a model and never inference).

The double answers by request role so the real runner, gateway, ledger, leases, budgets and resolver run end to
end on the rehearsal store in a temporary directory.
"""
import pytest

from sdbackend.models.base import GenResult, OutputInvalid, ProviderUnavailable, Usage

CASE = "C-e1da28538652e263887a"


def _final(cites=()):
    return {"targets": [], "timeline": [{"event_type": "other", "proposition": "specimen", "date": None,
                                         "state": "uncertain", "citations": list(cites)}] if cites else [],
            "administrative": {"value": "unknown", "reason": "", "citations": []}, "administrative_history": [],
            "merits": [], "service": [], "responses": [], "missing_facts": [], "contrary_evidence": [],
            "alternatives": [], "summary": "test double"}


class ScriptedProvider:
    name, model = "test-double", "scripted"

    def __init__(self, rt, *, final=None, deny=False, on_call=None):
        self.rt, self.final, self.deny, self.on_call, self.calls = rt, final, deny, on_call, []

    def generate(self, req):
        self.calls.append(req.role)
        if self.on_call:
            self.on_call(self, req)
        if self.deny:
            raise ProviderUnavailable("access denied (403) by test double")
        data, text = None, "DONE"
        if req.role == "source_interpreter":
            data, text = {"atoms": []}, '{"atoms": []}'
        elif req.role == "collective_investigator":
            data, text = {"findings": []}, '{"findings": []}'
        elif req.role == "case_investigator_final":
            data = self.final(self, req) if callable(self.final) else (self.final or _final())
            text = "{}"
        return GenResult(provider=self.name, model=self.model, text=text, data=data,
                         usage=Usage(10, 0, 0, 5, 15, "provider"), latency_ms=1)


@pytest.fixture()
def pipeline(ingested, monkeypatch):
    rt, summary = ingested
    from sdbackend.models import gateway as g
    providers = {}

    def make(name, settings):
        return providers[name]
    monkeypatch.setattr(g, "make_provider", make)
    monkeypatch.setattr(g.ModelGateway, "fingerprint",
                        lambda self: {"provider": self.provider.name, "model": self.model, "sha256": "f" * 64})
    return rt, summary, providers


def _runs(rt):
    return [dict(r) for r in rt.db.connect().execute("SELECT * FROM runs ORDER BY created_at").fetchall()]


def test_access_denied_gemini_falls_back_to_new_local_run(pipeline, monkeypatch):
    rt, summary, providers = pipeline
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    monkeypatch.setenv("SDB_GEMINI_FREE_TIER_ATTESTED", "1")
    monkeypatch.setenv("SDB_GEMINI_RPM", "1000")
    providers["gemini"] = ScriptedProvider(rt, deny=True)
    providers["gemma"] = ScriptedProvider(rt)
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="auto", stages=("source",), max_sources=2)
    assert [r["outcome"] for r in out["runs"]] == ["limited", "complete"]
    runs = _runs(rt)
    assert runs[0]["state"] == "limited" and runs[1]["parent_run_id"] == runs[0]["run_id"]
    assert out["runs"][1]["stages"]["s02"]["tasks"]["processed"] == 2
    denied = rt.db.connect().execute("SELECT status FROM usage_ledger WHERE run_id=?", (runs[0]["run_id"],)).fetchall()
    assert [r["status"] for r in denied] == ["ProviderUnavailable"]


def test_eta_threshold_routes_auto_to_local(rt, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    monkeypatch.setenv("SDB_GEMINI_FREE_TIER_ATTESTED", "1")
    monkeypatch.setenv("SDB_GEMINI_RPD", "100")
    monkeypatch.setenv("SDB_FALLBACK_MAX_ETA_SECONDS", "3600")
    from sdbackend.config import load_settings
    from sdbackend.models.gateway import choose_provider
    s = load_settings()
    if not (s.gemma_model_path / "config.json").is_file():
        pytest.skip("local weights absent")
    name, reason = choose_provider(s, "auto", None, expected_calls=500)
    assert name == "gemma" and "ETA" in reason
    assert choose_provider(s, "auto", None, expected_calls=10)[0] == "gemini"


def test_resume_continues_compatible_run_after_interrupt(pipeline):
    rt, summary, providers = pipeline
    state = {"n": 0}

    def crash(p, req):
        state["n"] += 1
        if state["n"] == 3:
            raise KeyboardInterrupt("simulated crash")
    providers["gemma"] = ScriptedProvider(rt, on_call=crash)
    from sdbackend.pipeline.runner import run_release_analysis
    with pytest.raises(KeyboardInterrupt):
        run_release_analysis("candidate", provider="gemma", stages=("source",), max_sources=4)
    first = _runs(rt)[0]
    assert first["state"] == "active"
    crashed = rt.db.connect().execute("SELECT status FROM usage_ledger WHERE run_id=? AND status='in_flight'",
                                      (first["run_id"],)).fetchall()
    assert len(crashed) == 1                           # the spent attempt survives the crash, usage unknown
    rt.db.connect().execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00Z' WHERE state='running'")
    providers["gemma"] = ScriptedProvider(rt)
    out = run_release_analysis("candidate", provider="gemma", stages=("source",), max_sources=4)
    rep = out["runs"][0]
    assert rep["run_id"] == first["run_id"] and rep["resumed"] and rep["outcome"] == "complete"
    assert rep["stages"]["s02"]["tasks"]["reused"] == 2 and rep["stages"]["s02"]["tasks"]["processed"] == 2
    usage = rep["stages"]["s02"]["usage"]
    assert usage["in_flight_or_crashed"] == 1 and usage["tokens_ok_calls"]["input"] == 40
    assert usage["tokens_all_attempts"]["input"] is None              # unknown (crashed) usage is never zero


def _case_handles(rt):
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.pipeline.passages import passages
    from sdbackend.policy import pipeline_authority
    repo = Repo(rt, served_release(rt), pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    out = []
    for sid in repo.case_source_ids(CASE):
        for p in passages(rt.db.get_object("source", sid)["data"]["text"]):
            out.append((f"{sid}~{p['handle']}", p["text"]))
    return out


def test_bounded_read_repair_and_budget(pipeline, monkeypatch):
    rt, summary, providers = pipeline
    from sdbackend.pipeline import case_assess
    monkeypatch.setattr(case_assess, "PREFETCH_CHAR_BUDGET", 300)        # leave most passages unread
    handles = _case_handles(rt)
    target, text = handles[-1]
    cite = {"passage_handle": target, "quote": text.strip()[:25]}
    providers["gemma"] = ScriptedProvider(rt, final=lambda p, req: _final([cite]))
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="gemma", stages=("case",))
    s03 = out["runs"][0]["stages"]["s03"]
    assert s03["tasks"]["processed"] == 1 and s03["outputs"]["read_repairs"] == 1
    stage = rt.db.list_objects("stage_s03")[0]
    assert stage["read_repair"]["performed"] and target in stage["read_repair"]["handles"]
    assert len(stage["timeline_events"]) == 1 and stage["requests"] <= case_assess.MAX_REQUESTS
    assert stage["read_coverage"]["unread_passages"] > 0                 # partial coverage stays visible


def test_invalid_finalisation_exhausts_budget_as_limited(pipeline):
    rt, summary, providers = pipeline
    providers["gemma"] = ScriptedProvider(rt, final=lambda p, req: {"not": "the dto"})
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="gemma", stages=("case",))
    rep = out["runs"][0]
    assert rep["stages"]["s03"]["tasks"]["limited"] == 1 and rep["outcome"] == "partial"
    calls = rt.db.connect().execute("SELECT COUNT(*) AS n FROM usage_ledger WHERE run_id=?", (rep["run_id"],)).fetchone()
    assert calls["n"] <= 8 and rt.db.list_objects("stage_s03") == []


def test_cancellation_mid_case_writes_nothing(pipeline):
    rt, summary, providers = pipeline
    from sdbackend.pipeline import control

    def cancel(p, req):
        if req.role == "case_investigator":
            run_id = rt.db.connect().execute("SELECT run_id FROM runs WHERE state='active'").fetchone()["run_id"]
            control.cancel_run(rt, run_id, "operator cancel (test)")
    providers["gemma"] = ScriptedProvider(rt, on_call=cancel)
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="gemma", stages=("case",))
    assert out["runs"][0]["outcome"] == "cancelled"
    assert rt.db.list_objects("stage_s03") == [] and _runs(rt)[0]["state"] == "cancelled"


def test_pacer_shares_reservations_bounds_waits_and_refuses_oversize(rt):
    from sdbackend.models.gateway import Pacer, RequestTooLarge, SustainedQuota
    a = Pacer(2, 1000, 100, rt.db, "gemini-developer-api", max_wait=1)
    b = Pacer(2, 1000, 100, rt.db, "gemini-developer-api", max_wait=1)     # another worker/process
    a.reserve(100)
    b.reserve(100)
    with pytest.raises(SustainedQuota):
        a.reserve(100, sleep=lambda s: None)                                # RPM shared across instances
    with pytest.raises(RequestTooLarge):
        a.reserve(5000)
    with pytest.raises(ProviderUnavailable):
        Pacer(0, 1000, 100, rt.db, "x")


def test_token_estimate_counts_tool_history_and_schema():
    from sdbackend.models.base import GenRequest, Message, Part, ToolResult
    from sdbackend.models.gateway import estimate_tokens
    from sdbackend.pipeline.case_assess import FINAL_SCHEMA
    from sdbackend.pipeline.tools import TOOL_SPECS
    base = GenRequest("r", "s", [Message("user", [Part.t("x")])], max_output_tokens=0)
    rich = GenRequest("r", "s", [Message("user", [Part.t("x")]),
                                 Message("tool", tool_results=[ToolResult("c", "t", {"text": "y" * 3000})])],
                      tools=TOOL_SPECS, response_schema=FINAL_SCHEMA, max_output_tokens=0)
    assert estimate_tokens(rich) > estimate_tokens(base) + 1000


def test_gemma_malformed_tool_syntax_is_bounded_output_invalid():
    from sdbackend.models.gemma_mlx import parse_gemma_args
    with pytest.raises(OutputInvalid):
        parse_gemma_args('query:<|"|>unterminated')


def test_resolver_rules_on_real_passages(ingested):
    rt, summary = ingested
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.pipeline.case_assess import materialise
    from sdbackend.pipeline.tools import ToolHost, build_index
    from sdbackend.policy import pipeline_authority
    rv = served_release(rt)
    repo = Repo(rt, rv, pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    host = ToolHost(rt, repo, run_id="RUN-t", index_version=build_index(rt, rv.release_id), case_scope=[CASE])
    handles = _case_handles(rt)
    (h1, t1), (h2, t2) = handles[0], next(x for x in handles if x[0].split("~")[0] != handles[0][0].split("~")[0])
    c1, c2 = {"passage_handle": h1, "quote": t1.strip()[:20]}, {"passage_handle": h2, "quote": t2.strip()[:20]}
    read = {h1, h2}
    final = _final()
    final["targets"] = [{"target_key": "T1", "kind": "service_action", "label": "Rollover", "citations": [c1]},
                        {"target_key": "T2", "kind": "other", "label": "uncited", "citations": []}]
    final["service"] = [
        {"target_key": "T1", "value": "confirmed_complete", "reason": "", "assertion_citations": [c1],
         "confirmation_citations": [c1], "contradictory_citations": []},          # same source: not independent
        {"target_key": "T2", "value": "confirmed_complete", "reason": "", "assertion_citations": [c1],
         "confirmation_citations": [c2], "contradictory_citations": [c2]}]        # contradiction blocks
    final["responses"] = [{"target_key": "T1", "dimension": "service", "respondent_role": "member",
                           "position": "accept", "reason": "", "citations": [{"passage_handle": h1, "quote": t1.strip()[:20]}],
                           "occasion_citations": []}]
    final["timeline"] = [{"event_type": "other", "proposition": "x", "date": None, "state": "supported",
                          "citations": [{"passage_handle": h1.split("~")[0] + "~P999", "quote": "nope"},
                                        {"passage_handle": "SRC-NOT-REAL~P1", "quote": "x"},
                                        {"passage_handle": handles[-1][0], "quote": handles[-1][1][:10]}]}]
    out = materialise(final, host, CASE, read, rt.contracts, "SSV-x")
    svc = out["state_projection_candidate"]["service"]
    assert svc[0]["value"] == "attempted" and svc[0]["target_reference"].startswith("TGT-")
    assert svc[1]["value"] == "conflicting" and svc[1]["target_reference"] is None
    assert out["timeline_events"] == [] and len(out["dropped_citations"]) >= 3   # unread/nonexistent never accepted
    assert out["state_projection_candidate"]["responses"][0]["position"] in ("accept", "update_only")
    thanks = materialise(dict(final, responses=[dict(final["responses"][0], citations=[])]), host, CASE, read,
                         rt.contracts, "SSV-x")
    assert thanks["state_projection_candidate"]["responses"][0]["position"] == "unknown"


def test_full_stage_chain_runs_fenced_and_reports_each_stage(pipeline):
    rt, summary, providers = pipeline
    providers["gemma"] = ScriptedProvider(rt)
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="gemma", stages=("source", "ledger", "case", "collective"),
                               max_sources=3)
    rep = out["runs"][0]
    assert rep["outcome"] == "complete", rep["task_states"]
    for st in ("s02", "s03", "s04", "s05"):
        assert rep["stages"][st]["tasks"]["processed"] >= 1, st
    assert rep["stages"]["s05"]["outputs"] == {"findings": 0, "dropped_citations": 0}   # no finding is required
    assert len(rt.db.list_objects("stage_s04")) == 1 and len(rt.db.list_objects("stage_s05")) == 1
    again = run_release_analysis("candidate", provider="gemma", stages=("source", "ledger", "case", "collective"),
                                 max_sources=3)["runs"][0]
    assert again["resumed"] is False or again["stages"]["s05"]["tasks"]["reused"] == 1


def test_policy_change_mid_run_revokes_worker_and_seals_nothing(pipeline, monkeypatch, tmp_path):
    rt, summary, providers = pipeline
    import json as _json
    import sdbackend.policy as policy

    def change_policy(p, req):
        if req.role == "case_investigator_final":
            alt = tmp_path / "policy.json"
            body = _json.loads(policy.POLICY_PATH.read_text())
            body["views"]["complaints-leadership"]["denied_case_ids"] = [CASE]
            alt.write_text(_json.dumps(body))
            monkeypatch.setattr(policy, "POLICY_PATH", alt)
            monkeypatch.setattr(policy.load_policy, "__defaults__", (alt,))
    providers["gemma"] = ScriptedProvider(rt, on_call=change_policy)
    from sdbackend.pipeline.runner import run_release_analysis
    out = run_release_analysis("candidate", provider="gemma", stages=("case",))
    assert out["runs"][0]["outcome"] == "authority_changed"
    assert rt.db.list_objects("stage_s03") == [] and _runs(rt)[0]["state"] == "expired"


def test_s02_schema_repair_states_the_defect(ingested):
    """Measured 15 Oct: an unchanged repair request made greedy local decoding repeat the same invalid reply."""
    rt, summary = ingested
    from sdbackend.pipeline.source_interpret import interpret
    src = rt.db.list_objects("source")[0]
    seen = []

    class Gw:
        def call(self, req, *, task_id, attempt):
            seen.append(req)
            data = {"atoms": [{}] * 41} if attempt == 1 else {"atoms": []}
            return GenResult(provider="test-double", model="scripted", text="{}", data=data,
                             usage=Usage(10, 0, 0, 5, 15, "provider"), latency_ms=1)
    out = interpret(Gw(), src, None, rt.contracts, task_id="T-s02")
    assert out["attempts"] == 2 and len(seen[1].messages) == len(seen[0].messages) + 1
    assert "41 atoms exceeds the maximum of 40" in seen[1].messages[-1].parts[0].text


def test_s03_dto3_accepts_measured_gemma_shape_without_weakening_evidence(ingested):
    """Measured failure on the real chain: long model target_key + omitted occasion_citations. s03-dto-3.0.0 accepts
    the shape (host assigns TGT- ids; omitted citation arrays are []), and evidence checks stay strict."""
    rt, summary = ingested
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.pipeline.case_assess import dto_errors, materialise, normalise
    from sdbackend.pipeline.tools import ToolHost, build_index
    from sdbackend.policy import pipeline_authority
    rv = served_release(rt)
    repo = Repo(rt, rv, pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    host = ToolHost(rt, repo, run_id="RUN-t", index_version=build_index(rt, rv.release_id), case_scope=[CASE])
    (h1, t1) = _case_handles(rt)[0]
    c1 = {"passage_handle": h1, "quote": t1.strip()[:20]}
    final = _final()
    key = "Disputed Processing Cause"
    final["targets"] = [{"target_key": key, "kind": "decision", "label": key, "citations": [c1]}]
    final["merits"] = [{"target_key": key, "value": "unknown", "reason": "", "citations": []}]
    final["responses"] = [{"target_key": "disputed processing  cause", "dimension": "decision",
                           "respondent_role": "member", "position": "accept", "reason": "", "citations": []}]
    final["service"] = [{"target_key": key, "value": "confirmed_complete", "reason": "",
                         "assertion_citations": [c1]}]                       # confirmation arrays omitted
    data, notes = normalise(final)
    assert dto_errors(data) == [], dto_errors(data)
    assert any("occasion_citations" in n for n in notes) and any("confirmation_citations" in n for n in notes)
    assert final["responses"][0].get("occasion_citations") is None          # caller's object untouched
    out = materialise(data, host, CASE, {h1}, rt.contracts, "SSV-x")
    proj = out["state_projection_candidate"]
    ref = proj["merits"][0]["target_reference"]
    assert ref and ref.startswith("TGT-") and ref == proj["responses"][0]["target_reference"]   # label alias resolves
    assert proj["responses"][0]["position"] == "unknown"                     # no citation → no position
    assert proj["service"][0]["value"] != "confirmed_complete"               # omitted confirmation is not proof
    again = materialise(normalise(final)[0], host, CASE, {h1}, rt.contracts, "SSV-x")
    assert again["state_projection_candidate"]["merits"][0]["target_reference"] == ref     # stable
    unread = materialise(data, host, CASE, set(), rt.contracts, "SSV-x")    # unread passage still rejected
    assert unread["state_projection_candidate"]["merits"][0]["target_reference"] is None


def test_r06_hung_model_call_times_out_defers_task_and_never_stages(pipeline, monkeypatch):
    """A call past its wall-clock deadline is recorded as `timeout`, the task is deferred (not retried in place,
    not staged), and the late reply — when it arrives — is discarded."""
    import threading
    import time
    rt, summary, providers = pipeline
    monkeypatch.setenv("SDB_MODEL_CALL_TIMEOUT_S", "0.3")
    release = threading.Event()

    def hang(p, req):
        release.wait(5)                                   # a hung local call (bounded so the test thread ends)
    providers["gemma"] = ScriptedProvider(rt, on_call=hang)
    from sdbackend.pipeline.runner import run_release_analysis
    t0 = time.monotonic()
    out = run_release_analysis("candidate", provider="gemma", stages=("source",), max_sources=2)
    assert time.monotonic() - t0 < 4                      # bounded by the deadline, not by the hung call
    release.set()
    rep = out["runs"][0]
    assert rep["outcome"] != "complete" and rt.db.list_objects("stage_s02") == []
    rows = rt.db.connect().execute("SELECT status, error_class FROM usage_ledger WHERE run_id=?",
                                   (rep["run_id"],)).fetchall()
    assert [r["status"] for r in rows] == ["timeout"] and "deadline" in rows[0]["error_class"]
    states = {r["state"] for r in rt.db.connect().execute("SELECT state FROM tasks WHERE run_id=?", (rep["run_id"],))}
    assert "running" not in states and "staged" not in states   # lease released, nothing sealed


def test_dto_compat_bare_source_handles_and_wrapped_findings_keep_strict_evidence():
    """Measured Gemma shapes (real run 15 Oct): bare `SRC-x` handles in S03 and a top-level finding object without
    contrary_citations in S05. Shape is repaired; evidence rules are not loosened."""
    from sdbackend.pipeline.collective import VALIDATOR
    from sdbackend.pipeline.dto_compat import canonical_handle, normalise_collective
    permitted = {"SRC-a~P1": "The form was received on 3 Feb.", "SRC-a~P2": "Nothing about forms here.",
                 "SRC-b~P1": "The form was received on 3 Feb."}
    assert canonical_handle("SRC-a", "received on 3 Feb", permitted)[0] == "SRC-a~P1"
    assert canonical_handle("[SRC-a~P2]", "Nothing", permitted)[0] == "SRC-a~P2"
    assert canonical_handle("SRC-a", "form", permitted)[0] is None            # 2 passages match: ambiguous
    assert canonical_handle("SRC-a", "invented words", permitted)[0] is None  # no exact quote: dropped
    assert canonical_handle("SRC-c", "received on 3 Feb", permitted)[0] is None   # not permitted (unread/unshown)
    assert canonical_handle("SRC-a~P9", "received", permitted)[0] is None     # wrong passage never re-pointed
    raw = {"title": "t", "claim": "c", "taxonomy_label": "x", "dimension": "handling_factor",
           "members": [{"case_id": "C1", "state": "included", "association": "active", "reason": "r"}]}
    fixed, notes = normalise_collective(raw)
    assert not list(VALIDATOR.iter_errors(fixed)) and fixed["findings"][0]["members"][0]["citations"] == []
    assert notes and normalise_collective({"findings": [], "rogue": 1})[0].get("rogue") == 1   # still rejected


def test_s03_cited_response_becomes_a_shared_response_record_and_silence_none(ingested):
    rt, summary = ingested
    from sdbackend.api.repo import Repo, served_release
    from sdbackend.engine.mx import response_status
    from sdbackend.pipeline.case_assess import materialise
    from sdbackend.pipeline.tools import ToolHost, build_index
    from sdbackend.policy import pipeline_authority
    rv = served_release(rt)
    repo = Repo(rt, rv, pipeline_authority(), "2026-10-01T00:00:00+10:00", "inclusive_cutoff")
    host = ToolHost(rt, repo, run_id="RUN-t", index_version=build_index(rt, rv.release_id), case_scope=[CASE])
    (h1, t1) = _case_handles(rt)[0]
    c1 = {"passage_handle": h1, "quote": t1.strip()[:20]}
    final = _final()
    final["targets"] = [{"target_key": "T1", "kind": "decision", "label": "Decision", "citations": [c1]}]
    base = {"target_key": "T1", "dimension": "decision", "respondent_role": "member", "reason": ""}
    final["responses"] = [dict(base, position="disagree", citations=[c1], occasion_citations=[c1]),
                          dict(base, target_key="T9", position="accept", citations=[c1], occasion_citations=[c1]),
                          dict(base, position="accept", citations=[c1], occasion_citations=[])]   # no occasion
    out = materialise(final, host, CASE, {h1}, rt.contracts, "SSV-x")
    recs = out["mx_records"]
    assert len(recs) == 2
    for r in recs:
        rt.contracts.check("member_outcome_response", r)
        assert r["common"]["member_identity_state"] == "unknown"           # identity never invented
    by_state = {response_status(r): r for r in recs}
    assert by_state["confirmed"]["expressed_position"] == "disagree"
    assert by_state["confirmed"]["target_id"].startswith("TGT-")
    assert by_state["candidate"]["target_identity_state"] == "unidentified"   # unresolved T9: candidate only


def test_s05_repair_states_the_defect_instead_of_echoing_the_reply():
    from sdbackend.pipeline.collective import VALIDATOR, _defect
    bare = [{"case_id": "C1", "state": "included", "association": "active", "reason": "r", "citations": []}]
    msg = _defect(next(VALIDATOR.iter_errors(bare)))
    assert msg == "at top level: expected object" and "C1" not in msg
    msg = _defect(next(VALIDATOR.iter_errors({"findings": [{"title": "t"}]})))
    assert msg.startswith("at findings/0:") and "missing" in msg
