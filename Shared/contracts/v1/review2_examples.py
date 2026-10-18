"""Isolated contract specimens and reference checks for review-1 closure.

Executed by check_contracts.py. These identities/records are not approved runtime
inputs, customer stories, diagnostic gold or outputs for the dashboard.
"""
from datetime import timedelta, date

examples = {"purpose": "isolated contract examples only; excluded from every runtime release"}
now = "2026-10-14T09:00:00+11:00"
root = {**context, "evidence_as_of": "2026-10-01T00:00:00+10:00", "evidence_boundary": "exclusive_stock_endpoint"}

def selection_digest(binding):
    names = ["predicate", "selectors", "effective_evidence_as_of", "evidence_boundary", "versions", "input_set_sha256", "assessment_id", "assessed_evidence_as_of", "analysis_mode", "analysis_as_of"]
    body = {name: copy.deepcopy(binding[name]) for name in names}
    for name in ["population_id", "population_version", "population_sha256"]:
        body["predicate"].pop(name)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def population(name, endpoint, selectors=None, measure="cases.open", stock=True):
    p = {"population_id": "probe-pop-"+name, "population_version": "probe-v1", "population_sha256": digest, "entity_unit": "case_id", "definition": {"registry_id": "runtime-measures-0.2.0", "measure_id": measure, "definition_version": "0.2.0"}, "activity_start": "2025-10-01T00:00:00+10:00", "activity_end_exclusive": endpoint, "receipt_basis": "original_institution", "receipt_cohort_start": None, "receipt_cohort_end_exclusive": None, "stock_endpoint": endpoint if stock else None, "evidence_cutoff": endpoint, "knowledge_rule": "stock_at_endpoint" if stock else "flow_at_selected_cutoff", "taxonomy_level": None, "taxonomy_version": None, "issue_ids": [], "selection_operator": "none", "issue_population_mode": "active_at_endpoint" if stock else "all_linked_to_date", "case_ids": [], "afca_matter_ids": [], "case_statuses": ["open"] if stock else [], "age_bands": [], "membership_states": ["included"], "combination_projection": "not_applicable", "extension_parameters": {}}
    b = {"population_id": p["population_id"], "binding_version": "probe-v1", "predicate": p, "selectors": selectors or [], "predicate_sha256": digest, "effective_evidence_as_of": endpoint, "evidence_boundary": "exclusive_stock_endpoint" if stock else "inclusive_cutoff", "versions": {"source_set_version": "probe-input-"+name, "analysis_set_version": "probe-analysis-"+name, "taxonomy_version": None, "algorithm_version": "probe-calculation-v1"}, "input_set_sha256": hashlib.sha256(name.encode()).hexdigest(), "assessment_id": "probe-assessment-"+name, "assessed_evidence_as_of": endpoint, "analysis_mode": "reconstructed", "analysis_as_of": None, "computed_at": now, "published_at": now}
    b["predicate_sha256"] = b["predicate"]["population_sha256"] = selection_digest(b)
    return b

def coverage(unit="case_id", dimension="eligibility", n=2):
    return {"coverage_ids": [], "eligible_count": n, "included_count": n, "excluded_count": 0, "uncertain_count": 0, "missing_count": 0, "assessment_state": "complete_for_declared_release", "limitations": [], "entity_unit": unit, "dimension": dimension}

def cell(name, binding, n=2):
    point = {"value": n, "state": "known", "unit": "cases", "reason": None, "contributor_set_reference": "probe-set-"+name, "included_count": n, "excluded_count": 0, "uncertain_count": 0, "coverage_ids": [], "calculation_reference": "probe-calc-"+name}
    return {"key": name, "label": "Contract probe "+name, "point": point, "distribution": None, "contributor_set_id": point["contributor_set_reference"], "population_id": binding["population_id"], "population": binding, "coverage": coverage(n=n), "additional_coverage": [], "interval_start": None, "interval_end_exclusive": binding["effective_evidence_as_of"], "effective_end": binding["effective_evidence_as_of"], "partial_bucket": False}

march = population("march", "2026-04-01T00:00:00+11:00")
september = population("september", "2026-10-01T00:00:00+10:00")
stock_cells = [cell("march", march, 2), cell("september", september, 3)]
stock_result = {"result_id": "probe-two-stocks", "contract_version": "1.0.0", "result_context": {"context": root, "predicate": september["predicate"], "versions": september["versions"], "computed_at": now, "published_at": now, "calculation_id": "probe-calc-series", "input_set_sha256": september["input_set_sha256"]}, "computation_state": "ready", "shape": "series", "cells": stock_cells, "legacy_result_reference": None, "synthetic_demo": True}
probe("two-endpoint whole result", "result", stock_result)
examples["two_endpoint_result"] = stock_result

