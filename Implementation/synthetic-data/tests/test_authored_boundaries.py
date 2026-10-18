"""Acceptance 6 and 7: runtime excludes private material; destinations cannot overwrite preserved datasets;
verification is read-only; reports cannot claim review or AI processing that did not happen."""
import json
import os
import shutil
import zipfile

import pytest

from authored_helpers import EXAMPLE, Input, codes
from sdgen.authored.export import BuildRefused, build
from sdgen.authored.verify import verify_candidate
from sdgen.paths import ROOT


@pytest.fixture(scope="module")
def cand(tmp_path_factory):
    out = tmp_path_factory.mktemp("c")
    assert build(EXAMPLE, out)["ok"]
    return out / "fx-five-members-p1"


@pytest.fixture()
def copy(cand, tmp_path):
    d = tmp_path / cand.name
    shutil.copytree(cand, d)
    return d


def test_runtime_package_excludes_private_material(cand):
    with zipfile.ZipFile(cand / f"{cand.name}-runtime.zip") as z:
        names = z.namelist()
        blob = b"".join(z.read(n) for n in names)
    assert names and all(n.startswith("runtime/") for n in names)
    for term in json.loads((EXAMPLE / "private/LEAK-TERMS.json").read_text())["terms"] + ["member-briefs", "episode_ref", "FX-J0"]:
        assert term.encode() not in blob, term
    with zipfile.ZipFile(cand / f"{cand.name}-private-review.zip") as z:
        assert not any(n.startswith("runtime/") for n in z.namelist())


@pytest.mark.parametrize("mutate,code", [
    (lambda i: i.source("FX-SRC-0503", lambda r: r["content"].update(text=r["content"]["text"] + "Tag: fee_schedule_misapplied\n")), "runtime_leakage"),
    (lambda i: i.source("FX-SRC-0503", lambda r: r.update(episode_ref="FX-EP-05")), "unknown_field"),
    (lambda i: i.source("FX-SRC-0106", lambda r: r.update(filename="FX-EP-01-note.txt")), "runtime_leakage"),
])
def test_private_labels_cannot_reach_runtime(tmp_path, mutate, code):
    inp = Input(tmp_path)
    mutate(inp)
    res = inp.check()
    assert not res["ok"] and code in codes(res)


@pytest.mark.parametrize("dest", ["Data/replacement-pilot-100-members-v2", "Data/synthesis/receiving-exports",
                                  "Data/synthesis/pilot-v2-releases/new", "Data/synthesis/worker-packs-v5", "Shared"])
def test_preserved_datasets_cannot_be_destinations(dest):
    with pytest.raises(BuildRefused):
        build(EXAMPLE, ROOT / dest)


def test_existing_candidate_is_never_replaced(cand):
    before = (cand / "runtime/intake-bundle/delivery-manifest.json").read_bytes()
    with pytest.raises(BuildRefused):
        build(EXAMPLE, cand.parent)
    assert (cand / "runtime/intake-bundle/delivery-manifest.json").read_bytes() == before


def test_errors_publish_nothing(tmp_path):
    inp = Input(tmp_path)
    inp.event("FX-FIN-0503", lambda r: r.update(obligation_id="FX-OBL-99"))
    res = build(inp.root, tmp_path / "out")
    assert not res["ok"] and res["candidate_dir"] is None
    assert not (tmp_path / "out" / "fx-five-members-p1").exists()


def test_verify_passes_and_does_not_mutate(cand):
    stat = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in cand.rglob("*") if p.is_file()}
    res = verify_candidate(cand)
    assert res["failed"] == [], res
    checks = {c["check"]: c for c in res["checks"]}
    assert checks["verification_did_not_mutate"]["status"] == "pass"
    assert checks["status_honesty"]["status"] == "pass" and checks["zip_parity"]["status"] == "pass"
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in cand.rglob("*") if p.is_file()} == stat


def test_tampered_original_and_zip_are_detected(copy):
    p = copy / "runtime/intake-bundle/originals/notes/2026-05-06_case-note_FX-C05.txt"
    p.write_text(p.read_text().replace("Unable to confirm", "Confirmed"))
    res = verify_candidate(copy)
    assert {"bytes_and_checksums", "zip_parity", "authored_content_preserved"} <= set(res["failed"])


def test_status_files_cannot_claim_review_or_ai_processing(copy):
    p = copy / "review/REVIEW-STATUS.json"
    s = json.loads(p.read_text())
    s.update(independent_source_review="performed", ai_processing="complete")
    p.write_text(json.dumps(s))
    assert "status_honesty" in verify_candidate(copy)["failed"]


def test_blanket_pass_report_is_contradicted_by_recomputation(copy):
    m = copy / "runtime/intake-bundle/delivery-manifest.json"
    d = json.loads(m.read_text())
    d["batch"]["contract_pin"]["manifest_content_sha256"] = "0" * 64     # a real defect…
    m.write_text(json.dumps(d))
    res = verify_candidate(copy)                                           # …while CHECK-RESULTS still says pass
    assert "manifest_shape_and_pin" in res["failed"] and "status_honesty" in res["failed"]


def test_review_form_is_empty_and_status_not_performed(cand):
    form = json.loads((cand / "review/INDEPENDENT-REVIEW-FORM.json").read_text())
    assert form["reviewer"]["name"] is None and form["conclusions"] == []
    st = json.loads((cand / "review/REVIEW-STATUS.json").read_text())
    assert st["independent_source_review"] == "not_performed" and st["ai_processing"] == "not_performed"
    m = json.loads((cand / "runtime/intake-bundle/delivery-manifest.json").read_text())
    assert m["status"]["independent_source_review"] == "not_performed" and m["status"]["source_acceptance"] == "pending_independent_review"
    assert "BLIND-RECONSTRUCTIONS" not in os.listdir(cand / "review")


@pytest.mark.parametrize("mutate,code", [
    (lambda i: i.batch(lambda b: b.update(independent_review="pass")), "status_claim_in_input"),
    (lambda i: i.source("FX-SRC-0106", lambda r: r.update(accepted=True)), "status_claim_in_input"),
    (lambda i: i.edit("private/expected-judgements.jsonl", "judgement_id", "FX-J01", lambda r: r.update(review_status="supported_gold")), "self_promoted_gold"),
    (lambda i: i.edit("private/expected-judgements.jsonl", "judgement_id", "FX-J04", lambda r: r.update(cutoff="2026-05-31")), "anchor_after_cutoff"),
    (lambda i: i.edit("private/expected-judgements.jsonl", "judgement_id", "FX-J03",
                      lambda r: r["contrary"][0].update(quote="the BSB was invalid")), "anchor_not_found"),
])
def test_authors_cannot_self_certify(tmp_path, mutate, code):
    inp = Input(tmp_path)
    mutate(inp)
    res = inp.check()
    assert not res["ok"] and code in codes(res)


def test_fixture_identities_cannot_enter_a_corpus_batch(tmp_path):
    inp = Input(tmp_path)
    inp.batch(lambda b: (b.update(purpose="corpus"), b.pop("excluded_from_corpus")))
    assert "fixture_identity" in codes(inp.check())


def test_corpus_batch_needs_the_september_checkpoints(tmp_path):
    inp = Input(tmp_path)
    inp.batch(lambda b: (b.update(purpose="corpus", replay_checkpoints=["2026-03-31", "2026-06-30", "2026-09-30"]),
                         b.pop("excluded_from_corpus")))
    assert "checkpoints_required" in codes(inp.check())
