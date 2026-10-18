"""v3 repair tests: SR-2 sentence, exact reuse of the ten accepted v2 originals, atomic no-replace publication.
Every adverse test uses disposable directories; the real v1/v2 packages are only read."""
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from sdgen import atomic
from sdgen import build as build_mod
from sdgen.atomic import PublishUnsupported, rename_noreplace
from sdgen.build import Batch, ReleaseExistsError, tree_digest
from sdgen.checks import eml_parts, run_checks
from sdgen.paths import AUTHORED

from conftest import REAL_V2, needs_v2

OLD = "I uploaded the papers to the Example Super portal on 21 December, but the death certificate has since been requested again."
NEW = "The attached portal receipt shows the papers as received on 21 December, but the death certificate has since been requested again."
FWD = "---------- Forwarded message ---------"
pytestmark = needs_v2


def sha(b):
    return hashlib.sha256(b).hexdigest()


def emls(pkg, prefix):
    return next((pkg / "intake-bundle/originals/emails").glob(prefix + "*.eml"))


def failed(res, cid=None):
    return [i for i in res["results"] if i["status"] == "fail" and (cid is None or i["id"] == cid)]


def edit_body(eml, fn):
    """Rewrite the plain-text part of an EML (QP re-encoded) so fixtures can change wording across line wraps."""
    import email
    from email import policy
    msg = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    part = msg.get_body(preferencelist=("plain",))
    part.set_content(fn(part.get_content()), cte="quoted-printable")
    eml.write_bytes(msg.as_bytes())


def man(pkg):
    return json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())


# ------------------------------------------------------------ SR-2 / R01-final
def test_sr2_replaces_exactly_the_ruled_sentence_in_d05(v3):
    new = eml_parts(emls(v3, "2026-01-02_0845").read_bytes())[1]
    old = eml_parts(emls(REAL_V2, "2026-01-02_0845").read_bytes())[1]
    assert old.count(OLD) == 1 and new.count(NEW) == 1 and OLD not in new and "I uploaded" not in new
    assert new == old.replace(OLD, NEW)
    assert "This is the third time I've sent these same papers" in new and "The portal receipt I've attached shows the three files" in new


def test_sr2_ambiguity_regression_rejects_the_first_person_upload_assertion(v3_copy):
    eml = emls(v3_copy, "2026-01-02_0845")
    edit_body(eml, lambda t: t.replace(NEW, OLD))
    res = run_checks(v3_copy, deep=False, base_root=v3_copy.parent)
    assert failed(res, "G1") and failed(res, "C2")  # forbidden upload assertion present AND ruled sentence missing


def test_sr2_regression_is_precise_to_the_ambiguous_sentence(v3_copy):
    """Removing the ruled sentence fails; a harmless edit elsewhere in D05 does not trip the upload patterns."""
    eml = emls(v3_copy, "2026-01-02_0845")
    edit_body(eml, lambda t: t.replace("I would be very grateful.", "I would be grateful."))
    res = run_checks(v3_copy, deep=False, base_root=v3_copy.parent)
    assert not failed(res, "G1") and not failed(res, "C2")


def test_sr2_only_the_d05_sentence_and_d10_quotation_change_business_text(v3):
    old_pkg, changed = REAL_V2, {}
    for rel in sorted(p.relative_to(v3 / "intake-bundle") for p in (v3 / "intake-bundle/originals").rglob("*") if p.is_file()):
        a, b = (old_pkg / "intake-bundle" / rel).read_bytes(), (v3 / "intake-bundle" / rel).read_bytes()
        if a != b:
            changed[str(rel)] = (a, b)
    assert sorted(Path(k).name for k in changed) == sorted(
        [emls(v3, "2026-01-02_0845").name, emls(v3, "2026-01-08_0930").name])
    d05 = [k for k in changed if "0845" in k][0]
    d10 = [k for k in changed if "0930" in k][0]
    # body text: D05 differs only by the ruled sentence; D10 only by the same sentence inside its quotation
    for k in (d05, d10):
        ob, nb = (eml_parts(x)[1] for x in changed[k])
        assert nb == ob.replace(OLD, NEW) and ob != nb
    nb10 = eml_parts(changed[d10][1])[1]
    assert nb10.split(FWD)[0] == eml_parts(changed[d10][0])[1].split(FWD)[0]
    assert nb10.split(FWD)[1].count(NEW) == 1


