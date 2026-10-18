"""Pack set 5: the case-11 successor of pack set 4 for the seventeen `remaining_cards` (case 11 only; pack set 4 is preserved unchanged).

Differences from pack set 4:
  * IP01: PACKS-INDEX.json declares `outputs_dirname`; the ledger binds it once at init and every stage operation resolves the owned
    folder from that binding.
  * IP02: the Principal Data Engineer's D29 ruling (DATA-RULINGS.json, CL11-EFFORT-02) is carried with provenance into
    plan/APPROVED-CLARIFICATIONS.json, and its effective asset definition is written to plan/EFFECTIVE-ASSET-SPECS.json. The author
    check, renderer and packager recompute that definition from the frozen plan and refuse any difference. 11-D29 is no longer pending.
Context, prerequisite and receiving-export binding are pack set 4's (same accepted documents, same earlier-only rule).
Building a pack authorises nothing."""
import glob
import json
import shutil
from pathlib import Path

from . import contract, effective_spec, workpack2, workpack4
from .paths import ROOT
from .workpack import ADDENDUM, SYNTH, _plans, _style_for, common_references, sha

PACK_SET_VERSION = 5
PACK_SET_DIRNAME = "worker-packs-v5"
OUTPUTS_DIRNAME = "worker-outputs-v5"
CASE = workpack4.CASE
PREDECESSOR = {"pack_set_version": 4, "path": "worker-packs-v4", "pack_digest": "bae8687a0a0bf4402909fab9b88413c1285345e506b7aae070c38118c3d0dab8",
               "review_decision": "repair_required (IP01 owned-folder binding; IP02 D29 ruling)"}
RULINGS = ROOT / "Data/reviews/case-11-integration-prep-v1-2026-10-15/DATA-RULINGS.json"
RULINGS_SHA256 = "51474d7be08631a8a651290a91d1faadb81cb41dc3e79c3ac858499c4b64eb3f"
REVIEW_DECISION = ROOT / "Data/reviews/case-11-integration-prep-v1-2026-10-15/REVIEW-DECISION.json"

CARD_NOTES = dict(workpack4.CARD_NOTES)
CARD_NOTES["11-D29"] = ("Native effort export (11-16, XLSX)", "Nothing to write: rendered from plan/EFFECTIVE-ASSET-SPECS.json (ruling CL11-EFFORT-02): "
                        "22 internal rows, 19,920 seconds, no authoring column.")
CARD_NOTES["11-D21"] = (CARD_NOTES["11-D21"][0], CARD_NOTES["11-D21"][1] + " Its planned duration is the external body's own time; no duration is written.")
CARD_NOTES["11-D25"] = (CARD_NOTES["11-D25"][0], CARD_NOTES["11-D25"][1] + " Its planned duration is the external body's own time; no duration is written.")


def ruling_doc(plan_raw):
    """The Data rulings, verified against the review decision's hash and the frozen plan."""
    raw = RULINGS.read_bytes()
    if sha(raw) != RULINGS_SHA256:
        raise RuntimeError("DATA-RULINGS.json differs from the hash recorded in the review decision")
    dec = json.loads(REVIEW_DECISION.read_text())
    if dec.get("ruling_sha256") != RULINGS_SHA256 or dec.get("authoring_stage_authorised") is not False:
        raise RuntimeError("the review decision does not bind these rulings (or claims to authorise a stage)")
    src = json.loads(raw)
    if src["bound_plan_sha256"] != sha(plan_raw) or src["bound_case"] != CASE or src.get("authoring_stage_authorised") is not False:
        raise RuntimeError("the Data rulings are not bound to this frozen plan and case, or claim to authorise a stage")
    return src


