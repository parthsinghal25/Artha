"""TXT, CSV and DOCX extractors (pure local parsing, exact decoded text)."""
from __future__ import annotations

import io
import re
import zipfile

from .base import MAX_TABLE_ROWS, ExtractionOutput, ExtractorProfile, Segment, failed

TXT_PROFILE = ExtractorProfile("sdb-text", "1.0.0", {"encoding": "utf-8", "bom": "rejected", "newlines": "preserved"})
CSV_PROFILE = ExtractorProfile("sdb-csv-rfc4180", "1.0.0", {
    "parser_profile_id": "csv-utf8-comma-header-rfc4180-v1", "encoding": "utf-8", "delimiter": ",",
    "quote": '"', "header": True, "record_index_basis": "one-based-excluding-header", "execution": "none"})
DOCX_PROFILE = ExtractorProfile("sdb-docx-ooxml", "1.1.0", {
    "omissions": "tabs/breaks, header/footer/footnote/endnote/comment parts and text boxes declared (state partial)",
    "part_name": "/word/document.xml", "paragraph_order": "all w:p in body document order (incl. tables)",
    "macros": "never executed", "external_links": "never loaded"})

CSV_PARSER_PROFILE_ID = CSV_PROFILE.config["parser_profile_id"]


def _decode(data: bytes) -> str | None:
    if data.startswith(b"\xef\xbb\xbf"):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def extract_txt(data: bytes) -> ExtractionOutput:
    text = _decode(data)
    if text is None:
        return failed("text is not BOM-free UTF-8 under the registered profile")
    out = ExtractionOutput(state="complete", text=text, representations={"text": text})
    if text:
        out.segments.append(Segment(0, len(text), "text", {"representation": "text"}, decoded_offset=0))
    return out


def tokenize_csv(text: str) -> list[list[tuple[int, int, str]]]:
    """RFC 4180 tokenizer returning, per record, (start, end, value) code-point spans of each raw field.

    Quoted newlines stay inside the field; they never create a record.
    """
    records: list[list[tuple[int, int, str]]] = []
    fields: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    if n == 0:
        return records
    while True:
        start = i
        if i < n and text[i] == '"':
            i += 1
            buf = []
            while True:
                if i >= n:
                    raise ValueError("unterminated quoted field")
                ch = text[i]
                if ch == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(ch)
                i += 1
            value = "".join(buf)
            if i < n and text[i] not in ",\r\n":
                raise ValueError("characters after closing quote")
        else:
            while i < n and text[i] not in ",\r\n":
                if text[i] == '"':
                    raise ValueError("bare quote in unquoted field")
                i += 1
            value = text[start:i]
        fields.append((start, i, value))
        if i >= n:
            records.append(fields)
            break
        if text[i] == ",":
            i += 1
            continue
        # record terminator
        if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
            i += 2
        else:
            i += 1
        records.append(fields)
        fields = []
        if i >= n:
            break
    return records


def extract_csv(data: bytes) -> ExtractionOutput:
    text = _decode(data)
    if text is None:
        return failed("CSV is not BOM-free UTF-8 under the registered profile")
    try:
        records = tokenize_csv(text)
    except ValueError as exc:
        return failed(f"CSV syntax rejected by registered profile: {exc}")
    if not records:
        return failed("CSV has no header record")
    if len(records) - 1 > MAX_TABLE_ROWS:
        return failed("CSV exceeds the 100,000-row extraction limit")
    header = [f[2] for f in records[0]]
    out = ExtractionOutput(state="complete", text=text, representations={"text": text})
    for col, (s, e, _v) in enumerate(records[0], start=1):
        if e > s:
            out.segments.append(Segment(s, e, "csv", {"header": True, "column_index": col}))
    ragged = False
    for rec_idx, fields in enumerate(records[1:], start=1):
        if len(fields) != len(header):
            ragged = True
        for col, (s, e, _v) in enumerate(fields, start=1):
            if e > s:
                out.segments.append(Segment(s, e, "csv", {
                    "parser_profile_id": CSV_PARSER_PROFILE_ID, "data_record_index": rec_idx, "column_index": col,
                    "column_name": header[col - 1] if col <= len(header) else None, "native_row_key": None}))
    if ragged:
        out.omissions.append("one or more records have a different field count from the header")
    out.headers = {"columns": header, "data_records": len(records) - 1}
    return out


def extract_docx(data: bytes) -> ExtractionOutput:
    try:
        import docx
        from docx.oxml.ns import qn
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if any(n.lower().endswith("vbaproject.bin") for n in z.namelist()):
                omission = "macro project present; never executed and not extracted"
            else:
                omission = None
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - any parser failure is an explicit failed extraction
        return failed(f"DOCX parse failed: {type(exc).__name__}")
    parts: list[str] = []
    out = ExtractionOutput(state="complete")
    cursor = 0
    for index, p in enumerate(document.element.body.iter(qn("w:p")), start=1):
        para_text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        if parts:
            parts.append("\n")
            cursor += 1
        parts.append(para_text)
        if para_text:
            out.segments.append(Segment(cursor, cursor + len(para_text), "docx",
                                        {"part_name": "/word/document.xml", "paragraph_index": index},
                                        decoded_offset=0))
        cursor += len(para_text)
    out.text = "".join(parts)
    out.representations = {"text": out.text}
    if omission:
        out.omissions.append(omission)
    if document.inline_shapes:
        out.omissions.append("embedded images are not transcribed")
        out.state = "partial"
    body = document.element.body
    tabs_breaks = sum(1 for _ in body.iter(qn("w:tab"))) + sum(1 for _ in body.iter(qn("w:br")))
    if tabs_breaks:
        out.omissions.append(f"{tabs_breaks} tab/break element(s) not represented in the extracted text")
        out.state = "partial"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
    other = sorted(n for n in names if re.match(r"word/(header|footer|footnotes|endnotes|comments)\d*\.xml$", n))
    if other:
        out.omissions.append("non-body parts not extracted: " + ", ".join(other))
        out.state = "partial"
    if any(True for _ in body.iter(qn("w:txbxContent"))):
        out.omissions.append("text boxes are not extracted")
        out.state = "partial"
    return out