# A reference semantic validator for these specimens, not an API implementation.
lineage = {b["versions"]["analysis_set_version"]: b["assessed_evidence_as_of"] for b in [march, september]}
def binding_ok(c):
    b = c["population"]
    return (c["population_id"] == b["population_id"] == b["predicate"]["population_id"] and
        b["binding_version"] == b["predicate"]["population_version"] and
        b["predicate_sha256"] == b["predicate"]["population_sha256"] == selection_digest(b) and
        c["contributor_set_id"] == c["point"]["contributor_set_reference"] and
        b["assessed_evidence_as_of"] == b["effective_evidence_as_of"] == b["predicate"]["evidence_cutoff"] and
        (b["predicate"]["knowledge_rule"] != "stock_at_endpoint" or
            b["predicate"]["stock_endpoint"] == c["effective_end"] == b["effective_evidence_as_of"] == lineage.get(b["versions"]["analysis_set_version"])))

check("stock cells have independent correct bindings", all(binding_ok(c) for c in stock_cells), "reference semantic specimen")
bad = copy.deepcopy(stock_cells[0]); bad["population"]["predicate_sha256"] = "a"*64
check("mismatched population hash rejected", not binding_ok(bad), "reference semantic specimen")
bad = copy.deepcopy(stock_cells[0]); bad["population"]["versions"] = september["versions"]; bad["population"]["predicate_sha256"] = bad["population"]["predicate"]["population_sha256"] = selection_digest(bad["population"])
check("September lineage cannot bind March", not binding_ok(bad), "reference semantic specimen")
bad = copy.deepcopy(stock_cells[0]); bad["point"]["contributor_set_reference"] = "probe-other-set"
check("conflicting contributor references rejected", not binding_ok(bad), "reference semantic specimen")
page = {"result_id": stock_result["result_id"], "cell_key": "march", "population_id": march["population_id"], "contributor_set_id": "probe-set-march", "predicate_sha256": march["predicate_sha256"], "population": march, "total_count": 2, "items": [{"entity_id": "probe-case-"+str(i), "case_ids": ["probe-case-"+str(i)], "source_refs": [citation], "inclusion_reason": "Isolated specimen", "observation_reference": None} for i in [1, 2]], "next_cursor": None}
probe("contributor page with full binding", "contributor_page", page)
check("contributor page binding equality", page["population"] == stock_cells[0]["population"] and page["predicate_sha256"] == march["predicate_sha256"] and page["contributor_set_id"] == stock_cells[0]["contributor_set_id"], "reference semantic specimen")
examples["contributor_page"] = page

partition = []
for state in ["on_time", "unknown"]:
    b = population(state, root["evidence_as_of"], [{"kind": "commitment_outcome", "outcome": state, "due_basis": "original"}], "mx.commitments.follow_through", stock=False)
    b["predicate"]["entity_unit"] = "commitment_id"; b["predicate"]["definition"] = {"registry_id": "shared-measures-1.0.0", "measure_id": "mx.commitments.follow_through", "definition_version": "1.0.0"}
    b["predicate_sha256"] = b["predicate"]["population_sha256"] = selection_digest(b)
    c = cell(state, b, 1); c["point"]["unit"] = "commitments"; c["coverage"] = coverage("commitment_id", n=1)
    probe("typed MX state cell:"+state, "result_cell", c); partition.append(c)
check("state partition uses distinct selectors", partition[0]["population"]["selectors"] != partition[1]["population"]["selectors"], "reference semantic specimen")
examples["mx_partition_cells"] = partition

