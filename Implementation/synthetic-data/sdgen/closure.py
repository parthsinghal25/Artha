"""R01-R04 closure table with exact files/tests and v1->v2 hash comparison. Marks nothing accepted."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .build import sha256, tree_digest

CLOSURE_V2 = {
    "R01": {
        "title": "Preserve uncertainty about submission occasions (D05, with D10 quotation dependency)",
        "files": ["authored/case-11/v2/11-D05.json", "originals/emails/2026-01-02_0845_beth-martin_to_alicia-romero.eml",
                  "originals/emails/2026-01-08_0930_alicia-romero_to_alice-hughes.eml"],
        "tests": ["test_r01_d05_uses_the_bounded_wording_and_keeps_the_rest", "test_r01_d05_receipt_and_attachment_bytes_unchanged",
                  "test_r01_d10_quotes_corrected_d05_exactly_with_original_header", "test_r01_d10_own_message_unchanged",
                  "test_r01_regression_blocks_unassigned_extra_submission_narrative_on_d05",
                  "test_r01_scoped_regression_does_not_ban_a_future_member_upload_elsewhere"],
    },
    "R02": {
        "title": "Complete summary fidelity and correct its matrix (D06)",
        "files": ["authored/case-11/v2/11-D06.json", "originals/call-summaries/2026-01-02_0955_case-summary-service_to_alicia-romero.txt",
                  "restricted/private-controls/fidelity/11-D06-summary-fidelity.json"],
        "tests": ["test_r02_summary_states_interruption_neutrally_and_stays_in_range", "test_r02_private_dialogue_unchanged_from_v1",
                  "test_r02_matrix_separates_spoken_from_header_metadata", "test_r02_fixture_omitted_interruption_is_detected",
                  "test_r02_fixture_negated_next_step_is_detected", "test_r02_fixture_changed_dissatisfaction_is_detected",
                  "test_r02_fixture_header_metadata_misattributed_as_spoken_is_detected",
                  "test_r02_fixture_case_reference_narrated_as_spoken_is_detected"],
    },
    "R03": {
        "title": "Protect reviewed releases and decisions",
        "files": ["sdgen/build.py", "sdgen/review.py", "sdgen/checks.py", "sdgen/cli.py"],
        "tests": ["test_r03_existing_release_is_never_replaced[delivered]", "test_r03_existing_release_is_never_replaced[accepted]",
                  "test_r03_existing_release_is_never_replaced[repair_required]", "test_r03_review_writers_refuse_existing_records",
                  "test_r03_check_write_refuses_existing_results_and_output_inside_package", "test_r03_publish_refuses_when_target_appears_after_staging",
                  "test_r03_failed_build_leaves_no_final_directory", "test_r03_cli_release_refuses_existing_package_and_requires_explicit_version"],
    },
    "R04": {
        "title": "Freeze exact inputs and reuse accepted bytes",
        "files": ["sdgen/build.py", "sdgen/checks.py"],
        "tests": ["test_r04_pins_load_the_recorded_revision_never_latest", "test_r04_byte_integrity_works_without_plan_authored_or_build_record",
                  "test_r04_changed_authored_input_makes_reconstruction_unavailable_not_an_integrity_failure",
                  "test_r04_changed_plan_makes_semantic_layer_unavailable_not_failed",
                  "test_r04_current_code_reproduces_delivered_v1_bytes_from_its_recorded_inputs",
                  "test_r04_v2_carries_the_nine_accepted_originals_byte_identical", "test_r04_only_d05_d06_d10_change_and_have_distinct_identity",
                  "test_r04_v2_records_overlay_and_inherited_input_hashes", "test_r04_refuses_to_carry_an_original_not_recorded_accepted",
                  "test_r04_refuses_to_carry_when_base_bytes_changed", "test_r04_v2_checks_all_layers_clean_and_reconstructs"],
    },
    "extra": {
        "title": "Handoff section 5: timezone and scan scope",
        "files": ["sdgen/tz.py", "sdgen/render_call.py", "sdgen/render_image.py", "sdgen/checks.py"],
        "tests": ["test_tz_summer_and_winter_call_summaries", "test_tz_offset_must_match_melbourne_rules", "test_tz_dst_boundaries",
                  "test_tz_receipt_uses_zone_of_supplied_date", "test_scan_allows_ordinary_words_but_catches_control_fields"],
    },
}

CLOSURE_V3 = {
    "SR-2 / R01-final": {
        "title": "D05 paragraph-2 sentence (external ruling SR-2) and exact D10 quotation",
        "files": ["authored/case-11/v3/11-D05.json", "authored/case-11/supplemental-rulings/SR-2-case11-rehearsal-v3.md",
                  "originals/emails/2026-01-02_0845_beth-martin_to_alicia-romero.eml",
                  "originals/emails/2026-01-08_0930_alicia-romero_to_alice-hughes.eml"],
        "tests": ["test_sr2_replaces_exactly_the_ruled_sentence_in_d05",
                  "test_sr2_ambiguity_regression_rejects_the_first_person_upload_assertion",
                  "test_sr2_only_the_d05_sentence_and_d10_quotation_change_business_text",
                  "test_sr2_d10_own_message_unchanged_and_quotes_corrected_d05_with_original_header",
                  "test_sr2_attachments_and_receipt_bytes_unchanged",
                  "test_r01_regression_blocks_unassigned_extra_submission_narrative_on_d05",
                  "test_r01_scoped_regression_does_not_ban_a_future_member_upload_elsewhere"],
    },
    "R03-A": {
        "title": "Atomic no-replace publication (kernel primitive, fail closed)",
        "files": ["sdgen/atomic.py", "sdgen/build.py"],
        "tests": ["test_atomic_primitive_refuses_existing_targets[empty_dir]", "test_atomic_primitive_refuses_existing_targets[nonempty_dir]",
                  "test_atomic_primitive_refuses_existing_targets[file]", "test_atomic_primitive_refuses_existing_targets[live_symlink]",
                  "test_atomic_primitive_refuses_existing_targets[dangling_symlink]", "test_atomic_primitive_moves_when_target_absent",
                  "test_atomic_race_empty_target_created_after_the_last_absence_check",
                  "test_atomic_race_nonempty_target_and_symlink_after_the_last_absence_check",
                  "test_atomic_publish_refuses_delivered_accepted_repair_required[delivered]",
                  "test_atomic_publish_refuses_delivered_accepted_repair_required[accepted]",
                  "test_atomic_publish_refuses_delivered_accepted_repair_required[repair_required]",
                  "test_atomic_competing_publishers_exactly_one_wins", "test_atomic_unsupported_host_fails_closed_and_moves_nothing",
                  "test_atomic_publish_has_no_second_existence_check",
                  "test_r03_review_writers_refuse_existing_records", "test_r03_publish_refuses_when_target_appears_after_staging"],
    },
    "reuse": {
        "title": "Exact reuse of the ten independently accepted v2 originals; final-identity naming",
        "files": ["sdgen/build.py (REPAIRS v3)", "sdgen/checks.py", "sdgen/closure.py", "sdgen/release.py"],
        "tests": ["test_v3_carries_the_ten_accepted_originals_byte_identical", "test_v3_exactly_two_originals_change_with_distinct_immutable_ids",
                  "test_v3_d06_summary_and_private_controls_carried_unchanged", "test_v3_records_pins_inherited_dependencies_and_base_bindings",
                  "test_v3_code_repair_does_not_rerender_accepted_originals", "test_v3_reports_use_final_package_identity_not_staging",
                  "test_v3_checks_all_layers_clean_and_reconstructs", "test_v3_review_material_has_blank_decisions"],
    },
}
CLOSURE = CLOSURE_V2


def junit_results(path):
    res = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        res[tc.get("name")] = not (tc.find("failure") is not None or tc.find("error") is not None)
    return res


def write_closure(pkg_dir, base_dir, junit_xml, check_summary, snapshots=None, final_name=None):
    """snapshots: {package_name: path-to-snapshot-json}; each package tree (next to the base) must equal its snapshot."""
    pkg, base = Path(pkg_dir), Path(base_dir)
    name = final_name or pkg.name
    pm = json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())
    ver = pm["batch"]["package_version"]
    table_def = CLOSURE_V3 if ver >= 3 else CLOSURE_V2
    j = junit_results(junit_xml)
    table = {}
    all_ok = True
    for rid, c in table_def.items():
        tests = {t: j.get(t) for t in c["tests"]}
        ok = all(v is True for v in tests.values())
        all_ok &= ok
        table[rid] = {**c, "test_results": {k: ("pass" if v else ("fail" if v is False else "missing")) for k, v in tests.items()},
                      "generator_status": "implemented_and_author_tests_pass" if ok else "NOT_CLOSED",
                      "independent_closure": "pending_external_review"}
    bm = json.loads((base / "intake-bundle/delivery-manifest.json").read_text())
    b_files = {o["file"]: (o["original_id"], o["sha256"]) for o in bm["originals"]}
    p_files = {o["file"]: (o["original_id"], o["sha256"], o["lineage"]["relation"]) for o in pm["originals"]}
    cmp = []
    for f in sorted(set(b_files) | set(p_files)):
        b, p = b_files.get(f), p_files.get(f)
        cmp.append({"file": f, "base_original_id": b and b[0], "base_sha256": b and b[1], "package_original_id": p and p[0],
                    "package_sha256": p and p[1], "result": "byte_identical" if b and p and b[1] == p[1] else "changed" if b and p else "new_or_removed"})
    unchanged = [r for r in cmp if r["result"] == "byte_identical"]
    changed = [r for r in cmp if r["result"] != "byte_identical"]
    trees = {}
    for pname, spath in (snapshots or {}).items():
        if not Path(spath).exists():
            trees[pname] = {"snapshot": str(spath), "identical": None, "note": "snapshot missing"}
            continue
        snap = json.loads(Path(spath).read_text())["files"]
        now = tree_digest(base.parent / pname)
        diff = sorted(k for k in set(snap) | set(now) if snap.get(k) != now.get(k))
        trees[pname] = {"snapshot": str(spath), "differences": diff, "identical": not diff}
    out = {"package": name, "base": base.name, "generator_checks": check_summary,
           "package_manifest_sha256": sha256((pm_path := pkg / "intake-bundle/delivery-manifest.json").read_bytes()),
           "base_manifest_sha256": sha256((base / "intake-bundle/delivery-manifest.json").read_bytes()),
           "closure": table, "base_to_package_original_hashes": cmp,
           "unchanged_originals": len(unchanged), "changed_originals": len(changed),
           "delivered_trees_vs_pre_work_snapshots": trees,
           "statement": "Author-side evidence only. No requirement is closed until an independent reviewer says so; changed originals have blank decisions."}
    rd = pkg / "restricted/review"
    (rd / "REPAIR-CLOSURE.json").write_text(json.dumps(out, indent=2) + "\n")
    L = [f"# Closure table for {name} (author-side evidence; independent closure pending)", "",
         f"Package `{name}` supersedes `{base.name}`. Base manifest `{out['base_manifest_sha256']}`; package manifest `{out['package_manifest_sha256']}`.", "",
         "| Req | Files | Tests | Generator status |", "|---|---|---|---|"]
    for rid, c in table.items():
        L.append(f"| {rid} {c['title']} | {'<br>'.join(c['files'])} | {'<br>'.join(k + ' (' + v + ')' for k, v in c['test_results'].items())} | {c['generator_status']} |")
    L += ["", f"## {base.name} to {name} original hashes: {len(unchanged)} unchanged, {len(changed)} changed", "",
          "| File | base sha256 | package sha256 | Result |", "|---|---|---|---|"]
    for r in cmp:
        L.append(f"| {r['file']} | {(r['base_sha256'] or '-')[:16]} | {(r['package_sha256'] or '-')[:16]} | {r['result']} |")
    for pname, t in trees.items():
        L += ["", f"`{pname}` tree identical to its pre-work snapshot: **{t['identical']}**" + (f" ({len(t['differences'])} differences)" if t.get("differences") is not None else "")]
    (rd / "REPAIR-CLOSURE.md").write_text("\n".join(L) + "\n")
    return out, all_ok
