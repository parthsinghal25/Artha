"""Packaging adapter: a VALID worker return + the pack's trusted plan -> final source files, using the same renderers as
the accepted rehearsal (EML builder, letter PDF, call-summary TXT) plus the native register/portal/note renderers.

The adapter, not the worker, supplies every header/metadata value (see contract.CENTRAL). It does not publish anything:
the files it returns are inputs for a later, separately authorised packaging step (Batch integration of the new
register/portal/note kinds is NOT built here)."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .assets import AuthoringRequired, render_asset
from .contract import EMAIL_KINDS, SCHEMAS, STAGING_CARD_REF, allowed_kinds, ledger_assets, native_extra_columns
from .render_call import render_call_summary
from .render_generic import render_note, render_portal_json, render_register_export
from .render_mail import build_eml, message_id
from .render_pdf import render_letter
from .textfmt import MONTHS, long_date


class ContractGap(Exception):
    """The card needs something the contract does not provide yet (explicit unknown, never improvised)."""


@dataclass
class RenderedCard:
    card: str
    kind: str
    files: dict = field(default_factory=dict)       # relative path -> bytes (intake-visible)
    private: dict = field(default_factory=dict)     # relative path -> bytes (restricted, never ingested)
    attachments: list = field(default_factory=list)  # [(filename, media_type, bytes)] actually attached/listed
    central: dict = field(default_factory=dict)     # the values the supervisor supplied (for the review record)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def stem_for(art, plan):
    from datetime import datetime
    dt = datetime.fromisoformat(art["event_at"])
    a = slug(plan.actor(art["author"])["name"])
    r = slug(plan.actor(art["recipients"][0])["name"]) if art["recipients"] else "file"
    return f"{dt:%Y-%m-%d_%H%M}_{a}_to_{r}"


def accepted_attachment_bytes(package_dir, expected_manifest_sha256=None):
    """{filename: bytes} of the accepted attachment originals of a delivered package, each verified against its manifest
    sha256 (and the manifest against `expected_manifest_sha256` when given), so reused attachments are the exact accepted bytes."""
    pkg = Path(package_dir) / "intake-bundle"
    man_path = pkg / "delivery-manifest.json"
    if expected_manifest_sha256 and hashlib.sha256(man_path.read_bytes()).hexdigest() != expected_manifest_sha256:
        raise ContractGap(f"{man_path} does not match the manifest hash the pack is bound to")
    out = {}
    for o in json.loads(man_path.read_text())["originals"]:
        if o["kind"] != "attachment_original":
            continue
        data = (pkg / o["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != o["sha256"]:
            raise ContractGap(f"accepted original {o['file']} no longer matches its manifest hash")
        out[o["filename"]] = data
    return out


def accepted_attachment_index(package_dir, expected_manifest_sha256=None):
    """{filename: (original_id, bytes)} for the accepted attachment originals, verified as in accepted_attachment_bytes."""
    pkg = Path(package_dir) / "intake-bundle"
    accepted_attachment_bytes(package_dir, expected_manifest_sha256)  # raises ContractGap on any mismatch
    return {o["filename"]: (o["original_id"], (pkg / o["file"]).read_bytes())
            for o in json.loads((pkg / "delivery-manifest.json").read_text())["originals"] if o["kind"] == "attachment_original"}


def native_cells(data, media_type):
    """Every cell value of a CSV or XLSX export, as text (for label/leak checks; not an extractor)."""
    if media_type == "text/csv":
        import csv
        import io
        return [c for row in csv.reader(io.StringIO(data.decode("utf-8"))) for c in row]
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    return [str(v) for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for v in row if v is not None]


def resolve_attachments(art, plan, accepted_bytes=None):
    """[(filename, media_type, bytes)] for the held files the card lists. Accepted originals are reused byte for byte from
    `accepted_bytes` ({filename: bytes}); other files are rendered from their locked spec. A file that needs authored prose
    (EML) is a ContractGap."""
    out = []
    for aid in art.get("attachments", []):
        a = plan.assets[aid]
        if a.get("delivery_state") != "held":
            continue
        if accepted_bytes and a["filename"] in accepted_bytes:
            out.append((a["filename"], a["media_type"], accepted_bytes[a["filename"]]))
            continue
        try:
            data, _ = render_asset(a, plan.actor(a["owner"])["name"])
        except AuthoringRequired:
            raise ContractGap(f"attachment {a['filename']} is an EML that needs authored prose; not supplied") from None
        out.append((a["filename"], a["media_type"], data))
    return out


def _letter_block(plan, art, authored):
    sender = plan.actor(art["author"])
    if "complaints" not in sender["role"].lower():
        raise ContractGap(f"{art['artifact_ref']}: letterhead for a sender whose role is {sender['role']!r} is not defined; "
                          "only complaints-team letters have a canonical letterhead")
    rcpt = plan.actor(art["recipients"][0])
    member = next((a for a in plan.pack["actors"] if a["role"].lower().startswith("member")), None)
    from datetime import datetime
    dt = datetime.fromisoformat(art["event_at"])
    date_line = f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"
    intake = plan.actors.get("STAFF-INTAKE01")
    title = authored["letter"]["title"]
    letter = {
        "title": title,
        "letterhead": {"name": plan.actors.get("ORG-EXAMPLE-SUPER", {"name": "Example Super"})["name"],
                       "lines": ["Complaints Team", (intake or sender)["email"]]},
        "date_line": date_line,
        "addressee": [rcpt["name"], f"By email: {rcpt['email']}"],
        "reference_block": [["Complaint reference", plan.case_id], ["Request reference", plan.request_reference]]
                           + ([["Member", member["name"] + (" (deceased)" if "deceased" in member["role"] else "")]] if member else [])
                           + [["Subject", title]],
        "salutation": authored["letter"]["salutation"],
        "blocks": authored["letter"]["blocks"],
        "signoff": authored["letter"]["signoff"],
        "footer": f"{plan.actors.get('ORG-EXAMPLE-SUPER', {'name': 'Example Super'})['name']}  |  Complaints Team  |  {title}",
    }
    fname = f"{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')}-{dt.day}-{MONTHS[dt.month - 1]}-{dt.year}.pdf"
    return letter, fname, date_line


def call_central(plan, art):
    vendor = plan.actor(art["author"])
    banner = f"{vendor['name'].upper()} - CALL SUMMARY"
    participants = [plan.actor(p)["name"] for p in art["participants"]]
    return {"service_banner": banner, "participants": participants}


def render_card(authored, plan, art, accepted_bytes=None):
    """Render one valid return. Raises ContractGap for anything the contract cannot supply."""
    kind = authored["kind"]
    ok, gap = allowed_kinds(art)
    if gap:
        raise ContractGap(f"{art['artifact_ref']}: {gap}")
    if kind not in ok:
        raise ContractGap(f"{art['artifact_ref']}: kind {kind!r} is not allowed for this card (allowed: {ok})")
    card, stem = art["artifact_ref"], stem_for(art, plan)
    out = RenderedCard(card, kind)
    if kind == "call_summary":
        central = call_central(plan, art)
        full = {**authored, **central}
        data = render_call_summary(card, art, full, plan)
        out.files[f"originals/call-summaries/{stem}.txt"] = data
        out.private[f"private-controls/conversations/{card}-conversation.json"] = json.dumps(
            {"card": card, "revision": authored["revision"], "kind": "simulated_conversation_for_summary_fidelity_review",
             "never_ingested": True, "turns": authored["conversation"]}, indent=2, ensure_ascii=False).encode() + b"\n"
        out.private[f"private-controls/fidelity/{card}-summary-fidelity.json"] = json.dumps(
            {"card": card, **authored["fidelity"], "independent_review": "pending"}, indent=2, ensure_ascii=False).encode() + b"\n"
        out.central = central
        return out
    if kind == "register_export":
        extra = native_extra_columns(art, getattr(plan, "clarifications", ()))
        out.files[f"originals/registers/{stem}.csv"] = render_register_export(art, plan, extra)
        out.central = {"approved_native_columns": extra}
        return out
    if kind == "ledger_export":
        assets = ledger_assets(plan, art)
        if not assets:
            raise ContractGap(f"{card}: a ledger card needs a held CSV/XLSX export asset; none is listed")
        for a in assets:
            if accepted_bytes and a["filename"] in accepted_bytes:
                data = accepted_bytes[a["filename"]]
            else:
                data, _ = render_asset(a, plan.actor(a["owner"])["name"])
            labels = sorted({m for cell in native_cells(data, a["media_type"]) for m in STAGING_CARD_REF.findall(cell)})
            if labels:
                raise ContractGap(f"{card}: {a['filename']} would carry staging card references {labels[:4]} (authoring labels, not native values); "
                                  "an approved clarification must rule on that column before it is rendered")
            out.files[f"originals/ledgers/{a['filename']}"] = data
        out.central = {"native_export_assets": [a["asset_id"] for a in assets]}
        return out
    if kind == "portal_json":
        atts = resolve_attachments(art, plan, accepted_bytes)
        out.files[f"originals/portal/{stem}.json"] = render_portal_json(art, plan, authored, atts)
        out.attachments = atts
        return out
    if kind == "note":
        out.files[f"originals/notes/{stem}.txt"] = render_note(art, plan, authored)
        return out
    # email kinds
    attachments = resolve_attachments(art, plan, accepted_bytes)
    letter_rel = None
    if kind == "letter_with_cover_email":
        letter, fname, date_line = _letter_block(plan, art, authored)
        lb = render_letter(letter, art["event_at"], plan.actor(art["author"])["name"])
        letter_rel = f"originals/letters/{fname}"
        out.files[letter_rel] = lb
        attachments = [(fname, "application/pdf", lb)] + attachments
        out.central = {"letter": {k: letter[k] for k in ("letterhead", "date_line", "addressee", "reference_block", "footer")}, "letter_filename": fname}
    irt = None
    if authored.get("in_reply_to_card"):
        prev = plan.artifact(authored["in_reply_to_card"])
        irt = message_id(authored["in_reply_to_card"], plan.actor(prev["author"])["email"])
    refs = [message_id(c, plan.actor(plan.artifact(c)["author"])["email"]) for c in authored.get("references_cards", [])]
    eml, preview, parts, mid = build_eml(card, art, authored, plan, attachments, None, irt, refs)
    out.files[f"originals/emails/{stem}.eml"] = eml
    out.files[f"previews/{stem}.preview.html"] = preview.encode("utf-8")
    out.attachments = attachments
    return out
