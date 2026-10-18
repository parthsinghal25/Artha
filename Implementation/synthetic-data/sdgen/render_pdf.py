"""Deterministic PDF rendering of case documents (reportlab, invariant mode)."""
import io
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)
from xml.sax.saxutils import escape

from .textfmt import display_value, label

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#5b6770")
RULE = colors.HexColor("#c4ccd2")
BAND = colors.HexColor("#eef2f5")

LABELS = {
    "member_death_date": "Date of death", "correspondence_contact": "Correspondence contact",
    "evidence_item": "Evidence item", "certification_statement": "Certification",
    "certifier": "Certified by", "certification_date": "Certification date",
    "declared_relationship": "Relationship to member", "witness_date": "Witness date",
    "member_account_number": "Member account number", "item_reference": "Item reference",
    "original_validity_decision": "Original validity decision",
    "request_reference": "Request reference", "member_name": "Member name",
}

# Per semantic type: ordered section -> field keys. Unlisted fields fall into the last section.
GROUPS = {
    "certificate_copy": [
        ("Document and item identification", ["document_reference", "version", "evidence_item", "document_date"]),
        ("Identity and relationship facts", ["member_name", "member_death_date", "correspondence_contact"]),
        ("Certification", ["certification_statement", "certifier", "certification_date"]),
    ],
    "certified_identity_copy": [
        ("Document and item identification", ["document_reference", "version", "evidence_item", "document_date"]),
        ("Identity and relationship facts", ["correspondence_contact", "member_name", "member_death_date"]),
        ("Certification", ["certification_statement", "certifier", "certification_date"]),
    ],
    "relationship_form": [
        ("Document and item identification", ["document_reference", "version", "evidence_item", "document_date"]),
        ("Identity and relationship facts", ["member_name", "member_death_date", "correspondence_contact",
                                              "declared_relationship"]),
        ("Witness", ["witness_date"]),
    ],
    "requirement_assessment": [
        ("Document and requirement reference", ["document_reference", "version", "request_reference",
                                                 "member_account_number", "member_name", "correspondence_contact",
                                                 "item_reference", "requirement", "document_date"]),
        ("Dated assessment", ["decision", "assessment_date"]),
        ("Scope of decision", ["original_validity_decision"]),
    ],
}

TITLES = {
    "certificate_copy": "Death certificate extract - certified copy",
    "certified_identity_copy": "Claimant identity - certified copy",
    "relationship_form": "Claimant relationship form",
    "requirement_assessment": "Document requirement assessment",
}


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14,
                          textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10, textColor=MUTED),
        "issuer": ParagraphStyle("issuer", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20,
                                spaceBefore=6, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                             textColor=MUTED, spaceBefore=12, spaceAfter=3, keepWithNext=1),
        "label": ParagraphStyle("label", parent=base, fontSize=9, leading=12, textColor=MUTED),
        "value": ParagraphStyle("value", parent=base, fontSize=10, leading=13),
        "body": ParagraphStyle("body", parent=base, spaceAfter=7),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14, bulletIndent=2, spaceAfter=4),
        "bold": ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold"),
    }


def _kv_table(pairs, st, width):
    rows = [[Paragraph(escape(k), st["label"]), Paragraph(escape(v), st["value"])] for k, v in pairs]
    t = Table(rows, colWidths=[width * 0.34, width * 0.66])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _rows_table(rows, st, width):
    cols = list(rows[0].keys())
    data = [[Paragraph(escape(label(c, LABELS)), st["bold"]) for c in cols]]
    for r in rows:
        data.append([Paragraph(escape(display_value(r.get(c, ""))) if r.get(c) not in (None, "") else "", st["value"])
                     for c in cols])
    t = Table(data, colWidths=[width / len(cols)] * len(cols), repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _stamp_dates(pdf: bytes, created_iso: str) -> bytes:
    """reportlab invariant mode writes a fixed 2000-01-01 date; replace with the scenario creation
    date at identical byte length so offsets and determinism are preserved."""
    d = created_iso[:19].replace("-", "").replace(":", "").replace("T", "")
    off = created_iso[19:] or "+00:00"
    stamp = f"D:{d}{off[:3]}'{off[4:6]}'".encode()
    old = b"D:20000101000000+00'00'"
    if len(stamp) != len(old):
        raise ValueError(f"unexpected date stamp length {stamp!r}")
    return pdf.replace(old, stamp)


def _build(story, asset_title, author, created_iso, footer_left):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm, title=asset_title, author=author,
                            invariant=1)

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, footer_left)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return _stamp_dates(buf.getvalue(), created_iso)


