"""Regression tests for the six authored-tooling repairs (review 2026-10-16-authored-tooling-review, R1–R6). Each
reproduces the reported failure through the complete checker/build path (or the real receiver tool) and asserts the
repaired behaviour."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from authored_helpers import EXAMPLE, Input, codes
from sdgen.authored.export import build
from sdgen.authored.verify import mechanical_checks, read_tree, verify_candidate

TOOL = Path(__file__).resolve().parents[1] / "tools/authored_receiver_check.py"
BACKEND = Path(__file__).resolve().parents[2] / "ai-backend"
BACKEND_PY = BACKEND / ".venv/bin/python"
needs_backend = pytest.mark.skipif(not BACKEND_PY.exists(), reason="backend interpreter not present")


def warnings(res):
    return {f["code"] for f in res["findings"] if f["severity"] == "warning"}


def metrics(inp, tmp_path):
    res = build(inp.root, tmp_path / "out")
    assert res["ok"], [f for f in res["findings"] if f["severity"] == "error"]
    c = Path(res["candidate_dir"])
    return c, json.loads((c / "private/EXPECTED-METRICS.json").read_text())


def val(m, cp, k):
    return m["checkpoints"][cp][k]["value"], m["checkpoints"][cp][k]["contributors"]


# ---------------------------------------------------------------- R1 known intraday order
def test_r1_payment_before_same_day_approval_is_rejected(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0501", lambda r: r.update(occurred="2026-05-08T16:00:00+10:00"))
    inp.event("FX-FIN-0502", lambda r: r.update(occurred="2026-05-08T09:00:00+10:00"))
    res = inp.check()
    assert not res["ok"] and "payment_before_approval" in codes(res)


def test_r1_valid_same_day_order_is_accepted_and_survives_into_the_export(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0501", lambda r: r.update(occurred="2026-05-08T09:00:00+10:00"))
    inp.event("FX-FIN-0502", lambda r: r.update(occurred="2026-05-08T16:00:00+10:00"))
    c, _ = metrics(inp, tmp_path)
    csv = (c / "runtime/intake-bundle/originals/tables/fin-export-FX-C05-2026-05-26.csv").read_text()
    assert "event_time" in csv.splitlines()[0]
    assert "2026-05-08T09:00:00+10:00" in csv and "2026-05-08T16:00:00+10:00" in csv
    assert verify_candidate(c)["failed"] == []


def test_r1_reversal_before_same_day_payment_is_rejected(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0502", lambda r: r.update(occurred="2026-05-12T15:00:00+10:00"))
    inp.event("FX-FIN-0503", lambda r: r.update(occurred="2026-05-12T10:00:00+10:00"))
    assert "reversal_before_payment" in codes(inp.check())


def test_r1_date_only_same_day_pair_stays_ambiguous_not_invented(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0502", lambda r: r.update(occurred="2026-05-08"))      # same day as the date-only approval
    res = inp.check()
    assert res["ok"], [f for f in res["findings"] if f["severity"] == "error"]
    assert "ordering_ambiguous" in warnings(res)


def test_r1_invalid_same_day_lifecycle_is_rejected_valid_is_accepted(tmp_path):
    bad = Input(tmp_path / "a")
    bad.event("FX-EV-0502", lambda r: r.update(occurred="2026-04-28T13:00:00+10:00"))   # closed before 13:47 receipt
    assert "lifecycle_order" in codes(bad.check())
    good = Input(tmp_path / "b")
    good.event("FX-EV-0502", lambda r: r.update(occurred="2026-04-28T17:00:00+10:00"))
    assert good.check()["ok"]


def test_r1_latest_approval_follows_known_time_not_event_id(tmp_path):
    def setup(root, when_revision):
        inp = Input(root)
        inp.event("FX-FIN-0501", lambda r: r.update(occurred="2026-05-08T09:00:00+10:00" if when_revision else "2026-05-08"))
        inp.add("private/events.jsonl", {"event_id": "FX-FIN-0500", "kind": "financial", "case_ids": ["FX-C05"],
                                         "occurred": when_revision or "2026-05-08", "state": "amount_revision",
                                         "obligation_id": "FX-OBL-05", "amount_minor": 25000, "category": "refund_entitlement"})
        return inp
    _, m = metrics(setup(tmp_path / "a", "2026-05-08T17:00:00+10:00"), tmp_path / "a")
    a = m["checkpoints"]["2026-05-31"]["approved_obligations_minor"]
    assert a["value"] == 50000 and "FX-FIN-0500" in a["events"] and "FX-FIN-0501" not in a["events"]
    _, m = metrics(setup(tmp_path / "b", None), tmp_path / "b")
    b = m["checkpoints"]["2026-05-31"]["approved_obligations_minor"]
    assert b["excluded_order_unknown"] == ["FX-OBL-05"] and b["value"] == 25000


# ---------------------------------------------------------------- R2 lifecycle evidence
def test_r2_missing_lifecycle_feed_is_unknown_not_open(tmp_path):
    inp = Input(tmp_path)
    inp.write("runtime/sources.jsonl", [r for r in inp.rows("runtime/sources.jsonl") if r["kind"] != "lifecycle_export"])
    _, m = metrics(inp, tmp_path)
    s = m["checkpoints"]["2026-09-30"]
    assert s["open_backlog_confirmed"]["value"] == 0 and s["closed_confirmed"]["value"] == 0
    assert s["status_unknown"]["value"] == 6 and s["received_without_evidenced_closure"]["value"] == 6
    assert s["lifecycle_coverage"] == {"cases_with_visible_lifecycle_rows": 0, "cases_arrived": 6}
    assert "open_backlog" not in s


def test_r2_reopened_then_closed_across_cutoffs(tmp_path):
    inp = Input(tmp_path)
    inp.add("private/events.jsonl", {"event_id": "FX-EV-0308", "kind": "case_closed", "case_ids": ["FX-C03"],
                                     "occurred": "2026-09-29T11:00:00+10:00"})
    _, m = metrics(inp, tmp_path)
    assert val(m, "2026-05-31", "closed_confirmed")[1] == ["FX-C03"]
    assert "FX-C03" in val(m, "2026-09-28", "closed_confirmed")[1]      # reopening not yet visible (feed of 30 Sep)
    assert "FX-C03" in val(m, "2026-09-30", "closed_confirmed")[1]      # reopened 3 Jun, closed again 29 Sep


def test_r2_outcome_letter_does_not_close_a_case(tmp_path):
    inp = Input(tmp_path)
    inp.add("runtime/sources.jsonl", {
        "source_id": "FX-SRC-0108", "kind": "letter", "semantic_type": "outcome_letter", "source_family": "S03",
        "format": "PDF", "filename": "outcome-letter-nair.pdf", "case_ids": ["FX-C01"], "author": "FX-STAFF-CM01",
        "clocks": {"created_at": "2026-09-30T15:00:00+10:00", "available_at": "2026-09-30T15:00:00+10:00"},
        "content": {"mode": "pdf_document", "title": "Outcome of your complaint",
                    "blocks": [{"p": "We have resolved your complaint and consider the matter closed."}]}})
    _, m = metrics(inp, tmp_path)
    assert "FX-C01" in val(m, "2026-09-30", "open_backlog_confirmed")[1]
    assert "FX-C01" not in val(m, "2026-09-30", "closed_confirmed")[1]


# ---------------------------------------------------------------- R3 cutoff boundaries
@pytest.mark.parametrize("avail,seconds", [
    ("2026-06-01T00:00:00+10:00", 0),        # exact next-day midnight: not in 31 May
    ("2026-05-31T23:59:59+10:00", 4500),     # immediately before
    ("2026-06-01T00:00:01+10:00", 0),        # immediately after
    ("2026-05-31", 4500),                    # day precision dated 31 May: in 31 May (interval convention)
    ("2026-06-01", 0),                       # day precision dated 1 June: not in 31 May
])
def test_r3_work_visibility_at_midnight(tmp_path, avail, seconds):
    inp = Input(tmp_path)
    inp.source("FX-SRC-0305", lambda r: r["clocks"].update(created_at=avail, available_at=avail))
    _, m = metrics(inp, tmp_path)
    assert val(m, "2026-05-31", "work_recorded_seconds")[0] == seconds


@pytest.mark.parametrize("avail,arrived", [("2026-04-29T00:00:00+10:00", False), ("2026-04-28T23:59:00+10:00", True),
                                           ("2026-04-28", True), ("2026-04-29", False)])
def test_r3_case_visibility_at_midnight(tmp_path, avail, arrived):
    inp = Input(tmp_path)
    inp.batch(lambda b: b.update(replay_checkpoints=["2026-04-28", "2026-05-31", "2026-09-28", "2026-09-30"]))
    inp.edit("runtime/cases.jsonl", "case_id", "FX-C05", lambda r: r["clocks"].update(available_at=avail, recorded_at=avail))
    _, m = metrics(inp, tmp_path)
    assert ("FX-C05" in val(m, "2026-04-28", "cases_arrived")[1]) is arrived


@pytest.mark.parametrize("avail,ok", [("2026-05-07T00:00:00+10:00", False), ("2026-05-06T23:59:59+10:00", True),
                                      ("2026-05-06", True)])
def test_r3_anchor_visibility_at_midnight(tmp_path, avail, ok):
    inp = Input(tmp_path)
    inp.source("FX-SRC-0503", lambda r: r["clocks"].update(created_at=avail, available_at=avail))
    inp.edit("private/expected-judgements.jsonl", "judgement_id", "FX-J05", lambda r: r.update(cutoff="2026-05-06"))
    res = inp.check()
    assert res["ok"] is ok and (("anchor_after_cutoff" in codes(res)) is not ok)


# ---------------------------------------------------------------- R4 exact copies
def _copy_of_note(inp, sid, text=None, **extra):
    src = next(r for r in inp.rows("runtime/sources.jsonl") if r["source_id"] == "FX-SRC-0503")
    c = json.loads(json.dumps(src))
    c.update(source_id=sid, filename=f"{sid}.txt", **extra)
    if text is not None:
        c["content"]["text"] = text
    inp.add("runtime/sources.jsonl", c)


def test_r4_changed_bytes_cannot_claim_exact_copy(tmp_path):
    inp = Input(tmp_path)
    _copy_of_note(inp, "FX-SRC-0505", "6 May RM: fee confirmed as charged under the old schedule.\n", copy_of_source_id="FX-SRC-0503")
    res = inp.check()
    assert not res["ok"] and "false_copy" in codes(res)


def test_r4_genuine_copy_and_truthful_derivation_pass_build_and_verify(tmp_path):
    inp = Input(tmp_path)
    _copy_of_note(inp, "FX-SRC-0505", copy_of_source_id="FX-SRC-0503")
    _copy_of_note(inp, "FX-SRC-0506", "FW: 6 May note\n\n6 May RM: The $200 admin fee may have been charged under the superseded fee schedule.\n",
                  derived_from={"source_id": "FX-SRC-0503", "relation": "forwarded"})
    c, _ = metrics(inp, tmp_path)
    m = {e["original_id"]: e for e in json.loads((c / "runtime/intake-bundle/delivery-manifest.json").read_text())["originals"]}
    assert m["FX-SRC-0505"]["lineage"]["relation"] == "exact_copy" and m["FX-SRC-0505"]["sha256"] == m["FX-SRC-0503"]["sha256"]
    assert m["FX-SRC-0506"]["lineage"]["relation"] == "derived" and m["FX-SRC-0506"]["lineage"]["derived_from"]["relation"] == "forwarded"
    assert verify_candidate(c)["failed"] == []


def test_r4_final_verification_catches_a_false_copy_in_delivered_bytes(tmp_path):
    inp = Input(tmp_path)
    _copy_of_note(inp, "FX-SRC-0505", copy_of_source_id="FX-SRC-0503")
    c, _ = metrics(inp, tmp_path)
    files = read_tree(c / "runtime/intake-bundle")
    man = json.loads(files["delivery-manifest.json"])
    e = next(x for x in man["originals"] if x["original_id"] == "FX-SRC-0505")
    files[e["file"]] = b"changed after build\n"
    import hashlib
    e["sha256"], e["size_bytes"] = hashlib.sha256(files[e["file"]]).hexdigest(), len(files[e["file"]])
    files["delivery-manifest.json"] = json.dumps(man).encode()
    files["INTAKE-FILES.sha256"] = "".join(f"{hashlib.sha256(b).hexdigest()}  {k}\n" for k, b in files.items()
                                           if k != "INTAKE-FILES.sha256").encode()
    res, _, _, _ = mechanical_checks(files, man, [], None)
    chk = next(i for i in res.items if i["check"] == "bytes_and_checksums")
    assert chk["status"] == "fail" and "exact copy" in " ".join(chk["failures"])


# ---------------------------------------------------------------- R5 receiver-check success
@pytest.fixture(scope="module")
def cand(tmp_path_factory):
    out = tmp_path_factory.mktemp("c")
    assert build(EXAMPLE, out)["ok"]
    return out / "fx-five-members-p1"


def run_backend(code, tmp_path, env=None, cwd=None):
    script = tmp_path / "snippet.py"
    script.write_text(textwrap.dedent(f"""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("arc", {str(TOOL)!r})
        arc = importlib.util.module_from_spec(spec); spec.loader.exec_module(arc)
    """) + textwrap.dedent(code))
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run([str(BACKEND_PY), str(script)], capture_output=True, text=True, timeout=300,
                          env={**base, **(env or {})}, cwd=cwd or tmp_path)


def run_tool(bundle, tmp_path, *extra, env=None):
    out = tmp_path / "rc.json"
    p = subprocess.run([str(BACKEND_PY), str(TOOL), str(bundle), str(out), *extra], capture_output=True, text=True,
                       timeout=300, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **(env or {})})
    return p.returncode, (json.loads(out.read_text()) if out.exists() else None), p.stderr


@needs_backend
def test_r5_clean_fixture_succeeds_with_disclosed_partial_and_f13_separate(cand, tmp_path):
    rc, r, _ = run_tool(cand / "runtime/intake-bundle", tmp_path)
    assert rc == 0 and r["ok"] is True and r["failures"] == []
    assert [x["original_id"] for x in r["disclosed_partial_extractions"]] == ["FX-SRC-0502"]
    assert sorted(x["row"] for x in r["known_f13_losses"]) == ["FX-FIN-0401", "FX-FIN-0402", "FX-WRK-0401"]


def _rehash(b):
    import hashlib
    m = json.loads((b / "delivery-manifest.json").read_text())
    for e in m["originals"]:
        data = (b / e["file"]).read_bytes()
        e["sha256"], e["size_bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    (b / "delivery-manifest.json").write_text(json.dumps(m, indent=1))
    lines = sorted(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(b)}" for p in b.rglob("*")
                   if p.is_file() and p.name != "INTAKE-FILES.sha256")
    (b / "INTAKE-FILES.sha256").write_text("\n".join(lines) + "\n")


@needs_backend
def test_r5_unrecognised_declared_profile_fails_the_command(cand, tmp_path):
    b = tmp_path / "b"
    shutil.copytree(cand / "runtime/intake-bundle", b)
    p = b / "originals/tables/fin-export-FX-C05-2026-05-26.csv"
    p.write_bytes(p.read_bytes().replace(b"amount_minor", b"amount", 1))
    _rehash(b)
    rc, r, err = run_tool(b, tmp_path)
    assert rc == 1 and r["ok"] is False
    assert any(f["original_id"] == "FX-SRC-0504" and "not recognised" in f["reason"] for f in r["failures"])
    assert "UNEXPECTED" in err


@needs_backend
def test_r5_failed_extraction_of_real_bytes_fails_the_command(cand, tmp_path):
    b = tmp_path / "b"
    shutil.copytree(cand / "runtime/intake-bundle", b)
    (b / "originals/documents/account-confirmation-nair.pdf").write_bytes(b"%PDF-1.4\n% not a real document body\n")
    _rehash(b)
    rc, r, _ = run_tool(b, tmp_path)
    assert rc == 1 and r["ok"] is False
    assert any(f["original_id"] == "FX-SRC-0102" for f in r["failures"])


@needs_backend
def test_r5_projection_exception_and_unexpected_partial_fail_the_command(cand, tmp_path):
    p = run_backend(f"""
        def boom(*a, **k): raise RuntimeError("simulated projection fault")
        arc.native_ledger.project = boom
        rc = arc.main({str(cand / 'runtime/intake-bundle')!r}, {str(tmp_path / 'a.json')!r})
        r = json.load(open({str(tmp_path / 'a.json')!r}))
        assert rc == 1 and r["ok"] is False, rc
        assert any("simulated projection fault" in f["reason"] for f in r["failures"])
        real = arc.extract
        def partial_csv(data, media):
            prof, out = real(data, media)
            if media == "text/csv":
                out.state = "partial"
            return prof, out
        arc.native_ledger.project = __import__("sdbackend.engine.native_ledger", fromlist=["x"]).project
        arc.extract = partial_csv
        rc = arc.main({str(cand / 'runtime/intake-bundle')!r}, {str(tmp_path / 'b.json')!r})
        r = json.load(open({str(tmp_path / 'b.json')!r}))
        assert rc == 1 and any("unexpected partial" in f["reason"] for f in r["failures"]), r["failures"]
        print("OK")
    """, tmp_path)
    assert p.returncode == 0 and "OK" in p.stdout, p.stderr


# ---------------------------------------------------------------- R6 scratch store binding
SCRATCH_PROBE = """
    import sdbackend.runtime as rtmod
    def forbidden(*a, **k): raise SystemExit("make_runtime must not be reached")
    rtmod.make_runtime = forbidden
    try:
        arc.validated_scratch_dir()
    except arc.ScratchRefused as exc:
        print("REFUSED", exc)
    else:
        print("ACCEPTED")
