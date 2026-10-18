"""WR1-WR4 regression tests (independent review of pack set 2, adapted from its disposable probes) plus the packaging path.
Disposable fixtures only: real approvals, accepted packages, worker outputs and pack sets are never written."""
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import specimens as S
from conftest import REAL_V3, needs_v3, write_decision
from sdgen import contract, microbatch, progress, workpack2
from sdgen.authoring_check import author_check, load_pack
from sdgen.build import ReleaseExistsError
from sdgen.card_adapter import ContractGap, accepted_attachment_bytes, render_card
from sdgen.cli import main
from sdgen.progress import GateRefused, LedgerIntegrityError

STAGE = "style_microbatch"
CODE = Path(__file__).resolve().parents[1]


def clar_pack(pack_set_copy, entries):
    pack = next(pack_set_copy.glob("11-C-*"))
    p = pack / "plan/APPROVED-CLARIFICATIONS.json"
    doc = json.loads(p.read_text())
    doc["clarifications"] = entries
    p.write_text(json.dumps(doc))
    return pack


def csv_rows(b):
    return list(csv.DictReader(io.StringIO(b.decode())))


# =========================================================================================== WR1
def test_probe_approved_native_columns_reach_the_actual_cli_csv(tmp_path, pack_set_copy):
    """Reproduces the independent probe: approved columns passed the check but were silently omitted by the real renderer/CLI."""
    plan0 = load_pack(next(pack_set_copy.glob("11-C-*")))[0]
    pack = clar_pack(pack_set_copy, [{"id": "CL-PROBE", "card": "11-D03", "status": "approved", "approved_by": "Principal Data Engineer (fixture)",
                                      "approved_on": "2026-10-15", "native_columns": {"member_reference": plan0.pack["member_reference"], "intake_channel": "portal"}}])
    f = S.write(tmp_path, S.d03())
    assert all(r["ok"] for r in author_check(f, pack, STAGE))
    main(["render-card", str(f), "--pack", str(pack), "--stage", STAGE, "--out", str(tmp_path / "out")])
    (csv_path,) = (tmp_path / "out/originals/registers").glob("*.csv")
    (row,) = csv_rows(csv_path.read_bytes())
    assert row["member_reference"] == plan0.pack["member_reference"] and row["intake_channel"] == "portal"
    assert list(row)[-2:] == ["member_reference", "intake_channel"]  # approved columns follow the locked ones, in issue order


def test_the_check_and_the_renderer_share_one_resolution_of_approved_values(tmp_path, pack_set_copy):
    pack = clar_pack(pack_set_copy, [{"id": "A", "card": "11-D03", "status": "approved", "approved_by": "PDE", "approved_on": "2026-10-15", "native_columns": {"x_col": "1"}},
                                     {"id": "B", "card": "11-D03", "status": "approved", "approved_by": "PDE", "approved_on": "2026-10-15", "native_columns": {"y_col": "2"}},
                                     {"id": "C", "card": "11-D03", "status": "pending", "approved_by": "", "approved_on": "", "native_columns": {"z_col": "3"}},
                                     {"id": "D", "card": "11-D01", "status": "approved", "approved_by": "PDE", "approved_on": "2026-10-15", "native_columns": {"w_col": "4"}}])
    plan, art = load_pack(pack)[0], None
    art = plan.artifact("11-D03")
    assert contract.native_extra_columns(art, plan.clarifications) == {"x_col": "1", "y_col": "2"}
    (row,) = csv_rows(render_card(S.d03(), plan, art).files.popitem()[1])
    assert row["x_col"] == "1" and row["y_col"] == "2" and "z_col" not in row and "w_col" not in row
    rows = author_check(S.write(tmp_path, S.d03()), pack, STAGE)
    assert [r["detail"] for r in rows if r["id"] == "A6n" and "approved column header" in r["detail"]] == [
        "rendered native record has the approved column header: x_col", "rendered native record has the approved column header: y_col"]


def test_check_fails_if_the_renderer_ever_drops_an_approved_column(tmp_path, pack_set_copy, monkeypatch):
    """The check validates the ACTUAL rendered record, so a renderer that omits approved values cannot pass any more."""
    pack = clar_pack(pack_set_copy, [{"id": "A", "card": "11-D03", "status": "approved", "approved_by": "PDE", "approved_on": "2026-10-15", "native_columns": {"x_col": "1"}}])
    import sdgen.card_adapter as ca
    real = ca.render_register_export
    monkeypatch.setattr(ca, "render_register_export", lambda art, plan, extra=None: real(art, plan, None))
    rows = author_check(S.write(tmp_path, S.d03()), pack, STAGE)
    assert [r for r in rows if not r["ok"] and r["id"] == "A6n"], rows