def clarification_doc(plan_raw):
    """Pack-4's approved clarifications plus the integration-prep rulings, each with its provenance."""
    base = workpack2.approved_clarification_doc(CASE, plan_raw)
    src = ruling_doc(plan_raw)
    ids = {c["id"] for c in base["clarifications"]}
    if ids & {c["id"] for c in src["clarifications"]}:
        raise RuntimeError("a ruling id repeats an existing clarification id; conflicts are resolved explicitly, not merged")
    added = [{**c, "provenance": "integration_prep_rulings"} for c in src["clarifications"]]
    return {**base, "clarifications": base["clarifications"] + added,
            "provenance": base["provenance"],
            "integration_prep_rulings_provenance": {
                "source": "Artha/Data/reviews/case-11-integration-prep-v1-2026-10-15/DATA-RULINGS.json", "source_sha256": RULINGS_SHA256,
                "issued_by": src["issued_by"], "issued_on": src["issued_on"], "bound_plan_sha256": src["bound_plan_sha256"], "review": src["review"],
                "note": src["application"]},
            "effective_assets": "CL11-EFFORT-02 defines an effective asset; see plan/EFFECTIVE-ASSET-SPECS.json (derived, never edited by hand)."}


def stage_doc(pd):
    sd = workpack4.stage_doc(pd)
    sd["pack_set_version"] = PACK_SET_VERSION
    st = next(s for s in sd["stages"] if s["stage"] == "remaining_cards")
    st.pop("pending_rulings", None)
    st["applied_rulings"] = {"11-D29": "CL11-EFFORT-02 (effective asset AST-eeb19c880495b2ead3; plan/EFFECTIVE-ASSET-SPECS.json)"}
    return sd


def guide(pd, sd, style):
    g = workpack2.guide_markdown(CASE, pd, sd, style).split("\n## 10. ")[0]
    rest = next(s for s in sd["stages"] if s["stage"] == "remaining_cards")["cards"]
    arts = {a["artifact_ref"]: a for a in pd["artifacts"]}
    rows = "\n".join(f"| {c} | {arts[c]['event_at'][:10]} | {CARD_NOTES[c][0]} | {CARD_NOTES[c][1]} |" for c in sorted(rest, key=lambda c: arts[c]["event_at"]))
    return g + f"""
## 10. This stage: the seventeen remaining cards, in date order
Twelve documents of this member are already accepted and fixed: the five rehearsal cards (11-D05, 11-D06, 11-D10, 11-D15, 11-D17) and the seven style
micro-batch cards (11-D01, 11-D02, 11-D03, 11-D07, 11-D11, 11-D13, 11-D20). You never rewrite them. Write the cards below in the order listed (actual date order).
Each card may read only the accepted documents listed for it in `context/ACCEPTED-PRIOR-ORIGINALS.json`, and your own earlier cards of this stage are not
accepted sources: take facts from the card, never from your own earlier text.

| Card | Date | What it is | Keep distinct |
|---|---|---|---|
{rows}

Native exports (11-D19, 11-D28, 11-D29) are rendered by the supervisor: return only the style row (`ledger_export`, profile S0, no errors).
An external body's message (11-D21, 11-D25, and 11-D26 from the same body) is an email you write in that body's voice; its notice, invoice or index files are
rendered by the supervisor. Copies of an earlier attachment are the same document again, not a new submission or new evidence.

### The effort export (11-D29): approved ruling CL11-EFFORT-02
The effort export is rendered from `plan/EFFECTIVE-ASSET-SPECS.json`, not from the planned rows: the same XLSX file, asset, export date and availability,
without the `related_artifact` column, and without the two planned rows of the external body's own staff time. It holds 22 internal rows, 19,920 seconds.
You make no selection: the rows are fixed by the ruling. Never write a duration, rate, task detail or matter link for effort in any document, and never
describe the external body's time as the fund's work. The planned `work_duration_seconds` of 11-D21 and 11-D25 is that external time.
"""