def render_document(asset: dict, author_name: str) -> bytes:
    """Form-style document from a locked document_spec. Only listed fields/rows are printed."""
    st = _styles()
    spec = asset["document_spec"]
    sem = asset["semantic_type"]
    width = A4[0] - 40 * mm
    fields = dict(spec.get("fields") or {})
    story = [
        Paragraph(escape(spec["issuer"]), st["issuer"]),
        HRFlowable(width="100%", thickness=0.8, color=INK, spaceBefore=3, spaceAfter=4),
        Paragraph(escape(TITLES.get(sem, asset["title"])), st["title"]),
    ]
    shown = dict(spec.get("display_overrides") or {})  # ruled presentation only (e.g. integer cents shown as A$); the stored value is unchanged

    def pair(k):
        return (shown[k]["label"], shown[k]["value"]) if k in shown else (label(k, LABELS), display_value(fields[k]))
    groups = GROUPS.get(sem)
    if groups:
        placed = set()
        for i, (head, keys) in enumerate(groups):
            keys = [k for k in keys if k in fields]
            if i == len(groups) - 1:
                keys += [k for k in fields if k not in placed and k not in keys and
                         all(k not in g[1] for g in groups)]
            if not keys:
                continue
            placed.update(keys)
            story.append(KeepTogether([Paragraph(escape(head), st["h2"]),
                                       _kv_table([pair(k) for k in keys], st, width)]))
    else:
        story.append(Paragraph("Details", st["h2"]))
        story.append(_kv_table([pair(k) for k in fields], st, width))
    if spec.get("rows"):
        story.append(Paragraph("Entries", st["h2"]))
        story.append(_rows_table(spec["rows"], st, width))
    return _build(story, asset["title"], author_name, asset["created_at"],
                  f"{spec['issuer']}  |  {fields.get('document_reference', '')}".rstrip(" |"))


def _inline(text: str) -> str:
    text = escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def render_letter(letter: dict, created_iso: str, author_name: str) -> bytes:
    """Correspondence letter from authored blocks (letterhead, addressee, headings, paragraphs, bullets)."""
    st = _styles()
    width = A4[0] - 40 * mm
    story = [Paragraph(escape(letter["letterhead"]["name"]), st["issuer"])]
    for line in letter["letterhead"].get("lines", []):
        story.append(Paragraph(escape(line), st["small"]))
    story.append(HRFlowable(width="100%", thickness=0.8, color=INK, spaceBefore=4, spaceAfter=8))
    story.append(Paragraph(escape(letter["date_line"]), st["body"]))
    for line in letter["addressee"]:
        story.append(Paragraph(escape(line), ParagraphStyle("addr", parent=st["base"], leading=13)))
    story.append(Spacer(1, 8))
    story.append(_kv_table(letter["reference_block"], st, width))
    story.append(Spacer(1, 10))
    story.append(Paragraph(_inline(letter["salutation"]), st["body"]))
    for b in letter["blocks"]:
        if "h" in b:
            story.append(Paragraph(escape(b["h"]), st["h2"]))
        elif "p" in b:
            story.append(Paragraph(_inline(b["p"]), st["body"]))
        elif "ul" in b:
            for item in b["ul"]:
                story.append(Paragraph(_inline(item), st["bullet"], bulletText="•"))
    story.append(Spacer(1, 6))
    for line in letter["signoff"]:
        story.append(Paragraph(_inline(line), st["body"] if line.startswith("Yours") else st["base"]))
    return _build(story, letter["title"], author_name, created_iso, letter["footer"])
