"""Complete-case receiving CANDIDATE (E02): built into tmp_path from the real accepted slice, the remaining-cards sign-off and the
published successor; read only."""
import json

import pytest

from sdgen import receiving_export as rx


@pytest.fixture(scope="module")
def cand(tmp_path_factory):
    return rx.case11_complete_candidate(tmp_path_factory.mktemp("cand"))


def man(p):
    return json.loads((p / "intake-bundle/delivery-manifest.json").read_text())


def test_candidate_counts_and_checks(cand):
    c = json.loads((cand / "restricted-review/CANDIDATE-COUNTS.json").read_text())
    assert (c["originals"], c["from_slice_verbatim"], c["remaining_accepted"], c["remaining_inherited_already_in_slice"], c["pending"], c["cards"]) == (43, 22, 16, 2, 5, 29)
    assert all(x["ok"] for x in json.loads((cand / "restricted-review/EXPORT-CHECKS.json").read_text())["checks"])
    m = man(cand)
    assert m["status"]["candidate"] is True and m["coverage"]["cards_of_case_not_in_export"] == []


def test_slice_entries_and_native_rows_are_carried_verbatim(cand):
    s = man(rx.CASE11_SLICE_EXPORT)
    m = man(cand)
    ids = {o["original_id"]: o for o in m["originals"]}
    assert all(ids[o["original_id"]] == o for o in s["originals"])
    assert m["native_administrative_inputs"] == s["native_administrative_inputs"]
    for n in s["native_administrative_inputs"]:
        assert (cand / "intake-bundle" / n["file"]).read_bytes() == (rx.CASE11_SLICE_EXPORT / "intake-bundle" / n["file"]).read_bytes()


def test_pending_are_exactly_the_repaired_successor_versions(cand):
    m = man(cand)
    pend = [o for o in m["originals"] if any(p["decision"] != "accepted" for p in o["review_provenance"])]
    assert sorted(o["original_id"] for o in pend) == sorted(m["status"]["pending_original_ids"])
    assert len(pend) == 5 and all(o["version"] == 2 and o["lineage"]["supersedes"]["package"] == "case-11-remaining-cards-v1" for o in pend)


def test_candidate_cannot_pass_the_accepted_export_checks(cand):
    rows = rx.verify_export(cand)
    assert [c["name"] for c in rows if not c["ok"]] == ["every original carries review provenance to an accepted sign-off"]


def test_ledger_and_note_entries_keep_native_identity_and_times(cand):
    m = man(cand)
    led = {o["filename"]: o for o in m["originals"] if o["kind"] == "ledger_export"}
    d29 = led["11-16-complaints-effort-export.xlsx"]
    assert d29["original_id"] == "AST-eeb19c880495b2ead3" and d29["scenario_time"]["available_at"]["value"] == "2026-03-27T23:15:00+11:00"
    assert d29["declared_media_type"].endswith("spreadsheetml.sheet") and d29["sha256"].startswith("a7e8c558")
    assert {o["kind"] for o in m["originals"]} >= {"email", "note", "ledger_export", "attachment_original", "portal_form", "call_summary", "native_register_export"}
    assert all(o["declared_media_type"] == "text/plain" for o in m["originals"] if o["kind"] == "note")
