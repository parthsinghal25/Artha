"""Case-11 E01 source repairs and the narrow card-ID guard (SC04). Real review/pack/package files are read only; everything written
goes to tmp_path."""
import copy
import json
import shutil

import pytest

from sdgen import source_repair as sr
from sdgen.assets import render_asset
from sdgen.authoring_check import load_pack
from sdgen.leakguard import card_id_hits, scan_files
from sdgen.microbatch import verify_package
from sdgen.render_pdf import render_document

V1 = sr.BASE / "intake-bundle"
GUARD = "no private authoring-card ID in any held text, stored XLSX cell, PDF text layer or decoded attachment"
IDX = "originals/attachments/11-13-external-submission-evidence-index.xlsx"
D23 = "originals/emails/2026-01-21_0930_alicia-romero_to_case-correspondence-team.eml"


@pytest.fixture(scope="module")
def rulings():
    return sr.load_rulings()


@pytest.fixture(scope="module")
def plan():
    return load_pack(sr.PACK)[0]


@pytest.fixture(scope="module")
def successor(tmp_path_factory):
    root = tmp_path_factory.mktemp("repair")
    sr.write_repair_pack(root=root / "rp")
    target, checks = sr.build_successor(out_root=root / "rel", repair_root=root / "rp")
    return target, checks, root


# ---------------------------------------------------------------- guard: negatives
def test_guard_rejects_delivered_v1_index_cells():
    hits = card_id_hits(IDX, (V1 / IDX).read_bytes())
    ids = sorted({i for h in hits for i in h["ids"]})
    assert len(ids) == 19 and all(h["where"].startswith("cell Export!E") for h in hits)


def test_guard_rejects_index_embedded_in_d23_message():
    hits = card_id_hits(D23, (V1 / D23).read_bytes())
    assert hits and all(h["where"].startswith("attachment 11-13-external-submission-evidence-index.xlsx: cell") for h in hits)


def test_guard_rejects_held_pdf_with_card_id(plan, tmp_path):
    a = copy.deepcopy(plan.assets_by_name["11-12-external-dispute-notice.pdf"])
    a["document_spec"]["fields"]["case_file_item"] = "11-D21"
    pdf = render_document(a, "Case correspondence team")
    assert card_id_hits("x.pdf", pdf) and card_id_hits("x.pdf", pdf)[0]["where"] == "page 1"
    pkg = tmp_path / "pkg"
    shutil.copytree(sr.BASE, pkg)
    (pkg / "intake-bundle/originals/attachments/11-12-external-dispute-notice.pdf").write_bytes(pdf)
    row = next(c for c in verify_package(pkg) if c["name"] == GUARD)
    assert not row["ok"] and "11-12-external-dispute-notice.pdf" in row["detail"]


def test_v1_package_now_fails_only_the_strengthened_guard():
    rows = verify_package(sr.BASE, accepted_package=sr.EXPORT)
    assert [c["name"] for c in rows if not c["ok"]] == [GUARD]


# ---------------------------------------------------------------- guard: positives
def test_guard_passes_business_language_and_references():
    text = ("Please see 11-13-external-submission-evidence-index.xlsx and the DC-11, ID-11 and RF-11 items. Invoice INV-11-AFCA for "
            "matter AF-e4f54a57eb8ac4390308, obligation OBL-11-REMEDY, member ES-M-11-2025, on 2026-01-14 at 10:20 (D-day 11). "
            "Draft 2 of the dispute file; section D12 of the policy.").encode()
    assert card_id_hits("note.txt", text) == []


def test_guard_ignores_raw_zip_bytes_but_reads_cells(plan):
    eff = sr.index_effective(plan.assets["AST-6c79aeba2042bfb60a"], sr.load_rulings()[1]["index_projection"])
    data, _ = render_asset(eff, "Alicia Romero")
    assert card_id_hits("i.xlsx", data) == [] and card_id_hits("i.bin", data) == []


