"""Antigravity CLI adapter: terminal-status parsing, schema/usage handling, isolation gate, quota pause, refusal of
untransportable stages, timeout/cancellation. Uses a fake `agy` executable (no network, no sign-in)."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from sdbackend.models import agy_cli
from sdbackend.models.agy_cli import AgyProvider, parse_usage_table
from sdbackend.models.base import (GenRequest, Message, OutputInvalid, Part, ProviderUnavailable, QuotaExhausted,
                                   ToolSpec, TransientError)

FAKE = r'''#!{py}
import json, os, sys, time
args = sys.argv[1:]
mode = os.environ.get("FAKE_AGY_MODE", "ok")
if args == ["--version"]:
    print("1.4.2"); sys.exit(0)
if args[:2] == ["-p", "/usage"]:
    pct = os.environ.get("FAKE_AGY_PCT", "80")
    print(f"Gemini Models\tWeekly Limit Remaining\t{pct}%\t2099-10-25T07:23:25Z")
    print(f"Gemini Models\tFive Hour Limit Remaining\t{pct}%\t2099-10-18T12:23:25Z"); sys.exit(0)
model = args[args.index("--model") + 1]
with open(os.environ["FAKE_AGY_LOG"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "args": args, "hooks": os.path.isfile(".agents/hooks.json")}) + "\n")
def ev(e): print(json.dumps(e), flush=True)
ev({"event": "init", "init": {"model": "gemini-3.8-flash-high" if mode == "wrong_model" else model,
                               "cwd": os.getcwd(), "tools": ["finish", "view_file"], "permission_mode": "request-review"}})
if mode == "hang":
    time.sleep(60)
if mode == "noresult":
    sys.exit(3)
if mode == "tool":
    ev({"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool", "tool_name": "view_file"}})
usage = {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 110}
if mode == "quota":
    ev({"event": "result", "result": {"status": "ERROR", "error": "RESOURCE_EXHAUSTED: model quota exceeded", "usage": {}}}); sys.exit(1)
if mode == "canceled":
    ev({"event": "result", "result": {"status": "CANCELED", "usage": {}}}); sys.exit(0)
resp = "not json at all" if mode == "badjson" else '{"claims": [{"quote": "abc"}]}'
ev({"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS", "response": resp, "num_turns": 1,
                                   "duration_seconds": 0.1, "usage": usage}})
if mode == "linger":
    time.sleep(120)                     # the real CLI can hang in shutdown after emitting its result
'''


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "agy"
    exe.write_text(FAKE.replace("{py}", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_AGY_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("SDB_AGY_QUOTA_CHECK_EVERY", "0")
    monkeypatch.setattr(agy_cli, "AGY_SETTINGS", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"useG1Credits": False}))

    def make(**kw):
        return AgyProvider(binary=str(exe), work_root=str(tmp_path / "work"), trace_dir=tmp_path / "traces",
                           attest_overages_never=kw.pop("attest", True), **kw)
    make.tmp = tmp_path
    return make


SCHEMA = {"type": "object", "properties": {"claims": {"type": "array"}}, "required": ["claims"]}


def _req(**kw):
    return GenRequest(role="case_reader", system="sys", messages=[Message("user", [Part.t("SOURCES: x")])],
                      response_schema=SCHEMA, prompt_version="t", **kw)


def test_success_parses_result_usage_and_isolated_workspace(fake):
    p = fake()
    r = p.generate(_req())
    assert r.data == {"claims": [{"quote": "abc"}]} and r.provider == "antigravity-cli"
    assert r.model == "gemini-3.8-flash-low" and r.usage.input_tokens == 100 and r.usage.source == "provider"
    call = json.loads((fake.tmp / "calls.jsonl").read_text().splitlines()[-1])
    assert call["hooks"] and Path(call["cwd"]).parent.parent == fake.tmp / "work"
    a = call["args"]
    assert a[a.index("--effort") + 1] == "low" and "--disable-slash-commands" in a and "--sandbox" in a
    assert "OUTPUT CONTRACT" in a[1] and "--json-schema" not in a     # prompt schema mode by default
    gate = (Path(call["cwd"]) / ".agents" / "gate.py").read_text()
    assert '"finish"' in gate and "deny" in gate
    trace = json.loads(next((fake.tmp / "traces").glob("*.jsonl")).read_text().splitlines()[-1])
    assert trace["status"] == "SUCCESS" and trace["init_model"] == "gemini-3.8-flash-low" and "prompt" not in trace


def test_terminal_status_not_exit_code(fake, monkeypatch):
    p = fake()
    monkeypatch.setenv("FAKE_AGY_MODE", "quota")
    with pytest.raises(QuotaExhausted):
        p.generate(_req())
    monkeypatch.setenv("FAKE_AGY_MODE", "canceled")      # exit 0 but CANCELED
    with pytest.raises(TransientError):
        p.generate(_req())
    monkeypatch.setenv("FAKE_AGY_MODE", "noresult")
    with pytest.raises(TransientError):
        p.generate(_req())
    monkeypatch.setenv("FAKE_AGY_MODE", "badjson")
    with pytest.raises(OutputInvalid):
        p.generate(_req())


def test_model_substitution_and_executed_tools_refused(fake, monkeypatch):
    p = fake()
    monkeypatch.setenv("FAKE_AGY_MODE", "wrong_model")
    with pytest.raises(ProviderUnavailable):
        p.generate(_req())
    monkeypatch.setenv("FAKE_AGY_MODE", "tool")
    with pytest.raises(OutputInvalid):
        p.generate(_req())


def test_untransportable_stages_refused(fake):
    p = fake()
    with pytest.raises(ProviderUnavailable):
        p.generate(_req(tools=[ToolSpec("search", "d", {"type": "object"})]))
    img = GenRequest(role="r", system="s", messages=[Message("user", [Part("image", data=b"x", mime="image/png")])])
    with pytest.raises(ProviderUnavailable):
        p.generate(img)


def test_overage_attestation_and_settings_required(fake):
    with pytest.raises(ProviderUnavailable):
        fake(attest=False)
    (fake.tmp / "settings.json").write_text(json.dumps({"useG1Credits": True}))
    with pytest.raises(ProviderUnavailable):
        fake()


def test_quota_floor_pauses(fake, monkeypatch):
    p = fake()
    monkeypatch.setenv("FAKE_AGY_PCT", "2")
    with pytest.raises(QuotaExhausted):
        p.check_quota()
    monkeypatch.setenv("FAKE_AGY_PCT", "2")
    import re as _re
    exe = Path(p.binary)
    exe.write_text(exe.read_text().replace("2099-10-18T12:23:25Z", "2000-01-01T00:00:00Z").replace("2099-10-25T07:23:25Z", "2000-01-01T00:00:00Z"))
    p.check_quota()                                  # readings past their own reset time are stale: no pause
    assert parse_usage_table("Gemini Models\tWeekly Limit Remaining\t55%\tX")["Gemini Models"][
        "Weekly Limit Remaining"]["remaining_pct"] == 55.0


def test_timeout_kills_and_cancellation(fake, monkeypatch):
    monkeypatch.setenv("SDB_AGY_TIMEOUT_S", "1")
    p = fake()
    p.timeout_s = -28.0                                  # host deadline = timeout + 30 s → 2 s
    monkeypatch.setenv("FAKE_AGY_MODE", "hang")
    t = time.monotonic()
    with pytest.raises(TransientError):
        p.generate(_req())
    assert time.monotonic() - t < 20 and not agy_cli._children
    assert agy_cli.terminate_children() == 0


def test_result_event_ends_a_lingering_invocation(fake, monkeypatch):
    p = fake()
    monkeypatch.setenv("FAKE_AGY_MODE", "linger")
    t = time.monotonic()
    r = p.generate(_req())
    assert r.data == {"claims": [{"quote": "abc"}]} and time.monotonic() - t < 30 and not agy_cli._children
    trace = json.loads(next((fake.tmp / "traces").glob("*.jsonl")).read_text().splitlines()[-1])
    assert trace["killed_after_result"] is True


def test_composite_digest_is_order_and_bundle_bound():
    from sdbackend.intel.corpus import basis_summary, composite_manifest_sha256
    a = {"corpus_id": "x", "bundles": [{"bundle_id": "a", "delivery_manifest_sha256": "1"},
                                       {"bundle_id": "b", "delivery_manifest_sha256": "2"}]}
    b = {"corpus_id": "x", "bundles": list(reversed(a["bundles"]))}
    assert composite_manifest_sha256(a) != composite_manifest_sha256(b)
    one = {"path": "p", "sha256": "s"}
    assert basis_summary(one) is one and basis_summary([one, one])["path"] == "p + p"
