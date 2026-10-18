"""Originals from authored sources. Finished files are copied byte-for-byte; structured drafts get neutral formatting:

* text → the authored UTF-8 text exactly (no greeting, signature or boilerplate added);
* email → RFC 5322 message whose text/plain body is the authored body exactly, headers from the source record, and
  real attachment bytes taken from the referenced attachment sources;
* pdf_document → neutral PDF of the authored blocks (reuses the existing reportlab styles/build);
* table / snapshot → CSV or deterministic XLSX via the existing renderers.

Business clocks live in the manifest. File metadata (PDF info dates, XLSX core properties) is stamped with the
source's created_at only when it is a known instant; otherwise a fixed non-business placeholder is left. The real
render wall-clock never enters any byte."""
from __future__ import annotations

import email
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import format_datetime, formataddr, getaddresses, parsedate_to_datetime
from xml.sax.saxutils import escape

from ..render_csv import render_rows_csv
from ..render_mail import QP_UTF8
from ..render_xlsx import render_rows_xlsx
from .events import TABLE_PROFILE, normalise_rows, snapshot_table, table_from_bytes
from .model import FORMATS, Report, safe_input_path

PLACEHOLDER_CREATED = "2000-01-01T00:00:00+00:00"   # explicit non-business file-metadata placeholder


@dataclass
class Rendered:
    data: bytes
    mode: str
    message_id: str | None = None
    mime_parts: list = field(default_factory=list)      # [{mime_part_path, filename, content_type, size_bytes, sha256}]
    body_text: str | None = None                        # the text a reader sees (TXT, EML plain body, CSV)
    table: tuple | None = None                          # (profile, columns, rows) from the final bytes


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def render_order(inp, rep: Report):
    """Sources ordered so attachments and replied-to messages render before the messages that need them."""
    deps = {}
    for sid, s in inp.sources.items():
        d = set()
        for a in s.get("attachments") or []:
            if a.get("source_id"):
                d.add(a["source_id"])
        c = s.get("content") or {}
        if c.get("in_reply_to_source_id"):
            d.add(c["in_reply_to_source_id"])
        d.update(c.get("references_source_ids") or [])
        deps[sid] = {x for x in d if x in inp.sources}
    order, state = [], {}

    def visit(n, stack):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            rep.add("dependency_cycle", f"source:{n}", f"attachment/reply references form a cycle via {stack}", "break the cycle")
            return
        state[n] = 1
        for m in sorted(deps[n]):
            visit(m, stack + [m])
        state[n] = 2
        order.append(n)
    for sid in inp.sources:
        visit(sid, [sid])
    return order


def _addr(p):
    return formataddr((p["display_name"], p["email"]))


def _msgid(inp, sid):
    s = inp.sources[sid]
    given = (s.get("content") or {}).get("message_id")
    if given:
        return given
    dom = (inp.participants.get(s["author"], {}).get("email") or "x@mail.example").split("@")[-1]
    return f"<{hashlib.sha1(f'msg|{sid}'.encode()).hexdigest()[:22]}@{dom}>"


def _text_of(inp, content, key_text, key_path, sid, rep):
    if key_text in content:
        return content[key_text]
    if key_path in content:
        p = safe_input_path(inp.root, content[key_path])
        if p is None or not p.is_file():
            rep.add("missing_file", f"source:{sid}", f"{key_path} {content[key_path]!r} not found under runtime/files/",
                    "supply the file or the text inline")
            return None
        return p.read_bytes().decode("utf-8")
    rep.add("missing_text", f"source:{sid}", f"content needs {key_text} or {key_path}", "the author supplies the text")
    return None


