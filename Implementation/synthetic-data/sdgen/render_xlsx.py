"""Deterministic XLSX: fixed workbook properties from the scenario creation date and a repacked zip with fixed entry
timestamps, so the same inputs always give the same bytes."""
import io
import re
import zipfile
from datetime import datetime

from openpyxl import Workbook


def _cell(v):
    return "" if v is None else v


def render_rows_xlsx(columns, rows, created_iso, creator, sheet="Export") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    ws.append(list(columns))
    for r in rows:
        ws.append([_cell(r.get(c)) for c in columns])
    for i, c in enumerate(columns, 1):
        width = max([len(str(c))] + [len(str(_cell(r.get(c)))) for r in rows]) + 2
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 60)
    ws.freeze_panes = "A2"
    dt = datetime.fromisoformat(created_iso).replace(tzinfo=None)
    wb.properties.created = wb.properties.modified = dt
    wb.properties.creator = wb.properties.lastModifiedBy = creator
    raw = io.BytesIO()
    wb.save(raw)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            data = src.read(name)
            if name == "docProps/core.xml":  # openpyxl stamps 'modified' with wall-clock time on save; pin it
                stamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + stamp.encode(), data)
            dst.writestr(zi, data)
    return out.getvalue()
