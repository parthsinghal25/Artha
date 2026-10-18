"""Complete RFC 5322 / MIME messages with real attachment payloads, plus an HTML preview."""
import hashlib
from email import encoders
from email.charset import Charset, QP
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime, formataddr
from datetime import datetime
from html import escape

from .textfmt import MONTHS

QP_UTF8 = Charset("utf-8")
QP_UTF8.body_encoding = QP
QP_UTF8.header_encoding = QP


def message_id(card: str, sender_email: str) -> str:
    h = hashlib.sha1(f"msg|{card}".encode()).hexdigest()[:22]
    return f"<{h}@{sender_email.split('@')[1]}>"


def _addr(actor):
    return formataddr((actor["name"], actor["email"]))


def blocks_to_text(blocks) -> str:
    out = []
    for b in blocks:
        if "p" in b:
            out.append(b["p"])
        elif "ul" in b:
            out.append("\n".join(f"- {i}" for i in b["ul"]))
        elif "ol" in b:
            out.append("\n".join(f"{n}. {i}" for n, i in enumerate(b["ol"], 1)))
        elif "quote" in b:
            out.append("\n".join(f"> {ln}" if ln else ">" for ln in b["quote"].split("\n")))
    return "\n\n".join(out)


def blocks_to_html(blocks) -> str:
    out = []
    for b in blocks:
        if "p" in b:
            out.append(f"<p>{escape(b['p'])}</p>")
        elif "ul" in b:
            out.append("<ul>" + "".join(f"<li>{escape(i)}</li>" for i in b["ul"]) + "</ul>")
        elif "ol" in b:
            out.append("<ol>" + "".join(f"<li>{escape(i)}</li>" for i in b["ol"]) + "</ol>")
        elif "quote" in b:
            out.append("<blockquote>" + "<br>".join(escape(ln) for ln in b["quote"].split("\n")) + "</blockquote>")
    return "\n".join(out)


def _forward_date(dt: datetime) -> str:
    return f"{dt.strftime('%a')}, {dt.day} {MONTHS[dt.month - 1][:3]} {dt.year} at {dt.strftime('%H:%M')}"


def compose_body(authored, plan, forward_source=None):
    """Return (plain, html) for the message body including signature and any forwarded block."""
    greeting = authored["greeting"]
    lines = ([greeting, ""] if greeting else []) + [blocks_to_text(authored["blocks"]), ""]
    html = ([f"<p>{escape(greeting)}</p>"] if greeting else []) + [blocks_to_html(authored["blocks"])]
    lines += ["\n".join(authored["signoff"])]
    html.append("<p>" + "<br>".join(escape(s) for s in authored["signoff"]) + "</p>")
    if forward_source is not None:
        src_art, src_auth = forward_source
        sender = plan.actor(src_art["author"])
        rcpt = ", ".join(_addr(plan.actor(r)) for r in src_art["recipients"])
        sdt = datetime.fromisoformat(src_art["event_at"])
        head = ["---------- Forwarded message ---------",
                f"From: {_addr(sender)}", f"Date: {_forward_date(sdt)}",
                f"Subject: {src_art['subject']}", f"To: {rcpt}"]
        qbody = "\n".join([src_auth["greeting"], "", blocks_to_text(src_auth["blocks"]), "",
                           "\n".join(src_auth["signoff"])])
        lines += ["", "\n".join(head), "", qbody]
        html.append("<hr><p>" + "<br>".join(escape(h) for h in head) + "</p>")
        html.append("<blockquote>" + f"<p>{escape(src_auth['greeting'])}</p>" + blocks_to_html(src_auth["blocks"]) +
                    "<p>" + "<br>".join(escape(s) for s in src_auth["signoff"]) + "</p></blockquote>")
    return "\n".join(lines).rstrip() + "\n", "\n".join(html)


def build_eml(card_ref, art, authored, plan, attachments, forward_source=None, in_reply_to=None,
              references=()):
    """attachments: list of (filename, mime, bytes). Returns (eml_bytes, preview_html, parts_meta)."""
    sender = plan.actor(art["author"])
    plain, html_body = compose_body(authored, plan, forward_source)
    boundary_seed = hashlib.sha1(card_ref.encode()).hexdigest()[:24]
    root = MIMEMultipart("mixed", boundary=f"----=_Part_{boundary_seed}_mixed")
    alt = MIMEMultipart("alternative", boundary=f"----=_Part_{boundary_seed}_alt")
    alt.attach(MIMEText(plain, "plain", _charset=QP_UTF8))
    alt.attach(MIMEText(f"<html><body style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px\">{html_body}</body></html>",
                        "html", _charset=QP_UTF8))
    root.attach(alt)
    dt = datetime.fromisoformat(art["event_at"])
    root["From"] = _addr(sender)
    root["To"] = ", ".join(_addr(plan.actor(r)) for r in art["recipients"])
    if authored.get("cc"):
        root["Cc"] = ", ".join(_addr(plan.actor(r)) for r in authored["cc"])
    root["Subject"] = art["subject"]
    root["Date"] = format_datetime(dt)
    mid = message_id(card_ref, sender["email"])
    root["Message-ID"] = mid
    if in_reply_to:
        root["In-Reply-To"] = in_reply_to
    if references:
        root["References"] = " ".join(references)
    parts = []
    for idx, (fname, mime, data) in enumerate(attachments, start=2):
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
        parts.append({"mime_part_path": str(idx), "filename": fname, "content_type": mime,
                      "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    eml = root.as_bytes()
    preview = preview_html(root, html_body, parts)
    return eml, preview, parts, mid


def preview_html(msg, html_body, parts):
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(msg[k]))}</td></tr>"
                   for k in ("From", "To", "Cc", "Date", "Subject") if msg[k])
    atts = "".join(f"<li>{escape(p['filename'])} ({p['size_bytes']:,} bytes)</li>" for p in parts)
    att_html = f"<h4>Attachments</h4><ul>{atts}</ul>" if parts else ""
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Message preview</title>"
            "<style>body{font-family:Arial,Helvetica,sans-serif;font-size:14px;max-width:760px;margin:24px auto;color:#1f2933}"
            "th{text-align:left;color:#5b6770;padding-right:12px;vertical-align:top}.note{color:#7b8791;font-size:12px;"
            "border-top:1px solid #d3dae0;margin-top:24px;padding-top:8px}</style></head><body>"
            f"<table>{rows}</table><hr>{html_body}{att_html}"
            "<p class=\"note\">Display preview of the original .eml message; not a separate source.</p></body></html>")
