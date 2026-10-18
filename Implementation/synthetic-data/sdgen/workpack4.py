"""Pack set 4: the case-11 successor pack for the seventeen `remaining_cards` (case 11 only; every other member stays on pack set 3).

Differences from pack set 3 for case 11:
  * `remaining_cards` is launchable. Its prerequisite is the external STAGE-ACCEPTANCE of the style micro-batch, verified at build
    (record hash, accepted output digest, accepted manifest) and recorded in STAGE.json; it is not re-decided here.
  * Context = every accepted case-11 original (rehearsal v3 + micro-batch v2), taken from the receiving export whose manifest binds
    them, each listed only for the remaining cards dated strictly after it. Email, call-summary, portal and register text is quotable;
    a letter's text is read from its PDF and is reading context only.
  * Return shapes now cover `external` (authored EML; notice PDF is a held asset) and `ledger` (native export rendered from locked rows).
Building a pack authorises nothing."""
import hashlib
import json
from pathlib import Path

from . import contract, workpack2
from .paths import ROOT
from .workpack import ADDENDUM, MICROBATCH, REHEARSAL, SYNTH, _plans, _style_for, common_references, sha

PACK_SET_VERSION = 4
PACK_SET_DIRNAME = "worker-packs-v4"
OUTPUTS_DIRNAME = "worker-outputs-v4"
CASE = 11
EXPORT = SYNTH / "receiving-exports/case-11-accepted-slice-v1"
EXPORT_MANIFEST_SHA256 = "cd2be75478bb00d6fbd304d77136a8d501995a69ae222f93a8b0bacf176a3f91"
MICROBATCH_ACCEPTANCE = ROOT / "Data/reviews/case-11-style-microbatch-v2-2026-10-15/STAGE-ACCEPTANCE.json"
MICROBATCH_ACCEPTANCE_SHA256 = "6fdb8d2688449aba14d197772e28742d6986ad2804255961c2d9552f5652020d"
PENDING_RULINGS = {
    "11-D29": ("the locked effort-export rows carry a `related_artifact` column whose values are staging card ids (e.g. 11-D03), an "
               "authoring label rather than a native value; the renderer refuses it until the Principal Data Engineer rules on that column"),
}

# what each remaining card must keep distinct; derived from its frozen card and the accepted chronology, not new facts
CARD_NOTES = {
    "11-D04": ("Complaint acknowledgement", "Acknowledges the 31 December complaint only; no assessment, outcome or promise the card does not give."),
    "11-D08": ("Specialist reply (attaches 11-06)", "Reply to 11-D07 using only the card's held facts; never assert or invent an original validity assessment of DC-11."),
    "11-D09": ("Promised member progress update", "Keeps the existing 12 January update promise exactly; a progress update, not a decision."),
    "11-D12": ("Reasoned authority request", "Asks for authority; a request is not the approval (11-D13 is later); no invented payment authority."),
    "11-D14": ("Action/task record", "Records the task set by the approval; recording a task is not completing it."),
    "11-D16": ("Scoped action evidence (attaches 11-10)", "Supplies the new assessment for the approved scope only; it does not become the historic validity record."),
    "11-D21": ("External body registration notice (EML; 11-12 rendered)", "The external body's own message; you write only the email text."),
    "11-D22": ("Internal preparation for the external dispute", "Preparation, not submission; copies of 11-06 are not new evidence."),
    "11-D23": ("Institution position and evidence submission (EML; 11-13, 11-06 rendered)", "Submitted position only; no outcome."),
    "11-D24": ("Member update during the external process", "Status only; no outcome, payment or closure the card does not give."),
    "11-D26": ("Case-fee invoice delivery (attaches 11-14)", "An invoice delivered is not a payment."),
    "11-D27": ("Finance allocation/approval note", "Approval to pay is not payment; amounts only as the card states."),
    "11-D28": ("Native external-cost export (11-15)", "Nothing to write: the export is rendered from its locked rows (attest profile S0)."),
    "11-D18": ("Administrative closure note", "Administrative closure is not service completion; keep the residual service state the card gives."),
    "11-D19": ("Native remedy ledger extract (11-11)", "Nothing to write: approved and paid stay separate rows from the locked spec."),
    "11-D25": ("External outcome or ongoing state (EML only)", "Only the state the card gives; no invented satisfied ending."),
    "11-D29": ("Native effort export (11-16)", "Nothing to write. Rendering is refused until the pending column ruling is issued; record that as a question."),
}


