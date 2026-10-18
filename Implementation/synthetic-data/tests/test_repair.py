"""Repair-release tests (R01-R04 plus handoff section 5). All mutation happens on disposable copies; the real delivered v1
package is only ever read."""
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdgen import checks as checks_mod
from sdgen.build import Batch, InputUnavailable, ReleaseExistsError, build, load_pinned, tree_digest
from sdgen.checks import FORBIDDEN_MANIFEST_KEYS, FORBIDDEN_TEXT, ReviewRecordProtected, eml_parts, run_checks
from sdgen.paths import AUTHORED, TOOL_ROOT
from sdgen.render_call import render_call_summary
from sdgen.render_image import render_upload_receipt
from sdgen.review import write_review_material
from sdgen.tz import OffsetMismatch, melbourne_abbr

from conftest import REAL_V1, RULING, needs_v1

BOUNDED = ("This is the third time I've sent these same papers: with my first email on 21 December, with my complaint on 31 December, "
           "and now again here. They are the same copies, not new versions. The portal receipt I've attached shows the three files as "
           "received on 21 December.")
pytestmark = needs_v1


def sha(b):
    return hashlib.sha256(b).hexdigest()


def emls(pkg, prefix):
    return next((pkg / "intake-bundle/originals/emails").glob(prefix + "*.eml"))


def failed(res, cid=None, name=None):
    return [i for i in res["results"] if i["status"] == "fail" and (cid is None or i["id"] == cid) and (name is None or name in i["name"])]


def summary_file(pkg):
    return next((pkg / "intake-bundle/originals/call-summaries").glob("*.txt"))


def fid_file(pkg):
    return pkg / "restricted/private-controls/fidelity/11-D06-summary-fidelity.json"


# ---------------------------------------------------------------- R01
def test_r01_d05_uses_the_bounded_wording_and_keeps_the_rest(v2):
    body = eml_parts(emls(v2, "2026-01-02_0845").read_bytes())[1]
    v1body = eml_parts(emls(REAL_V1, "2026-01-02_0845").read_bytes())[1]
    assert BOUNDED in body and "To be clear" not in body and "also uploaded" not in body
    old = re.search(r"To be clear, this is the third time.*?on 21 December\.\n", v1body, re.S).group(0)
    assert body.replace(BOUNDED + "\n", old) == v1body  # only that paragraph changed
    for keep in ("7 January", "Being told it was all accepted", "which specific item is missing"):
        assert keep in body


def test_r01_d05_receipt_and_attachment_bytes_unchanged(v2):
    a2 = {n: sha(p) for n, _, p in eml_parts(emls(v2, "2026-01-02_0845").read_bytes())[2]}
    a1 = {n: sha(p) for n, _, p in eml_parts(emls(REAL_V1, "2026-01-02_0845").read_bytes())[2]}
    assert a2 == a1 and len(a2) == 4 and "11-05-portal-upload-receipt.jpg" in a2


def test_r01_d10_quotes_corrected_d05_exactly_with_original_header(v2):
    msg, body, atts = eml_parts(emls(v2, "2026-01-08_0930").read_bytes())
    d05 = eml_parts(emls(v2, "2026-01-02_0845").read_bytes())[1]
    quoted = body.split("---------- Forwarded message ---------", 1)[1].split("\n\n", 1)[1]
    assert quoted.split() == d05.split()
    assert "Date: Fri, 2 Jan 2026 at 08:45" in body and "Beth Martin <beth-martin@member-mail.example>" in body
    assert BOUNDED in body and len(atts) == 5
    res = run_checks(v2, deep=False, base_root=v2.parent)
    assert [i["status"] for i in res["results"] if i["id"] == "D6"] == ["pass"]


def test_r01_d10_own_message_unchanged(v2):
    mine = lambda b: b.split("---------- Forwarded message ---------")[0]
    new = eml_parts(emls(v2, "2026-01-08_0930").read_bytes())
    old = eml_parts(emls(REAL_V1, "2026-01-08_0930").read_bytes())
    assert mine(new[1]) == mine(old[1])
    assert [(n, sha(p)) for n, _, p in new[2]] == [(n, sha(p)) for n, _, p in old[2]]