def test_accepted_packages_stay_clean():
    for d in (sr.EXPORT / "intake-bundle", sr.BASE.parent / "case-11-rehearsal-v3/intake-bundle"):
        files = {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*") if p.is_file() and p.name not in ("delivery-manifest.json", "INTAKE-FILES.sha256")}
        assert scan_files(files) == {}


# ---------------------------------------------------------------- rulings
def test_index_projection_matches_ruling_and_keeps_order(plan, rulings):
    r = rulings[1]["index_projection"]
    eff = sr.index_effective(plan.assets[r["asset_id"]], r)
    assert eff["document_spec"]["rows"] == r["expected_rows"] and eff["available_at"] == "2026-01-21T16:05:00+11:00"
    assert not any("case_file_item" in p for p in eff["document_spec"]["field_temporal_roles"])
    assert [c["row"] for c in eff["effective_spec"]["title_changes"]] == [7, 10, 16]
    assert "seconds" not in json.dumps(eff["document_spec"]["rows"])


@pytest.mark.parametrize("tamper", ["title", "date", "crosswalk", "available", "extra_change"])
def test_index_projection_refuses_disagreement(plan, rulings, tamper):
    r = copy.deepcopy(rulings[1]["index_projection"])
    if tamper == "title":
        r["expected_rows"][3]["title"] = "Complaint acknowledgment"
    elif tamper == "date":
        r["expected_rows"][0]["date"] = "2025-12-22"
    elif tamper == "crosswalk":
        r["private_original_row_to_card_crosswalk"]["0"] = "11-D02"
    elif tamper == "available":
        r["available_at"] = "2026-01-21T09:00:00+11:00"
    else:
        r["private_title_replacements"]["11-D01"] = "Service request"
        r["expected_rows"][0]["title"] = "Service request"
    with pytest.raises(sr.RepairRefused):
        sr.index_effective(plan.assets[r["asset_id"]], r)


def test_money_presentation_is_exact():
    assert sr.format_aud(250000) == "A$2,500.00" and sr.parse_aud("A$2,500.00") == 250000
    assert sr.format_aud(10000) == "A$100.00" and sr.format_aud(18840000) == "A$188,400.00"
    assert sr.parse_aud("A$2,500") is None and sr.parse_aud("$2,500.00") is None and sr.parse_aud("A$2500.00") is None


@pytest.mark.parametrize("value", ["A$25,000.00", "A$2,500", "$2,500.00", "A$2,500.01"])
def test_invoice_display_refuses_inexact_amount(plan, rulings, value):
    r = dict(rulings[1]["invoice_display"], display_value=value)
    with pytest.raises(sr.RepairRefused):
        sr.invoice_effective(plan.assets[r["asset_id"]], r)


def test_invoice_must_reconcile_with_native_fee_ledger(plan, rulings):
    r = rulings[1]["invoice_display"]
    good = (V1 / "originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv").read_bytes()
    sr.invoice_effective(plan.assets[r["asset_id"]], r, good)
    with pytest.raises(sr.RepairRefused):
        sr.invoice_effective(plan.assets[r["asset_id"]], r, good.replace(b",invoiced,250000,", b",invoiced,25000,"))


def test_unruled_pdf_rendering_is_unchanged(plan):
    a = plan.assets_by_name["11-12-external-dispute-notice.pdf"]
    assert render_asset(a, plan.actor(a["owner"])["name"])[0] == (V1 / "originals/attachments/11-12-external-dispute-notice.pdf").read_bytes()


def test_literal_repair_needs_exactly_one_occurrence():
    rep = {"id": "X", "operation": "replace_once", "old": "abc", "new": "x"}
    assert sr.apply_literal({"blocks": [{"p": "1 abc 2"}]}, rep) == ({"blocks": [{"p": "1 x 2"}]}, "/blocks/0/p")
    for bad in ({"blocks": [{"p": "none"}]}, {"blocks": [{"p": "abc abc"}]}, {"a": "abc", "b": "abc"}):
        with pytest.raises(sr.RepairRefused):
            sr.apply_literal(bad, rep)


def test_tampered_repair_pack_is_refused(tmp_path):
    rp = tmp_path / "rp"
    d, _ = sr.write_repair_pack(root=rp)
    spec = json.loads((d / "REPAIR-SPEC.json").read_text())
    spec["literal_repairs"][0]["new"] = "Nothing further is asserted."
    (d / "REPAIR-SPEC.json").write_text(json.dumps(spec))
    with pytest.raises(sr.RepairRefused):
        sr.load_repair(root=rp)


# ---------------------------------------------------------------- successor
def test_successor_checks_all_pass_and_package_verifies(successor):
    target, checks, _ = successor
    assert checks and all(c["ok"] for c in checks)
    assert all(c["ok"] for c in verify_package(target, accepted_package=sr.EXPORT))


def test_successor_changes_exactly_five_originals_with_supersession(successor):
    target, _, _ = successor
    man = json.loads((target / "intake-bundle/delivery-manifest.json").read_text())
    v2 = [o for o in man["originals"] if o["version"] == 2]
    assert sorted(o["original_id"] for o in v2) == sorted(sr.load_rulings()[2]["changed_originals"])
    assert all(o["review_status"] == "pending_independent_review" and o["supersedes"]["package"] == "case-11-remaining-cards-v1" for o in v2)
    assert all(o["review_status"] in ("accepted", "accepted_inherited") for o in man["originals"] if o["version"] == 1)
    old = {o["original_id"]: o for o in json.loads((V1 / "delivery-manifest.json").read_text())["originals"]}
    assert all(o.get("event_at") == old[o["original_id"]].get("event_at") for o in man["originals"])
    cmp = json.loads((target / "restricted/review/V1-TO-SUCCESSOR-COMPARISON.json").read_text())
    assert cmp["counts"] == {"originals": 23, "byte_identical": 18, "repaired": 5}


def test_existing_package_is_preserved(successor):
    assert sr.sha((V1 / "delivery-manifest.json").read_bytes()) == "97c60b8c32c3002be78d81f60f883a32152e00390b09b40c386d93a1699c1fd8"


def test_complete_case_inventory_counts(successor):
    inv = json.loads((successor[0] / "restricted/review/COMPLETE-CASE-INVENTORY.json").read_text())
    c = inv["counts"]
    assert (c["cards"], c["accepted_in_receiving_export"], c["source_accepted_new"], c["pending_repaired"], c["missing"]) == (29, 12, 14, 3, 0)
    assert c["successor_originals_by_status"] == {"accepted": 16, "accepted_inherited": 2, "pending_independent_review": 5}
    assert not any(a["status"] == "MISSING" for card in inv["cards"] for a in card["attachments"])