def test_sr2_d10_own_message_unchanged_and_quotes_corrected_d05_with_original_header(v3):
    msg, body, atts = eml_parts(emls(v3, "2026-01-08_0930").read_bytes())
    d05 = eml_parts(emls(v3, "2026-01-02_0845").read_bytes())[1]
    quoted = body.split(FWD, 1)[1].split("\n\n", 1)[1]
    assert quoted.split() == d05.split()
    assert "Date: Fri, 2 Jan 2026 at 08:45" in body and "Beth Martin <beth-martin@member-mail.example>" in body
    old = eml_parts(emls(REAL_V2, "2026-01-08_0930").read_bytes())
    assert body.split(FWD)[0] == old[1].split(FWD)[0]
    assert body.split(FWD)[1].split("\n\n", 1)[0] == old[1].split(FWD)[1].split("\n\n", 1)[0]  # forwarded header block
    assert [i["status"] for i in run_checks(v3, deep=False, base_root=v3.parent)["results"] if i["id"] == "D6"] == ["pass"]


def test_sr2_attachments_and_receipt_bytes_unchanged(v3):
    for prefix, n in (("2026-01-02_0845", 4), ("2026-01-08_0930", 5)):
        a3 = [(x, sha(p)) for x, _, p in eml_parts(emls(v3, prefix).read_bytes())[2]]
        a2 = [(x, sha(p)) for x, _, p in eml_parts(emls(REAL_V2, prefix).read_bytes())[2]]
        assert a3 == a2 and len(a3) == n


# ------------------------------------------------------------ reuse
def test_v3_carries_the_ten_accepted_originals_byte_identical(v3):
    b2, b3 = man(REAL_V2), man(v3)
    status = json.loads((REAL_V2 / "restricted/review/REVIEW-STATUS.json").read_text())
    accepted = status["accepted_records"]
    assert len(accepted) == 10
    by2 = {o["original_id"]: o for o in b2["originals"]}
    by3 = {o["original_id"]: o for o in b3["originals"]}
    for oid in accepted:
        assert oid in by3, oid  # identity preserved
        assert by3[oid]["sha256"] == by2[oid]["sha256"] and by3[oid]["version"] == by2[oid]["version"]
        assert (v3 / "intake-bundle" / by3[oid]["file"]).read_bytes() == (REAL_V2 / "intake-bundle" / by2[oid]["file"]).read_bytes()
        assert by3[oid]["lineage"]["carried_forward"]["relation"] == "byte_identical_copy"


def test_v3_exactly_two_originals_change_with_distinct_immutable_ids(v3):
    b2, b3 = man(REAL_V2), man(v3)
    by2 = {o["original_id"]: o for o in b2["originals"]}
    changed = [o for o in b3["originals"] if o["lineage"]["relation"] == "revision" and "carried_forward" not in o["lineage"]]
    assert sorted(o["staging_card_ref"] for o in changed) == ["11-D05", "11-D10"]
    assert len(b3["originals"]) == 12
    for o in changed:
        old = by2[o["lineage"]["supersedes_original_id"]]
        assert o["original_id"] != old["original_id"] and o["original_id"].endswith(".v3") and o["version"] == 3
        assert o["sha256"] != old["sha256"] and o["staging_card_ref"] == old["staging_card_ref"]
        assert o["lineage"]["supersedes_sha256"] == old["sha256"]
    assert sum(1 for o in b3["originals"] if o["sha256"] == by2.get(o["original_id"], {}).get("sha256")) == 10
    assert (v3 / "intake-bundle/delivery-manifest.json").read_bytes() != (REAL_V2 / "intake-bundle/delivery-manifest.json").read_bytes()