def instructions(pd, sd, out_rel):
    launch = [s for s in sd["stages"] if s.get("launchable")]
    stages = "\n".join(f"- stage `{s['stage']}`: write only {', '.join(s['cards'])}; owned folder `{out_rel}/{s['stage']}/`" for s in launch)
    return f"""# Work pack - member case {CASE:02d} ({pd['story']['name']}), complaint {pd['case_id']}  [pack set {PACK_SET_VERSION}]

**Launch status: not authorised.** Building this pack authorises nothing. You write only after the supervisor names one of the stages below as authorised.

Read `shared/WORKER-GUIDE.md` first; it is complete. Then `shared/RETURN-SCHEMAS.md`, the case study, the assistant pack, `plan/APPROVED-CLARIFICATIONS.json`
and `plan/EFFECTIVE-ASSET-SPECS.json`.

{stages}

Return one JSON file per card (`<card>.json`) inside your stage's owned folder, run the check (guide section 7) on each, and put a completed COPY of `RETURN-CHECKLIST.json`
in that same owned folder as `{out_rel}/<stage>/RETURN-CHECKLIST.json`. Never edit the pack's own `RETURN-CHECKLIST.json`; it is an immutable template.
Any helper file you create belongs inside your owned folder too.
Check command (the supervisor's launch message gives the real folders):
`.venv/bin/python -m sdgen author-check <card>.json --pack <pack folder> --stage <stage>`

Use only this pack folder and your owned folder. Do not mark anything accepted.
"""