def test_supplied_case_11_clarifications_are_in_the_pack_with_provenance_and_apply_to_the_real_csv(tmp_path, pack11):
    doc = json.loads((pack11 / "plan/APPROVED-CLARIFICATIONS.json").read_text())
    assert [c["id"] for c in doc["clarifications"]] == ["CL11-REGISTER-01", "CL11-APPROVAL-01", "CL11-CALL-HEADER-01"]
    prov = doc["provenance"]
    src = workpack2.CLARIFICATIONS_SOURCE
    assert prov["source_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest() and prov["bound_plan_sha256"] == "df33c8afd103b5b476005e9ee47deddae9af72477bb156450cd8f57946c7b6de"
    f = S.write(tmp_path, S.d03())
    assert all(r["ok"] for r in author_check(f, pack11, STAGE))
    main(["render-card", str(f), "--pack", str(pack11), "--stage", STAGE, "--out", str(tmp_path / "o")])
    (row,) = csv_rows(next((tmp_path / "o/originals/registers").glob("*.csv")).read_bytes())
    assert row == {"request_reference": "REQ-2025-11-01", "case_id": "C-e1da28538652e263887a", "original_receipt": "2025-12-31", "complaints_receipt": "2025-12-31",
                   "manager": "STAFF-CM03", "member_reference": "MR-3f85043fe7f9c98e19f3", "product": "death_benefit", "intake_channel": "portal",
                   "existing_tag": "service", "manager_name": "Alicia Romero"}
    for banned in ("episode", "mechanism", "post_closure", "expected", "root"):
        assert banned not in ",".join(row)


def test_clarifications_not_bound_to_the_frozen_plan_are_refused_at_build(tmp_path, monkeypatch):
    bad = tmp_path / "c.json"
    doc = json.loads(workpack2.CLARIFICATIONS_SOURCE.read_text())
    doc["bound_plan_sha256"] = "0" * 64
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(workpack2, "CLARIFICATIONS_SOURCE", bad)
    with pytest.raises(RuntimeError, match="not bound to this frozen plan"):
        workpack2.build_all(tmp_path / "ps", cases=[11])


def test_d11_request_reference_is_validated_in_the_native_header_not_forced_into_prose(tmp_path, pack11, pack_set_copy):
    d = S.d11(summary=S.d11()["summary"].replace("REQ-2025-11-01", "the request"))
    rows = author_check(S.write(tmp_path, d), pack11, STAGE)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    heads = [r for r in rows if r["id"] == "A6h"]
    assert len(heads) == 2 and any("waived value: request_reference REQ-2025-11-01" in r["detail"] and "CL11-CALL-HEADER-01" in r["detail"] for r in heads)
    # the substantive references stay required; only the duplicate request reference is waived
    for gone in ("DC-11", "RF-11", "ID-11"):
        bad = S.d11(summary=d["summary"].replace(gone, "that item"))
        assert any(not r["ok"] and gone in r["detail"] for r in author_check(S.write(tmp_path, bad, f"x{gone}.json"), pack11, STAGE)), gone
    # without the approved waiver the old requirement applies
    pack = clar_pack(pack_set_copy, [])
    assert any(not r["ok"] and "request_reference" in r["detail"] for r in author_check(S.write(tmp_path, d, "nw.json"), pack, STAGE))
    # the rendered header really carries the trusted references and no spoken metadata was invented
    plan = load_pack(pack11)[0]
    txt = next(v for k, v in render_card(d, plan, plan.artifact("11-D11")).files.items() if k.endswith(".txt")).decode()
    assert "Request reference:  REQ-2025-11-01" in txt and "Case:               C-e1da28538652e263887a" in txt


def test_d13_is_an_email_with_its_existing_authority_attachment_and_no_pdf_renderer_is_needed(pack11):
    plan = load_pack(pack11)[0]
    art = plan.artifact("11-D13")
    assert contract.allowed_kinds(art) == (["email"], None)  # a note cannot carry the held attachment
    assert [plan.assets[a]["filename"] for a in art["attachments"]] == ["11-08-applicable-delegate-authority-excerpt.pdf"]


def test_references_named_only_inside_a_prohibition_are_not_required(pack11):
    plan = load_pack(pack11)[0]
    labels = lambda c, k: [g.label for g in contract.derive_requirements(plan, plan.artifact(c), k, plan.clarifications, []).required]  # noqa: E731
    assert "reference DC-11" not in labels("11-D13", "email") and "reference RF-11" in labels("11-D13", "email")
    assert "reference DC-11" in labels("11-D01", "email")  # a positive request to acknowledge DC-11 still requires it
    assert contract.positive_clauses("RF-11 has a separate witness field; do not confuse that issue with DC-11.") == ["RF-11 has a separate witness field;"]
    assert contract.positive_clauses("No invented earlier acceptance note.") == [] and contract.positive_clauses("Never print DC-11.") == []


@needs_v3
def test_cli_reuses_accepted_attachment_bytes_exactly_from_their_bound_originals(tmp_path, pack11):
    f = S.write(tmp_path, S.d01())
    main(["render-card", str(f), "--pack", str(pack11), "--stage", STAGE, "--out", str(tmp_path / "o"), "--accepted-package", str(REAL_V3)])
    import email
    from email import policy
    m = email.message_from_bytes(next((tmp_path / "o/originals/emails").glob("*.eml")).read_bytes(), policy=policy.default)
    acc = REAL_V3 / "intake-bundle/originals/attachments"
    assert {p.get_filename(): p.get_payload(decode=True) for p in m.iter_attachments()} == {n: (acc / n).read_bytes() for n in S.FILES_D01}
    # a package that is not the one the pack is bound to is refused
    other = tmp_path / "other"
    shutil.copytree(REAL_V3 / "intake-bundle", other / "intake-bundle")
    (other / "intake-bundle/delivery-manifest.json").write_text((other / "intake-bundle/delivery-manifest.json").read_text() + " ")
    with pytest.raises(SystemExit) as e:
        main(["render-card", str(f), "--pack", str(pack11), "--stage", STAGE, "--out", str(tmp_path / "o2"), "--accepted-package", str(other)])
    assert e.value.code == 2
    # tampering with an accepted original's bytes is detected against its manifest hash
    t2 = tmp_path / "t2"
    shutil.copytree(REAL_V3 / "intake-bundle", t2 / "intake-bundle")
    (t2 / "intake-bundle/originals/attachments/DC-11.pdf").write_bytes(b"changed")
    with pytest.raises(ContractGap, match="no longer matches"):
        accepted_attachment_bytes(t2)


# =========================================================================================== WR2
@pytest.fixture()
def ledger(pack_set_copy):
    progress.init(pack_set_copy)
    return pack_set_copy


def _submitted(ledger, reviews, files=None):
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    owned = ledger.parent / workpack2.OUTPUTS_DIRNAME / job["pack"] / STAGE
    cards = next(x for x in json.loads((ledger / job["pack"] / "STAGE.json").read_text())["stages"] if x["stage"] == STAGE)["cards"]
    for name, text in (files or {"11-D01.json": "{}", "11-D02.json": "{}"}).items():
        (owned / name).write_text(text)
    (owned / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": 11, "cards": [{"card": c} for c in cards]}))
    st = progress.set_state(11, STAGE, "submitted", ledger)
    return owned, st["output_digest"]


def _accept_record(ledger, reviews, digest, name="acc.json"):
    return write_decision(reviews, ledger, 11, STAGE, kind="authoring_stage_acceptance", decision="accepted", output_digest=digest, name=name, decided_at="2026-11-30T00:00:00Z")


def _stage(ledger):
    return next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)["stages"][STAGE]


NOW = __import__("datetime").datetime(2026, 12, 1, tzinfo=__import__("datetime").timezone.utc)


@pytest.mark.parametrize("mutate", [
    lambda o: (o / "11-D01.json").write_text("different bytes after submission"),
    lambda o: (o / "11-D09.json").write_text("{}"),
    lambda o: (o / "11-D02.json").unlink(),
    lambda o: (o / "RETURN-CHECKLIST.json").write_text('{"case": 11, "cards": []}'),
    lambda o: (o / "README.txt").write_text("edited readme"),
    lambda o: (o / "emptydir").mkdir(),
    lambda o: (o / "sub").mkdir() or (o / "sub/x.json").write_text("{}"),
    lambda o: shutil.rmtree(o),
], ids=["changed", "added", "deleted", "checklist-changed", "readme-changed", "empty-dir-added", "subdir-added", "directory-missing"])
def test_probe_acceptance_of_changed_output_is_refused_and_routes_to_repair(ledger, reviews, mutate):
    """Reproduces the independent probe: acceptance used the OLD submitted digest after the files changed."""
    owned, digest = _submitted(ledger, reviews)
    rec = _accept_record(ledger, reviews, digest)
    rec_bytes = rec.read_bytes()
    mutate(owned)
    with pytest.raises(GateRefused, match="acceptance refused: output folder differs from the submitted digest"):
        progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=rec, reviews_root=reviews, now=NOW)
    st = _stage(ledger)
    assert st["state"] == "repair_required" and st["acceptance"] is None and st["acceptance_history"] == []
    assert st["submissions"][0]["output_digest"] == digest  # the original submission is preserved in history
    assert rec.read_bytes() == rec_bytes  # the external record is never edited
    events = [json.loads(x) for x in (ledger / "PROGRESS-EVENTS.jsonl").read_text().splitlines()]
    assert events[-1]["action"] == "stale_submission_detected" and events[-1]["detail"]["submitted_digest"] == digest
    assert progress.verify_ledger(ledger)