def test_v3_d06_summary_and_private_controls_carried_unchanged(v3):
    for rel in ("restricted/private-controls/conversations/11-D06-conversation.json",
                "restricted/private-controls/fidelity/11-D06-summary-fidelity.json"):
        assert (v3 / rel).read_bytes() == (REAL_V2 / rel).read_bytes()
    s = next((v3 / "intake-bundle/originals/call-summaries").glob("*.txt"))
    assert s.read_bytes() == next((REAL_V2 / "intake-bundle/originals/call-summaries").glob("*.txt")).read_bytes()


def test_v3_records_pins_inherited_dependencies_and_base_bindings(v3):
    rec = json.loads((v3 / "restricted/private-controls/build-record.json").read_text())
    assert sorted(rec["authored_used"]) == ["11-D05", "11-D10"]
    assert rec["authored_used"]["11-D05"]["revision"] == 3 and rec["authored_used"]["11-D05"]["path"].endswith("v3/11-D05.json")
    assert rec["authored_used"]["11-D10"]["revision"] == 1
    assert rec["authored_inherited"]["11-D05"]["role"] == "forward_quote_dependency"
    for c in ("11-D06", "11-D15", "11-D17"):
        assert rec["authored_inherited"][c]["role"] == "inherited_unchanged_input_of_carried_original"
        assert rec["authored_inherited"][c]["sha256"]
    bp = rec["base_package"]
    assert bp["name"] == "case-11-rehearsal-v2"
    assert bp["manifest_sha256"] == sha((REAL_V2 / "intake-bundle/delivery-manifest.json").read_bytes())
    assert bp["review_status_sha256"] == sha((REAL_V2 / "restricted/review/REVIEW-STATUS.json").read_bytes())
    assert rec["plan_pack_sha256"] and rec["rerendered_cards"] == ["11-D05", "11-D10"]
    m = man(v3)
    assert m["batch"]["supersedes_package"]["manifest_sha256"] == bp["manifest_sha256"]
    # availability clocks identical for every carried and changed original
    by2 = {o["original_id"]: o for o in man(REAL_V2)["originals"]}
    for o in m["originals"]:
        if "scenario_time" in o:
            ref = by2.get(o["original_id"]) or by2[o["lineage"]["supersedes_original_id"]]
            assert o["scenario_time"] == ref["scenario_time"]


def test_v3_code_repair_does_not_rerender_accepted_originals(v3):
    """Carried bytes come from the base package, not from a render: the build record shows no render log for them."""
    rec = json.loads((v3 / "restricted/private-controls/build-record.json").read_text())
    assert len(rec["carried_forward"]) == 10 and all(c["verified_byte_equal_to_base"] for c in rec["carried_forward"])
    r = run_checks(v3, deep=True, base_root=v3.parent)
    assert [i["status"] for i in r["results"] if i["id"] == "I1"] == ["pass"]


def test_v3_reports_use_final_package_identity_not_staging(v3):
    assert ".staging" not in str(v3) and "staging-" not in v3.name
    blob = "".join(p.read_text() for p in (v3 / "restricted/review").rglob("*") if p.is_file() and p.suffix in (".json", ".md"))
    assert "staging-" not in blob and ".staging" not in blob
    assert json.loads((v3 / "restricted/review/CHECK-RESULTS.json").read_text())["package"] == v3.name == "case-11-rehearsal-v3"


def test_v3_checks_all_layers_clean_and_reconstructs(v3):
    r = run_checks(v3, deep=True, base_root=v3.parent)
    s = r["summary"]["by_layer"]
    assert all(s[l]["fail"] == 0 and s[l].get("unavailable", 0) == 0 for l in s), s
    assert s["reconstruction"]["pass"] == 1


