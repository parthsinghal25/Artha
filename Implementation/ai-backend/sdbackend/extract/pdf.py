"""PDF text-layer extractor with per-character geometry.

Text comes from the PDF's own text layer (pdfplumber/pdfminer). Every non-whitespace character is aligned to its
glyph box so citation spans map to a normalised, top-left, upright crop-box rectangle. Rotated pages are mapped at
page precision only (no region) until rotation-aware geometry is verified.

Pages without a text layer (scanned pages) are rendered locally (pypdfium2, crop box, upright) and transcribed by the
same local Apple Vision OCR used for images: line-level geometry only, low-confidence lines omitted, extraction
declared `partial` with an explicit "unreviewed machine OCR" omission. If OCR is unavailable the page stays an
explicit omission. A vision LLM is never used to produce this text.
"""
from __future__ import annotations

import io
import re

from .base import ExtractionOutput, ExtractorProfile, Segment, failed

PDF_PROFILE = ExtractorProfile("sdb-pdf-textlayer", "1.3.0", {
    "scanned_pages": "pypdfium2 render at 2x of crop box + Apple Vision OCR, line boxes, min confidence 0.5",
    "library": "pdfplumber", "text": "extract_text_lines(return_chars=True) joined by \\n", "page_separator": "\\n\\n",
    "geometry": "glyph boxes normalised to cropbox, top-left origin", "rotated_pages": "page precision only", "unmapped_glyphs": "U+FFFD with declared omission",
    "javascript": "never executed", "ocr": "only for pages without a text layer"})


def _ocr_page(data: bytes, page_index: int) -> tuple[list[tuple[str, list[float]]], str | None]:
    """[(line text, [x0,y0,x1,y1] normalised top-left)] for one rendered page, or ([], reason)."""
    try:
        import pypdfium2 as pdfium
        from .image import MIN_LINE_CONFIDENCE, OCRUnavailable, _ocr_lines
        doc = pdfium.PdfDocument(data)
        try:
            img = doc[page_index].render(scale=2.0).to_pil().convert("RGB")
        finally:
            doc.close()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        lines = _ocr_lines(buf.getvalue())
    except Exception as exc:  # noqa: BLE001 - includes OCRUnavailable
        return [], f"{type(exc).__name__}: {str(exc)[:160]}"
    return [(t, r) for t, conf, r in lines if conf >= MIN_LINE_CONFIDENCE and t.strip()], None

PAGE_SEPARATOR = "\n\n"
CID_RE = re.compile(r"\(cid:\d+\)")
REPLACEMENT = "\ufffd"


def extract_pdf(data: bytes) -> ExtractionOutput:
    try:
        import pdfplumber
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return failed(f"PDF parse failed: {type(exc).__name__}")
    out = ExtractionOutput(state="complete")
    texts: list[str] = []
    cursor = 0
    with pdf:
        if getattr(pdf.doc, "encryption", None):
            return failed("encrypted PDF is unsupported")
        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                lines = page.extract_text_lines(return_chars=True, strip=False)
            except Exception:  # noqa: BLE001
                lines = []
                out.omissions.append(f"page {page_no} text layer could not be read")
            unmapped = 0
            for line in lines:
                line["text"], n = CID_RE.subn(REPLACEMENT, line["text"])
                unmapped += n
                for ch in line["chars"]:
                    if CID_RE.fullmatch(ch.get("text") or ""):
                        ch["text"] = REPLACEMENT
            if unmapped:
                out.omissions.append(f"page {page_no}: {unmapped} glyph(s) without a Unicode mapping shown as U+FFFD")
            page_text = "\n".join(line["text"] for line in lines)
            ocr_boxes = None
            if not page_text.strip():
                ocr, why = _ocr_page(data, page_no - 1)
                if ocr:
                    page_text = "\n".join(t for t, _ in ocr)
                    ocr_boxes = []
                    for k, (t, r) in enumerate(ocr):
                        if k:
                            ocr_boxes.append(None)
                        ocr_boxes.extend([r] * len(t))
                    out.omissions.append(f"page {page_no} has no text layer; text is unreviewed machine OCR with "
                                         "line-level geometry")
                else:
                    out.omissions.append(f"page {page_no} has no text layer and OCR was unavailable ({why})")
                out.state = "partial"
            if texts:
                texts.append(PAGE_SEPARATOR)
                cursor += len(PAGE_SEPARATOR)
            rotation = int(page.rotation or 0) % 360
            x0c, top_c, x1c, bottom_c = page.cropbox if page.cropbox else page.bbox
            width, height = (x1c - x0c), (bottom_c - top_c)
            boxes: list[list[float] | None] | None = None
            if ocr_boxes is not None:
                boxes = ocr_boxes if rotation == 0 else None
            elif rotation == 0 and page_text:
                boxes = []
                for k, line in enumerate(lines):
                    if k:
                        boxes.append(None)
                    line_boxes = _align(line["text"], line["chars"], x0c, top_c, width, height)
                    if line_boxes is None:
                        boxes = None
                        break
                    boxes.extend(line_boxes)
                if boxes is None:
                    out.omissions.append(f"page {page_no} glyph alignment failed; page-precision mapping only")
            texts.append(page_text)
            if page_text:
                out.segments.append(Segment(cursor, cursor + len(page_text), "pdf",
                                            {"page": page_no, "rotation_applied": str(rotation)},
                                            char_boxes=boxes))
            cursor += len(page_text)
        out.headers = {"page_count": len(pdf.pages)}
    out.text = "".join(texts)
    out.representations = {"text": out.text}
    if not out.text.strip():
        return failed("PDF has no text layer on any page")
    return out


def _align(text: str, chars: list[dict], x0c: float, top_c: float, width: float, height: float):
    """Align each non-space code point of `text` to the next matching glyph; spaces get no box."""
    glyphs = [c for c in chars if c.get("text") and not c["text"].isspace()]
    boxes: list[list[float] | None] = []
    j = 0
    for ch in text:
        if ch.isspace():
            boxes.append(None)
            continue
        while j < len(glyphs) and glyphs[j]["text"] != ch:
            # pdfminer may emit ligatures/multichar glyphs; give up on mismatch beyond a short lookahead
            if len(glyphs[j]["text"]) > 1 and glyphs[j]["text"].startswith(ch):
                break
            j += 1
            if j >= len(glyphs):
                return None
        if j >= len(glyphs):
            return None
        g = glyphs[j]
        boxes.append([_clip((g["x0"] - x0c) / width), _clip((g["top"] - top_c) / height),
                      _clip((g["x1"] - x0c) / width), _clip((g["bottom"] - top_c) / height)])
        j += 1
    return boxes


def _clip(v: float) -> float:
    return round(min(1.0, max(0.0, v)), 6)