"""


@needs_backend
@pytest.mark.parametrize("value", [None, "", "   "])
def test_r6_missing_or_blank_path_refused_from_empty_working_dir(tmp_path, value):
    empty = tmp_path / "empty-cwd"
    empty.mkdir()
    p = run_backend(SCRATCH_PROBE, tmp_path, env={} if value is None else {"SDB_DATA_DIR": value}, cwd=empty)
    assert "REFUSED" in p.stdout and "missing/blank" in p.stdout, p.stdout + p.stderr


@needs_backend
def test_r6_occupied_path_refused(tmp_path):
    d = tmp_path / "occupied"
    d.mkdir()
    (d / "control.sqlite3").write_text("x")
    p = run_backend(SCRATCH_PROBE, tmp_path, env={"SDB_DATA_DIR": str(d)})
    assert "REFUSED" in p.stdout and "not empty" in p.stdout


@needs_backend
def test_r6_default_store_location_refused_without_touching_it(tmp_path):
    fake_backend = tmp_path / "fake-ai-backend"
    (fake_backend / "var/data").mkdir(parents=True)                # an EMPTY stand-in for the default store
    p = run_backend(textwrap.dedent(f"""
        import pathlib
        arc.BACKEND = pathlib.Path({str(fake_backend)!r})
    """) + textwrap.dedent(SCRATCH_PROBE), tmp_path, env={"SDB_DATA_DIR": str(fake_backend / "var/data")})
    assert "REFUSED" in p.stdout and "protected/default store" in p.stdout


@needs_backend
def test_r6_whole_command_refuses_before_any_runtime(cand, tmp_path):
    rc, r, err = run_tool(cand / "runtime/intake-bundle", tmp_path, "--ingest-scratch", env={"SDB_DATA_DIR": ""})
    assert rc == 2 and r is None and "REFUSED" in err


@needs_backend
def test_r6_valid_scratch_is_bound_explicitly_into_settings(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    p = run_backend(f"""
        import sdbackend.runtime as rtmod
        seen = {{}}
        def capture(settings=None):
            seen["dir"] = str(settings.data_dir) if settings is not None else None
            raise SystemExit(0)
        rtmod.make_runtime = capture
        path = arc.validated_scratch_dir()
        try:
            arc.scratch_runtime(path)
        except SystemExit:
            pass
        print("BOUND", seen["dir"], str(path))
    """, tmp_path, env={"SDB_DATA_DIR": str(d)})
    parts = p.stdout.split()
    assert parts[0] == "BOUND" and parts[1] == parts[2] == str(d.resolve()), p.stdout + p.stderr