def test_r01_regression_blocks_unassigned_extra_submission_narrative_on_d05(v2_copy):
    eml = emls(v2_copy, "2026-01-02_0845")
    eml.write_bytes(eml.read_bytes().replace(b"Hi Alicia,", b"Hi Alicia, I also uploaded them through the portal as well.", 1))
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "G1")


def test_r01_scoped_regression_does_not_ban_a_future_member_upload_elsewhere(v2_copy):
    """The extra-upload patterns are scoped to D05's own card record. A genuine upload mentioned by another record is fine."""
    eml = emls(v2_copy, "2026-01-14_0955")
    eml.write_bytes(eml.read_bytes().replace(b"Hi Alicia,", b"Hi Alicia, Beth also uploaded the receipt again.", 1))
    assert not failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "G1")


# ---------------------------------------------------------------- R02
def test_r02_summary_states_interruption_neutrally_and_stays_in_range(v2):
    t = summary_file(v2).read_text()
    head, summ = t.split("\nSUMMARY\n", 1)[0], t.split("\nSUMMARY\n", 1)[1].split("\n\nFOLLOW-UP")[0]
    assert "Beth interrupted Alicia's explanation to ask what was missing." in summ
    assert not re.search(r"deliberate|hung up|disconnect|rude|abus|terminat", summ, re.I)
    assert 60 <= len(summ.split()) <= 180
    assert "no due date was stated" in summ and "C-e1da" not in summ and "C-e1da28538652e263887a" in head


def test_r02_private_dialogue_unchanged_from_v1(v2):
    a = json.loads((v2 / "restricted/private-controls/conversations/11-D06-conversation.json").read_text())["turns"]
    b = json.loads((REAL_V1 / "restricted/private-controls/conversations/11-D06-conversation.json").read_text())["turns"]
    assert a == b


def test_r02_matrix_separates_spoken_from_header_metadata(v2):
    fid = json.loads(fid_file(v2).read_text())
    turns = {t["n"]: t["text"] for t in json.loads((v2 / "restricted/private-controls/conversations/11-D06-conversation.json").read_text())["turns"]}
    req = next(c for c in fid["claims"] if c["claim"].startswith("request reference"))
    assert req["source"] == "spoken" and req["turns"] == [5] and "C-e1da" not in turns[5]
    meta = next(c for c in fid["claims"] if "case reference" in c["claim"])
    assert meta["source"] == "metadata" and meta["turns"] == []
    assert not any("C-e1da" in turns[n] for n in turns)  # the case reference is never spoken
    ids = {r["id"] for r in fid["required_content"]}
    assert {"interruption", "next_step", "no_due_date", "dissatisfaction", "cannot_confirm_earlier"} <= ids


def _edit_summary(pkg, old, new):
    f = summary_file(pkg)
    t = f.read_text()
    assert old in t
    f.write_text(t.replace(old, new, 1))


def test_r02_fixture_omitted_interruption_is_detected(v2_copy):
    _edit_summary(v2_copy, " Beth interrupted Alicia's explanation to ask what was missing.", "")
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "F6", "interruption")


def test_r02_fixture_negated_next_step_is_detected(v2_copy):
    _edit_summary(v2_copy, "will ask Alice Hughes", "will not ask Alice Hughes")
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "F6", "next_step")


def test_r02_fixture_changed_dissatisfaction_is_detected(v2_copy):
    _edit_summary(v2_copy, "Beth agreed to that step but said the matter is not sorted and she is not happy.",
                  "Beth agreed to that step and was satisfied the matter is resolved.")
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "F6", "dissatisfaction")


def test_r02_fixture_header_metadata_misattributed_as_spoken_is_detected(v2_copy):
    f = fid_file(v2_copy)
    d = json.loads(f.read_text())
    c = next(c for c in d["claims"] if "case reference" in c["claim"])
    c["source"], c["turns"] = "spoken", [5]  # claim the case reference was said on turn 5
    f.write_text(json.dumps(d))
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "F4", "case reference")


def test_r02_fixture_case_reference_narrated_as_spoken_is_detected(v2_copy):
    _edit_summary(v2_copy, "discussed request REQ-2025-11-01.", "discussed request REQ-2025-11-01 (complaint C-e1da28538652e263887a).")
    assert failed(run_checks(v2_copy, deep=False, base_root=v2_copy.parent), "F8")