def stage_doc(pd):
    cards = [a["artifact_ref"] for a in pd["artifacts"]]
    rest = [c for c in cards if c not in MICROBATCH + REHEARSAL]
    acc_raw = MICROBATCH_ACCEPTANCE.read_bytes()
    if sha(acc_raw) != MICROBATCH_ACCEPTANCE_SHA256:
        raise RuntimeError("the micro-batch stage acceptance record differs from the recorded hash")
    acc = json.loads(acc_raw)
    if acc.get("decision") != "accepted" or acc.get("stage") != "style_microbatch" or acc.get("case") != CASE:
        raise RuntimeError("the micro-batch stage acceptance is not an acceptance of case 11 style_microbatch")
    prereq = {"record": "external stage acceptance of the case-11 style micro-batch v2 (Principal Data Engineer)", "record_sha256": MICROBATCH_ACCEPTANCE_SHA256,
              "accepted_output_digest": acc["output_digest"], "accepted_manifest_sha256": acc["bound_manifest_sha256"], "pack_set_version": acc["pack_set_version"]}
    return {"case": CASE, "pack_set_version": PACK_SET_VERSION, "stages": [
        {"stage": "rehearsal_repair", "cards": REHEARSAL, "launchable": False, "state": "accepted originals (rehearsal v3) preserved unchanged; never re-authored"},
        {"stage": "style_microbatch", "cards": MICROBATCH, "launchable": False, "state": "accepted (case-11-style-microbatch-v2) in pack set 3; preserved unchanged",
         "acceptance": prereq},
        {"stage": "remaining_cards", "cards": rest, "launchable": True, "requires_accepted_stage": None,
         "prerequisite_acceptance": prereq, "pending_rulings": PENDING_RULINGS},
        {"stage": "complete_case_review", "cards": [], "launchable": False, "blocked_reason": "independent source review of the complete 29-card journey"}]}


def _pdf_text(data):
    import io
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def accepted_context(pd, stage_cards):
    """Accepted case-11 documents with their text, each listed for the stage cards dated strictly after it. Bound to the export manifest."""
    intake = EXPORT / "intake-bundle"
    raw = (intake / "delivery-manifest.json").read_bytes()
    if sha(raw) != EXPORT_MANIFEST_SHA256:
        raise RuntimeError("the receiving export manifest differs from the hash this pack set is bound to")
    man = json.loads(raw)
    arts = {a["artifact_ref"]: a for a in pd["artifacts"]}
    names = {a["id"]: a["name"] for a in pd["actors"]}
    out = []
    for o in man["originals"]:
        ref = o.get("staging_card_ref")
        if ref not in arts or o["kind"] == "attachment_original":
            continue
        data = (intake / o["file"]).read_bytes()
        if sha(data) != o["sha256"]:
            raise RuntimeError(f"{o['file']} differs from its export manifest hash")
        usable = [c for c in stage_cards if arts[c]["event_at"] > arts[ref]["event_at"]]
        if not usable:
            continue
        quotable = True
        if o["kind"] == "email":
            text = workpack2._plain_body(intake / o["file"])
        elif o["kind"] == "portal_form":
            text = json.loads(data)["free_text"]
        elif o["kind"] == "letter":
            text, quotable = _pdf_text(data), False
        else:
            text = data.decode("utf-8")
        out.append({"card": ref, "kind": o["kind"], "event_at": arts[ref]["event_at"], "subject": o.get("subject") or arts[ref]["subject"],
                    "author": names.get(arts[ref]["author"], arts[ref]["author"]), "original_id": o["original_id"], "file_sha256": o["sha256"],
                    "quotable": quotable, "usable_before_cards": usable, "text": text})
    return {"kind": "accepted_prior_originals_for_context_and_exact_quotation",
            "bound_to_manifest": {"package": EXPORT.name, "delivery_manifest_sha256": EXPORT_MANIFEST_SHA256},
            "note": ("Accepted, immutable documents of this member. Read them for continuity; quote only quotable ones, with a quote block. Each card may "
                     "use only those listed for it; no document dated on or after a card appears for that card. A letter's text was read from its PDF "
                     "and is for reading only."),
            "originals": sorted(out, key=lambda x: (x["event_at"], x["kind"]))}


