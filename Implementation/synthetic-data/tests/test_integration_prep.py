"""Integration preparation: the 0.2.0 receiving export, native candidates, pack set 4 (case-11 remaining cards) and the new
ledger/external return shapes. All writes go to disposable folders; the accepted packages, the published export and the
review records are only read."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from sdgen import contract, receiving_export as rx, workpack4
from sdgen.authoring_check import author_check, load_pack
from sdgen.build import ReleaseExistsError
from sdgen.card_adapter import ContractGap, accepted_attachment_index, native_cells, render_card
from sdgen.microbatch import PackagingRefused, build_package, verify_package

STAGE = "remaining_cards"
REMAINING = ["11-D04", "11-D08", "11-D09", "11-D12", "11-D14", "11-D16", "11-D21", "11-D22", "11-D23", "11-D24", "11-D26", "11-D27",
             "11-D28", "11-D18", "11-D19", "11-D25", "11-D29"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def ledger_return(card):
    return {"card": card, "revision": 1, "kind": "ledger_export",
            "style_completion": {"card": card, "applied_profile": "S0", "features_used": [], "typos_used": [], "protected_values_checked": True, "concerns": []}}


def put(folder, d):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{d['card']}.json"
    f.write_text(json.dumps(d, indent=1))
    return f


@pytest.fixture(scope="module")
def pack4(tmp_path_factory):
    root = tmp_path_factory.mktemp("ps4") / "worker-packs-v4"
    e = workpack4.build(root)
    return root / e["pack"]


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    return rx.case11_export(tmp_path_factory.mktemp("exp"), "case-11-accepted-slice-test")


# ------------------------------------------------------------------ receiving export
def test_export_holds_exactly_the_signed_originals_byte_for_byte(export):
    man = json.loads((export / "intake-bundle/delivery-manifest.json").read_text())
    signed = {}
    for s in rx.CASE11_SOURCES:
        for o in json.loads(Path(s["signoff"]).read_text())["originals"]:
            signed[o["original_id"]] = o["sha256"]
    got = {o["original_id"]: sha((export / "intake-bundle" / o["file"]).read_bytes()) for o in man["originals"]}
    assert got == signed and len(got) == 22
    shared = [o for o in man["originals"] if len(o["review_provenance"]) == 2]
    assert sorted(o["filename"] for o in shared) == ["11-05-portal-upload-receipt.jpg", "DC-11.pdf", "ID-11.pdf", "RF-11.pdf"]
    assert man["manifest_kind"] == "pre-extraction-delivery-manifest" and man["manifest_version"] == "0.2.0"


def test_export_reproduces_every_signed_v3_occurrence_from_the_actual_message_bytes(export):
    man = json.loads((export / "intake-bundle/delivery-manifest.json").read_text())
    v3 = json.loads((rx.CASE11_SOURCES[0]["package"] / "intake-bundle/delivery-manifest.json").read_text())
    key = lambda o: (o["parent_message_file"], o["mime_part_path"], o["sha256"], o["references_original"], o["relation"])  # noqa: E731
    assert {key(o) for o in v3["attachment_occurrences"]} <= {key(o) for o in man["attachment_occurrences"]}
    # the D20 letter is the message's own document; the D13 authority excerpt is reused accepted bytes
    d20 = [o for o in man["attachment_occurrences"] if o.get("staging_card_ref") == "11-D20"]
    assert [o["relation"] for o in d20] == ["message_own_document"]


def test_portal_json_stays_json_and_its_form_references_resolve(export):
    man = json.loads((export / "intake-bundle/delivery-manifest.json").read_text())
    portal = next(o for o in man["originals"] if o["kind"] == "portal_form")
    assert portal["declared_media_type"] == "application/json" and portal["declared_format"] == "JSON"
    ids = {o["original_id"] for o in man["originals"]}
    assert len(portal["form_attachment_references"]) == 4 and all(r["references_original"] in ids for r in portal["form_attachment_references"])
    # form references are not MIME occurrences
    assert not any(o["parent_message_file"] == portal["file"] for o in man["attachment_occurrences"])


def test_native_rows_are_schema_valid_candidates_with_review_metadata_outside_the_bundle(export):
    rows = [json.loads(l) for f in ("case", "party") for l in (export / f"intake-bundle/native/{f}.jsonl").read_text().splitlines()]
    case = next(r for r in rows if r["kind"] == "case")
    assert case["data"]["channel"] == "portal" and case["data"]["manager_reference"] == "STAFF-CM03" and case["envelope"]["evidence_refs"] == []
    contact = next(r for r in rows if r["kind"] == "party" and r["data"]["member_reference"] is None)
    assert contact["data"]["party_role"] == "other"  # representative/beneficiary is not asserted
    review = json.loads((export / "restricted-review/NATIVE-CANDIDATES-REVIEW.json").read_text())
    assert any(n["candidate"].startswith("procedure") for n in review["not_emitted"])
    assert not (export / "intake-bundle/restricted-review").exists()
    assert all(c["ok"] for c in rx.verify_export(export))


def test_a_schema_invalid_native_row_fails_export_verification(export, tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(export, bad)
    f = bad / "intake-bundle/native/case.jsonl"
    rec = json.loads(f.read_text())
    rec["data"]["channel"] = "carrier pigeon"
    f.write_text(json.dumps(rec) + "\n")
    failed = {c["name"] for c in rx.verify_export(bad) if not c["ok"]}
    assert "native rows validate against the frozen runtime input_record schema" in failed
    assert "every intake file matches its listed hash" in failed


def test_private_dialogue_planted_in_an_intake_file_is_caught(export, tmp_path):
    bad = tmp_path / "leak"
    shutil.copytree(export, bad)
    conv = json.loads((rx.CASE11_SOURCES[1]["package"] / "restricted/private-controls/conversations/11-D11-conversation.json").read_text())
    turn = next(t["text"] for t in conv["turns"] if len(t["text"]) >= 25)
    f = bad / "intake-bundle/originals/call-summaries/2026-01-09_0930_case-summary-service_to_alicia-romero.txt"
    f.write_text(f.read_text() + "\n" + turn + "\n")
    loaded = [rx.load_source(s) for s in rx.CASE11_SOURCES]
    assert "no private dialogue, style control or authoring marker in any intake text" in {c["name"] for c in rx.verify_export(bad, loaded) if not c["ok"]}


def test_export_refuses_an_existing_target_and_a_source_that_differs_from_its_signoff(export, tmp_path):
    with pytest.raises(ReleaseExistsError):
        rx.case11_export(export.parent, export.name)
    fake = tmp_path / "fake-v2"
    shutil.copytree(rx.CASE11_SOURCES[1]["package"], fake)
    p = fake / "intake-bundle/originals/registers/2025-12-31_0955_alicia-romero_to_file.csv"
    p.write_bytes(p.read_bytes().replace(b"portal", b"phone!"))
    src = dict(rx.CASE11_SOURCES[1], package=fake)
    with pytest.raises(rx.ExportRefused, match="differ from the sign-off"):
        rx.load_source(src)
    with pytest.raises(rx.ExportRefused, match="not the signed manifest"):
        rx.load_source(dict(rx.CASE11_SOURCES[1], manifest_sha256="0" * 64))


def test_accepted_attachments_come_from_the_export_bound_by_its_hash(export):
    idx = accepted_attachment_index(export, sha((export / "intake-bundle/delivery-manifest.json").read_bytes()))
    assert sha(idx["11-08-applicable-delegate-authority-excerpt.pdf"][1]) == "c580d8bdfca543ff96b25d9c986c206f7b24456f95fa73e54469913c780c8657"
    with pytest.raises(ContractGap):
        accepted_attachment_index(export, "f" * 64)


# ------------------------------------------------------------------ pack set 4
def test_pack4_makes_only_the_remaining_cards_launchable_and_passes_its_checks(pack4):
    st = json.loads((pack4 / "STAGE.json").read_text())
    launch = [s for s in st["stages"] if s.get("launchable")]
    assert [s["stage"] for s in launch] == [STAGE] and launch[0]["cards"] == [c for c in launch[0]["cards"] if c in REMAINING] and len(launch[0]["cards"]) == 17
    assert launch[0]["prerequisite_acceptance"]["record_sha256"] == workpack4.MICROBATCH_ACCEPTANCE_SHA256
    assert all(c["ok"] for c in json.loads((pack4 / "PACK-MANIFEST.json").read_text())["checks"])


def test_pack4_context_never_offers_a_document_to_a_card_dated_on_or_before_it(pack4):
    plan = load_pack(pack4)[0]
    ctx = json.loads((pack4 / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    assert ctx["bound_to_manifest"]["delivery_manifest_sha256"] == workpack4.EXPORT_MANIFEST_SHA256
    for o in ctx["originals"]:
        for c in o["usable_before_cards"]:
            assert plan.artifact(c)["event_at"] > o["event_at"], (o["card"], c)
    # the 13 March final response reaches only the four cards after it; the 14 January assessment never reaches 11-D14 (earlier that day)
    d17 = {c for o in ctx["originals"] if o["card"] == "11-D17" for c in o["usable_before_cards"]}
    assert d17 == {"11-D18", "11-D19", "11-D25", "11-D29"}
    assert not any("11-D14" in o["usable_before_cards"] for o in ctx["originals"] if o["card"] == "11-D15")


def test_letter_text_is_reading_context_only_and_cannot_back_a_quote(pack4, tmp_path):
    ctx = json.loads((pack4 / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())
    letter = next(o for o in ctx["originals"] if o["kind"] == "letter" and o["card"] == "11-D20")
    assert letter["quotable"] is False
    line = next(l for l in letter["text"].splitlines() if len(l) > 40)
    d = {"card": "11-D24", "revision": 1, "kind": "email", "greeting": "Dear Beth,", "blocks": [{"quote": line, "from_card": "11-D20"}],
         "signoff": ["Alicia Romero"], "style_completion": {"card": "11-D24", "applied_profile": "F1", "features_used": ["x"], "typos_used": [],
                                                            "protected_values_checked": True, "concerns": []}}
    rows = author_check(put(tmp_path, d), pack4, STAGE)
    assert any(r["id"] == "A5" and not r["ok"] for r in rows)


def test_pack4_refuses_a_changed_export_or_prerequisite_and_an_existing_directory(pack4, monkeypatch, tmp_path):
    with pytest.raises(FileExistsError):
        workpack4.build(pack4.parent)
    monkeypatch.setattr(workpack4, "EXPORT_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="receiving export manifest"):
        workpack4.build(tmp_path / "a")
    monkeypatch.undo()
    monkeypatch.setattr(workpack4, "MICROBATCH_ACCEPTANCE_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="stage acceptance record"):
        workpack4.build(tmp_path / "b")


# ------------------------------------------------------------------ new return shapes
@pytest.mark.parametrize("card", ["11-D19", "11-D28"])
def test_ledger_card_renders_its_locked_native_export_and_passes(pack4, tmp_path, card):
    rows = author_check(put(tmp_path, ledger_return(card)), pack4, STAGE)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    assert sum(r["id"] == "A6n" for r in rows) >= 14
    plan = load_pack(pack4)[0]
    rc = render_card(ledger_return(card), plan, plan.artifact(card))
    [(rel, data)] = rc.files.items()
    assert rel.startswith("originals/ledgers/") and rel.endswith(".csv") and b"FIN-R2-" in data


def test_ledger_rows_keep_approved_and_paid_as_separate_rows(pack4):
    plan = load_pack(pack4)[0]
    data = next(iter(render_card(ledger_return("11-D19"), plan, plan.artifact("11-D19")).files.values())).decode()
    states = [l.split(",")[5] for l in data.splitlines()[1:]]
    assert states == ["approved", "payment"]


def test_effort_export_with_staging_card_labels_is_refused_not_rendered(pack4, tmp_path):
    rows = author_check(put(tmp_path, ledger_return("11-D29")), pack4, STAGE)
    assert any(r["id"] == "A6x" and not r["ok"] and "staging card references" in r["detail"] for r in rows)
    plan = load_pack(pack4)[0]
    with pytest.raises(ContractGap, match="staging card references"):
        render_card(ledger_return("11-D29"), plan, plan.artifact("11-D29"))


def test_prose_kinds_are_refused_for_native_and_native_kind_for_prose_cards(pack4, tmp_path):
    d = ledger_return("11-D21")
    assert any(r["id"] == "A3" and not r["ok"] for r in author_check(put(tmp_path / "a", d), pack4, STAGE))
    e = {"card": "11-D28", "revision": 1, "kind": "email", "greeting": "Hi", "blocks": [{"p": "x"}], "signoff": ["Zoe"],
         "style_completion": ledger_return("11-D28")["style_completion"]}
    assert any(r["id"] == "A3" and not r["ok"] for r in author_check(put(tmp_path / "b", e), pack4, STAGE))
    plan = load_pack(pack4)[0]
    assert contract.allowed_kinds(plan.artifact("11-D21"))[0] == ["email"]


def test_native_cells_reads_csv_and_xlsx(pack4):
    plan = load_pack(pack4)[0]
    from sdgen.card_adapter import render_asset
    a = plan.assets_by_name["11-16-complaints-effort-export.xlsx"]
    data, _ = render_asset(a, "x")
    cells = native_cells(data, a["media_type"])
    assert "WORK-R2-41133154ecaca7e3ef" in cells and any(contract.STAGING_CARD_REF.search(c) for c in cells)


# ------------------------------------------------------------------ packaging the remaining stage
def test_remaining_stage_packages_ledgers_with_asset_ids_and_refuses_the_unruled_effort_export(pack4, export, tmp_path):
    outs = tmp_path / "owned"
    for c in ("11-D19", "11-D28"):
        put(outs, ledger_return(c))
    # an export other than the one the pack is bound to (same originals, different manifest) is refused
    with pytest.raises(ContractGap, match="manifest hash the pack is bound to"):
        build_package(pack4, STAGE, outs, tmp_path / "rel", "case-11-remaining-fixture-0", accepted_package=export)
    bound = workpack4.EXPORT  # the published, bound export; only read
    pkg = build_package(pack4, STAGE, outs, tmp_path / "rel", "case-11-remaining-fixture", accepted_package=bound)
    man = json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())
    led = {o["file"]: o["original_id"] for o in man["originals"]}
    assert led == {"originals/ledgers/11-11-case-remedy-or-refund-ledger.csv": "AST-f4df49e29bd3b37f3f",
                   "originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv": "AST-2149cab174f28959fd"}
    assert all(c["ok"] for c in verify_package(pkg, accepted_package=bound))
    put(outs, ledger_return("11-D29"))
    with pytest.raises(PackagingRefused, match="11-D29 fails the author check"):
        build_package(pack4, STAGE, outs, tmp_path / "rel", "case-11-remaining-fixture-2", accepted_package=bound)
    assert not (tmp_path / "rel/case-11-remaining-fixture-2").exists()