# ---------------------------------------------------------------- R03
def _states(v1_root, tmp_path, state):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(REAL_V1, root / "case-11-rehearsal-v1")
    st = root / "case-11-rehearsal-v1/restricted/review/REVIEW-STATUS.json"
    d = json.loads(st.read_text())
    if state == "delivered":
        d = {"source_acceptance": "pending_independent_review", "accepted_records": [], "rejected_records": [], "pending_records": ["x"]}
    elif state == "accepted":
        d["source_acceptance"] = "accepted"
        d["repair_required_records"] = []
        d["accepted_records"] = d["accepted_records"] + d.pop("repair_required_records", [])
    st.write_text(json.dumps(d))
    return root


@pytest.mark.parametrize("state", ["delivered", "accepted", "repair_required"])
def test_r03_existing_release_is_never_replaced(v1_root, tmp_path, state):
    root = _states(v1_root, tmp_path, state)
    before = tree_digest(root)
    with pytest.raises(ReleaseExistsError):
        build("case11-rehearsal", 1, 1, root)
    assert tree_digest(root) == before


def test_r03_review_writers_refuse_existing_records(tmp_path):
    d = tmp_path / "case-11-rehearsal-v1"
    shutil.copytree(REAL_V1, d)
    before = tree_digest(d)
    from sdgen.review import ReviewRecordProtected as RRP
    with pytest.raises(RRP):
        write_review_material(d)
    assert tree_digest(d) == before


def test_r03_check_write_refuses_existing_results_and_output_inside_package(tmp_path):
    d = tmp_path / "case-11-rehearsal-v1"
    shutil.copytree(REAL_V1, d)
    before = tree_digest(d)
    with pytest.raises(ReviewRecordProtected):
        run_checks(d, write=True, deep=False)
    with pytest.raises(ReviewRecordProtected):
        run_checks(d, output=d / "restricted/out.json", deep=False)
    assert tree_digest(d) == before
    out = tmp_path / "elsewhere" / "res.json"
    run_checks(d, output=out, deep=False)
    assert out.exists() and tree_digest(d) == before


def test_r03_publish_refuses_when_target_appears_after_staging(tmp_path):
    b = Batch("case11-rehearsal", 1, 1, tmp_path)
    b.render_assets(), b.render_cards(), b.write_stage()
    b.dir.mkdir(parents=True)
    (b.dir / "marker.txt").write_text("delivered")
    with pytest.raises(ReleaseExistsError):
        b.publish()
    assert (b.dir / "marker.txt").read_text() == "delivered" and b.stage.exists()


def test_r03_failed_build_leaves_no_final_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Batch, "write_reading_copies", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        build("case11-rehearsal", 1, 1, tmp_path)
    assert not (tmp_path / "case-11-rehearsal-v1").exists()


