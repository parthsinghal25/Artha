import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REHEARSAL = BACKEND.parents[1] / "Data" / "synthesis" / "pilot-v2-releases" / "case-11-rehearsal-v1" / "intake-bundle"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Isolated temporary store. Specimen reviewers exist ONLY in this temp registry and are honoured only because
    SDB_ALLOW_SPECIMEN_ACCEPTANCE is set for the test process; the served store's config/reviewers.json is empty."""
    import json
    d = tmp_path / "data"
    monkeypatch.setenv("SDB_DATA_DIR", str(d))
    monkeypatch.setenv("SDB_ALLOW_UNREVIEWED_SOURCES", "1")
    registry = tmp_path / "reviewers.json"
    registry.write_text(json.dumps({"registry_id": "pytest-specimen-only", "reviewers": {
        "specimen-reviewer": {"display_name": "pytest specimen (not a real review)", "specimen": True,
                              "gates": ["source", "native_input", "extraction", "diagnostic_evaluation",
                                        "result_integrity"]},
        "specimen-source-only": {"display_name": "pytest specimen, source gate only", "specimen": True,
                                 "gates": ["source"]}}}))
    monkeypatch.setenv("SDB_REVIEWER_REGISTRY", str(registry))
    monkeypatch.setenv("SDB_ALLOW_SPECIMEN_ACCEPTANCE", "1")
    monkeypatch.setenv("SDB_REPORT_DIR", str(tmp_path / "reports"))     # test-double runs never reach reports/
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return d


@pytest.fixture()
def rt(data_dir):
    from sdbackend.runtime import make_runtime
    return make_runtime()


@pytest.fixture(scope="session")
def rehearsal_bundle():
    if not (REHEARSAL / "delivery-manifest.json").is_file():
        pytest.skip("case-11 rehearsal delivery not present")
    return REHEARSAL


@pytest.fixture()
def ingested(rt, rehearsal_bundle):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    summary = ingest(rt, receive(rehearsal_bundle))
    return rt, summary
