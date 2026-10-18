"""The published v3 economics prose carries v2 denominators (external erratum, immutable file). Future reports must be correct and
self-consistent; the published file is only READ here."""
import hashlib
import json

import pytest

from conftest import REAL_V3, needs_v3

ERRATUM = REAL_V3.parents[2] / "reviews/case-11-rehearsal-v3-2026-10-15/ECONOMICS-ERRATUM.json"


def test_freshly_generated_v3_report_states_the_correct_denominators(v3):
    res = json.loads((v3 / "restricted/review/ECONOMICS-AND-REPAIR.json").read_text())["repair_scope"]
    assert res["counts"] == {"cards_in_batch": 5, "directly_edited_cards": 1, "original_files": 12, "changed_original_files": 2, "unchanged_original_files": 10}
    assert res["directly_edited_prose_cards"] == ["11-D05"]
    assert res["quote_dependent_originals_changed_without_own_edit"] == ["11-D10"]
    assert res["reviewed_denominators"].startswith("1 of 5 cards directly edited; 2 of 12 original files changed bytes; 10 of 12 unchanged")
    n = res["counts"]
    assert f"{n['directly_edited_cards']} of {n['cards_in_batch']}" in res["reviewed_denominators"]
    assert f"{n['changed_original_files']} of {n['original_files']}" in res["reviewed_denominators"]


@needs_v3
def test_published_v3_file_is_unchanged_and_matches_the_external_erratum_hash():
    erratum = json.loads(ERRATUM.read_text())
    f = REAL_V3 / erratum["published_file"]
    assert hashlib.sha256(f.read_bytes()).hexdigest() == erratum["published_file_sha256"]  # still the immutable file the erratum names
    assert "2 of 5" in json.loads(f.read_text())["repair_scope"]["reviewed_denominators"]  # the stale wording is preserved, not edited
    assert erratum["correction"]["directly_edited_cards"] == 1 and erratum["correction"]["unchanged_original_files"] == 10