def render_source(inp, sid, done: dict, rep: Report) -> Rendered | None:
    s = inp.sources[sid]
    c = s["content"]
    mode, fmt = c.get("mode"), s["format"]
    if mode == "supplied_file":
        p = safe_input_path(inp.root, c.get("path", ""))
        if p is None or not p.is_file():
            rep.add("missing_file", f"source:{sid}", f"supplied file {c.get('path')!r} not found under runtime/files/",
                    "put the finished original under runtime/files/ and reference it as files/<name>")
            return None
        data = p.read_bytes()
        r = Rendered(data, mode)
        if fmt == "EML":
            _inspect_eml(inp, sid, data, r, done, rep)
        elif fmt == "TXT":
            try:
                r.body_text = data.decode("utf-8")
            except UnicodeDecodeError:
                rep.add("not_utf8", f"source:{sid}", "TXT original is not UTF-8", "save as UTF-8")
        elif fmt == "CSV":
            r.body_text = data.decode("utf-8", "replace")
        return _with_table(s, r, fmt, sid, rep)
    if mode == "text":
        text = _text_of(inp, c, "text", "path", sid, rep)
        if text is None:
            return None
        return Rendered(text.encode("utf-8"), mode, body_text=text)
    if mode == "email":
        return _render_email(inp, sid, done, rep)
    if mode == "pdf_document":
        return Rendered(_render_pdf(inp, sid), mode)
    if mode in ("table", "snapshot"):
        if mode == "table":
            cols = c.get("columns") or []
            rows = normalise_rows(cols, c.get("rows") or [])
        else:
            if s["kind"] not in TABLE_PROFILE:
                rep.add("snapshot_kind", f"source:{sid}", "snapshot needs kind financial_export/work_export/lifecycle_export", "")
                return None
            cl = inp.clocks[sid].get("created_at")
            if cl is None or not cl.known:
                rep.add("snapshot_clock", f"source:{sid}", "a snapshot needs a known created_at (its as-of time)", "")
                return None
            cols, rows = snapshot_table(inp, sid)
        if fmt == "CSV":
            data = render_rows_csv(cols, rows)
        else:
            created = inp.clocks[sid].get("created_at")
            iso = created.value if created is not None and created.known and created.precision != "day" else PLACEHOLDER_CREATED
            author = inp.participants.get(s["author"], {}).get("display_name", s["author"])
            data = render_rows_xlsx(cols, [{k: _typed(v) for k, v in r.items()} for r in rows], iso, author,
                                    sheet=c.get("sheet") or "Export")
        r = Rendered(data, mode, body_text=data.decode("utf-8") if fmt == "CSV" else None)
        return _with_table(s, r, fmt, sid, rep)
    rep.add("content_mode", f"source:{sid}", f"unknown content mode {mode!r}", f"use one of supplied_file, text, email, pdf_document, table, snapshot")
    return None


def _typed(v):
    """XLSX stores integer cells as numbers (the receiver reads the stored value); everything else as text."""
    return int(v) if isinstance(v, str) and v.lstrip("-").isdigit() and len(v.lstrip("-")) <= 12 and not v.startswith("0") else v


def _with_table(s, r, fmt, sid, rep):
    if s["kind"] in TABLE_PROFILE and fmt in ("CSV", "XLSX"):
        try:
            cols, rows = table_from_bytes(fmt, r.data)
            r.table = (TABLE_PROFILE[s["kind"]], cols, rows)
        except Exception as exc:  # noqa: BLE001
            rep.add("table_unreadable", f"source:{sid}", f"cannot read the table: {type(exc).__name__}: {exc}", "")
    return r


