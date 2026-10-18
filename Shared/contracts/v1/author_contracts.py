"""Author companion contracts, not application code or synthetic evidence.

Run from any directory. No Data/UI-owned files are modified.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "urn:example-super:runtime:0.2.0"
URN = "urn:example-super:shared:1.0.0"


def obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def ref(name):
    return {"$ref": f"#/$defs/{name}"}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


ID = {"type": "string", "minLength": 1, "maxLength": 160, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}
N = {"type": "integer", "minimum": 0}
TS = {"type": "string", "format": "date-time"}
DATE = {"type": "string", "format": "date"}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
IDS = {**arr(ID), "uniqueItems": True}
AGE_BANDS = {**arr(enum("0_7", "8_30", "31_60", "61_90", "91_180", "over_180", "unknown")), "uniqueItems": True}
PARAMETERS = {"type": "object", "properties": {
    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
    "relation_type": enum("co_occurrence", "ordered_follow_on", "supported_operational_consequence"),
    "closure_selection": enum("last_closure_in_window_per_case"),
    "duration_basis": enum("original_receipt_to_selected_closure", "open_spell_to_closure"),
    "dimension": enum("explanation", "decision", "handling", "service"),
    "commitment_scope": enum("member_facing", "internal"),
}, "additionalProperties": False}
EVIDENCE = {"$ref": BASE + "#/$defs/evidence_ref"}
ACCESS = {"$ref": BASE + "#/$defs/access"}
INPUT = {"$ref": BASE + "#/$defs/input_record"}
ANALYSIS = {"$ref": BASE + "#/$defs/analysis_record"}
ENTITY_KIND = enum("extracted_fact", "episode", "contact_link", "post_closure_return", "finding", "work_entry", "obligation", "financial_event", "afca_matter", "afca_link", "membership_interval", "handling_relationship", "commitment", "member_effort_event", "outcome_target", "member_outcome_response")

d = {}
d["definition_ref"] = obj(registry_id=ID, measure_id=ID, definition_version=ID)
d["time_value"] = {"oneOf": [obj(precision={"const": "instant"}, instant=TS), obj(precision={"const": "day"}, date=DATE), obj(precision={"const": "interval"}, earliest=TS, latest=TS), obj(precision={"const": "unknown"}, reason=STR)]}
d["policy_binding"] = obj(access=ACCESS, entitlement_policy_id=ID, permission_revision=ID)
d["context"] = obj(
    context_id=ID, contract_version={"const": "1.0.0"}, release_id=ID,
    scope_token=ID, view_id=ID, permission_revision=ID,
    capabilities={**arr(enum("aggregate", "contributors", "cases", "sources", "originals", "previews", "download")), "uniqueItems": True},
    timezone={"const": "Australia/Melbourne"}, evidence_as_of=TS,
    evidence_boundary=enum("inclusive_cutoff", "exclusive_stock_endpoint"), origin_population_reference=nullable(ref("population_reference")),
    analysis_mode=enum("reconstructed", "as_reported"), analysis_as_of=nullable(TS),
    source_set_version=ID, analysis_set_version=nullable(ID), taxonomy_version=nullable(ID),
    supported_definitions=arr(ref("definition_ref")), supported_response_dimensions={**arr(enum("explanation", "decision", "handling", "service")), "uniqueItems": True},
    resolved_at=TS, expires_at=TS, synthetic_demo={"const": True}
)
d["context"]["allOf"] = [
    {"if": {"properties": {"analysis_mode": {"const": "reconstructed"}}}, "then": {"properties": {"analysis_as_of": {"type": "null"}}}},
    {"if": {"properties": {"analysis_mode": {"const": "as_reported"}}}, "then": {"properties": {"analysis_as_of": TS}}},
]
d["asset"] = obj(
    asset_version_id=ID, logical_document_id=ID, version=ID,
    representation_role=enum("supplied_original", "derived_redacted", "preview"),
    filename=STR, media_type=enum("application/pdf", "message/rfc822", "text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "image/png", "image/jpeg", "text/plain", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/html"),
    byte_size=N, bytes_sha256=HASH, byte_validation_id=ID, blob_reference=ID,
    scenario_document_time=ref("time_value"), scenario_available_at=TS,
    ingested_at=TS, case_ids=IDS, afca_matter_ids=IDS,
    policy=ref("policy_binding"), source_ids=IDS, extraction_ids=IDS, preview_ids=IDS,
    derived_from_asset_version_ids=IDS, supersedes_asset_version_id=nullable(ID),
    lineage_reason=enum("initial", "new_document_version", "copy", "redaction", "preview"),
    acceptance_id=ID,
)
d["attachment"] = obj(
    attachment_occurrence_id=ID, parent_source_id=ID, parent_source_sha256=HASH,
    business_occurrence_id=nullable(ID), child_asset_version_id=nullable(ID),
    relation=enum("embedded", "separately_supplied", "forwarded_copy", "new_version", "advertised_unheld"),
    mime_part_path=nullable({"type": "string", "pattern": "^0(?:\\.[1-9][0-9]*)*$"}),
    advertised_filename=nullable(STR), scenario_available_at=TS, policy=ref("policy_binding"),
    copy_of_attachment_occurrence_id=nullable(ID), supporting_refs=arr(EVIDENCE, 1),
)
d["attachment"]["allOf"] = [
    {"if": {"properties": {"relation": {"const": "advertised_unheld"}}}, "then": {"properties": {"child_asset_version_id": {"type": "null"}}}, "else": {"properties": {"child_asset_version_id": ID}}},
    {"if": {"properties": {"relation": {"const": "embedded"}}}, "then": {"properties": {"mime_part_path": {"type": "string", "pattern": "^0(?:\\.[1-9][0-9]*)+$"}}}},
]
d["source_binding"] = obj(source_id=ID, content_sha256=HASH)
d["extraction"] = obj(
    extraction_id=ID, version=ID, asset_version_id=ID, bytes_sha256=HASH,
    extractor=STR, extractor_version=ID, configuration_sha256=HASH, processed_at=TS,
    state=enum("complete", "partial", "failed", "pending"), omissions=arr(STR),
    emitted_sources=arr(ref("source_binding")), acceptance_id=nullable(ID),
)
d["rect"] = obj(x0={"type": "number", "minimum": 0, "maximum": 1}, y0={"type": "number", "minimum": 0, "maximum": 1}, x1={"type": "number", "minimum": 0, "maximum": 1}, y1={"type": "number", "minimum": 0, "maximum": 1})
d["native_location"] = {"oneOf": [
    obj(kind={"const": "pdf"}, page={"type": "integer", "minimum": 1}, region=nullable(ref("rect")), coordinate_basis={"const": "upright_cropbox_top_left_normalized"}, rotation_applied=enum("0", "90", "180", "270")),
    obj(kind={"const": "image"}, original_width={"type": "integer", "minimum": 1}, original_height={"type": "integer", "minimum": 1}, exif_orientation={"type": "integer", "minimum": 1, "maximum": 8}, coordinate_basis={"const": "exif_normalized_top_left"}, region=nullable(ref("rect"))),
    obj(kind={"const": "xlsx"}, sheet_id=ID, sheet_name=STR, a1_range={"type": "string", "pattern": "^[A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?$"}),
    obj(kind={"const": "csv"}, parser_profile_id=ID, data_record_index={"type": "integer", "minimum": 1}, column_index={"type": "integer", "minimum": 1}, column_name=nullable(STR), native_row_key=nullable(STR)),
    obj(kind={"const": "eml"}, mime_part_path={"type": "string", "pattern": "^0(?:\\.[1-9][0-9]*)*$"}, section=enum("headers", "body_text", "body_html", "attachment"), decoded_representation_sha256=HASH, start_codepoint=nullable(N), end_codepoint=nullable(N)),
    obj(kind={"const": "text"}, decoded_representation_sha256=HASH, start_codepoint=N, end_codepoint=N),
    obj(kind={"const": "docx"}, part_name=STR, paragraph_index={"type": "integer", "minimum": 1}, start_codepoint=nullable(N), end_codepoint=nullable(N)),
]}
d["locator"] = obj(
    mapping_id=ID, citation=EVIDENCE, extraction_id=ID, asset_version_id=ID,
    bytes_sha256=HASH, state=enum("verified", "unmapped", "invalid"),
    precision=enum("region", "page", "cell_range", "record_column", "section", "paragraph", "text_span", "none"),
    location=nullable(ref("native_location")), validation_id=nullable(ID), reason=nullable(STR),
)
d["locator"]["allOf"] = [{"if": {"properties": {"state": {"const": "verified"}}}, "then": {"properties": {"location": ref("native_location"), "validation_id": ID, "reason": {"type": "null"}}}, "else": {"properties": {"location": {"type": "null"}, "precision": {"const": "none"}, "reason": STR}}}]
d["release_entry"] = obj(kind=enum("input", "asset", "extraction", "attachment", "mapping", "analysis"), immutable_id=ID, blob_reference=ID, bytes_sha256=HASH, acceptance_id=ID)
d["release"] = obj(
    release_id=ID, contract_version={"const": "1.0.0"}, runtime_schema_version={"const": "0.2.0"},
    registry_ids=IDS, published_at=TS, observation_start=DATE, observation_end_exclusive=DATE,
    timezone={"const": "Australia/Melbourne"}, synthetic_demo={"const": True},
    time_provenance={"const": "scenario_evidence_times_with_real_ingestion_and_analysis_audit"},
    entries=arr(ref("release_entry")), source_acceptance_id=ID, extraction_acceptance_id=nullable(ID),
)
d["predicate"] = obj(
    population_id=ID, population_version=ID, population_sha256=HASH,
    entity_unit=enum("case_id", "member_reference", "contact_id", "episode_id", "work_id", "financial_event_id", "obligation_id", "afca_matter_id", "closure_transition_id", "relationship_id", "commitment_id", "submission_id", "request_event_id", "response_relationship_id"),
    definition=ref("definition_ref"), activity_start=TS, activity_end_exclusive=TS,
    receipt_basis=enum("original_institution", "complaints_team"),
    receipt_cohort_start=nullable(DATE), receipt_cohort_end_exclusive=nullable(DATE),
    stock_endpoint=nullable(TS), evidence_cutoff=TS, knowledge_rule=enum("flow_at_selected_cutoff", "stock_at_endpoint", "population_at_selected_cutoff"),
    taxonomy_level=nullable(ID), taxonomy_version=nullable(ID), issue_ids=IDS,
    selection_operator=enum("none", "any", "all", "exact"),
    issue_population_mode=enum("active_in_window", "all_linked_to_date", "active_at_endpoint"),
    case_ids=IDS, afca_matter_ids=IDS, case_statuses={**arr(enum("open", "awaiting", "reopened", "closed", "unknown")), "uniqueItems": True}, age_bands=AGE_BANDS,
    membership_states={"const": ["included"]},
    combination_projection=enum("not_applicable", "full_level", "selected_only"),
    extension_parameters=PARAMETERS,
)
d["versions"] = obj(source_set_version=ID, analysis_set_version=ID, taxonomy_version=nullable(ID), algorithm_version=ID)
d["result_context"] = obj(context=ref("context"), predicate=ref("predicate"), versions=ref("versions"), computed_at=TS, published_at=TS, calculation_id=ID, input_set_sha256=HASH)
d["coverage"] = obj(coverage_ids=IDS, eligible_count=N, included_count=N, excluded_count=N, uncertain_count=N, missing_count=N, assessment_state=enum("complete_for_declared_release", "partial", "unknown", "not_applicable"), limitations=arr(STR))
d["coverage"]["properties"].update(entity_unit=enum("case_id", "member_reference", "contact_id", "episode_id", "work_id", "financial_event_id", "obligation_id", "afca_matter_id", "commitment_id", "submission_id", "request_event_id", "response_relationship_id", "closure_transition_id", "candidate_relationship_id"), dimension=enum("eligibility", "source_history", "identity", "linkage", "time", "classification"))
d["coverage"]["required"].extend(["entity_unit", "dimension"])
d["distribution"] = obj(
    unit=enum("days", "seconds"), total_count=N, known_count=N, missing_count=N,
    min=nullable({"type": "number"}), p25=nullable({"type": "number"}), median=nullable({"type": "number"}), p75=nullable({"type": "number"}), p90=nullable({"type": "number"}), max=nullable({"type": "number"}),
    quantile_algorithm={"const": "R7_linear"}, duration_basis=enum("original_receipt_to_cutoff", "current_open_spell_to_cutoff", "original_receipt_to_selected_closure", "open_spell_to_closure", "dependency_elapsed"),
    censoring=enum("not_censored", "open_inventory_separate"), eligible_cohort_reference=ID,
)
d["membership_interval"] = obj(membership_version_id=ID, finding_id=ID, finding_version=ID, taxonomy_node_id=ID, taxonomy_version=ID, case_id=ID, episode_id=nullable(ID), state=enum("included", "excluded", "uncertain"), start=ref("time_value"), end=nullable(ref("time_value")), ongoing_at=nullable(TS), endpoint_state=enum("active", "historical", "unknown"), supporting_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE), assessed_evidence_as_of=TS, published_at=TS)
d["handling_relationship"] = obj(relationship_id=ID, version=ID, case_id=ID, problem_ref=ID, handling_occurrence_ref=ID, relation=enum("co_occurrence", "ordered_follow_on", "supported_operational_consequence"), proposition=STR, evidence_state=enum("supported", "uncertain", "excluded", "conflicting"), chronology_state=enum("ordered", "unordered", "unknown"), supporting_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE), consequence_refs=arr(EVIDENCE), assessed_evidence_as_of=TS, published_at=TS)
d["participant"] = obj(party_reference=nullable(ID), role=enum("member", "authorised_representative", "beneficiary", "staff", "institution", "external_party", "unknown"), represented_member_reference=nullable(ID), authority_state=enum("supported", "uncertain", "not_applicable"), evidence_refs=arr(EVIDENCE))
d["mx_common"] = obj(analysis_id=ID, logical_occurrence_id=ID, version=ID, supersedes_analysis_id=nullable(ID), case_ids=IDS, member_reference=nullable(ID), member_identity_state=enum("known", "unknown", "conflicting"), member_life_state=enum("living", "deceased", "unknown"), problem_reference=nullable(ID), problem_link_state=enum("supported", "uncertain", "excluded"), event_time=ref("time_value"), source_bindings=arr(ref("source_binding"), 1), supporting_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE), evidence_state=enum("supported", "uncertain", "excluded", "conflicting"), attribution=enum("direct_words", "captured_undertaking", "attributed_report", "observed_record"), context_id=ID, assessed_evidence_as_of=TS, computed_at=TS, published_at=TS, policy=ref("policy_binding"), review_state=enum("not_reviewed", "challenged", "accepted_for_discussion"))
d["deadline"] = obj(wording=STR, evidence_refs=arr(EVIDENCE), interpretation_state=enum("interpreted", "unknown", "conflicting", "not_applicable"), boundary=nullable(TS), boundary_kind=enum("inclusive_instant", "exclusive_next_local_day", "unknown"), cohort_anchor=nullable(ref("time_value")), timezone=STR, precision=enum("instant", "day", "unknown"), calendar_id=nullable(ID), calendar_version=nullable(ID), interpretation_basis=STR)
d["deadline"]["allOf"] = [
    {"if": {"properties": {"interpretation_state": {"const": "interpreted"}}}, "then": {"properties": {"boundary": TS, "boundary_kind": enum("inclusive_instant", "exclusive_next_local_day"), "precision": enum("instant", "day"), "cohort_anchor": ref("time_value")}}, "else": {"properties": {"boundary": {"type": "null"}, "cohort_anchor": {"type": "null"}}}},
    {"if": {"properties": {"precision": {"const": "day"}, "interpretation_state": {"const": "interpreted"}}}, "then": {"properties": {"boundary_kind": {"const": "exclusive_next_local_day"}, "cohort_anchor": obj(precision={"const": "day"}, date=DATE)}}},
    {"if": {"properties": {"precision": {"const": "instant"}, "interpretation_state": {"const": "interpreted"}}}, "then": {"properties": {"boundary_kind": {"const": "inclusive_instant"}, "cohort_anchor": obj(precision={"const": "instant"}, instant=TS)}}},
]
d["fulfilment"] = obj(observation_id=ID, action_target_reference=ID, fulfils_criteria=enum("supported", "uncertain", "excluded", "conflicting"), completion_time=ref("time_value"), proposition=STR, supporting_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE))
d["commitment"] = obj(kind={"const": "commitment"}, common=ref("mx_common"), commitment_id=ID, promisor=ref("participant"), beneficiary=ref("participant"), scope=enum("member_facing", "internal"), action=STR, action_target_reference=nullable(ID), action_target_state=enum("identified", "unidentified"), action_target_reason=STR, fulfilment_criteria=STR, undertaking_basis=enum("direct", "captured", "reported_only", "uncertain"), original_due=ref("deadline"), conditions=arr(obj(proposition=STR, activation_state=enum("supported_active", "supported_inactive", "unknown", "conflicting"), activation_time=ref("time_value"), evidence_refs=arr(EVIDENCE))), original_opportunity_applicability=enum("applicable", "cancelled_before_opportunity", "condition_not_activated", "unknown", "conflicting"), applicability_refs=arr(EVIDENCE), cancellations=arr(obj(time=ref("time_value"), cancelled_by=ref("participant"), evidence_refs=arr(EVIDENCE))), revisions=arr(obj(revision_id=ID, changed_at=ref("time_value"), changed_by=ref("participant"), revised_due=ref("deadline"), assent_state=enum("supported", "unknown", "contested", "not_applicable"), evidence_refs=arr(EVIDENCE))), fulfilment_observations=arr(ref("fulfilment")), original_due_outcome=enum("on_time", "late", "explicitly_unmet", "unknown", "not_yet_due", "not_applicable"), classification_reason=STR, classification_refs=arr(EVIDENCE), revised_due_outcome=nullable(enum("on_time", "late", "explicitly_unmet", "unknown", "not_yet_due", "not_applicable")))
d["commitment"]["allOf"] = [{"if": {"properties": {"action_target_state": {"const": "identified"}}}, "then": {"properties": {"action_target_reference": ID}}, "else": {"properties": {"action_target_reference": {"type": "null"}, "common": {"properties": {"evidence_state": enum("uncertain", "excluded", "conflicting")}}}}}]
d["item_comparison"] = obj(item_reference=nullable(ID), item_version_reference=nullable(ID), requirement_reference=nullable(ID), prior_occurrence_reference=nullable(ID), receipt_state=enum("received", "not_received", "unknown", "conflicting"), validity_state=enum("recognised_valid_before_request", "not_established", "invalid", "expired", "changed_requirement", "conflicting", "not_applicable"), comparison_state=enum("eligible_repeat", "legitimate_new_or_changed", "uncertain", "excluded"), order_state=enum("supported_later", "not_later", "unknown"), supporting_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE), reason=STR)
d["item_comparison"]["allOf"] = [{"if": {"properties": {"comparison_state": {"const": "eligible_repeat"}}}, "then": {"properties": {"item_reference": ID, "item_version_reference": ID, "requirement_reference": ID, "prior_occurrence_reference": ID, "order_state": {"const": "supported_later"}, "supporting_refs": arr(EVIDENCE, 1)}}}]
d["member_effort_event"] = obj(kind={"const": "member_effort_event"}, common=ref("mx_common"), effort_kind=enum("repeated_explanation", "repeated_submission", "repeat_valid_evidence_request"), occasion_id=ID, occasion_type=enum("contact", "submission_attempt", "institution_request"), initiator=ref("participant"), prior_occurrence_ids=IDS, continuity_state=enum("supported", "uncertain", "excluded"), repetition_state=enum("supported", "uncertain", "excluded", "conflicting"), receipt_state=enum("received", "not_received", "unknown", "not_applicable"), repeated_concern=nullable(STR), item_comparisons=arr(ref("item_comparison")), qualifiers={**arr(enum("legitimate_repetition", "useful_new_information", "changed_requirement", "corrected_version", "staff_forward", "necessity_unknown")), "uniqueItems": True})
d["member_effort_event"]["allOf"] = [
    {"if": {"properties": {"effort_kind": {"const": "repeated_explanation"}}}, "then": {"properties": {"occasion_type": {"const": "contact"}}}},
    {"if": {"properties": {"effort_kind": {"const": "repeated_submission"}}}, "then": {"properties": {"occasion_type": {"const": "submission_attempt"}, "item_comparisons": arr(ref("item_comparison"), 1)}}},
    {"if": {"properties": {"effort_kind": {"const": "repeat_valid_evidence_request"}}}, "then": {"properties": {"occasion_type": {"const": "institution_request"}, "item_comparisons": arr(ref("item_comparison"), 1)}}},
]
d["outcome_target"] = obj(target_id=ID, version=ID, case_ids=IDS, problem_reference=nullable(ID), dimension=enum("explanation", "decision", "handling", "service"), proposition=STR, communication_occurrence_id=ID, communicated_at=ref("time_value"), recipient=ref("participant"), supporting_refs=arr(EVIDENCE, 1), contradictory_refs=arr(EVIDENCE), assessed_evidence_as_of=TS, published_at=TS)
d["member_outcome_response"] = obj(kind={"const": "member_outcome_response"}, common=ref("mx_common"), response_relationship_id=ID, response_occasion_id=ID, respondent=ref("participant"), target_id=nullable(ID), target_version=nullable(ID), target_link_state=enum("supported", "uncertain", "excluded"), target_identity_state=enum("identified", "unidentified"), target_link_reason=STR, dimension=enum("explanation", "decision", "handling", "service", "unknown"), expressed_position=enum("accept", "disagree", "unresolved", "update_only", "unknown", "conflicting"), stated_reason=nullable(STR), supersedes_position_relationship_id=nullable(ID))
d["member_outcome_response"]["allOf"] = [
    {"if": {"properties": {"target_identity_state": {"const": "unidentified"}}}, "then": {"properties": {"target_id": {"type": "null"}, "target_version": {"type": "null"}, "target_link_state": enum("uncertain", "excluded")}}, "else": {"properties": {"target_id": ID, "target_version": ID}}},
    {"if": {"properties": {"target_link_state": {"const": "supported"}}}, "then": {"properties": {"target_identity_state": {"const": "identified"}, "target_id": ID, "target_version": ID, "dimension": enum("explanation", "decision", "handling", "service")}}},
]
d["companion_analysis"] = {"oneOf": [ref(k) for k in ["membership_interval", "handling_relationship", "commitment", "member_effort_event", "outcome_target", "member_outcome_response"]]}
d["cell_selector"] = {"oneOf": [
    obj(kind={"const": "taxonomy_node"}, node_id=ID, taxonomy_level=ID),
    obj(kind={"const": "observed_combination"}, node_ids=IDS, taxonomy_level=ID, projection=enum("full_level", "selected_only"), assessment=enum("observed_supported_set", "assessed_none", "unassessed"), uncertain_membership_state=enum("any", "present", "absent")),
    obj(kind={"const": "combination_remainder"}, excluded_top_sets=arr(IDS), taxonomy_level=ID, projection=enum("full_level", "selected_only")),
    obj(kind={"const": "age_band"}, band=enum("0_7", "8_30", "31_60", "61_90", "91_180", "over_180", "unknown")),
    obj(kind={"const": "commitment_outcome"}, outcome=enum("on_time", "late", "explicitly_unmet", "unknown", "not_yet_due", "not_applicable"), due_basis={"const": "original"}),
    obj(kind={"const": "response_position"}, position=enum("accept", "disagree", "unresolved", "update_only", "unknown", "conflicting"), dimension=enum("explanation", "decision", "handling", "service")),
    obj(kind={"const": "handling_pair"}, problem_node_id=ID, handling_node_id=ID, relation_type=enum("co_occurrence", "ordered_follow_on", "supported_operational_consequence")),
    obj(kind={"const": "afca_route"}, route=enum("negotiation_agreement", "conciliation_agreement", "determination", "withdrawn", "other", "unknown")),
    obj(kind={"const": "afca_stage"}, stage_id=ID),
    obj(kind={"const": "coverage_disposition"}, disposition=enum("included", "excluded", "uncertain", "missing")),
    obj(kind={"const": "known_duration"}, state=enum("known", "missing")),
]}
d["population_binding"] = obj(
    population_id=ID, binding_version=ID, predicate=ref("predicate"), selectors=arr(ref("cell_selector")),
    predicate_sha256=HASH, effective_evidence_as_of=TS, evidence_boundary=enum("inclusive_cutoff", "exclusive_stock_endpoint"),
    versions=ref("versions"), input_set_sha256=HASH, assessment_id=ID, assessed_evidence_as_of=TS,
    analysis_mode=enum("reconstructed", "as_reported"), analysis_as_of=nullable(TS), computed_at=TS, published_at=TS,
)
d["result_cell"] = obj(key=ID, label=STR, point={"$ref": BASE + "#/$defs/metric_point"}, distribution=nullable(ref("distribution")), contributor_set_id=nullable(ID), population_id=ID, population=ref("population_binding"), coverage=ref("coverage"), additional_coverage=arr(ref("coverage")), interval_start=nullable(TS), interval_end_exclusive=nullable(TS), effective_end=nullable(TS), partial_bucket=BOOL)
d["result"] = obj(result_id=ID, contract_version={"const": "1.0.0"}, result_context=ref("result_context"), computation_state=enum("ready", "partial", "failed", "cancelled", "limit_reached"), shape=enum("scalar", "series", "partition", "distribution", "matrix"), cells=arr(ref("result_cell")), legacy_result_reference=nullable(ID), synthetic_demo={"const": True})
d["contributor"] = obj(entity_id=ID, case_ids=IDS, source_refs=arr(EVIDENCE), inclusion_reason=STR, observation_reference=nullable(ID))
d["contributor_page"] = obj(result_id=ID, cell_key=ID, population_id=ID, contributor_set_id=ID, predicate_sha256=HASH, population=ref("population_binding"), total_count=N, items=arr(ref("contributor")), next_cursor=nullable(STR))
d["asset_action"] = obj(action_id=ID, asset_version_id=ID, representation=enum("original", "derived_redacted", "preview"), disposition=enum("inline", "attachment"), retrieval_token=ID, expires_at=TS, media_type=STR, bytes_sha256=HASH)
d["population_reference"] = obj(result_id=ID, cell_key=ID, population_id=ID, predicate_sha256=HASH)
d["taxonomy_node"] = obj(node_id=ID, level=ID, label=STR, definition=STR, dimension=enum("original_problem", "handling_factor", "current_dependency"), parent_node_id=nullable(ID), aliases=arr(STR), permitted_finding_ids=IDS)
d["taxonomy"] = obj(taxonomy_version=ID, published_at=TS, acceptance_id=ID, nodes=arr(ref("taxonomy_node")), lineage_notes=arr(STR))
d["state_projection"] = obj(projection_id=ID, case_id=ID, assessed_evidence_as_of=TS, computed_at=TS, published_at=TS, source_set_version=ID, analysis_set_version=ID,
    administrative=obj(value=enum("open", "awaiting", "closed", "reopened", "unknown", "conflicting"), evidence_refs=arr(EVIDENCE), reason=STR),
    merits=arr(obj(target_reference=nullable(ID), value=enum("pending", "communicated", "accepted", "declined", "partial", "unknown", "conflicting", "not_applicable"), evidence_refs=arr(EVIDENCE), reason=STR)),
    service=arr(obj(target_reference=nullable(ID), value=enum("not_started", "attempted", "partial", "confirmed_complete", "unknown", "conflicting", "not_applicable"), assertion_refs=arr(EVIDENCE), confirmation_refs=arr(EVIDENCE), contradictory_refs=arr(EVIDENCE), reason=STR)),
    responses=arr(obj(target_reference=nullable(ID), dimension=enum("explanation", "decision", "handling", "service", "unknown"), respondent=ref("participant"), position=enum("accept", "disagree", "unresolved", "update_only", "unknown", "conflicting", "no_evidenced_response"), relationship_references=IDS, evidence_refs=arr(EVIDENCE), reason=STR)),
    coverage=arr(ref("coverage")), resolver_version=ID)
d["timeline_event"] = obj(event_id=ID, occurrence=ref("time_value"), event_type=enum("contact", "receipt", "request", "assessment", "decision", "approval", "attempt", "execution", "closure", "reopening", "work", "financial", "afca", "other"), proposition=STR, source_refs=arr(EVIDENCE, 1), observation_reference=nullable(ID), state=enum("supported", "uncertain", "conflicting"))
d["public_asset"] = obj(asset_version_id=ID, logical_document_id=ID, version=ID, representation_role=enum("supplied_original", "derived_redacted", "preview"), filename=STR, media_type=STR, byte_size=N, bytes_sha256=HASH, scenario_document_time=ref("time_value"), scenario_available_at=TS, permitted_source_ids=IDS, permitted_parent_asset_version_ids=IDS, lineage_disclosure=enum("complete_permitted", "limited"), permitted_actions={**arr(enum("inline", "download")), "uniqueItems": True})
d["public_extraction"] = obj(extraction_id=ID, version=ID, permitted_asset_version_id=nullable(ID), permitted_asset_bytes_sha256=nullable(HASH), extractor=STR, extractor_version=ID, processed_at=TS, state=enum("complete", "partial", "failed", "pending"), omissions=arr(STR), permitted_emitted_sources=arr(ref("source_binding")), lineage_disclosure=enum("complete_permitted", "limited"))
d["public_attachment"] = obj(attachment_occurrence_id=ID, parent_source_id=ID, parent_source_sha256=HASH, child_asset_version_id=nullable(ID), relation=enum("embedded", "separately_supplied", "forwarded_copy", "new_version", "advertised_unheld"), mime_part_path=nullable(STR), advertised_filename=nullable(STR), scenario_available_at=TS, permitted_copy_of_attachment_occurrence_id=nullable(ID))
d["source_payload"] = obj(record=INPUT, source_binding=ref("source_binding"), citation=nullable(EVIDENCE), citation_state=enum("valid", "invalid", "not_requested"), extractions=arr(ref("public_extraction")), permitted_asset_actions=arr(ref("asset_action")), permitted_attachments=arr(ref("public_attachment")), mappings=arr(ref("locator")))
d["case_payload"] = obj(case_record=INPUT, state_projection=ref("state_projection"), events=arr(ref("timeline_event")), stream_reference=ID, total_permitted_event_count=N, next_cursor=nullable(STR), completeness=enum("complete_permitted", "partial", "unknown"), limitations=arr(STR))
d["source_inventory"] = obj(case_id=ID, stream_reference=ID, total_permitted_count=N, completeness=enum("complete_permitted", "partial", "unknown"), items=arr(obj(source_id=ID, content_sha256=HASH, source_family=STR, subtype=STR, scenario_available_at=TS, occurrence=ref("time_value"), permitted_asset_version_ids=IDS)), next_cursor=nullable(STR), limitations=arr(STR))
d["entity_payload"] = obj(resolved_entity_id=ID, immutable_record_id=ID, entity_kind=ENTITY_KIND, record={"oneOf": [ANALYSIS, ref("companion_analysis")]}, related_records=arr({"oneOf": [ANALYSIS, ref("companion_analysis")]}), source_refs=arr(EVIDENCE), population_reference=nullable(ref("population_reference")), next_cursor=nullable(STR), total_permitted_related_count=N, stream_reference=ID)
d["file_inventory"] = obj(case_id=ID, total_permitted_count=N, items=arr(obj(asset=ref("public_asset"), permitted_parent_source_ids=IDS, permitted_attachment_occurrence_ids=IDS, extraction_state=enum("complete", "partial", "failed", "pending", "not_applicable"), mapping_state=enum("verified", "unmapped", "invalid", "not_applicable"))), next_cursor=nullable(STR))
d["finding_page"] = obj(items=arr({"oneOf": [ANALYSIS, ref("companion_analysis")]}), population_reference=nullable(ref("population_reference")), total_permitted_count=N, stream_reference=ID, next_cursor=nullable(STR))
d["error"] = obj(code=enum("RESOURCE_UNAVAILABLE", "CONTEXT_EXPIRED", "CONTEXT_INCOMPATIBLE", "INVALID_REQUEST", "DEFINITION_UNAVAILABLE", "HISTORY_UNAVAILABLE", "NOT_CALCULATED", "INTEGRITY_FAILURE", "UNSUPPORTED_REPRESENTATION", "LIMIT_REACHED", "INTERNAL_FAILURE"), message=STR, retryable=BOOL, correlation_id=ID)

requests = {}
requests["context.resolve"] = obj(operation={"const": "context.resolve"}, preferred_view_id=nullable(ID), release_id=ID, evidence_as_of=TS, analysis_mode=enum("reconstructed", "as_reported"), analysis_as_of=nullable(TS))
requests["analysis.query"] = obj(operation={"const": "analysis.query"}, context_id=ID, query_id=ID, definition=ref("definition_ref"), filters=obj(activity_start=TS, activity_end_exclusive=TS, receipt_cohort_start=nullable(DATE), receipt_cohort_end_exclusive=nullable(DATE), receipt_basis=enum("original_institution", "complaints_team"), granularity=enum("none", "daily", "weekly", "monthly", "quarterly", "annual"), issue_ids=IDS, selection_operator=enum("none", "any", "all", "exact"), issue_population_mode=enum("active_in_window", "all_linked_to_date", "active_at_endpoint"), case_ids=IDS, afca_matter_ids=IDS, case_statuses={**arr(enum("open", "awaiting", "closed", "reopened", "unknown")), "uniqueItems": True}, age_bands=AGE_BANDS, taxonomy_level=nullable(ID), combination_projection=enum("not_applicable", "full_level", "selected_only"), parameters=PARAMETERS))
requests["findings.list"] = obj(operation={"const": "findings.list"}, context_id=ID, result_id=nullable(ID), case_id=nullable(ID), cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["contributors.list"] = obj(operation={"const": "contributors.list"}, context_id=ID, result_id=ID, cell_key=ID, cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["case.get"] = obj(operation={"const": "case.get"}, context_id=ID, case_id=ID, cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["case.files.list"] = obj(operation={"const": "case.files.list"}, context_id=ID, case_id=ID, media_types={**arr(STR), "uniqueItems": True}, cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["source.get"] = obj(operation={"const": "source.get"}, context_id=ID, source_id=ID, content_sha256=nullable(HASH), citation=nullable(EVIDENCE))
requests["asset.get"] = obj(operation={"const": "asset.get"}, context_id=ID, asset_version_id=ID, action=enum("inline", "download"))
requests["location.resolve"] = obj(operation={"const": "location.resolve"}, context_id=ID, citation=EVIDENCE, extraction_id=ID, asset_version_id=ID)
requests["taxonomy.get"] = obj(operation={"const": "taxonomy.get"}, context_id=ID, taxonomy_version=ID)
requests["case.sources.list"] = obj(operation={"const": "case.sources.list"}, context_id=ID, case_id=ID, source_families={**arr(STR), "uniqueItems": True}, cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["entity.get"] = obj(operation={"const": "entity.get"}, context_id=ID, entity_id=ID, entity_kind=ENTITY_KIND, immutable_record_id=nullable(ID), cursor=nullable(STR), page_size={"type": "integer", "minimum": 1, "maximum": 100})
requests["context.resolve"]["properties"]["population_reference"] = nullable(ref("population_reference"))
requests["context.resolve"]["required"].append("population_reference")
requests["analysis.query"]["properties"]["population_reference"] = nullable(ref("population_reference"))
requests["analysis.query"]["required"].append("population_reference")
requests["findings.list"]["properties"].update(population_reference=nullable(ref("population_reference")), record_kinds={**arr(ENTITY_KIND), "uniqueItems": True}, membership_dispositions={**arr(enum("included", "excluded", "uncertain")), "uniqueItems": True})
requests["findings.list"]["required"].extend(["population_reference", "record_kinds", "membership_dispositions"])
payloads = {"context.resolve": ref("context"), "analysis.query": ref("result"), "findings.list": ref("finding_page"), "contributors.list": ref("contributor_page"), "case.get": ref("case_payload"), "case.files.list": ref("file_inventory"), "case.sources.list": ref("source_inventory"), "taxonomy.get": ref("taxonomy"), "entity.get": ref("entity_payload"), "source.get": ref("source_payload"), "asset.get": ref("asset_action"), "location.resolve": ref("locator")}
for name, spec in requests.items():
    d[name + ".request"] = spec
    good = obj(operation={"const": name}, request_id=ID, status={"const": "ok"}, context=ref("context"), payload=payloads[name])
    bad = obj(operation={"const": name}, request_id=ID, status={"const": "unavailable"}, context=nullable(ref("context")), error=ref("error"))
    d[name + ".response"] = {"oneOf": [good, bad]}
d["request"] = {"oneOf": [ref(name + ".request") for name in requests]}
d["response"] = {"oneOf": [ref(name + ".response") for name in requests]}

schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": URN, "title": "Example Super shared companion/API contracts 1.0.0", "$defs": d, "oneOf": [ref(k) for k in ["asset", "attachment", "extraction", "locator", "release", "taxonomy", "state_projection", "membership_interval", "handling_relationship", "commitment", "member_effort_event", "outcome_target", "member_outcome_response", "result", "request", "response"]]}
(ROOT / "shared.schema.json").write_text(json.dumps(schema, indent=2) + "\n")

registry = {"registry_id": "shared-measures-1.0.0", "definition_version": "1.0.0", "baseline_registry": "Data/contracts/metric-registry.json@0.2.0", "definitions": [
    {"measure_id": "cases.closure_transitions", "unit": "closure_transitions", "entity": "closure_transition_id", "basis": "flow", "rule": "Unique source-backed nonclosed-to-closed transitions in the activity window, deduplicated across copies; a later genuine close after reopening is another transition. Unknown predecessor or ambiguous ordering excluded and disclosed."},
    {"measure_id": "cases.selected_closure_duration", "unit": "days", "entity": "case_id", "basis": "flow_distribution", "rule": "Unique cases with a supported closure in the window, whether closed or subsequently reopened at selected evidence cutoff. Select last supported closure in that window per case. Original receipt to that administrative closure in local calendar days; unknown endpoints excluded/disclosed. Distinct from baseline cases.closed_duration@0.2.0, which excludes still-open cases at selected evidence cutoff."},
    {"measure_id": "cases.open_original_age", "unit": "days", "entity": "case_id", "basis": "stock_distribution", "rule": "Eligible open cases at endpoint; local calendar date minus original receipt date (or explicitly selected team basis). Missing receipt dates stay missing. R7 quantiles with N; no average-age substitution."},
    {"measure_id": "issues.observed_combinations", "unit": "cases", "entity": "case_id", "basis": "population_partition", "rule": "Each assessed eligible case assigned once by its exact supported set at one taxonomy level; separate cases with no supported membership into assessed-none and unassessed. Additional uncertain memberships disclosed without promotion. Full-level or explicitly selected projection; top K descending count then node-ID order plus union remainder."},
    {"measure_id": "issues.handling_relationship_cases", "unit": "cases", "entity": "case_id", "basis": "relationship_matrix", "rule": "Distinct case union per problem and handling pair under an explicit accepted relation type. Cells overlap. Ordered association is not causal impact; operational consequence requires its own evidence. No avoided cost/delay inference."},
]}
proposal = json.loads((ROOT.parents[2] / "Data/contracts/proposals/member-experience-2026-10-14.json").read_text())
adopted = {"commitments.recorded", "commitments.follow_through", "effort.repeated_explanation_contacts", "effort.repeated_submissions", "effort.repeat_valid_evidence_requests", "effort.members_repeat_valid_evidence", "responses.expressed_positions"}
for m in proposal["metrics"]:
    suffix = m["proposal_id"].removeprefix("proposal.mx.")
    if suffix not in adopted:
        continue
    copy = {k: v for k, v in m.items() if k not in {"status", "definition_version", "normative_definition"}}
    copy.update(measure_id="mx." + suffix, definition_version="1.0.0", normative_definition="MEMBER-EXPERIENCE.md")
    registry["definitions"].append(copy)
(ROOT / "measure-additions.json").write_text(json.dumps(registry, indent=2) + "\n")
print(f"Authored {len(d)} definitions and {len(requests)} operations; no evidence or application created.")