def test_after_a_stale_acceptance_resubmission_and_a_fresh_record_are_required(ledger, reviews):
    owned, digest = _submitted(ledger, reviews)
    old = _accept_record(ledger, reviews, digest)
    (owned / "11-D01.json").write_text("repaired")
    with pytest.raises(GateRefused):
        progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=old, reviews_root=reviews, now=NOW)
    with pytest.raises(GateRefused, match="not a permitted transition"):
        progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=old, reviews_root=reviews, now=NOW)  # repair_required cannot be accepted
    progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    new_digest = progress.set_state(11, STAGE, "submitted", ledger)["output_digest"]
    assert new_digest != digest
    with pytest.raises(GateRefused, match="different output digest"):
        progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=old, reviews_root=reviews, now=NOW)  # the stale record cannot be reused
    fresh = _accept_record(ledger, reviews, new_digest, "fresh.json")
    st = progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=fresh, reviews_root=reviews, now=NOW)
    assert st["state"] == "independently_accepted" and [s["output_digest"] for s in st["submissions"]] == [digest, new_digest]
    assert st["acceptance_history"][0]["output_digest"] == new_digest and progress.verify_ledger(ledger)


def test_unchanged_output_is_accepted_and_the_pack_binding_is_rechecked(ledger, reviews):
    owned, digest = _submitted(ledger, reviews)
    rec = _accept_record(ledger, reviews, digest)
    pack = next(ledger.glob("11-C-*"))
    (pack / "STAGE.json").write_text((pack / "STAGE.json").read_text() + " ")
    with pytest.raises(GateRefused, match="pack files changed after the pack digest was recorded; acceptance refused"):
        progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=rec, reviews_root=reviews, now=NOW)
    assert _stage(ledger)["state"] == "submitted"  # a pack problem is not a stale submission: nothing was rerouted
    (pack / "STAGE.json").write_text((pack / "STAGE.json").read_text().rstrip(" "))
    assert progress.set_state(11, STAGE, "independently_accepted", ledger, acceptance_record=rec, reviews_root=reviews, now=NOW)["state"] == "independently_accepted"