def build(out_root=None):
    """Build pack set 5 (case 11 only) into a fresh directory, with its index (declaring its owned-output folder), inventory and protocol."""
    out_root = Path(out_root) if out_root else SYNTH / PACK_SET_DIRNAME
    outputs_root = out_root.parent / OUTPUTS_DIRNAME
    if out_root.exists():
        raise FileExistsError(f"{out_root} exists; a pack set is built once into a fresh directory")
    plans = _plans()
    p = plans[CASE]
    pd = p["data"]
    assign = json.loads((ADDENDUM / "ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    profiles = json.loads((ADDENDUM / "STYLE-PROFILES.json").read_text())
    fp = {Path(x["path"]).name: x["sha256"] for x in assign["base_plan_fingerprints"]}
    if fp[p["path"].name] != sha(p["raw"]):
        raise RuntimeError("case 11 plan changed since style assignment (fingerprint mismatch)")
    clar = clarification_doc(p["raw"])
    eff = effective_spec.document(pd, sha(p["raw"]), clar["clarifications"])  # refuses unless the ruling reconciles with the frozen plan
    sd = stage_doc(pd)
    rest = next(s for s in sd["stages"] if s["stage"] == "remaining_cards")["cards"]
    root = out_root / p["path"].stem
    for sub in ("plan", "style", "shared", "context"):
        (root / sub).mkdir(parents=True)
    shutil.copy(p["path"], root / "plan/assistant-pack.json")
    shutil.copy(glob.glob(str(SYNTH / f"pilot-v2-planning-2026-10-13/case-studies/{CASE:02d}-C-*.md"))[0], root / "plan/case-study.md")
    (root / "plan/APPROVED-CLARIFICATIONS.json").write_text(json.dumps(clar, indent=1) + "\n")
    (root / "plan/EFFECTIVE-ASSET-SPECS.json").write_text(json.dumps(eff, indent=1) + "\n")
    style = _style_for(CASE, pd["case_id"], assign, profiles)
    (root / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").write_text(json.dumps({k: style[k] for k in ("kind", "version", "seed", "case_reference", "cards", "attachment_briefs")}, indent=1) + "\n")
    (root / "style/STYLE-PROFILES.json").write_text(json.dumps({k: style[k] for k in ("profiles", "actor_voices", "protected_content_rules", "allowed_error_scope")}, indent=1) + "\n")
    (root / "STAGE.json").write_text(json.dumps(sd, indent=1) + "\n")
    (root / "shared/WORKER-GUIDE.md").write_text(guide(pd, sd, style))
    (root / "shared/RETURN-SCHEMAS.md").write_text(workpack2.returns_markdown())
    (root / "shared/RETURN-SCHEMAS.json").write_text(json.dumps({"kinds": contract.SCHEMAS, "supervisor_supplied": contract.CENTRAL, "kind_for_plan_kind": contract.KIND_MAP,
                                                               "unsupported_plan_kinds": contract.UNSUPPORTED_PLAN_KINDS,
                                                               "unsupported_formats": contract.UNSUPPORTED_FORMATS}, indent=1, sort_keys=True) + "\n")
    common = dict(common_references(plans))
    common["procedures"] = {"status": "no stand-alone procedure corpus has been supplied",
                            "use": "Procedure-like facts available are exactly the case's own writer_constraints, source_assertions, work_entries (as ruled in plan/EFFECTIVE-ASSET-SPECS.json), external_matters, story and approved clarifications. A rule not present there is an open dependency: raise a question, do not invent one."}
    (root / "shared/COMMON-REFERENCES.json").write_text(json.dumps(common, indent=1) + "\n")
    (root / "context/ACCEPTED-PRIOR-ORIGINALS.json").write_text(json.dumps(workpack4.accepted_context(pd, rest), indent=1, ensure_ascii=False) + "\n")
    eff_ids = {a["asset_id"]: a["provenance"]["clarification"] for a in eff["assets"]}
    assets = []
    for a in pd["assets"]:
        how = ("not_rendered_advertised_only_no_bytes" if a.get("delivery_state") != "held" else
               "authoring_required_eml" if a["media_type"] == "message/rfc822" else
               "supervisor_renders_from_effective_spec" if a["asset_id"] in eff_ids else "supervisor_renders_from_locked_spec")
        assets.append({"asset_id": a["asset_id"], "filename": a["filename"], "media_type": a["media_type"], "delivery_state": a.get("delivery_state"),
                       "copy_of_asset_id": a.get("copy_of_asset_id"), "production": how, "ruling": eff_ids.get(a["asset_id"]),
                       "style_profile": next((b["profile_id"] for b in style["attachment_briefs"] if b["asset_ref"] == a["asset_id"]), None)})
    (root / "assets-to-render.json").write_text(json.dumps({"note": "Attachments are produced once by the supervisor; accepted ones are reused byte for byte.", "assets": assets}, indent=1) + "\n")
    (root / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": CASE, "cards": [
        {"card": a["artifact_ref"], "kind": a["kind"], "event_at": a["event_at"], "status": "not_started", "authored_file": None, "questions": []}
        for a in sorted(pd["artifacts"], key=lambda a: a["event_at"]) if a["artifact_ref"] in rest],
        "note": "IMMUTABLE TEMPLATE. The worker copies this file into its owned output folder and completes the copy there; supervisor and independent reviewers decide acceptance."}, indent=1) + "\n")
    out_rel = f"{OUTPUTS_DIRNAME}/{root.name}"
    (root / "INSTRUCTIONS.md").write_text(instructions(pd, sd, out_rel))
    d = outputs_root / root.name / "remaining_cards"
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.txt").write_text(f"Owned output folder for case {CASE:02d}, stage remaining_cards. Only the worker assigned to that stage writes here.\n")
    checks = workpack2.verify_pack(root, plans) + verify_ruling(root)
    digest, files = workpack2.pack_digest(root)
    entry = {"case": CASE, "pack": root.name, "pack_set_version": PACK_SET_VERSION, "pack_digest": digest, "files": files,
             "input_hashes": {"plan_sha256": sha((root / "plan/assistant-pack.json").read_bytes()), "case_study_sha256": files["plan/case-study.md"],
                              "style_assignments_sha256": files["style/ARTIFACT-STYLE-ASSIGNMENTS.json"], "style_profiles_sha256": files["style/STYLE-PROFILES.json"],
                              "clarifications_sha256": files["plan/APPROVED-CLARIFICATIONS.json"], "effective_asset_specs_sha256": files["plan/EFFECTIVE-ASSET-SPECS.json"],
                              "data_rulings_sha256": RULINGS_SHA256, "guide_sha256": files["shared/WORKER-GUIDE.md"],
                              "return_schemas_sha256": files["shared/RETURN-SCHEMAS.json"], "accepted_prior_context_sha256": files["context/ACCEPTED-PRIOR-ORIGINALS.json"],
                              "accepted_context_export_manifest_sha256": workpack4.EXPORT_MANIFEST_SHA256,
                              "prerequisite_acceptance_sha256": workpack4.MICROBATCH_ACCEPTANCE_SHA256},
             "checks": [{"check": c, "ok": ok, "detail": det} for c, ok, det in checks], "launch_status": "not_authorised"}
    (root / "PACK-MANIFEST.json").write_text(json.dumps(entry, indent=1) + "\n")
    index = [{"case": CASE, "pack": root.name, "pack_digest": digest, "plan_sha256": entry["input_hashes"]["plan_sha256"],
              "all_checks_ok": all(c["ok"] for c in entry["checks"]), "file_count": len(files) + 1, "launchable_stages": ["remaining_cards"], "blocked": None}]
    (out_root / "PACKS-INDEX.json").write_text(json.dumps({"kind": "worker_pack_index", "pack_set_version": PACK_SET_VERSION, "outputs_dirname": OUTPUTS_DIRNAME,
                                                          "launch_authorised": False, "scope": "case 11 only; every other member stays on pack set 3",
                                                          "supersedes": "case 11 in worker-packs-v4 (pack set 4, preserved unchanged; review decision repair_required)",
                                                          "predecessor": PREDECESSOR, "packs": index}, indent=1) + "\n")
    workpack2.write_inventory(out_root)
    (out_root / "SUPERVISOR-LAUNCH-PROTOCOL.md").write_text(protocol(root.name, digest))
    return entry


