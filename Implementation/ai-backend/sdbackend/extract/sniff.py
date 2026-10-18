"""Byte-signature/container detection. Filenames are never MIME authority."""
from __future__ import annotations

import io
import zipfile

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def detect_media_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if len(names) > 2000:
                    return None
                if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
                    return XLSX
                if "[Content_Types].xml" in names and "word/document.xml" in names:
                    return DOCX
        except zipfile.BadZipFile:
            return None
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    head = text[:4096]
    lowered = head.lower()
    if _looks_like_rfc822(head):
        return "message/rfc822"
    if lowered.lstrip().startswith(("<!doctype html", "<html")):
        return "text/html"
    if text.lstrip()[:1] in ("{", "["):
        from .jsondoc import parse_strict
        try:
            parse_strict(text)
            return "application/json"
        except ValueError:
            pass
    return "text/plain"   # CSV is plain text at signature level; semantic format decides csv vs txt


def _looks_like_rfc822(head: str) -> bool:
    header_block = head.split("\n\n", 1)[0].split("\r\n\r\n", 1)[0]
    names = {line.split(":", 1)[0].strip().lower() for line in header_block.splitlines()
             if ":" in line and not line.startswith((" ", "\t"))}
    return {"from", "date"} <= names and ("mime-version" in names or "subject" in names)


def agrees(declared: str, detected: str | None) -> bool:
    """Declared media type must agree with the detected signature/container."""
    if detected is None:
        return False
    if declared == detected:
        return True
    return declared == "text/csv" and detected == "text/plain"
