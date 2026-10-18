"""XLSX extractor: stored cell values only; formulas are reported, never evaluated; macros/links never run."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from .base import MAX_TABLE_ROWS, ExtractionOutput, ExtractorProfile, Segment, failed

XLSX_PROFILE = ExtractorProfile("sdb-xlsx-openpyxl", "1.0.0", {
    "values": "cached stored values (data_only=True)", "formulas": "flagged, never evaluated",
    "text_layout": "'## Sheet: <name>' then one 'A1: value' line per non-empty cell, row-major",
    "external_links": "never loaded", "macros": "never executed"})

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sheet_ids(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("xl/workbook.xml"))
    return {s.get("name"): s.get("sheetId") for s in root.iterfind("m:sheets/m:sheet", _NS)}


def _render(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def extract_xlsx(data: bytes) -> ExtractionOutput:
    try:
        import openpyxl
        values_wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False, keep_links=False)
        formula_wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=False, keep_links=False)
        sheet_ids = _sheet_ids(data)
    except Exception as exc:  # noqa: BLE001
        return failed(f"XLSX parse failed: {type(exc).__name__}")
    out = ExtractionOutput(state="complete")
    parts: list[str] = []
    cursor = 0
    formula_cells = 0
    missing_cached = 0
    for ws in values_wb.worksheets:
        fws = formula_wb[ws.title]
        if ws.max_row > MAX_TABLE_ROWS:
            out.omissions.append(f"sheet {ws.title} exceeds 100,000 rows; not extracted")
            out.state = "partial"
            continue
        heading = f"## Sheet: {ws.title}"
        if parts:
            parts.append("\n")
            cursor += 1
        parts.append(heading)
        cursor += len(heading)
        for row in ws.iter_rows():
            for cell in row:
                fcell = fws[cell.coordinate]
                is_formula = isinstance(fcell.value, str) and fcell.value.startswith("=")
                formula_cells += is_formula
                if cell.value is None:
                    if is_formula:
                        missing_cached += 1
                    continue
                rendered = _render(cell.value)
                prefix = f"\n{cell.coordinate}: "
                parts.append(prefix)
                cursor += len(prefix)
                out.segments.append(Segment(cursor, cursor + len(rendered), "xlsx", {
                    "sheet_id": sheet_ids.get(ws.title, ws.title), "sheet_name": ws.title,
                    "a1_range": re.sub(r"\$", "", cell.coordinate), "formula": is_formula}))
                parts.append(rendered)
                cursor += len(rendered)
    out.text = "".join(parts)
    out.representations = {"text": out.text}
    if formula_cells:
        out.omissions.append(f"{formula_cells} formula cell(s) shown as stored values; formulas not evaluated")
    if missing_cached:
        out.omissions.append(f"{missing_cached} formula cell(s) lack a stored value and are omitted")
        out.state = "partial"
    return out