def test_symlinks_in_the_owned_folder_cannot_be_submitted_or_hidden(ledger, reviews):
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    owned = ledger.parent / workpack2.OUTPUTS_DIRNAME / job["pack"] / STAGE
    (owned / "11-D01.json").write_text("{}")
    os.symlink(ledger / "PACKS-INDEX.json", owned / "link.json")
    with pytest.raises(GateRefused, match="symlink"):
        progress.set_state(11, STAGE, "submitted", ledger)


# ------------------------------------------------- checklist copy (WR4) and the immutable pack template
def test_submission_needs_the_completed_checklist_copy_and_never_touches_the_pack_template(ledger, reviews):
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    pack = next(ledger.glob("11-C-*"))
    before = workpack2.pack_digest(pack)
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    owned = ledger.parent / workpack2.OUTPUTS_DIRNAME / job["pack"] / STAGE
    (owned / "11-D01.json").write_text("{}")
    with pytest.raises(GateRefused, match="copy is missing"):
        progress.set_state(11, STAGE, "submitted", ledger)
    (owned / "RETURN-CHECKLIST.json").write_text("not json")
    with pytest.raises(GateRefused, match="not a valid checklist"):
        progress.set_state(11, STAGE, "submitted", ledger)
    (owned / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": 12, "cards": [{"card": "11-D01"}]}))
    with pytest.raises(GateRefused, match="must be for case 11"):
        progress.set_state(11, STAGE, "submitted", ledger)
    shutil.copy(pack / "RETURN-CHECKLIST.json", owned / "RETURN-CHECKLIST.json")  # the documented procedure: copy the template, complete the copy
    assert progress.set_state(11, STAGE, "submitted", ledger)["state"] == "submitted"
    assert workpack2.pack_digest(pack) == before  # the immutable template and every other pack file are unchanged


def test_guides_state_the_checklist_copy_path_and_the_template_is_marked_immutable(pack_set):
    root, idx = pack_set
    for row in idx:
        p = root / row["pack"]
        guide, ins = (p / "shared/WORKER-GUIDE.md").read_text(), (p / "INSTRUCTIONS.md").read_text()
        if not row["launchable_stages"]:
            continue
        assert "Copy the pack's `RETURN-CHECKLIST.json` to `<owned folder>/RETURN-CHECKLIST.json`" in guide and "Never edit anything inside the pack folder" in guide
        assert f"{workpack2.OUTPUTS_DIRNAME}/{row['pack']}/<stage>/RETURN-CHECKLIST.json" in ins and "immutable template" in ins
        assert "update `RETURN-CHECKLIST.json`" not in guide + ins
        assert "IMMUTABLE TEMPLATE" in json.loads((p / "RETURN-CHECKLIST.json").read_text())["note"]


# =========================================================================================== WR3
def _tamper(ledger, fn):
    p = ledger / "PROGRESS.json"
    st = json.loads(p.read_text())
    fn(st)
    p.write_text(json.dumps(st, indent=1) + "\n")


def _job11(st):
    return next(j for j in st["jobs"] if j["case"] == 11)


TAMPERS = {
    "stage-state": lambda st: _job11(st)["stages"][STAGE].update(state="working"),
    "approval-sha": lambda st: _job11(st)["stages"][STAGE]["approval"].update(sha256="0" * 64),
    "approval-reviewer": lambda st: _job11(st)["stages"][STAGE]["approval"].update(reviewer="somebody else"),
    "approval-removed": lambda st: _job11(st)["stages"][STAGE].update(approval=None),
    "job-pack-digest": lambda st: _job11(st).update(pack_digest="f" * 64),
    "worker-cap": lambda st: st.update(max_concurrent_workers=40),
    "revision": lambda st: st.update(revision=st["revision"] + 5),
    "head-hash": lambda st: st.update(head_hash="a" * 64),
    "blocked-removed": lambda st: next(j for j in st["jobs"] if j["case"] == 32).update(blocked=None),
    "stage-launchable": lambda st: next(j for j in st["jobs"] if j["case"] == 32)["stages"]["whole_journey"].update(launchable=True),
    "format-downgraded": lambda st: st.update(ledger_format=1),
}


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_probe_direct_state_edit_is_detected_and_blocks_every_next_operation(ledger, reviews, name):
    """Reproduces the independent probe: a PROGRESS.json payload edit left verify_ledger true. Now it is detected and nothing proceeds."""
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    assert progress.verify_ledger(ledger)
    _tamper(ledger, TAMPERS[name])
    assert progress.verify_ledger(ledger) is False
    problems = progress.status(ledger)["integrity_problems"]
    assert problems and progress.status(ledger)["ledger_intact"] is False
    before = (ledger / "PROGRESS.json").read_bytes(), (ledger / "PROGRESS-EVENTS.jsonl").read_bytes()
    g = write_decision(reviews, ledger, 12, "whole_journey")
    for op in (lambda: progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews),
               lambda: progress.authorise(12, "whole_journey", g, ledger, reviews_root=reviews),
               lambda: progress.withdraw_approval(11, STAGE, "reason", ledger)):
        with pytest.raises(LedgerIntegrityError, match="ledger integrity check failed"):
            op()
    assert ((ledger / "PROGRESS.json").read_bytes(), (ledger / "PROGRESS-EVENTS.jsonl").read_bytes()) == before  # a refused operation writes nothing


