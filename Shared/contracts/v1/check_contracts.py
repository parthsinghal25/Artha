"""Offline contract probes. No source/demo/gold/application records are exported.

Requires jsonschema/referencing. Shape probes are deliberately separate from
semantic reference probes; these do not certify a future service implementation.
"""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
BASE_PATH = PROJECT / "Data/contracts/runtime.schema.json"
shared = json.loads((HERE / "shared.schema.json").read_text())
base = json.loads(BASE_PATH.read_text())
evaluation = json.loads((PROJECT / "Data/contracts/evaluation.schema.json").read_text())
registry = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in [shared, base, evaluation]])
checks = []


def check(name, condition, layer):
    assert condition, name
    checks.append({"check": name, "layer": layer, "passed": True})


def valid(name, specimen):
    schema = {"$schema": shared["$schema"], "$ref": shared["$id"] + "#/$defs/" + name}
    return not list(Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(specimen))


def probe(name, definition, specimen, expected=True):
    check(name, valid(definition, specimen) == expected, "schema")


for s in [shared, base, evaluation]:
    Draft202012Validator.check_schema(s)
    check("metaschema:" + s["$id"], True, "schema")

digest = hashlib.sha256(b"isolated-contract-probe-not-evidence").hexdigest()
asset_request = {"operation": "asset.get", "context_id": "test-context", "asset_version_id": "test-asset", "action": "inline"}
probe("asset request", "asset.get.request", asset_request)
bad = {**asset_request, "access": {"view_ids": ["admin"]}}
probe("client cannot supply access grants", "asset.get.request", bad, False)
probe("download enum rejects execution", "asset.get.request", {**asset_request, "action": "execute"}, False)
probe("arbitrary filesystem identity rejected", "asset.get.request", {**asset_request, "asset_version_id": "/tmp/source.pdf"}, False)

source_request = {"operation": "source.get", "context_id": "test-context", "source_id": "test-source", "content_sha256": None, "citation": None}
probe("full source without citation", "source.get.request", source_request)
probe("malformed digest", "source.get.request", {**source_request, "content_sha256": "pending"}, False)

citation = {"source_id": "test-source", "content_sha256": digest, "anchor": {"kind": "text", "start_codepoint": 1, "end_codepoint": 2}}
locator = {"mapping_id": "test-map", "citation": citation, "extraction_id": "test-extraction", "asset_version_id": "test-asset", "bytes_sha256": digest, "state": "verified", "precision": "page", "location": {"kind": "pdf", "page": 1, "region": None, "coordinate_basis": "upright_cropbox_top_left_normalized", "rotation_applied": "0"}, "validation_id": "test-validation", "reason": None}
probe("verified whole-page locator", "locator", locator)
unmapped = {**locator, "state": "unmapped", "precision": "none", "location": None, "validation_id": None, "reason": "No native mapping"}
probe("valid unmapped locator", "locator", unmapped)
probe("unmapped may not invent location", "locator", {**unmapped, "location": locator["location"]}, False)
bad = copy.deepcopy(locator); bad["location"]["page"] = 0
probe("PDF pages are one based", "locator", bad, False)
bad = copy.deepcopy(locator); bad["location"]["coordinate_basis"] = "pixels"
probe("undeclared coordinate basis", "locator", bad, False)
bad = copy.deepcopy(locator); bad["location"]["region"] = {"x0": -0.1, "y0": 0, "x1": 0.5, "y1": 1}
probe("bounds outside normalised page", "locator", bad, False)

csv_location = {"kind": "csv", "parser_profile_id": "csv-utf8-comma-header-v1", "data_record_index": 1, "column_index": 2, "column_name": "note", "native_row_key": None}
probe("CSV location", "native_location", csv_location)
probe("CSV header is not a data record", "native_location", {**csv_location, "data_record_index": 0}, False)
probe("EML stable root part", "native_location", {"kind": "eml", "mime_part_path": "0.1.2", "section": "body_text", "decoded_representation_sha256": digest, "start_codepoint": 0, "end_codepoint": 3})

error = {"code": "RESOURCE_UNAVAILABLE", "message": "Resource unavailable in this context", "retryable": False, "correlation_id": "test-correlation"}
for operation in ["context.resolve", "analysis.query", "findings.list", "contributors.list", "case.get", "case.files.list", "case.sources.list", "taxonomy.get", "entity.get", "source.get", "asset.get", "location.resolve"]:
    response = {"operation": operation, "request_id": "test-request", "status": "unavailable", "context": None, "error": error}
    probe("safe unavailable:" + operation, operation + ".response", response)
    bad = copy.deepcopy(response); bad["error"]["restricted_filename"] = "secret.pdf"
    probe("no error metadata extension:" + operation, operation + ".response", bad, False)

