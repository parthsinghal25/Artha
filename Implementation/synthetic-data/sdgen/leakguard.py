"""Narrow private-authoring-card-ID guard over the representations a receiver actually reads (ruling SC04).

The pattern is the existing staging card reference (`contract.STAGING_CARD_REF`, e.g. `11-D08`); there is no word blacklist and no
model-based leak detection. What is inspected, per held file:
  * XLSX: every stored cell value of every sheet (openpyxl, formulas as their stored text; raw ZIP bytes are never scanned);
  * PDF: the extractable text layer of every page plus the document information fields (pypdf);
  * EML: subject and address headers, every decoded text part, and every decoded attachment by its own type (recursively);
  * CSV / TXT / JSON / JSONL / HTML: the decoded UTF-8 text.
Images carry no text layer here and are not OCR'd by this guard (stated, not hidden). Passing means only that no card ID was found
in those representations; prose meaning still needs independent reading."""
import email
import io
from email import policy
from pathlib import PurePosixPath

from .contract import STAGING_CARD_REF

TEXT_EXTS = {".txt", ".csv", ".json", ".jsonl", ".html"}
MAX_DEPTH = 3


def xlsx_cells(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    out.append((f"{ws.title}!{c.coordinate}", str(c.value)))
    wb.close()
    return out


def pdf_text(data):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    out = [(f"page {i}", p.extract_text() or "") for i, p in enumerate(r.pages, 1)]
    meta = r.metadata or {}
    out += [(f"info {k}", str(v)) for k, v in meta.items()]
    return out


def representations(name, data, depth=0):
    """[(where, text)] for one file's receiver-readable representations, selected by its extension."""
    ext = PurePosixPath(name).suffix.lower()
    if ext == ".xlsx":
        return [(f"cell {w}", t) for w, t in xlsx_cells(data)]
    if ext == ".pdf":
        return pdf_text(data)
    if ext == ".eml":
        msg = email.message_from_bytes(data, policy=policy.default)
        out = [(f"header {h}", str(msg[h])) for h in ("Subject", "From", "To", "Cc") if msg[h]]
        for i, part in enumerate(msg.walk()):
            if part.is_multipart():
                continue
            fname = part.get_filename()
            if fname:
                out.append((f"attachment filename {fname}", fname))
                if depth < MAX_DEPTH:
                    out += [(f"attachment {fname}: {w}", t) for w, t in representations(fname, part.get_payload(decode=True), depth + 1)]
            elif part.get_content_maintype() == "text":
                out.append((f"part {i} {part.get_content_type()}", part.get_content()))
        return out
    if ext in TEXT_EXTS:
        return [("text", data.decode("utf-8", "replace"))]
    return []


def card_id_hits(name, data):
    """[{where, ids}] for every representation of `name` that contains a private authoring-card ID."""
    hits = []
    for where, text in representations(name, data):
        ids = sorted(set(STAGING_CARD_REF.findall(text)))
        if ids:
            hits.append({"where": where, "ids": ids})
    return hits


def scan_files(files):
    """{relative path: hits} over {relative path: bytes}; only files with hits are returned."""
    out = {}
    for rel, data in files.items():
        h = card_id_hits(rel, data)
        if h:
            out[rel] = h
    return out