def test_event_log_edits_are_detected_too(ledger, reviews):
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    ev = ledger / "PROGRESS-EVENTS.jsonl"
    lines = ev.read_text().splitlines()
    good = ev.read_text()
    for bad in ("\n".join(lines[:-1]) + "\n",                      # truncated
                good + lines[-1] + "\n",                              # duplicated event
                good.replace('"authorise"', '"authorize"'),           # edited event
                "\n".join(reversed(lines)) + "\n",                    # reordered
                good + "not json\n"):
        ev.write_text(bad)
        assert progress.verify_ledger(ledger) is False, bad[:40]
    ev.write_text(good)
    assert progress.verify_ledger(ledger)  # restoring the recorded history makes it verifiable again


def test_every_event_binds_the_state_payload_it_produced(ledger, reviews):
    f = write_decision(reviews, ledger, 11, STAGE)
    progress.authorise(11, STAGE, f, ledger, reviews_root=reviews)
    progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    events = [json.loads(x) for x in (ledger / "PROGRESS-EVENTS.jsonl").read_text().splitlines()]
    assert all(e["state_sha256"] for e in events) and len({e["state_sha256"] for e in events}) == 3
    st = json.loads((ledger / "PROGRESS.json").read_text())
    assert progress.state_payload_hash(st) == events[-1]["state_sha256"] and st["ledger_format"] == 2


def test_a_legacy_state_unbound_ledger_is_read_only_history_and_never_silently_reset(ledger, reviews):
    _tamper(ledger, lambda st: st.pop("ledger_format"))
    assert any("legacy ledger" in p for p in progress.integrity_problems(ledger))
    with pytest.raises(LedgerIntegrityError, match="legacy ledger"):
        write_decision(reviews, ledger, 11, STAGE) and progress.set_state(11, STAGE, "working", ledger, reviews_root=reviews)
    with pytest.raises(FileExistsError):
        progress.init(ledger)  # no reset or re-creation path