def guide(pd, sd, style):
    g = workpack2.guide_markdown(CASE, pd, sd, style)
    g = g.split("\n## 10. ")[0]
    rest = next(s for s in sd["stages"] if s["stage"] == "remaining_cards")["cards"]
    arts = {a["artifact_ref"]: a for a in pd["artifacts"]}
    rows = "\n".join(f"| {c} | {arts[c]['event_at'][:10]} | {CARD_NOTES[c][0]} | {CARD_NOTES[c][1]} |"
                     for c in sorted(rest, key=lambda c: arts[c]["event_at"]))
    return g + f"""
## 10. This stage: the seventeen remaining cards, in date order
Twelve documents of this member are already accepted and fixed: the five rehearsal cards (11-D05, 11-D06, 11-D10, 11-D15, 11-D17) and the seven style
micro-batch cards (11-D01, 11-D02, 11-D03, 11-D07, 11-D11, 11-D13, 11-D20). You never rewrite them. Write the cards below in the order listed (actual date order).
Each card may read only the accepted documents listed for it in `context/ACCEPTED-PRIOR-ORIGINALS.json`, and your own earlier cards of this stage are not
accepted sources: take facts from the card, never from your own earlier text.

| Card | Date | What it is | Keep distinct |
|---|---|---|---|
{rows}

Native exports (11-D19, 11-D28, 11-D29) are rendered by the supervisor from locked rows: return only the style row (`ledger_export`, profile S0, no errors).
An external body's message (11-D21, 11-D25, and 11-D26 from the same body) is an email you write in that body's voice; its notice, invoice or index files are
rendered by the supervisor. Copies of an earlier attachment are the same document again, not a new submission or new evidence.
Pending ruling: 11-D29 cannot be rendered until the Principal Data Engineer rules on its `related_artifact` column; write its style row, run the check, and record
the failure as a question in your checklist copy. Do not work around it.
"""


def instructions(pd, sd, out_rel):
    launch = [s for s in sd["stages"] if s.get("launchable")]
    stages = "\n".join(f"- stage `{s['stage']}`: write only {', '.join(s['cards'])}; owned folder `{out_rel}/{s['stage']}/`" for s in launch)
    return f"""# Work pack - member case {CASE:02d} ({pd['story']['name']}), complaint {pd['case_id']}  [pack set {PACK_SET_VERSION}]

**Launch status: not authorised.** Building this pack authorises nothing. You write only after the supervisor names one of the stages below as authorised.

Read `shared/WORKER-GUIDE.md` first; it is complete. Then `shared/RETURN-SCHEMAS.md`, the case study and the assistant pack.

{stages}

Return one JSON file per card (`<card>.json`) inside your stage's owned folder, run the check (guide section 7) on each, and put a completed COPY of `RETURN-CHECKLIST.json`
in that same owned folder as `{out_rel}/<stage>/RETURN-CHECKLIST.json`. Never edit the pack's own `RETURN-CHECKLIST.json`; it is an immutable template.
Any helper file you create belongs inside your owned folder too.
Check command (the supervisor's launch message gives the real folders):
`.venv/bin/python -m sdgen author-check <card>.json --pack <pack folder> --stage <stage>`

Use only this pack folder and your owned folder. Do not mark anything accepted.
"""