def test_v3_review_material_has_blank_decisions(v3):
    form = json.loads((v3 / "restricted/review/review-form.json").read_text())
    assert form["status"] == "pending_independent_review" and len(form["records"]) == 12
    assert all(r["decision"] is None and r["reviewer"] is None if "reviewer" in r else r["decision"] is None for r in form["records"])
    assert form["realisation_choices"] == []
    st = json.loads((v3 / "restricted/review/REVIEW-STATUS.json").read_text())
    assert st["accepted_records"] == [] and sorted(st["pending_records_changed_bytes"]) == [
        "ORIG-case11-rehearsal-11-D05.v3", "ORIG-case11-rehearsal-11-D10.v3"]


# ------------------------------------------------------------ R03-A atomic no-replace
def _mk(d, kind):
    if kind == "empty_dir":
        d.mkdir()
    elif kind == "nonempty_dir":
        d.mkdir()
        (d / "x").write_text("x")
    elif kind == "file":
        d.write_text("x")
    elif kind == "live_symlink":
        t = d.parent / "real_target"
        t.mkdir(exist_ok=True)
        d.symlink_to(t)
    elif kind == "dangling_symlink":
        d.symlink_to(d.parent / "nowhere")


@pytest.mark.parametrize("kind", ["empty_dir", "nonempty_dir", "file", "live_symlink", "dangling_symlink"])
def test_atomic_primitive_refuses_existing_targets(tmp_path, kind):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "payload").write_text("p")
    _mk(dst, kind)
    before = (dst.is_symlink(), os.path.lexists(dst), dst.lstat().st_ino)
    with pytest.raises(FileExistsError):
        rename_noreplace(src, dst)
    assert (src / "payload").read_text() == "p"
    assert (dst.is_symlink(), os.path.lexists(dst), dst.lstat().st_ino) == before
    assert not (dst.is_dir() and not dst.is_symlink() and (dst / "payload").exists())


def test_atomic_primitive_moves_when_target_absent(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "payload").write_text("p")
    rename_noreplace(src, dst)
    assert not src.exists() and (dst / "payload").read_text() == "p"
    assert atomic.self_test(str(tmp_path))


def _staged(tmp_path):
    b = Batch("case11-rehearsal", 1, 1, tmp_path)
    b.render_assets(), b.render_cards(), b.write_stage()
    return b


@pytest.mark.parametrize("kind", ["empty_dir", "nonempty_dir", "live_symlink", "dangling_symlink", "file"])
def test_atomic_race_target_created_after_the_last_absence_check(tmp_path, monkeypatch, kind):
    """The competing target appears AFTER the last absence check, inside the interval that broke os.rename()."""
    b = _staged(tmp_path)
    real = build_mod.rename_noreplace
    calls = []

    def racing(src, dst):
        _mk(Path(dst), kind)  # created after every check the generator made, immediately before the move
        calls.append(kind)
        return real(src, dst)

    monkeypatch.setattr(build_mod, "rename_noreplace", racing)
    with pytest.raises(ReleaseExistsError):
        b.publish()
    assert calls == [kind]
    assert b.stage.exists() and (b.stage / "intake-bundle/delivery-manifest.json").exists()  # checked stage recoverable
    if kind == "empty_dir":
        assert b.dir.is_dir() and list(b.dir.iterdir()) == []  # destination identity preserved, not replaced
    if kind == "nonempty_dir":
        assert [p.name for p in b.dir.iterdir()] == ["x"]