def test_r03_cli_release_refuses_existing_package_and_requires_explicit_version(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(REAL_V1, root / "case-11-rehearsal-v1")
    before = tree_digest(root)
    base = [sys.executable, "-m", "sdgen", "release", "--authored-version", "1"]
    r = subprocess.run(base + ["--package-version", "1", "--out-root", str(root)], capture_output=True, text=True, cwd=TOOL_ROOT)
    assert r.returncode == 2 and "REFUSED" in r.stderr and "immutable" in r.stderr
    r2 = subprocess.run(base + ["--out-root", str(root)], capture_output=True, text=True, cwd=TOOL_ROOT)
    assert r2.returncode != 0 and "package-version" in r2.stderr
    assert tree_digest(root) == before


# ---------------------------------------------------------------- R04
def test_r04_pins_load_the_recorded_revision_never_latest():
    rec = json.loads((REAL_V1 / "restricted/private-controls/build-record.json").read_text())["authored_used"]["11-D05"]
    assert "To be clear" in json.dumps(load_pinned(rec)[0])
    b = Batch("case11-rehearsal", 99, 1, "/nonexistent", pins={"11-D05": {"path": rec["path"], "sha256": rec["sha256"]}})
    assert b._authored("11-D05")[0]["revision"] == 1  # authored_version=99 does not override the pin
    assert Batch("case11-rehearsal", 2, 2, "/nonexistent")._authored("11-D05")[0]["revision"] == 2
    with pytest.raises(InputUnavailable):
        load_pinned({"path": rec["path"], "sha256": "0" * 64})


def test_r04_byte_integrity_works_without_plan_authored_or_build_record(tmp_path, monkeypatch):
    d = tmp_path / "case-11-rehearsal-v1"
    shutil.copytree(REAL_V1, d)
    (d / "restricted/private-controls/build-record.json").unlink()
    monkeypatch.setattr(checks_mod, "CasePlan", lambda n: (_ for _ in ()).throw(FileNotFoundError("plan gone")))
    res = run_checks(d, deep=True)
    s = res["summary"]
    assert s["byte_integrity_ok"] and s["by_layer"]["integrity"]["fail"] == 0
    assert s["by_layer"]["provenance"]["unavailable"] >= 1 and s["by_layer"]["semantic"]["unavailable"] >= 1
    assert s["by_layer"]["reconstruction"]["unavailable"] == 1 and s["failed"] == 0


def test_r04_changed_authored_input_makes_reconstruction_unavailable_not_an_integrity_failure(tmp_path):
    d = tmp_path / "case-11-rehearsal-v1"
    shutil.copytree(REAL_V1, d)
    p = d / "restricted/private-controls/build-record.json"
    rec = json.loads(p.read_text())
    rec["authored_used"]["11-D05"]["sha256"] = "0" * 64  # simulate: the authored file is no longer the recorded one
    p.write_text(json.dumps(rec))
    s = run_checks(d, deep=True)["summary"]
    assert s["byte_integrity_ok"] and s["by_layer"]["reconstruction"]["fail"] == 0
    assert s["by_layer"]["reconstruction"]["unavailable"] == 1 and s["by_layer"]["provenance"]["unavailable"] >= 1


def test_r04_changed_plan_makes_semantic_layer_unavailable_not_failed(tmp_path, monkeypatch):
    d = tmp_path / "case-11-rehearsal-v1"
    shutil.copytree(REAL_V1, d)

    class ChangedPlan(checks_mod.CasePlan):
        def __init__(self, n):
            super().__init__(n)
            self.sha256 = "f" * 64

    monkeypatch.setattr(checks_mod, "CasePlan", ChangedPlan)
    s = run_checks(d, deep=True)["summary"]
    assert s["byte_integrity_ok"] and s["by_layer"]["semantic"]["fail"] == 0 and s["by_layer"]["semantic"]["unavailable"] == 1
    assert s["by_layer"]["reconstruction"]["unavailable"] == 1


def test_r04_current_code_reproduces_delivered_v1_bytes_from_its_recorded_inputs(v1_root):
    res = run_checks(v1_root / "case-11-rehearsal-v1", deep=True)
    i1 = next(i for i in res["results"] if i["id"] == "I1")
    assert i1["status"] == "pass" and "recorded renderer r2, current r3" in i1["name"]
    assert res["summary"]["byte_integrity_ok"] and res["summary"]["by_layer"]["provenance"]["fail"] == 0
    # the only semantic finding on frozen v1 under the stricter checker is the R02 defect itself
    assert {i["id"] for i in failed(res)} == {"F8"}


def test_r04_v2_carries_the_nine_accepted_originals_byte_identical(v2):
    status = json.loads((REAL_V1 / "restricted/review/REVIEW-STATUS.json").read_text())["accepted_records"]
    assert len(status) == 9
    m1 = {o["original_id"]: o for o in json.loads((REAL_V1 / "intake-bundle/delivery-manifest.json").read_text())["originals"]}
    m2 = {o["original_id"]: o for o in json.loads((v2 / "intake-bundle/delivery-manifest.json").read_text())["originals"]}
    for oid in status:
        a, b = m1[oid], m2[oid]
        assert a["file"] == b["file"] and a["sha256"] == b["sha256"]
        assert (REAL_V1 / "intake-bundle" / a["file"]).read_bytes() == (v2 / "intake-bundle" / b["file"]).read_bytes()
        assert b["lineage"]["carried_forward"]["relation"] == "byte_identical_copy" and b["version"] == 1
    occ1 = [o for o in json.loads((REAL_V1 / "intake-bundle/delivery-manifest.json").read_text())["attachment_occurrences"]
            if o["staging_card_ref"] in ("11-D15", "11-D17")]
    occ2 = [o for o in json.loads((v2 / "intake-bundle/delivery-manifest.json").read_text())["attachment_occurrences"]
            if o["staging_card_ref"] in ("11-D15", "11-D17")]
    assert occ1 == occ2 and occ2
    for pv in ("2026-01-14_0955", "2026-03-13_0930"):  # previews of carried messages stay identical
        assert (REAL_V1 / f"intake-bundle/previews/{pv}_{'alice-hughes_to_alicia-romero' if '01-14' in pv else 'alicia-romero_to_beth-martin'}.preview.html").read_bytes() == \
               (v2 / f"intake-bundle/previews/{pv}_{'alice-hughes_to_alicia-romero' if '01-14' in pv else 'alicia-romero_to_beth-martin'}.preview.html").read_bytes()


def test_r04_only_d05_d06_d10_change_and_have_distinct_identity(v2):
    m1 = json.loads((REAL_V1 / "intake-bundle/delivery-manifest.json").read_text())
    m2 = json.loads((v2 / "intake-bundle/delivery-manifest.json").read_text())
    f1 = {o["file"]: o for o in m1["originals"]}
    f2 = {o["file"]: o for o in m2["originals"]}
    assert set(f1) == set(f2)
    changed = {f for f in f1 if f1[f]["sha256"] != f2[f]["sha256"]}
    assert {f2[f]["staging_card_ref"] for f in changed} == {"11-D05", "11-D06", "11-D10"} and len(changed) == 3
    for f in changed:
        a, b = f1[f], f2[f]
        assert b["original_id"] != a["original_id"] and b["original_id"].endswith(".v2") and b["version"] == 2
        assert b["staging_card_ref"] == a["staging_card_ref"]
        assert b["lineage"]["relation"] == "revision" and b["lineage"]["supersedes_original_id"] == a["original_id"]
        assert b["lineage"]["supersedes_sha256"] == a["sha256"]
        assert b["audit"]["review_status"] == "pending_independent_review_changed_bytes"
    st = json.loads((v2 / "restricted/review/REVIEW-STATUS.json").read_text())
    assert st["accepted_records"] == [] and len(st["pending_records_changed_bytes"]) == 3
    form = json.loads((v2 / "restricted/review/review-form.json").read_text())
    assert all(r["decision"] is None for r in form["records"]) and form["reviewer"] is None


def test_r04_v2_records_overlay_and_inherited_input_hashes(v2):
    rec = json.loads((v2 / "restricted/private-controls/build-record.json").read_text())
    assert {c: i["revision"] for c, i in rec["authored_used"].items()} == {"11-D05": 2, "11-D06": 2, "11-D10": 1}
    roles = {c: i["role"] for c, i in rec["authored_inherited"].items()}
    assert roles == {"11-D05": "forward_quote_dependency", "11-D15": "inherited_unchanged_input_of_carried_original",
                     "11-D17": "inherited_unchanged_input_of_carried_original"}
    assert rec["authored_inherited"]["11-D05"]["sha256"] == rec["authored_used"]["11-D05"]["sha256"] and rec["authored_inherited"]["11-D05"]["revision"] == 2
    for i in [*rec["authored_used"].values(), *rec["authored_inherited"].values()]:
        assert sha((TOOL_ROOT / i["path"]).read_bytes()) == i["sha256"]
    assert rec["base_package"]["manifest_sha256"] == sha((REAL_V1 / "intake-bundle/delivery-manifest.json").read_bytes())
    assert rec["plan_pack_sha256"] and rec["renderer_version"] and len(rec["carried_forward"]) == 9
    assert all(c["verified_byte_equal_to_base"] for c in rec["carried_forward"])
    assert rec["supplemental_rulings"][0]["sha256"] == sha(RULING.read_bytes())
    assert (v2 / "restricted/review/supplemental-rulings" / RULING.name).exists()


def test_r04_refuses_to_carry_an_original_not_recorded_accepted(v1_root, tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(REAL_V1, root / "case-11-rehearsal-v1")
    st = root / "case-11-rehearsal-v1/restricted/review/REVIEW-STATUS.json"
    d = json.loads(st.read_text())
    d["accepted_records"] = d["accepted_records"][1:]
    st.write_text(json.dumps(d))
    with pytest.raises(InputUnavailable):
        build("case11-rehearsal", 2, 2, root)
    assert not (root / "case-11-rehearsal-v2").exists()


def test_r04_refuses_to_carry_when_base_bytes_changed(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    shutil.copytree(REAL_V1, root / "case-11-rehearsal-v1")
    p = root / "case-11-rehearsal-v1/intake-bundle/originals/attachments/DC-11.pdf"
    p.write_bytes(p.read_bytes() + b"0")
    with pytest.raises(InputUnavailable):
        build("case11-rehearsal", 2, 2, root)
    assert not (root / "case-11-rehearsal-v2").exists()


def test_r04_v2_checks_all_layers_clean_and_reconstructs(v2):
    res = run_checks(v2, deep=True, base_root=v2.parent)
    s = res["summary"]
    assert s["failed"] == 0, failed(res)
    assert s["byte_integrity_ok"] and s["by_layer"]["reconstruction"]["pass"] == 1
    assert s["by_layer"]["provenance"]["fail"] == 0 and s["by_layer"]["semantic"]["pass"] > 100


# ---------------------------------------------------------------- handoff section 5
def _call(event, recorded):
    art = {"event_at": event, "recorded_at": recorded}
    auth = {"service_banner": "CASE SUMMARY SERVICE - CALL SUMMARY", "participants": ["A", "B"], "summary": "x", "follow_up_items": []}
    return render_call_summary("c", art, auth, SimpleNamespace(case_id="C-x", request_reference="REQ-x")).decode()


def test_tz_summer_and_winter_call_summaries():
    summer = _call("2026-01-02T09:55:00+11:00", "2026-01-02T10:18:00+11:00")
    winter = _call("2026-07-10T09:55:00+10:00", "2026-07-10T10:18:00+10:00")
    assert "9:55 am AEDT" in summer and "10:18 am AEDT" in summer
    assert "9:55 am AEST" in winter and "10:18 am AEST" in winter and "AEDT" not in winter


def test_tz_offset_must_match_melbourne_rules():
    with pytest.raises(OffsetMismatch):
        melbourne_abbr("2026-07-10T09:55:00+11:00")  # winter time with a summer offset
    with pytest.raises(OffsetMismatch):
        melbourne_abbr("2026-01-02T09:55:00+10:00")
    with pytest.raises(OffsetMismatch):
        melbourne_abbr("2026-01-02T09:55:00")


def test_tz_dst_boundaries():
    assert melbourne_abbr("2026-04-04T23:00:00+11:00") == "AEDT" and melbourne_abbr("2026-04-05T04:00:00+10:00") == "AEST"
    assert melbourne_abbr("2026-10-04T01:59:00+10:00") == "AEST" and melbourne_abbr("2026-10-04T03:01:00+11:00") == "AEDT"


def test_tz_receipt_uses_zone_of_supplied_date():
    asset = {"created_at": "2026-07-09T09:00:00+10:00"}
    rows = [{"file": "A.pdf", "receipt_state": "received"}]
    mk = lambda up: render_upload_receipt(asset, rows, {"upload_reference": "UP-1", "uploaded_at": up, "member_account_number": "M"})[1]["drawn_text"]
    assert any(t.endswith("AEST") for t in mk("2026-07-08T10:00:00+10:00"))
    assert any(t.endswith("AEDT") for t in mk("2026-01-02T10:00:00+11:00"))


def test_scan_allows_ordinary_words_but_catches_control_fields():
    ordinary = ("my family, a planned transfer, the incident at the hospital, our private health fund, an evaluation of the replay, "
                "a gold card, the diagnosis, a split payment, the tier of cover")
    assert not [p for p in FORBIDDEN_TEXT if re.search(p, ordinary, re.I)]
    for leak in ["source_assertions", "assertion_type", "writer_constraints", "SRC-R2-abc", "AST-f6928ad642", "THR-R2-x", "11-D05",
                 "family A", "incident_id", '{"key": 1}', "gold label", "synthetic data"]:
        assert any(re.search(p, leak, re.I) for p in FORBIDDEN_TEXT), leak
    assert not FORBIDDEN_MANIFEST_KEYS.match("source_family") and not FORBIDDEN_MANIFEST_KEYS.match("label")
    assert FORBIDDEN_MANIFEST_KEYS.match("incident_id") and FORBIDDEN_MANIFEST_KEYS.match("writer_constraints")