context = {"context_id": "test-context", "contract_version": "1.0.0", "release_id": "test-release", "scope_token": "test-scope", "view_id": "complaints", "permission_revision": "test-policy", "capabilities": ["sources"], "timezone": "Australia/Melbourne", "evidence_as_of": "2026-09-30T23:59:59+10:00", "evidence_boundary": "inclusive_cutoff", "origin_population_reference": None, "analysis_mode": "reconstructed", "analysis_as_of": None, "source_set_version": "test-inputs", "analysis_set_version": "test-outputs", "taxonomy_version": None, "supported_definitions": [], "supported_response_dimensions": [], "resolved_at": "2026-10-14T09:00:00+11:00", "expires_at": "2026-10-14T09:05:00+11:00", "synthetic_demo": True}
probe("explicit reconstruction context", "context", context)
probe("reconstruction cannot pretend historical publication", "context", {**context, "analysis_as_of": "2026-09-30T23:59:59+10:00"}, False)
probe("as-reported requires publication cutoff", "context", {**context, "analysis_mode": "as_reported"}, False)
probe("as-reported explicit cutoff", "context", {**context, "analysis_mode": "as_reported", "analysis_as_of": "2026-10-14T09:00:00+11:00"})

deadline = {"wording": "by Friday", "evidence_refs": [citation], "interpretation_state": "interpreted", "boundary": "2026-05-09T00:00:00+10:00", "boundary_kind": "exclusive_next_local_day", "cohort_anchor": {"precision": "day", "date": "2026-05-08"}, "timezone": "Australia/Melbourne", "precision": "day", "calendar_id": None, "calendar_version": None, "interpretation_basis": "Explicit named date resolved by context"}
probe("interpreted deadline", "deadline", deadline)
probe("interpreted deadline must have boundary", "deadline", {**deadline, "boundary": None}, False)
probe("unknown deadline cannot carry guessed boundary", "deadline", {**deadline, "interpretation_state": "unknown"}, False)

point = {"value": None, "state": "unavailable", "unit": "cases", "reason": "No feed", "contributor_set_reference": None, "included_count": 0, "excluded_count": 0, "uncertain_count": 0, "coverage_ids": [], "calculation_reference": "test-calc"}
v = Draft202012Validator({"$ref": base["$id"] + "#/$defs/metric_point"}, registry=registry)
check("unavailable is not numeric zero", not v.is_valid({**point, "value": 0}), "baseline schema")

# Independently specified mathematical/convention checks, not future service tests.
def r7(xs, p):
    xs = sorted(xs)
    if not xs: return None
    h = (len(xs) - 1) * p
    i = int(h)
    return xs[i] if i == len(xs)-1 else xs[i] + (h-i)*(xs[i+1]-xs[i])

check("even median", r7([2, 4, 9, 12], .5) == 6.5, "reference convention")
check("R7 p25", r7([2, 4, 9, 12], .25) == 3.5, "reference convention")
check("R7 p90", abs(r7([2, 4, 9, 12], .9)-11.1) < 1e-10, "reference convention")
check("empty distribution is null", r7([], .5) is None, "reference convention")
check("single observation", r7([7], .9) == 7, "reference convention")
a, b = {"case-1", "case-2"}, {"case-2", "case-3"}
check("Any union excludes duplication", len(a | b) == 3, "reference convention")
check("All intersection", a & b == {"case-2"}, "reference convention")
check("linked sums can exceed union", len(a)+len(b) > len(a|b), "reference convention")
rows = list(csv.reader(io.StringIO('id,note\n1,"two\nlines"\n2,normal\n')))
check("quoted newline not an extra CSV record", len(rows)-1 == 2 and rows[1][1] == "two\nlines", "reference convention")
check("code-point span survives astral Unicode", "A💡B"[1:2] == "💡", "reference convention")
zone = ZoneInfo("Australia/Melbourne")
check("October Melbourne DST", datetime(2026, 10, 14, tzinfo=zone).utcoffset().total_seconds() == 11*3600, "reference convention")
check("September Melbourne offset", datetime(2026, 9, 30, tzinfo=zone).utcoffset().total_seconds() == 10*3600, "reference convention")

# Additional review-closure specimens/reference probes. This script is not a service.
exec(compile((HERE / "review2_examples.py").read_text(), "review2_examples.py", "exec"))
report = {"contract_version": "1.0.0", "scope": "isolated schema and reference-convention probes only; not evidence, pipeline, access-enforcement, model or UI acceptance", "passed": len(checks), "failed": 0, "checks": checks}
(HERE / "CONTRACT-CHECK-RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"passed": len(checks), "failed": 0, "scope": report["scope"]}))
