"""Unit tests for Phase 2 parameterized complaint archetypes."""
import json
from pathlib import Path

import jsonschema
import pytest
from sdgen.archetypes import (
    ARCHETYPE_REGISTRY,
    ArchetypeCaseContext,
    create_archetype_instance,
)
from sdgen.paths import ROOT
from sdgen.tz import OffsetMismatch, melbourne_abbr

RUNTIME_SCHEMA_PATH = ROOT / "Data/contracts/runtime.schema.json"


@pytest.fixture(scope="module")
def runtime_schema():
    with open(RUNTIME_SCHEMA_PATH) as f:
        return json.load(f)


@pytest.mark.parametrize("archetype_id", list(ARCHETYPE_REGISTRY.keys()))
def test_archetype_generation_and_zero_leakage(archetype_id):
    ctx = ArchetypeCaseContext(
        case_reference=f"C-TEST-{archetype_id[:6]}",
        member_reference=f"MEM-TEST-9921",
        request_reference=f"REQ-TEST-001",
        member_name="Gweneth Sterling",
        start_date="2025-11-01",
        seed=101
    )
    arch = create_archetype_instance(archetype_id, ctx)
    specs = arch.generate_artifact_specs()
    assert len(specs) >= 3, f"Archetype {archetype_id} should produce at least 3 artifacts"

    # Verify zero leakage across all generated text specs
    for s in specs:
        spec_text = str(s.spec) + s.title + s.filename
        leaks = arch.validate_zero_leakage(spec_text, source_identifier=s.filename)
        assert not leaks, f"Leakage detected in {archetype_id} / {s.filename}: {leaks}"


@pytest.mark.parametrize("archetype_id", list(ARCHETYPE_REGISTRY.keys()))
def test_native_rows_runtime_schema_compliance(archetype_id, runtime_schema):
    ctx = ArchetypeCaseContext(
        case_reference=f"C-TEST-{archetype_id[:6]}",
        member_reference=f"MEM-TEST-9921",
        request_reference=f"REQ-TEST-001",
        member_name="Gweneth Sterling",
        start_date="2025-11-01",
        seed=101
    )
    arch = create_archetype_instance(archetype_id, ctx)
    native = arch.generate_native_rows()

    assert "cases" in native and len(native["cases"]) == 1
    assert "parties" in native and len(native["parties"]) >= 1

    # Validate each case row against runtime.schema.json
    for case_row in native["cases"]:
        jsonschema.validate(instance=case_row, schema=runtime_schema)
        # Verify required envelope fields
        env = case_row["envelope"]
        for req_env_field in ["record_id", "logical_id", "recorded_at", "available_at", "access", "evidence_refs"]:
            assert req_env_field in env, f"Missing {req_env_field} in case envelope"
        # Verify required data fields
        data = case_row["data"]
        for req_data_field in ["case_id", "party_ids", "channel", "product_or_journey", "manager_reference",
                               "original_receipt_date", "complaints_receipt_date", "broad_tags"]:
            assert req_data_field in data, f"Missing {req_data_field} in case data"

    # Validate each party row against runtime.schema.json
    for party_row in native["parties"]:
        jsonschema.validate(instance=party_row, schema=runtime_schema)
        env = party_row["envelope"]
        for req_env_field in ["record_id", "logical_id", "recorded_at", "available_at", "access", "evidence_refs"]:
            assert req_env_field in env, f"Missing {req_env_field} in party envelope"
        data = party_row["data"]
        for req_data_field in ["party_id", "party_role", "member_reference", "support_preferences"]:
            assert req_data_field in data, f"Missing {req_data_field} in party data"


@pytest.mark.parametrize("start_date, expected_tz", [
    ("2025-10-15", "AEDT"),  # October (Daylight Saving, UTC+11)
    ("2025-12-31", "AEDT"),  # December (Daylight Saving, UTC+11)
    ("2026-03-25", "AEDT"),  # Late March (Daylight Saving, UTC+11)
    ("2026-04-10", "AEST"),  # Early April (Standard Time, UTC+10)
    ("2026-07-01", "AEST"),  # July (Standard Time, UTC+10)
    ("2026-09-15", "AEST"),  # September (Standard Time, UTC+10)
])
def test_dst_transition_and_tz_offset(start_date, expected_tz):
    ctx = ArchetypeCaseContext(
        case_reference="C-TZ-001",
        member_reference="MEM-TZ-001",
        request_reference="REQ-TZ-001",
        member_name="Clara Oswald",
        start_date=start_date,
        seed=77
    )
    arch = create_archetype_instance("death_benefit_distribution", ctx)
    # Generate timestamp and verify offset
    iso = arch.build_iso_timestamp(day_offset=0, hour=10, minute=0)
    abbr = melbourne_abbr(iso)
    assert abbr == expected_tz, f"Expected {expected_tz} for {start_date}, got {abbr}"

    # Also test an offset that crosses the April DST boundary:
    # 2026-03-30 (AEDT) + 10 days -> 2026-04-09 (AEST)
    if start_date == "2026-03-25":
        iso_future = arch.build_iso_timestamp(day_offset=15, hour=10, minute=0)
        assert melbourne_abbr(iso_future) == "AEST"


def test_archetype_determinism():
    ctx1 = ArchetypeCaseContext(
        case_reference="C-DET-001",
        member_reference="MEM-DET-001",
        request_reference="REQ-DET-001",
        member_name="Edward Hopper",
        start_date="2025-10-15",
        seed=4242
    )
    ctx2 = ArchetypeCaseContext(
        case_reference="C-DET-001",
        member_reference="MEM-DET-001",
        request_reference="REQ-DET-001",
        member_name="Edward Hopper",
        start_date="2025-10-15",
        seed=4242
    )
    arch1 = create_archetype_instance("death_benefit_distribution", ctx1)
    arch2 = create_archetype_instance("death_benefit_distribution", ctx2)

    specs1 = arch1.generate_artifact_specs()
    specs2 = arch2.generate_artifact_specs()

    assert len(specs1) == len(specs2)
    for s1, s2 in zip(specs1, specs2):
        assert s1.artifact_id == s2.artifact_id
        assert s1.filename == s2.filename
        assert s1.created_at_iso == s2.created_at_iso
        assert s1.spec == s2.spec

    native1 = arch1.generate_native_rows()
    native2 = arch2.generate_native_rows()
    assert native1 == native2


def test_100_case_bundle_manifest_and_schema(runtime_schema):
    import hashlib
    bundle = ROOT / "Data/synthesis/receiving-exports/batch-100-complete-accepted-v1/intake-bundle"
    if not bundle.exists():
        pytest.skip("100-case bundle not yet generated")

    manifest_bytes = (bundle / "delivery-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["manifest_kind"] == "pre-extraction-delivery-manifest"
    assert manifest["manifest_version"] == "0.2.0"
    assert len(manifest["originals"]) == 385

    # Check native files
    validator = jsonschema.Draft202012Validator(runtime_schema)
    case_lines = (bundle / "native/case.jsonl").read_text().splitlines()
    assert len(case_lines) == 100
    for ln in case_lines:
        validator.validate(json.loads(ln))

    party_lines = (bundle / "native/party.jsonl").read_text().splitlines()
    assert len(party_lines) == 160
    for ln in party_lines:
        validator.validate(json.loads(ln))

