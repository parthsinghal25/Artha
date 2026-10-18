"""RFC 5322/MIME extractor.

Source text = a decoded header block followed by the decoded text/plain body. MIME paths follow the Shared
convention: root `0`, ordered children `.1`, `.2`, recursively. Spans in locators refer to the digest-bound decoded
representation of the part/section (never raw-byte offsets). Attachment parts are returned as children with exact
decoded bytes so they can be bound to separately supplied originals or registered as embedded assets.
"""
from __future__ import annotations

import email
import hashlib
import html
import re
from email import policy
from email.message import EmailMessage

from .base import (MAX_CHILD_ENTRIES, MAX_NESTING_DEPTH, MAX_TOTAL_CHILD_BYTES, ChildPart, ExtractionOutput,
                   ExtractorProfile, Segment, failed)

EML_PROFILE = ExtractorProfile("sdb-eml", "1.2.0", {
    "attached_messages": "message/rfc822 parts are leaf children (own asset, recursive extraction, depth-bounded)",
    "attachment_listing": "trailing 'Attachments:' list (filename, content type, MIME path) per held part",
    "policy": "email.policy.default", "headers": ["From", "To", "Cc", "Date", "Subject"],
    "body": "first text/plain in first multipart/alternative or root; html-only bodies are tag-stripped",
    "mime_path_convention": "root 0; children .1,.2 recursively", "remote_content": "never loaded",
    "limits": {"depth": MAX_NESTING_DEPTH, "children": MAX_CHILD_ENTRIES, "child_bytes": MAX_TOTAL_CHILD_BYTES}})

HEADER_ORDER = ("From", "To", "Cc", "Date", "Subject")


def _walk(part: EmailMessage, path: str, depth: int, acc: list[tuple[str, EmailMessage, int]]) -> None:
    acc.append((path, part, depth))
    if part.get_content_type() == "message/rfc822" and depth > 0:
        return            # attached message: one child asset, extracted recursively as its own EML by ingest
    if part.is_multipart() and depth < MAX_NESTING_DEPTH:
        for i, child in enumerate(part.iter_parts(), start=1):
            _walk(child, f"{path}.{i}", depth + 1, acc)


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def extract_eml(data: bytes) -> ExtractionOutput:
    try:
        msg = email.message_from_bytes(data, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        return failed(f"EML parse failed: {type(exc).__name__}")
    if not msg.keys():
        return failed("EML has no headers")
    parts: list[tuple[str, EmailMessage, int]] = []
    _walk(msg, "0", 0, parts)
    out = ExtractionOutput(state="complete")
    if any(depth >= MAX_NESTING_DEPTH and p.is_multipart() for _, p, depth in parts):
        out.omissions.append("MIME nesting deeper than 5 levels not extracted")
        out.state = "partial"

    header_lines = []
    for name in HEADER_ORDER:
        value = msg.get(name)
        if value is not None:
            header_lines.append(f"{name}: {str(value)}")
    header_block = "\n".join(header_lines)
    out.headers = {name.lower(): str(msg.get(name)) for name in
                   ("From", "To", "Cc", "Date", "Subject", "Message-ID", "In-Reply-To", "References") if msg.get(name)}

    body_part, body_section, body_path = None, None, None
    for path, part, _ in parts:
        if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
            body_part, body_section, body_path = part, "body_text", path
            break
    if body_part is None:
        for path, part, _ in parts:
            if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                body_part, body_section, body_path = part, "body_html", path
                out.omissions.append("no text/plain body; HTML body tag-stripped, formatting omitted")
                out.state = "partial"
                break
    body = ""
    if body_part is not None:
        try:
            decoded = body_part.get_content()
        except Exception:  # noqa: BLE001
            decoded = None
            out.omissions.append("body part could not be decoded")
            out.state = "partial"
        if decoded is not None:
            body = _strip_html(decoded) if body_section == "body_html" else decoded
    else:
        out.omissions.append("message has no readable body part")

    separator = "\n\n"
    out.text = header_block + separator + body
    out.representations = {"0:headers": header_block}
    if body_path:
        out.representations[f"{body_path}:{body_section}"] = body
    # segments: each header line, then the body
    cursor = 0
    for line in header_lines:
        out.segments.append(Segment(cursor, cursor + len(line), "eml",
                                    {"mime_part_path": "0", "section": "headers", "representation": "0:headers"},
                                    decoded_offset=cursor))
        cursor += len(line) + 1
    body_start = len(header_block) + len(separator)
    if body:
        out.segments.append(Segment(body_start, body_start + len(body), "eml",
                                    {"mime_part_path": body_path, "section": body_section,
                                     "representation": f"{body_path}:{body_section}"}, decoded_offset=0))

    total = 0
    for path, part, _ in parts:
        if (part.is_multipart() and part.get_content_type() != "message/rfc822") or path == body_path:
            continue
        ctype = part.get_content_type()
        disposition = part.get_content_disposition()
        if ctype in ("text/plain", "text/html") and disposition != "attachment" and part.get_filename() is None:
            continue   # alternative body representation
        if len(out.children) >= MAX_CHILD_ENTRIES:
            out.omissions.append("more than 100 MIME children; remainder not extracted")
            out.state = "partial"
            break
        payload = part.get_payload(decode=True)
        if ctype == "message/rfc822":
            inner = part.get_payload()
            payload = inner[0].as_bytes() if isinstance(inner, list) and inner else payload
        if payload is None:
            out.omissions.append(f"MIME part {path} could not be decoded")
            out.state = "partial"
            continue
        total += len(payload)
        if total > MAX_TOTAL_CHILD_BYTES:
            out.omissions.append("attachment bytes exceed 50 MiB; remainder not extracted")
            out.state = "partial"
            break
        out.children.append(ChildPart(path, part.get_filename(), ctype, payload, disposition))
    if out.children:
        listing_head = "\n\nAttachments:"
        out.text += listing_head
        cursor = len(out.text)
        for child in out.children:
            line = f"\n- {child.filename or '(unnamed part)'} ({child.content_type}, MIME part {child.mime_part_path})"
            start = cursor + 1   # after the newline
            out.text += line
            cursor = len(out.text)
            out.segments.append(Segment(start, cursor, "eml", {
                "mime_part_path": child.mime_part_path, "section": "attachment", "representation": None,
                "child_bytes_sha256": hashlib.sha256(child.data).hexdigest()}))
    return out