def test_atomic_race_empty_target_created_after_the_last_absence_check(tmp_path, monkeypatch):
    """The exact interval independently reproduced for os.rename(): prove os.rename WOULD have replaced it, ours does not."""
    probe_src, probe_dst = tmp_path / "ps", tmp_path / "pd"
    probe_src.mkdir(), probe_dst.mkdir()
    os.rename(probe_src, probe_dst)  # baseline: plain rename silently replaces an empty directory
    assert probe_dst.exists() and not probe_src.exists()
    b = _staged(tmp_path)
    ino = []
    real = build_mod.rename_noreplace

    def racing(src, dst):
        Path(dst).mkdir()
        ino.append(Path(dst).stat().st_ino)
        return real(src, dst)

    monkeypatch.setattr(build_mod, "rename_noreplace", racing)
    with pytest.raises(ReleaseExistsError):
        b.publish()
    assert b.dir.stat().st_ino == ino[0] and list(b.dir.iterdir()) == [] and b.stage.exists()


def test_atomic_race_nonempty_target_and_symlink_after_the_last_absence_check(tmp_path, monkeypatch):
    for kind in ("nonempty_dir", "dangling_symlink"):
        sub = tmp_path / kind
        sub.mkdir()
        b = Batch("case11-rehearsal", 1, 1, sub)
        b.render_assets(), b.render_cards(), b.write_stage()
        real = build_mod.rename_noreplace
        monkeypatch.setattr(build_mod, "rename_noreplace", lambda s, d, k=kind, r=real: (_mk(Path(d), k), r(s, d))[1])
        with pytest.raises(ReleaseExistsError):
            b.publish()
        assert b.stage.exists()


@pytest.mark.parametrize("state", ["delivered", "accepted", "repair_required"])
def test_atomic_publish_refuses_delivered_accepted_repair_required(tmp_path, state):
    from test_repair import _states
    root = _states(None, tmp_path, state)
    before = tree_digest(root)
    b = Batch("case11-rehearsal", 1, 1, root)
    b.stage.parent.mkdir(parents=True, exist_ok=True)
    b.stage.mkdir()
    (b.stage / "marker").write_text("staged")
    with pytest.raises(ReleaseExistsError):
        b.publish()
    after = tree_digest(root)
    assert {k: v for k, v in after.items() if not k.startswith(".staging")} == {k: v for k, v in before.items() if not k.startswith(".staging")}
    assert (b.stage / "marker").read_text() == "staged"


def test_atomic_competing_publishers_exactly_one_wins(tmp_path):
    batches = []
    for _ in range(2):
        b = Batch("case11-rehearsal", 1, 1, tmp_path)
        b.stage.mkdir(parents=True)
        (b.stage / "owner").write_text(b.stage_id)
        batches.append(b)
    assert batches[0].stage != batches[1].stage and batches[0].dir == batches[1].dir
    barrier, out = threading.Barrier(2), {}

    def run(b):
        barrier.wait()
        try:
            b.publish()
            out[b.stage_id] = "won"
        except ReleaseExistsError:
            out[b.stage_id] = "refused"

    ts = [threading.Thread(target=run, args=(b,)) for b in batches]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out.values()) == ["refused", "won"]
    winner = next(b for b in batches if out[b.stage_id] == "won")
    loser = next(b for b in batches if out[b.stage_id] == "refused")
    assert (winner.dir / "owner").read_text() == winner.stage_id and not winner.stage.exists()
    assert loser.stage.exists() and (loser.stage / "owner").read_text() == loser.stage_id


def test_atomic_unsupported_host_fails_closed_and_moves_nothing(tmp_path, monkeypatch):
    b = _staged(tmp_path)
    monkeypatch.setattr(atomic.sys, "platform", "plan9")
    with pytest.raises(PublishUnsupported):
        b.publish()
    assert b.stage.exists() and not b.dir.exists()
    monkeypatch.undo()
    monkeypatch.setattr(atomic, "_libc", lambda: type("L", (), {})())
    with pytest.raises(PublishUnsupported):
        b.publish()
    assert b.stage.exists() and not b.dir.exists()


def test_atomic_publish_has_no_second_existence_check():
    import inspect
    src = inspect.getsource(Batch.publish)
    assert "_assert_target_free" not in src and ".exists(" not in src and "os.rename" not in src
    assert "rename_noreplace" in src
