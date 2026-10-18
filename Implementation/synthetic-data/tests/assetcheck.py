"""Content checks for rendered attachments: every locked spec value must be readable in the actual output, not merely stable."""
import io

import openpyxl
from pypdf import PdfReader

from sdgen.textfmt import date_variants


def text_of(asset, data, meta=None):
    mt = asset["media_type"]
    if mt == "application/pdf":
        t = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    elif mt == "text/csv":
        t = data.decode()
    elif mt.endswith("sheet"):
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        t = " ".join(str(c.value) for r in ws.iter_rows() for c in r if c.value is not None)
    else:
        t = " ".join((meta or {}).get("drawn_text", []))
    return " ".join(t.split()).lower()


def spec_values(spec):
    for k, v in (spec.get("fields") or {}).items():
        yield k, v
    for i, r in enumerate(spec.get("rows") or []):
        for k, v in r.items():
            yield f"row{i}.{k}", v


def present(v, text):
    if v is None or v == "":
        return True
    if isinstance(v, bool):
        return ("yes" if v else "no") in text or str(v).lower() in text
    sv = " ".join(str(v).split()).lower()
    if sv in text:
        return True
    if len(sv) == 10 and sv[4] == "-" and sv[7] == "-":
        return any(x.lower() in text for x in date_variants(sv))
    return False


def missing_values(asset, data, meta=None, allow=()):
    text = text_of(asset, data, meta)
    return [(k, v) for k, v in spec_values(asset["document_spec"]) if not present(v, text) and k not in allow]