def _render_email(inp, sid, done, rep) -> Rendered | None:
    s = inp.sources[sid]
    c = s["content"]
    body = _text_of(inp, c, "body_text", "body_path", sid, rep)
    if body is None:
        return None
    author = inp.participants.get(s["author"])
    ev = inp.clocks[sid].get("event_at")
    if ev is None or not ev.known or ev.precision == "day":
        rep.add("email_needs_time", f"source:{sid}", "a rendered email needs event_at with minute/second precision (Date header)",
                "give the sent time, or supply the finished .eml if the time is genuinely unknown")
        return None
    if not s.get("subject"):
        rep.add("missing_field", f"source:{sid}", "a rendered email needs subject", "supply the subject line")
        return None
    atts = []
    for a in s.get("attachments") or []:
        if a.get("disposition") in ("supplied", "reused", "superseded"):
            r = done.get(a["source_id"])
            if r is None:
                return None
            atts.append((inp.sources[a["source_id"]]["filename"], FORMATS[inp.sources[a["source_id"]]["format"]][0], r.data))
    plain = MIMEText(body, "plain", _charset=QP_UTF8)
    if atts:
        seed = hashlib.sha1(sid.encode()).hexdigest()[:24]
        root = MIMEMultipart("mixed", boundary=f"----=_Part_{seed}_mixed")
        root.attach(plain)
    else:
        root = plain
    root["From"] = _addr(author)
    root["To"] = ", ".join(_addr(inp.participants[x]) for x in s.get("recipients") or [])
    if s.get("cc"):
        root["Cc"] = ", ".join(_addr(inp.participants[x]) for x in s["cc"])
    root["Subject"] = s["subject"]
    root["Date"] = format_datetime(datetime.fromisoformat(ev.value))
    mid = _msgid(inp, sid)
    root["Message-ID"] = mid
    if c.get("in_reply_to_source_id"):
        root["In-Reply-To"] = done[c["in_reply_to_source_id"]].message_id
    refs = [done[x].message_id for x in c.get("references_source_ids") or [] if x in done and done[x].message_id]
    if refs:
        root["References"] = " ".join(refs)
    parts = []
    for idx, (fname, mime, data) in enumerate(atts, start=2):
        maintype, subtype = mime.split("/")
        if maintype == "image":
            part = MIMEImage(data, _subtype=subtype)
        elif maintype == "application":
            part = MIMEApplication(data, _subtype=subtype)
        else:
            part = MIMEBase(maintype, subtype)
            part.set_payload(data)
            encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=fname)
        part.set_param("name", fname)
        root.attach(part)
        parts.append({"mime_part_path": str(idx), "filename": fname, "content_type": mime, "size_bytes": len(data),
                      "sha256": sha256(data)})
    return Rendered(root.as_bytes(), "email", message_id=mid, mime_parts=parts, body_text=body)


def eml_parts(data: bytes):
    """(message, plain body text or None, [{mime_part_path, filename, content_type, size_bytes, sha256}])."""
    msg = email.message_from_bytes(data, policy=policy.default)
    parts, body = [], None

    def walk(m, path):
        nonlocal body
        if m.is_multipart():
            for i, sub in enumerate(m.iter_parts(), start=1):
                walk(sub, f"{path}.{i}" if path else str(i))
            return
        fname = m.get_filename()
        if fname or m.get_content_disposition() == "attachment":
            payload = m.get_payload(decode=True) or b""
            parts.append({"mime_part_path": path or "1", "filename": fname, "content_type": m.get_content_type(),
                          "size_bytes": len(payload), "sha256": sha256(payload)})
        elif m.get_content_type() == "text/plain" and body is None:
            body = m.get_content()
    walk(msg, "")
    return msg, body, parts