def test_pack_set_2_ledger_on_disk_is_legacy_and_unmodified():
    old = workpack2.SYNTH / "worker-packs-v2"
    if not old.exists():
        pytest.skip("pack set 2 not present")
    st = json.loads((old / "PROGRESS.json").read_text())
    assert "ledger_format" not in st and st["revision"] == 1
    assert any("legacy" in p for p in progress.integrity_problems(old))
    assert not any(s["approval"] for j in st["jobs"] for s in j["stages"].values())


# =========================================================================================== WR4
def _instantiate(sk, card="11-D01", profile="M2"):
    """Fill the documented skeleton's placeholders with concrete values, changing nothing else about its shape."""
    if isinstance(sk, dict):
        return {k: _instantiate(v, card, profile) for k, v in sk.items()}
    if isinstance(sk, list):
        return [_instantiate(v, card, profile) for v in sk]
    if isinstance(sk, str) and sk.startswith("<"):
        return {"<card id>": card, "<see below; applied_profile S0, no typos>": None}.get(sk, "fixture text")
    return sk


@pytest.mark.parametrize("kind", sorted(contract.SCHEMAS))
def test_documented_minimum_for_every_kind_validates_against_the_schema_once_instantiated(kind):
    sk = _instantiate(workpack2.SKELETONS[kind])
    sk["style_completion"] = S.sc(sk["card"], "M2", features=("x",))
    assert contract.schema_errors(sk) == [], contract.schema_errors(sk)


def test_documented_call_skeleton_shows_four_valid_turns_and_the_schema_still_requires_four():
    sk = workpack2.SKELETONS["call_summary"]
    assert [t["n"] for t in sk["conversation"]] == [1, 2, 3, 4]
    assert contract.SCHEMAS["call_summary"]["properties"]["conversation"]["minItems"] == 4  # the requirement was not weakened to fit the example
    short = _instantiate(sk)
    short["style_completion"] = S.sc("11-D11", "V2")
    short["conversation"] = short["conversation"][:3]
    assert any("conversation" in e for e in contract.schema_errors(short))


def test_returns_markdown_and_guides_document_the_four_turn_minimum_and_quote_convention(pack11):
    md = (pack11 / "shared/RETURN-SCHEMAS.md").read_text()
    assert "AT LEAST FOUR numbered turns" in md and "character for character" in md and "CRLF = LF" in md
    g = (pack11 / "shared/WORKER-GUIDE.md").read_text()
    assert "character for character" in g and "(CRLF = LF)" in g