participant = {"party_reference": "probe-member", "role": "member", "represented_member_reference": "probe-member", "authority_state": "supported", "evidence_refs": [citation]}
common = {"analysis_id": "probe-response-analysis", "logical_occurrence_id": "probe-response-occurrence", "version": "probe-v1", "supersedes_analysis_id": None, "case_ids": ["probe-case"], "member_reference": "probe-member", "member_identity_state": "known", "member_life_state": "living", "problem_reference": None, "problem_link_state": "uncertain", "event_time": {"precision": "day", "date": "2026-05-31"}, "source_bindings": [{"source_id": citation["source_id"], "content_sha256": citation["content_sha256"]}], "supporting_refs": [citation], "contradictory_refs": [], "evidence_state": "uncertain", "attribution": "direct_words", "context_id": root["context_id"], "assessed_evidence_as_of": root["evidence_as_of"], "computed_at": now, "published_at": now, "policy": {"access": {"view_ids": ["complaints"], "classification": "standard", "redaction_state": "none"}, "entitlement_policy_id": "probe-policy", "permission_revision": "probe-v1"}, "review_state": "not_reviewed"}
response = {"kind": "member_outcome_response", "common": common, "response_relationship_id": "probe-response-candidate", "response_occasion_id": "probe-response-occasion", "respondent": participant, "target_id": None, "target_version": None, "target_link_state": "uncertain", "target_identity_state": "unidentified", "target_link_reason": "Available statement does not establish target", "dimension": "unknown", "expressed_position": "disagree", "stated_reason": None, "supersedes_position_relationship_id": None}
probe("unidentified response target candidate", "member_outcome_response", response)
known_uncertain = {**response, "target_id": "probe-target", "target_version": "probe-v1", "target_identity_state": "identified", "dimension": "service"}
probe("known target uncertain linkage", "member_outcome_response", known_uncertain)
supported = {**known_uncertain, "target_link_state": "supported", "common": {**common, "evidence_state": "supported"}}
probe("supported exact response target", "member_outcome_response", supported)
probe("supported missing target rejected", "member_outcome_response", {**response, "target_link_state": "supported"}, False)
eligible = lambda r: r["target_link_state"] == "supported" and r["target_identity_state"] == "identified" and r["dimension"] != "unknown" and r["common"]["evidence_state"] == "supported"
check("only supported exact-target relationship counted", [eligible(r) for r in [response, known_uncertain, supported]] == [False, False, True], "reference semantic specimen")
examples["response_candidates_and_supported"] = [response, known_uncertain, supported]

day_due = {**deadline, "wording": "by 31 May", "boundary": "2026-06-01T00:00:00+10:00", "cohort_anchor": {"precision": "day", "date": "2026-05-31"}}
instant_due = {**day_due, "wording": "at midnight 1 June", "precision": "instant", "boundary_kind": "inclusive_instant", "cohort_anchor": {"precision": "instant", "instant": "2026-06-01T00:00:00+10:00"}}
probe("day-only due with May cohort", "deadline", day_due)
probe("instant due with June cohort", "deadline", instant_due)
probe("day/inclusive mismatch rejected", "deadline", {**day_due, "boundary_kind": "inclusive_instant"}, False)
probe("instant/day-anchor mismatch rejected", "deadline", {**instant_due, "cohort_anchor": day_due["cohort_anchor"]}, False)
check("day-only promise stays in May", day_due["cohort_anchor"]["date"] < "2026-06-01", "reference semantic specimen")
check("exact midnight promise belongs to June", datetime.fromisoformat(instant_due["cohort_anchor"]["instant"]).month == 6, "reference semantic specimen")
completion = datetime.fromisoformat(day_due["boundary"])
check("exclusive day-boundary completion is late", not completion < datetime.fromisoformat(day_due["boundary"]), "reference semantic specimen")
check("inclusive instant completion on time", completion <= datetime.fromisoformat(instant_due["boundary"]), "reference semantic specimen")
dst_day = datetime(2026, 10, 4, tzinfo=zone); dst_next = datetime(2026, 10, 5, tzinfo=zone)
check("calendar next-day boundary across DST is 23 hours", dst_next.timestamp()-dst_day.timestamp() == 23*3600, "reference semantic specimen")
unknown_due = {**deadline, "wording": "soon", "interpretation_state": "unknown", "boundary": None, "boundary_kind": "unknown", "cohort_anchor": None, "precision": "unknown"}
probe("vague due has no cohort anchor", "deadline", unknown_due)
examples["deadline_cohorts"] = [day_due, instant_due, unknown_due]

closures = {"never_closed": {"status": "open", "closures": []}, "closed_stays_closed": {"status": "closed", "closures": ["2026-03-10"]}, "closed_reopened": {"status": "reopened", "closures": ["2026-03-10"]}, "closed_reclosed": {"status": "closed", "closures": ["2026-03-10", "2026-03-25"]}}
baseline_selected = {k: max(v["closures"]) for k, v in closures.items() if v["closures"] and v["status"] == "closed"}
companion_selected = {k: max(v["closures"]) for k, v in closures.items() if v["closures"]}
check("baseline excludes still-open reopened case", set(baseline_selected) == {"closed_stays_closed", "closed_reclosed"}, "reference semantic specimen")
check("companion closure cohort includes subsequently reopened", set(companion_selected) == {"closed_stays_closed", "closed_reopened", "closed_reclosed"}, "reference semantic specimen")
check("genuine reclose chooses final window closure", baseline_selected["closed_reclosed"] == "2026-03-25", "reference semantic specimen")
examples["closure_crosswalk"] = {"cases": closures, "baseline": baseline_selected, "companion": companion_selected}

