"""Restricted review material for the independent reviewer. Contains planning facts, so it never enters the intake
bundle. Every decision field is left null: this tool never records an acceptance, and it refuses to touch an existing
review form or status (R03)."""
import json
import shutil
from pathlib import Path

from .build import BATCH, sha256
from .plan import CasePlan

AMBIGUITIES_V1 = [
    ("11-D17", "Two attachments (letter + 11-10) although the card lists one.", "approved by independent review"),
    ("11-D06", "Call direction unstated.", "retain unknown"),
    ("11-D05", "Three communications plus the 21 December portal receipt.", "required R01 repair"),
    ("11-D15", "Unheld historic assessment cited by request reference only.", "approved"),
    ("11-10", "No signer printed.", "approved as missing field"),
    ("11-D17", "A$100 payee not named.", "approved as unknown"),
]
OPEN_BY_VERSION = {3: []}
OPEN_V2 = [
    ("11-D05", "Paragraph 2 of D05 is unchanged from v1 and still reads 'I uploaded the papers to the Example Super portal on 21 December'. "
               "The handoff replaced only the 'To be clear...' paragraph. Please rule whether this sentence is consistent with the bounded R01 wording "
               "or needs a further targeted change."),
]
DETAILS_BEYOND_LOCKED = [
    "Beth's wording, tone and first-person detail; greetings, sign-offs and job titles in signatures.",
    "PDF titles and layout headings; form-style grouping of locked fields.",
    "Receipt screenshot chrome (status bar, address bar, 'Secure upload' header, 'Received' wording).",
    "Letterhead lines and the letter's section headings.",
    "Call summary banner, header layout and 'Follow-up items'.",
]


class ReviewRecordProtected(RuntimeError):
    pass


def _refuse_if_present(out: Path):
    for name in ("review-form.json", "REVIEW-STATUS.json", "REVIEW-PACKET.md"):
        if (out / name).exists():
            raise ReviewRecordProtected(f"{out / name} already exists; review records are never reset or rewritten by the generator")


def write_review_material(pkg_dir, base_dir=None, rulings=()):
    pkg = Path(pkg_dir)
    man = json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())
    key = man["batch"]["batch_id"]
    spec = BATCH[key]
    plan = CasePlan(spec["case_number"])
    out = pkg / "restricted/review"
    out.mkdir(parents=True, exist_ok=True)
    _refuse_if_present(out)
    base_form = None
    if base_dir:
        bf = Path(base_dir) / "restricted/review/review-form.json"
        base_form = {r["original_id"]: r for r in json.loads(bf.read_text())["records"]}
    pv = man["batch"]["package_version"]
    open_choices = OPEN_BY_VERSION.get(pv, OPEN_V2)
    form = {"form_version": "2", "batch_id": key, "package_version": pv, "status": "pending_independent_review",
            "instruction": "Only an external reviewer fills decision fields. The generator never sets them. Source acceptance and gold acceptance are separate.",
            "reviewer": None, "review_started_utc": None, "review_finished_utc": None, "review_minutes": None, "records": []}
    lines = [f"# Independent review packet - {key} v{pv}", "",
             "**Restricted. Do not place in the intake bundle or any retrieval store.** Contains private card facts.", "",
             "Status: **pending independent review.** Nothing here is accepted by this tool.", ""]
    carried = []
    for o in man["originals"]:
        card = o.get("staging_card_ref")
        rel = o["lineage"]
        is_carried = "carried_forward" in rel
        is_changed = rel["relation"] == "revision" and not is_carried
        rec = {"original_id": o["original_id"], "file": o["file"], "sha256": o["sha256"], "card": card, "decision": None,
               "facts_exact": None, "chronology_no_later_knowledge": None, "voice_and_realism": None,
               "missing_or_conflicting_evidence_preserved": None, "attachment_bytes_and_lineage": None,
               "summary_fidelity_to_private_conversation": None if o["kind"] == "call_summary" else "n/a",
               "defects": [], "notes": None}
        if is_carried:
            prior = (base_form or {}).get(o["original_id"], {})
            rec["carried_forward_unchanged_from"] = {"package": rel["carried_forward"]["from_package"],
                                                      "from_sha256": rel["carried_forward"]["from_file_sha256"],
                                                      "base_external_decision": prior.get("decision"),
                                                      "note": "reference only; reviewer to confirm byte equality and re-bind to this package"}
            carried.append(o["original_id"])
        if is_changed:
            rec["supersedes"] = {"original_id": rel["supersedes_original_id"], "sha256": rel["supersedes_sha256"]}
        form["records"].append(rec)
        tag = "CARRIED UNCHANGED" if is_carried else ("CHANGED BYTES" if is_changed else "")
        lines.append(f"## {o['filename']}  ({o['kind']}) {tag}")
        lines.append(f"- file `{o['file']}` sha256 `{o['sha256'][:16]}...`")
        if is_changed:
            lines.append(f"- supersedes `{rel['supersedes_original_id']}` sha256 `{rel['supersedes_sha256'][:16]}...`")
        if card:
            a = plan.artifact(card)
            lines.append(f"- card {card}: event {a['event_at']}")
            if is_changed or not is_carried:
                lines.append("- locked assertions: " + "; ".join(f"{s['key']}={json.dumps(s['value'])[:140]}" for s in a["source_assertions"]))
        if o["kind"] == "call_summary":
            lines.append(f"- compare with `restricted/private-controls/conversations/{card}-conversation.json` and fidelity matrix `.../fidelity/{card}-summary-fidelity.json`")
        lines.append("")
    lines += ["## Open realisation choices needing a ruling", ""] + ([f"- **{c}**: {t}" for c, t in open_choices] or ["- None. SR-2 (external) has been applied; see supplemental-rulings."])
    lines += ["", "## Rulings carried forward from the v1 external review (not re-asked)", ""] + [f"- **{c}**: {t} - {r}" for c, t, r in AMBIGUITIES_V1]
    lines += ["", "## Authored detail beyond locked facts", ""] + [f"- {d}" for d in DETAILS_BEYOND_LOCKED]
    (out / "REVIEW-PACKET.md").write_text("\n".join(lines) + "\n")
    form["realisation_choices"] = [{"card": c, "choice": t, "ruling": None} for c, t in open_choices]
    (out / "review-form.json").write_text(json.dumps(form, indent=2) + "\n")
    changed = [o["original_id"] for o in man["originals"] if o["lineage"]["relation"] == "revision" and "carried_forward" not in o["lineage"]]
    (out / "REVIEW-STATUS.json").write_text(json.dumps({
        "source_acceptance": "pending_independent_review",
        "accepted_records": [], "rejected_records": [],
        "pending_records_changed_bytes": changed or [o["original_id"] for o in man["originals"]],
        "carried_forward_byte_identical_to_base_accepted_originals": carried,
        "note": "Carried-forward ids are listed for the reviewer to confirm; this tool records no acceptance. Only an external reviewer may change this file."}, indent=2) + "\n")
    sr = out / "supplemental-rulings"
    for r in rulings:
        sr.mkdir(exist_ok=True)
        shutil.copy(r, sr / Path(r).name)