def build(out_root=None):
    """Build pack set 4 (case 11 only) into a fresh directory, with its index, inventory and launch protocol."""
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
    sd = stage_doc(pd)
    rest = next(s for s in sd["stages"] if s["stage"] == "remaining_cards")["cards"]
    root = out_root / p["path"].stem
    for sub in ("plan", "style", "shared", "context"):
        (root / sub).mkdir(parents=True)
    import glob
    import shutil
    shutil.copy(p["path"], root / "plan/assistant-pack.json")
    shutil.copy(glob.glob(str(SYNTH / f"pilot-v2-planning-2026-10-13/case-studies/{CASE:02d}-C-*.md"))[0], root / "plan/case-study.md")
    (root / "plan/APPROVED-CLARIFICATIONS.json").write_text(json.dumps(workpack2.approved_clarification_doc(CASE, p["raw"]), indent=1) + "\n")
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
                            "use": "Procedure-like facts available are exactly the case's own writer_constraints, source_assertions, work_entries, external_matters, story and approved clarifications. A rule not present there is an open dependency: raise a question, do not invent one."}
    (root / "shared/COMMON-REFERENCES.json").write_text(json.dumps(common, indent=1) + "\n")
    (root / "context/ACCEPTED-PRIOR-ORIGINALS.json").write_text(json.dumps(accepted_context(pd, rest), indent=1, ensure_ascii=False) + "\n")
    assets = []
    for a in pd["assets"]:
        how = ("not_rendered_advertised_only_no_bytes" if a.get("delivery_state") != "held" else
               "authoring_required_eml" if a["media_type"] == "message/rfc822" else "supervisor_renders_from_locked_spec")
        assets.append({"asset_id": a["asset_id"], "filename": a["filename"], "media_type": a["media_type"], "delivery_state": a.get("delivery_state"),
                       "copy_of_asset_id": a.get("copy_of_asset_id"), "production": how,
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
    checks = workpack2.verify_pack(root, plans)
    digest, files = workpack2.pack_digest(root)
    entry = {"case": CASE, "pack": root.name, "pack_set_version": PACK_SET_VERSION, "pack_digest": digest, "files": files,
             "input_hashes": {"plan_sha256": sha((root / "plan/assistant-pack.json").read_bytes()), "case_study_sha256": files["plan/case-study.md"],
                              "style_assignments_sha256": files["style/ARTIFACT-STYLE-ASSIGNMENTS.json"], "style_profiles_sha256": files["style/STYLE-PROFILES.json"],
                              "clarifications_sha256": files["plan/APPROVED-CLARIFICATIONS.json"], "guide_sha256": files["shared/WORKER-GUIDE.md"],
                              "return_schemas_sha256": files["shared/RETURN-SCHEMAS.json"], "accepted_prior_context_sha256": files["context/ACCEPTED-PRIOR-ORIGINALS.json"],
                              "accepted_context_export_manifest_sha256": EXPORT_MANIFEST_SHA256, "prerequisite_acceptance_sha256": MICROBATCH_ACCEPTANCE_SHA256},
             "checks": [{"check": c, "ok": ok, "detail": det} for c, ok, det in checks], "launch_status": "not_authorised"}
    (root / "PACK-MANIFEST.json").write_text(json.dumps(entry, indent=1) + "\n")
    index = [{"case": CASE, "pack": root.name, "pack_digest": digest, "plan_sha256": entry["input_hashes"]["plan_sha256"],
              "all_checks_ok": all(c["ok"] for c in entry["checks"]), "file_count": len(files) + 1, "launchable_stages": ["remaining_cards"], "blocked": None}]
    (out_root / "PACKS-INDEX.json").write_text(json.dumps({"kind": "worker_pack_index", "pack_set_version": PACK_SET_VERSION, "launch_authorised": False,
                                                          "scope": "case 11 only; every other member stays on pack set 3",
                                                          "supersedes": "case 11 in worker-packs-v3 (preserved unchanged)", "packs": index}, indent=1) + "\n")
    workpack2.write_inventory(out_root)
    (out_root / "SUPERVISOR-LAUNCH-PROTOCOL.md").write_text(protocol(root.name, digest))
    return entry


def protocol(pack, digest):
    return f"""# Supervisor launch protocol - pack set {PACK_SET_VERSION} (case 11 remaining cards)

Building this pack set authorises nothing. The ledger (`PROGRESS.json`, ledger format 2) starts every stage `queued` with no approval.

1. The Principal Data Engineer issues an external `authoring_stage_signoff` under `Data/reviews/` bound to: case 11, stage `remaining_cards`,
   pack `{pack}`, pack digest `{digest}`, pack set version {PACK_SET_VERSION}, and its owned folder
   `Artha/Data/synthesis/{OUTPUTS_DIRNAME}/{pack}/remaining_cards/`.
2. The coding assistant records it with `progress.authorise(11, "remaining_cards", <signoff>, <pack set root>)` and starts the stage with
   `progress.set_state(..., "working", worker=...)`, after a write snapshot.
3. One fresh worker, given only this pack, the stage, the date order and the owned folder; requested model Sonnet 5.5 where selectable. Record requested
   selector, reported self-identity and any provider-observed identity separately; without provider metadata the exact identity is unknown.
4. Helper files stay in the owned folder; any write outside it is reported in the scope audit.
5. Check every card, render with `sdgen package-stage --accepted-package {rel(EXPORT)}`, publish a fresh package, submit, and stop for review.
   11-D29 stays unrenderable until the pending column ruling exists; it is reported, not worked around.
"""


def rel(p):
    return str(Path("Artha") / Path(p).resolve().relative_to(ROOT.resolve()))