def verify_ruling(root):
    """The pack's effective definition is the one the check/renderer recompute, and the D29 renderer output agrees with it."""
    from .authoring_check import load_pack
    from .card_adapter import native_cells, render_card
    res = []
    try:
        plan = load_pack(root)[0]
        ok = True
    except effective_spec.RulingMismatch as e:
        return [("effective asset definition reconciles with the frozen plan and the ruling", False, str(e))]
    res.append(("effective asset definition reconciles with the frozen plan and the ruling", ok, ""))
    art = plan.artifact("11-D29")
    ret = {"card": "11-D29", "revision": 1, "kind": "ledger_export",
           "style_completion": {"card": "11-D29", "applied_profile": "S0", "features_used": [], "typos_used": [], "protected_values_checked": True, "concerns": []}}
    rc = render_card(ret, plan, art)
    [(rel, data)] = rc.files.items()
    a = plan.assets["AST-eeb19c880495b2ead3"]
    cells = native_cells(data, a["media_type"])
    res.append(("11-D29 renders as the ruled XLSX with no staging reference or excluded row",
                rel == f"originals/ledgers/{a['filename']}" and data[:2] == b"PK" and not any(contract.STAGING_CARD_REF.search(c) for c in cells)
                and not any(w in cells for w in a["effective_spec"]["excluded_work_ids"]), rel))
    return res


def protocol(pack, digest):
    return f"""# Supervisor launch protocol - pack set {PACK_SET_VERSION} (case 11 remaining cards)

Building this pack set authorises nothing. The ledger (`PROGRESS.json`, ledger format 2) starts every stage `queued` with no approval, and binds the
owned-output folder `{OUTPUTS_DIRNAME}` declared by this pack set's `PACKS-INDEX.json` (submission and acceptance hash exactly that folder).

1. The Principal Data Engineer issues an external `authoring_stage_signoff` under `Data/reviews/` bound to: case 11, stage `remaining_cards`,
   pack `{pack}`, pack digest `{digest}`, pack set version {PACK_SET_VERSION}, and its owned folder
   `Artha/Data/synthesis/{OUTPUTS_DIRNAME}/{pack}/remaining_cards/`.
2. The coding assistant records it with `progress.authorise(11, "remaining_cards", <signoff>, <pack set root>)` and starts the stage with
   `progress.set_state(..., "working", worker=...)`, after a write snapshot. `progress.owned_folder_for(11, "remaining_cards", <root>)` names the folder.
3. One fresh worker, given only this pack, the stage, the date order and the owned folder; requested model Sonnet 5.5. Record the requested selector,
   the worker's reported self-identity and any provider-observed identity separately; without provider metadata the exact identity is unknown.
   No other model is substituted without the participant's instruction. The completed seven-card permission is not reused.
4. Helper files stay in the owned folder; any write outside it is reported in the scope audit.
5. Check every card, render with `sdgen package-stage --accepted-package {workpack4.rel(workpack4.EXPORT)}`, publish a fresh package, submit, and stop for review.
   11-D29 renders from plan/EFFECTIVE-ASSET-SPECS.json (ruling CL11-EFFORT-02).
"""