def _inspect_eml(inp, sid, data, r, done, rep):
    """A finished .eml must agree with its source record: sender, Date, Subject and every attachment's bytes."""
    s = inp.sources[sid]
    subj = f"source:{sid}"
    try:
        msg, body, parts = eml_parts(data)
    except Exception as exc:  # noqa: BLE001
        rep.add("eml_unparseable", subj, f"{type(exc).__name__}: {exc}", "supply a valid RFC 5322 message")
        return
    r.body_text, r.mime_parts, r.message_id = body, parts, (str(msg["Message-ID"]) if msg["Message-ID"] else None)
    author = inp.participants.get(s["author"], {})
    senders = [a.lower() for _, a in getaddresses([str(msg["From"] or "")])]
    if not author.get("email") or author["email"].lower() not in senders:
        rep.add("eml_author_mismatch", subj, f"From {msg['From']!r} is not the author {s['author']} <{author.get('email')}>",
                "the record's author must be the actual sender")
    ev = inp.clocks[sid].get("event_at")
    if msg["Date"] is None:
        rep.add("eml_no_date", subj, "message has no Date header", "add Date")
    elif ev is not None and ev.known and ev.precision != "day":
        hdr = parsedate_to_datetime(str(msg["Date"]))
        if not (ev.start() <= hdr <= ev.end()):
            rep.add("inconsistent_clock", subj, f"Date header {msg['Date']} differs from event_at {ev.value}",
                    "the record's event_at is the message's own sent time")
    elif ev is not None and ev.known:
        hdr = parsedate_to_datetime(str(msg["Date"]))
        if hdr.astimezone(ev.start().tzinfo).date() != ev.local_day():
            rep.add("inconsistent_clock", subj, f"Date header {msg['Date']} is not on event_at day {ev.value}", "")
    if s.get("subject") and str(msg["Subject"] or "") != s["subject"]:
        rep.add("eml_subject_mismatch", subj, f"Subject {msg['Subject']!r} differs from the record {s['subject']!r}", "")
    want = {}
    for a in s.get("attachments") or []:
        if a.get("disposition") in ("supplied", "reused", "superseded") and a.get("source_id") in done:
            want[done[a["source_id"]].data and sha256(done[a["source_id"]].data)] = a["source_id"]
    have = {p["sha256"] for p in parts}
    for h, aid in want.items():
        if h not in have:
            rep.add("attachment_bytes_mismatch", subj, f"declared attachment {aid} is not byte-identical to any MIME part",
                    "attach the exact bytes of the held original")
    for p in parts:
        if p["sha256"] not in want:
            rep.add("undeclared_attachment", subj, f"MIME part {p['mime_part_path']} ({p['filename']}) is not a declared held source",
                    "add the attachment as its own source (kind attachment_original) and list it in attachments")


def _render_pdf(inp, sid) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

    from ..render_pdf import BAND, INK, RULE, _build, _kv_table, _styles
    s = inp.sources[sid]
    c = s["content"]
    st = _styles()
    width = A4[0] - 40 * mm
    story = []
    if c.get("issuer"):
        story += [Paragraph(escape(c["issuer"]), st["issuer"]),
                  HRFlowable(width="100%", thickness=0.8, color=INK, spaceBefore=3, spaceAfter=4)]
    story.append(Paragraph(escape(c["title"]), st["title"]))
    for b in c.get("blocks") or []:
        if "h" in b:
            story.append(Paragraph(escape(b["h"]), st["h2"]))
        elif "p" in b:
            story.append(Paragraph(escape(b["p"]).replace("\n", "<br/>"), st["body"]))
        elif "ul" in b:
            story += [Paragraph(escape(i), st["bullet"], bulletText="•") for i in b["ul"]]
        elif "kv" in b:
            story.append(_kv_table([(str(k), str(v)) for k, v in b["kv"]], st, width))
        elif "table" in b:
            cols = b["table"]["columns"]
            data = [[Paragraph(escape(str(x)), st["bold"]) for x in cols]]
            data += [[Paragraph(escape("" if x is None else str(x)), st["value"]) for x in row] for row in b["table"]["rows"]]
            t = Table(data, colWidths=[width / len(cols)] * len(cols), repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BAND), ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [Spacer(1, 4), t]
    created = inp.clocks[sid].get("created_at")
    iso = created.value if created is not None and created.known and created.precision != "day" else None
    author = inp.participants.get(s["author"], {}).get("display_name", s["author"])
    footer = c.get("footer") or (c.get("issuer") or "")
    if iso is not None:
        return _build(story, c["title"], author, iso, footer)
    # unknown/day-precision creation: a fixed 2000-01-01 info date, an explicit non-business placeholder
    return _build(story, c["title"], author, PLACEHOLDER_CREATED, footer)


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