def _passage(pack, card="11-D05"):
    ctx = {o["card"]: o for o in json.loads((pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())["originals"]}
    text = ctx[card]["text"]
    return text, next(ln for ln in text.split("\n") if len(ln.strip()) > 40 and ln == ln.strip() and "  " not in ln)


def _q(pack, tmp_path, quote, card="11-D05"):
    d = S.d07()
    d["blocks"].insert(1, {"quote": quote, "from_card": card})
    return [r for r in author_check(S.write(tmp_path, d, "q.json"), pack, STAGE) if r["id"] == "A5"]


def test_probe_altered_quote_spacing_no_longer_passes_as_exact(tmp_path, pack11):
    """Reproduces the independent probe: doubling the spaces in an accepted passage passed because all whitespace was normalised."""
    text, line = _passage(pack11)
    assert all(r["ok"] for r in _q(pack11, tmp_path, line))
    for altered in (line.replace(" ", "  "), line.replace(" ", "  ", 1), line.replace(" ", "\t", 1), " " + line, line + " ", line.replace(" ", " ", 1),
                    text.strip().replace(" ", "  ")):
        assert not all(r["ok"] for r in _q(pack11, tmp_path, altered)), repr(altered[:50])
    # a multi-line quote must keep its blank lines
    if "\n\n" in text.strip():
        a, b = text.strip().split("\n\n", 1)
        joined = a.splitlines()[-1] + "\n\n" + b.splitlines()[0]
        assert all(r["ok"] for r in _q(pack11, tmp_path, joined))
        assert not all(r["ok"] for r in _q(pack11, tmp_path, joined.replace("\n\n", "\n")))


def test_only_the_documented_line_ending_convention_is_normalised(tmp_path, pack11):
    text, _ = _passage(pack11)
    lines = [ln for ln in text.split("\n") if ln.strip()][:2]
    assert len(lines) == 2
    adjacent = next((a, b) for a, b in zip(text.split("\n"), text.split("\n")[1:]) if a.strip() and b.strip())
    assert all(r["ok"] for r in _q(pack11, tmp_path, "\r\n".join(adjacent)))  # CRLF == LF
    assert not all(r["ok"] for r in _q(pack11, tmp_path, "\n ".join(adjacent)))  # an added space after the break is not a line-ending difference


def test_quotes_still_preserve_copied_wording_and_errors(tmp_path, pack_set_copy):
    pack = next(pack_set_copy.glob("11-C-*"))
    ctx = json.loads((pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    quoted = "pleae  send   the thnaks mesage – exactly  as  written."
    next(o for o in ctx["originals"] if o["card"] == "11-D05")["text"] += "\n" + quoted
    (pack / "context/ACCEPTED-PRIOR-ORIGINALS.json").write_text(json.dumps(ctx))
    assert all(r["ok"] for r in _q(pack, Path(pack_set_copy.parent), quoted)), "an original containing double spaces and typos is copied exactly"
    assert not all(r["ok"] for r in _q(pack, Path(pack_set_copy.parent), quoted.replace("  ", " ")))
    assert not all(r["ok"] for r in _q(pack, Path(pack_set_copy.parent), quoted.replace("pleae", "please")))


# ============================================================================ packaging path (portal / register / note)
@pytest.fixture()
def outputs(tmp_path):
    d = tmp_path / "owned"
    d.mkdir()
    for card, fn in S.ALL.items():
        S.write(d, fn())
    (d / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": 11, "cards": [{"card": c} for c in S.ALL]}))
    return d


@needs_v3
def test_seven_card_stage_is_packaged_with_exact_accepted_attachments_and_private_material_outside_intake(tmp_path, pack11, outputs):
    root = tmp_path / "pkgs"
    pkg = microbatch.build_package(pack11, STAGE, outputs, root, "fixture-microbatch", REAL_V3)
    assert pkg == root / "fixture-microbatch" and not (root / ".staging" / "fixture-microbatch").exists()
    assert all(c["ok"] for c in microbatch.verify_package(pkg, REAL_V3)), [c for c in microbatch.verify_package(pkg, REAL_V3) if not c["ok"]]
    intake = pkg / "intake-bundle"
    man = json.loads((intake / "delivery-manifest.json").read_text())
    assert man["review_status"] == "pending_independent_review" and man["pack_digest"] == json.loads((pack11 / "PACK-MANIFEST.json").read_text())["pack_digest"]
    files = sorted(str(f.relative_to(intake)) for f in intake.rglob("*") if f.is_file())
    assert all(microbatch.allowlisted(r) or r in ("delivery-manifest.json", "INTAKE-FILES.sha256") for r in files)
    kinds = [o["kind"] for o in man["originals"]]
    assert kinds.count("email") == 4 and kinds.count("call_summary") == 1 and kinds.count("letter") == 1 and kinds.count("attachment_original") == 6
    assert any(r.startswith("originals/portal/") for r in files) and any(r.startswith("originals/registers/") for r in files)
    reused = {o["filename"] for o in man["originals"] if o.get("reuse") == "accepted_exact"}
    assert reused == {"DC-11.pdf", "ID-11.pdf", "RF-11.pdf", "11-05-portal-upload-receipt.jpg"}
    for n in reused:
        assert (intake / "originals/attachments" / n).read_bytes() == (REAL_V3 / "intake-bundle/originals/attachments" / n).read_bytes()
    # private dialogue and fidelity controls live only under restricted/
    assert (pkg / "restricted/private-controls/conversations/11-D11-conversation.json").exists()
    blob = b"".join((intake / r).read_bytes() for r in files if r.endswith((".txt", ".json", ".csv", ".html", ".eml")))
    assert b"Fixture: it's about" not in blob and b"never_ingested" not in blob and b"style_completion" not in blob
    assert json.loads((pkg / "restricted/review/REVIEW-STATUS.json").read_text())["accepted_records"] == []
    assert all(d["decision"] is None for d in json.loads((pkg / "restricted/review/review-form.json").read_text())["decisions"])


@needs_v3
def test_publication_never_replaces_an_existing_target_and_keeps_the_verified_stage(tmp_path, pack11, outputs):
    root = tmp_path / "pkgs"
    (root / "empty-target").mkdir(parents=True)
    with pytest.raises(ReleaseExistsError, match="nothing was replaced"):
        microbatch.build_package(pack11, STAGE, outputs, root, "empty-target", REAL_V3)
    assert (root / "empty-target").is_dir() and not any((root / "empty-target").iterdir())
    kept = list((root / ".staging").glob("empty-target.staging-*"))
    assert len(kept) == 1 and all(c["ok"] for c in microbatch.verify_package(kept[0], REAL_V3))
    (root / "full-target").mkdir()
    (root / "full-target/x").write_text("existing")
    with pytest.raises(ReleaseExistsError):
        microbatch.build_package(pack11, STAGE, outputs, root, "full-target", REAL_V3)
    assert (root / "full-target/x").read_text() == "existing"
    (root / "file-target").write_text("a file")
    with pytest.raises(ReleaseExistsError):
        microbatch.build_package(pack11, STAGE, outputs, root, "file-target", REAL_V3)
    os.symlink(root / "nowhere", root / "dangling")
    with pytest.raises(ReleaseExistsError):
        microbatch.build_package(pack11, STAGE, outputs, root, "dangling", REAL_V3)
    # the race: a target appears after the last absence check, immediately before the atomic call
    import sdgen.microbatch as mb
    real = mb.rename_noreplace

    def late_target(src, dst):
        Path(dst).mkdir()
        return real(src, dst)
    mb.rename_noreplace = late_target
    try:
        with pytest.raises(ReleaseExistsError):
            microbatch.build_package(pack11, STAGE, outputs, root, "raced", REAL_V3)
    finally:
        mb.rename_noreplace = real
    assert (root / "raced").is_dir() and not any((root / "raced").iterdir())
    first = microbatch.build_package(pack11, STAGE, outputs, root, "ok-once", REAL_V3)
    with pytest.raises(ReleaseExistsError):
        microbatch.build_package(pack11, STAGE, outputs, root, "ok-once", REAL_V3)
    assert first.is_dir()


def test_packaging_refuses_a_failing_card_a_foreign_card_and_non_launchable_stages(tmp_path, pack11, outputs):
    root = tmp_path / "pkgs"
    S.write(outputs, S.d13(blocks=[{"p": "decision made."}]))
    with pytest.raises(microbatch.PackagingRefused, match="11-D13 fails the author check"):
        microbatch.build_package(pack11, STAGE, outputs, root, "bad")
    assert not (root / "bad").exists()
    S.write(outputs, S.d13())
    S.write(outputs, S.d01(card="11-D14"), "11-D14.json")
    with pytest.raises(microbatch.PackagingRefused, match="outside the stage"):
        microbatch.build_package(pack11, STAGE, outputs, root, "bad2")
    with pytest.raises(microbatch.PackagingRefused, match="not a launchable stage"):
        microbatch.build_package(pack11, "remaining_cards", outputs, root, "bad3")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(microbatch.PackagingRefused, match="no returned card files"):
        microbatch.build_package(pack11, STAGE, empty, root, "bad4")


def test_allowlist_and_private_leak_guards_reject_bad_paths_and_leaked_dialogue():
    for bad in ("../x.pdf", "/abs/x.pdf", "originals/emails/../x.eml", "private-controls/x.json", "originals/emails/x.exe", "other/x.eml", "originals//x.eml"):
        assert not microbatch.allowlisted(bad), bad
    assert microbatch.allowlisted("originals/portal/a.json") and microbatch.allowlisted("originals/registers/a.csv") and microbatch.allowlisted("originals/notes/a.txt")
    conv = json.dumps({"turns": [{"n": 1, "text": "this exact private spoken sentence must stay private"}]}).encode()
    priv = {"private-controls/conversations/c.json": conv}
    assert microbatch.private_leaks({"originals/call-summaries/s.txt": b"summary with this exact private spoken sentence must stay private inside"}, priv) == ["originals/call-summaries/s.txt"]
    assert microbatch.private_leaks({"originals/notes/n.txt": b"mentions private-controls path"}, priv) == ["originals/notes/n.txt"]
    assert microbatch.private_leaks({"originals/notes/n.txt": b"a clean note"}, priv) == []


@needs_v3
def test_verify_package_detects_tampering_and_unlisted_or_private_files(tmp_path, pack11, outputs):
    pkg = microbatch.build_package(pack11, STAGE, outputs, tmp_path / "pkgs", "verify-me", REAL_V3)
    intake = pkg / "intake-bundle"
    reg = next((intake / "originals/registers").glob("*.csv"))
    original = reg.read_bytes()
    reg.write_bytes(original + b"x")
    assert any(not c["ok"] for c in microbatch.verify_package(pkg, REAL_V3))
    reg.write_bytes(original)
    (intake / "originals/notes").mkdir()
    (intake / "originals/notes/stray.txt").write_text("unlisted")
    assert any(not c["ok"] and "inventory" in c["name"] for c in microbatch.verify_package(pkg, REAL_V3))
    (intake / "originals/notes/stray.txt").unlink()
    (intake / "originals/notes").rmdir()
    (intake / "private-controls").mkdir()
    (intake / "private-controls/c.json").write_text("{}")
    assert any(not c["ok"] and "allowlisted" in c["name"] for c in microbatch.verify_package(pkg, REAL_V3))


@needs_v3
def test_package_stage_cli_publishes_once_and_refuses_the_second_time(tmp_path, pack11, outputs, capsys):
    args = ["package-stage", "--pack", str(pack11), "--stage", STAGE, "--outputs", str(outputs), "--out-root", str(tmp_path / "pk"), "--name", "cli-pkg",
            "--accepted-package", str(REAL_V3)]
    main(args)
    assert json.loads(capsys.readouterr().out)["published"].endswith("cli-pkg")
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2 and "already exists" in capsys.readouterr().err
