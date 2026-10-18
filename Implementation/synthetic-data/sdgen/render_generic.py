"""Native structured renderers driven only by locked document_spec fields/rows (never prose):
CSV / XLSX tables, a system-screenshot PNG, a register export and a portal-submission JSON envelope.
Prose-bearing records (emails, letters, notes, call summaries) come from authored overlays instead."""
import io
import json
from datetime import datetime

from PIL import Image, ImageDraw

from .render_csv import render_rows_csv
from .render_image import _font
from .render_xlsx import render_rows_xlsx


def table_from_spec(spec):
    """Columns/rows for a tabular export. Row-bearing specs repeat their header fields on every row (as the
    checklist export does); field-only specs give a single row. Values stay literal; None stays blank.
    A row column whose name equals a header field (e.g. a header total and a per-row `amount_minor`) is renamed
    `row_<name>` so neither locked value is overwritten."""
    fields, rows = dict(spec.get("fields") or {}), list(spec.get("rows") or [])
    if not rows:
        return list(fields), [fields]
    row_cols = []
    for r in rows:
        row_cols += [c for c in r if c not in row_cols]
    clash = {c for c in row_cols if c in fields}
    ren = {c: (f"row_{c}" if c in clash else c) for c in row_cols}
    cols = list(fields) + [ren[c] for c in row_cols]
    return cols, [{**fields, **{ren[k]: v for k, v in r.items()}} for r in rows]


def render_spec_csv(asset) -> bytes:
    cols, rows = table_from_spec(asset["document_spec"])
    return render_rows_csv(cols, rows)


def render_spec_xlsx(asset, creator) -> bytes:
    cols, rows = table_from_spec(asset["document_spec"])
    return render_rows_xlsx(cols, rows, asset["created_at"], creator)


def render_spec_png(asset):
    spec = asset["document_spec"]
    cols, rows = table_from_spec({"fields": {}, "rows": spec.get("rows") or []}) if spec.get("rows") else ([], [])
    fields = spec.get("fields") or {}
    W = 1200
    H = 260 + 62 * (len(fields) + len(rows) + 3)
    img = Image.new("RGB", (W, H), "#f3f5f7")
    d = ImageDraw.Draw(img)
    drawn, fonts = [], set()

    def text(xy, s, kind="regular", size=30, fill="#1f2933", anchor="la"):
        f, p = _font(kind, size)
        fonts.add(p)
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
        drawn.append(str(s))

    d.rectangle([0, 0, W, 110], fill="#17324d")
    text((48, 55), spec["issuer"], "bold", 40, "#ffffff", "lm")
    d.rounded_rectangle([40, 140, W - 40, H - 40], radius=20, fill="#ffffff", outline="#d3dae0", width=3)
    text((80, 190), asset["title"], "bold", 40, anchor="lm")
    y = 250
    for k, v in fields.items():
        text((80, y), k.replace("_", " ").capitalize(), "regular", 26, "#5b6770")
        text((520, y), "" if v is None else str(v), "bold", 28)
        y += 62
    if rows:
        y += 20
        for r in rows:
            d.line([80, y - 12, W - 80, y - 12], fill="#d3dae0", width=2)
            text((80, y + 12), "   |   ".join("" if v is None else str(v) for v in r.values()), "regular", 27)
            y += 62
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue(), {"drawn_text": drawn, "fonts": sorted(fonts)}


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}{k}."))
        return out
    return {prefix[:-1]: value if not isinstance(value, list) else "; ".join(map(str, value))}


def render_register_export(art, plan=None, extra_columns=None) -> bytes:
    """Native one-row register export, deterministic, no prose. Columns: the locked request reference (when the card has
    one) then the flattened locked `register` assertion values, then any APPROVED clarification columns. No derived,
    root-cause or expected-answer column. Values are literal; nothing is invented."""
    reg = next((a["value"] for a in art["source_assertions"] if a["key"] == "register"), None)
    if reg is None:
        raise KeyError(f"{art['artifact_ref']}: no register assertion to export")
    req = next((a["value"] for a in art["source_assertions"] if a["key"] == "request_reference"), None)
    row = ({"request_reference": req} if req else {}) | flatten(reg) | dict(extra_columns or {})
    return render_rows_csv(list(row), [row])


def render_portal_json(art, plan, authored, attachments) -> bytes:
    """Portal submission envelope. `authored["free_text"]` is the only worker prose; every other value comes from the card.
    `attachments`: [(filename, media_type, bytes)] for the held files the card lists."""
    import hashlib
    req = next((a["value"] for a in art["source_assertions"] if a["key"] == "request_reference"), None)
    sender = plan.actor(art["author"])
    doc = {"form": art["purpose"], "submitted_at": art["event_at"], "reference": req,
           "channel": "portal",
           "submitted_by": {"name": sender["name"], "email": sender["email"]},
           "fields": {"subject": art["subject"]},
           "free_text": authored["free_text"],
           "attachments": [{"filename": n, "media_type": m, "size_bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                           for n, m, b in attachments]}
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()


def render_note(art, plan, authored) -> bytes:
    """Plain case/action note: ordinary header lines (all from the card) then the authored paragraphs."""
    dt = datetime.fromisoformat(art["event_at"])
    head = [f"Author: {plan.actor(art['author'])['name']}", f"Date: {dt:%d %B %Y %H:%M}",
            f"Case: {plan.case_id}", f"Subject: {art['subject']}", ""]
    body = []
    for b in authored["blocks"]:
        if "p" in b:
            body += [b["p"], ""]
        elif "ul" in b:
            body += ["\n".join(f"- {x}" for x in b["ul"]), ""]
        elif "ol" in b:
            body += ["\n".join(f"{n}. {x}" for n, x in enumerate(b["ol"], 1)), ""]
        elif "quote" in b:
            body += ["\n".join(f"> {x}" for x in b["quote"].split("\n")), ""]
    return ("\n".join(head + body).rstrip() + "\n").encode("utf-8")