duration_coverage = coverage("case_id", "time", 2)
identity_coverage = {**coverage("request_event_id", "identity", 3), "included_count": 2, "missing_count": 1}
probe("duration coverage counts cases not days", "coverage", duration_coverage)
probe("member-reach missing identity coverage counts requests", "coverage", identity_coverage)
examples["coverage_units"] = [duration_coverage, identity_coverage]
public = {"asset_version_id": "probe-redacted-asset", "logical_document_id": "probe-document", "version": "probe-v1", "representation_role": "derived_redacted", "filename": "redacted.pdf", "media_type": "application/pdf", "byte_size": 32, "bytes_sha256": digest, "scenario_document_time": {"precision": "unknown", "reason": "Unknown"}, "scenario_available_at": "2026-05-31T10:00:00+10:00", "permitted_source_ids": ["probe-redacted-source"], "permitted_parent_asset_version_ids": [], "lineage_disclosure": "limited", "permitted_actions": ["inline"]}
probe("redacted-child public projection hides denied parent", "public_asset", public)
probe("internal blob metadata not serialised", "public_asset", {**public, "blob_reference": "probe-private-blob"}, False)
examples["permission_filtered_redacted_asset"] = public

taxonomy = {"taxonomy_version": "probe-tax-v2", "published_at": now, "acceptance_id": "probe-tax-acceptance", "nodes": [{"node_id": "probe-family", "level": "family", "label": "Renamed family", "definition": "Contract example", "dimension": "original_problem", "parent_node_id": None, "aliases": ["Old label"], "permitted_finding_ids": ["probe-finding-1", "probe-finding-2"]}, {"node_id": "probe-mechanism", "level": "mechanism", "label": "Specific mechanism", "definition": "Contract example", "dimension": "original_problem", "parent_node_id": "probe-family", "aliases": [], "permitted_finding_ids": ["probe-finding-1"]}], "lineage_notes": []}
probe("typed renamed taxonomy with real child refs", "taxonomy", taxonomy)
examples["taxonomy"] = taxonomy
source_inventory = {"case_id": "probe-case", "stream_reference": "probe-sources-stream", "total_permitted_count": 1, "completeness": "complete_permitted", "items": [{"source_id": "probe-uncited-summary", "content_sha256": digest, "source_family": "call_summary", "subtype": "member_call", "scenario_available_at": "2026-05-31T10:00:00+10:00", "occurrence": {"precision": "day", "date": "2026-05-31"}, "permitted_asset_version_ids": []}], "next_cursor": None, "limitations": []}
probe("uncited source-only summary discoverable", "source_inventory", source_inventory)
examples["source_only_inventory"] = source_inventory
files = {"case_id": "probe-case", "total_permitted_count": 2, "items": [{"asset": {**public, "asset_version_id": name}, "permitted_parent_source_ids": ["probe-source-1", "probe-source-2"] if name.endswith("shared") else [], "permitted_attachment_occurrence_ids": ["probe-attachment-1", "probe-attachment-2"] if name.endswith("shared") else [], "extraction_state": state, "mapping_state": "unmapped"} for name,state in [("probe-failed-pdf", "failed"), ("probe-shared", "complete")]], "next_cursor": None}
probe("failed-extraction file and shared attachment inventory", "file_inventory", files)
examples["independent_file_inventory"] = files
projection = {"projection_id": "probe-case-state", "case_id": "probe-case", "assessed_evidence_as_of": root["evidence_as_of"], "computed_at": now, "published_at": now, "source_set_version": "probe-inputs", "analysis_set_version": "probe-analysis", "administrative": {"value": "closed", "evidence_refs": [citation], "reason": "Sourced closure"}, "merits": [{"target_reference": "probe-merits", "value": "communicated", "evidence_refs": [citation], "reason": "Decision communicated"}], "service": [{"target_reference": "probe-service", "value": "conflicting", "assertion_refs": [citation], "confirmation_refs": [], "contradictory_refs": [citation], "reason": "Completion assertion conflicts with execution evidence"}], "responses": [{"target_reference": "probe-update", "dimension": "explanation", "respondent": participant, "position": "update_only", "relationship_references": ["probe-update-response"], "evidence_refs": [citation], "reason": "Thanks for update, not acceptance of completion"}], "coverage": [], "resolver_version": "probe-resolver-v1"}
probe("independent administrative service response summaries", "state_projection", projection)
examples["independent_case_dimensions"] = projection
(HERE / "CONTRACT-EXAMPLES.json").write_text(json.dumps(examples, indent=2) + "\n")
