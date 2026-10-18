"""Rendered attachment CONTENT, not just repeatability: every locked field/row value of every held non-EML attachment in all
forty plans must be readable in the actual output, tables must keep header and per-row values apart, and sampled
documents must have a sensible page structure. (Visual layout of a sample was also inspected by eye when this was written;
that is a human check, recorded in the readiness report, not something a test can establish.)"""
import csv
import glob
import io
import json

import openpyxl
import pytest
from pypdf import PdfReader

from assetcheck import missing_values, text_of
from sdgen.assets import AuthoringRequired, render_asset
from sdgen.paths import PLAN_DIR

# the upload receipt prints the capture/upload moments in screenshot form ("21 Dec 2025, 10:00 am AEDT"), not as ISO strings
RECEIPT_ISO_FIELDS = {"uploaded_at", "captured_date"}


def _assets():
    for f in sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json"))):
        d = json.load(open(f))
        names = {a["id"]: a["name"] for a in d["actors"]}
        for a in d["assets"]:
            if a.get("delivery_state") == "held" and a["media_type"] != "message/rfc822":
                yield d["case_number"], a, names[a["owner"]]


def test_every_locked_value_of_every_held_attachment_is_readable_in_the_rendered_output():
    n, bad = 0, []
    for case, a, owner in _assets():
        data, meta = render_asset(a, owner)
        allow = RECEIPT_ISO_FIELDS if a["media_type"] == "image/jpeg" else ()
        for k, v in missing_values(a, data, meta, allow):
            bad.append((case, a["filename"], k, v))
        n += 1
    assert n == 351 and not bad, bad[:10]


def test_header_total_and_per_row_amount_no_longer_overwrite_each_other():
    """Content sweep finding: 05-06 refund confirmation lost its 13800 header total because a row column had the same name."""
    case, a, owner = next(x for x in _assets() if x[1]["filename"] == "05-06-fee-refund-transaction-confirmation.csv")
    rows = list(csv.DictReader(io.StringIO(render_asset(a, owner)[0].decode())))
    assert [r["amount_minor"] for r in rows] == ["13800", "13800"] and [r["row_amount_minor"] for r in rows] == ["6900", "6900"]
    assert [r["reversal_reference"] for r in rows] == ["REV-05-01", "REV-05-02"]


def test_csv_and_xlsx_tables_keep_one_row_per_spec_row_with_header_fields_repeated():
    checked = 0
    for case, a, owner in _assets():
        if not (a["media_type"] == "text/csv" or a["media_type"].endswith("sheet")):
            continue
        data, _ = render_asset(a, owner)
        if a["media_type"] == "text/csv":
            table = list(csv.reader(io.StringIO(data.decode())))
        else:
            table = [[("" if c.value is None else str(c.value)) for c in r] for r in openpyxl.load_workbook(io.BytesIO(data)).active.iter_rows()]
        head, body = table[0], table[1:]
        spec = a["document_spec"]
        assert len(body) == max(1, len(spec.get("rows") or [])), a["filename"]
        assert len(set(head)) == len(head), f"duplicate column names in {a['filename']}"
        for f in (spec.get("fields") or {}):
            assert f in head, (a["filename"], f)
        for i, r in enumerate(spec.get("rows") or []):
            row = dict(zip(head, body[i]))
            for k, v in r.items():
                col = k if k in head and k not in (spec.get("fields") or {}) else f"row_{k}"
                assert row[col] == ("" if v is None else str(v)), (a["filename"], i, k)
        checked += 1
    assert checked == 64 + 62


def test_pdf_attachments_carry_issuer_reference_and_footer_with_a_sensible_page_count():
    pages = {}
    for case, a, owner in _assets():
        if a["media_type"] != "application/pdf":
            continue
        data, _ = render_asset(a, owner)
        r = PdfReader(io.BytesIO(data))
        pages[a["filename"]] = len(r.pages)
        text = " ".join(" ".join(p.extract_text().split()) for p in r.pages)
        assert a["document_spec"]["issuer"] in text and "Page 1" in text, a["filename"]  # the printed title is a rendering of the semantic type, not the asset title
        ref = (a["document_spec"].get("fields") or {}).get("document_reference")
        if ref:
            assert text.count(ref) >= 1
        assert r.metadata is not None
    assert pages and max(pages.values()) <= 6 and sum(1 for n in pages.values() if n == 1) > 150


def test_png_and_jpeg_screenshots_are_non_blank_with_the_expected_text_drawn():
    from PIL import Image
    seen = {"image/png": 0, "image/jpeg": 0}
    for case, a, owner in _assets():
        if a["media_type"] not in seen:
            continue
        data, meta = render_asset(a, owner)
        img = Image.open(io.BytesIO(data))
        assert img.size[0] >= 1000 and img.size[1] >= 400
        assert len(set(img.convert("L").resize((64, 64)).getdata())) > 4  # not a blank canvas
        assert meta["drawn_text"] and all(isinstance(t, str) for t in meta["drawn_text"])
        if a["media_type"] == "image/jpeg":
            f = a["document_spec"]["fields"]
            joined = " ".join(meta["drawn_text"])
            assert f["upload_reference"] in joined and f["member_account_number"] in joined and "Upload receipt" in joined
            assert "21 Dec 2025, 10:00 am AEDT" in joined  # the ISO upload time is shown in the screenshot's own style
            for r in a["document_spec"]["rows"]:
                assert r["file"] in joined and r["receipt_state"].capitalize() in joined
        seen[a["media_type"]] += 1
    assert seen == {"image/png": 7, "image/jpeg": 1}


def test_eml_attachments_remain_explicit_authoring_gaps():
    n = 0
    for f in sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json"))):
        d = json.load(open(f))
        for a in d["assets"]:
            if a.get("delivery_state") == "held" and a["media_type"] == "message/rfc822":
                with pytest.raises(AuthoringRequired):
                    render_asset(a, "x")
                n += 1
    assert n == 15
