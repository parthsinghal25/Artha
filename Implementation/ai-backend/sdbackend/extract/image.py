"""PNG/JPEG extractor using local Apple Vision OCR (macOS; free, on-device).

OCR output is a machine transcription. It is emitted as a *partial* extraction with an explicit omission stating
that the transcription is unreviewed; low-confidence lines are omitted rather than guessed. Line boxes come from
the OCR engine on the EXIF-normalised displayed image (top-left normalised), satisfying the Shared image locator
convention. A vision LLM is never used to create a verified transcription or location.
"""
from __future__ import annotations

import io
import platform
from functools import lru_cache

from .base import ExtractionOutput, ExtractorProfile, Segment, failed

MIN_LINE_CONFIDENCE = 0.5
IMAGE_PROFILE = ExtractorProfile("sdb-image-applevision-ocr", "1.0.0", {
    "engine": "Apple Vision VNRecognizeTextRequest", "level": "accurate", "language_correction": False,
    "languages": ["en-AU", "en-US"], "min_line_confidence": MIN_LINE_CONFIDENCE,
    "orientation": "EXIF transposed before OCR", "line_separator": "\\n", "review": "unreviewed machine OCR"})


def image_metadata(data: bytes) -> tuple[int, int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        orientation = int(im.getexif().get(274, 1) or 1)
        return im.size[0], im.size[1], orientation if 1 <= orientation <= 8 else 1


def _ocr_lines(png_bytes: bytes) -> list[tuple[str, float, list[float]]]:
    import Vision
    from Foundation import NSData
    ns = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    request.setRecognitionLanguages_(["en-AU", "en-US"])
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        if error is None:
            raise OCRUnavailable("Apple Vision text recognition returned failure without an NSError: the on-device "
                                 "recognition service is unreachable from this process (observed when a sandbox "
                                 "blocks the com.apple.appleneuralengine mach service)")
        raise OCRUnavailable(f"Apple Vision text recognition failed: {error.localizedDescription()}")
    lines = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        cand = candidates[0]
        bb = obs.boundingBox()            # normalised, bottom-left origin
        x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
        rect = [round(max(0.0, x), 6), round(max(0.0, 1 - (y + h)), 6),
                round(min(1.0, x + w), 6), round(min(1.0, 1 - y), 6)]
        lines.append((str(cand.string()), float(cand.confidence()), rect))
    # reading order: top-to-bottom, then left-to-right
    lines.sort(key=lambda l: (round(l[2][1], 2), l[2][0]))
    return lines


class OCRUnavailable(RuntimeError):
    """The local OCR engine cannot run in this process (platform, framework or OS service unavailable)."""


@lru_cache(maxsize=1)
def ocr_runtime_status() -> dict:
    """Self-check: OCR a generated synthetic image. {available, reason}. Never claims success it did not observe."""
    if platform.system() != "Darwin":
        return {"available": False, "reason": "local OCR engine (Apple Vision) unavailable on this platform"}
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (480, 120), "white")
        ImageDraw.Draw(im).text((20, 40), "OCR SELF CHECK 2026", fill="black", font_size=36)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        lines = _ocr_lines(buf.getvalue())
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    text = " ".join(t for t, _, _ in lines).upper()
    if "SELF" not in text:
        return {"available": False, "reason": f"self-check recognised {text!r}, not the probe text"}
    return {"available": True, "reason": "Apple Vision self-check recognised the probe text"}


def extract_image(data: bytes) -> ExtractionOutput:
    if platform.system() != "Darwin":
        return failed("local OCR engine (Apple Vision) unavailable on this platform")
    try:
        from PIL import Image, ImageOps
        width, height, orientation = image_metadata(data)
        with Image.open(io.BytesIO(data)) as im:
            upright = ImageOps.exif_transpose(im).convert("RGB")
            buf = io.BytesIO()
            upright.save(buf, format="PNG")
        lines = _ocr_lines(buf.getvalue())
    except OCRUnavailable as exc:
        return failed(f"image OCR unavailable in this runtime: {exc}")
    except Exception as exc:  # noqa: BLE001
        return failed(f"image OCR failed: {type(exc).__name__}: {str(exc)[:200]}")
    out = ExtractionOutput(state="partial")
    out.omissions.append("machine OCR transcription; not independently reviewed for fidelity")
    kept, dropped = [], 0
    for text, conf, rect in lines:
        if conf < MIN_LINE_CONFIDENCE or not text.strip():
            dropped += 1
            continue
        kept.append((text, rect))
    if dropped:
        out.omissions.append(f"{dropped} OCR line(s) below confidence {MIN_LINE_CONFIDENCE} omitted")
    cursor = 0
    texts = []
    for text, rect in kept:
        if texts:
            texts.append("\n")
            cursor += 1
        out.segments.append(Segment(cursor, cursor + len(text), "image", {
            "original_width": width, "original_height": height, "exif_orientation": orientation,
            "line_region": {"x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]}}))
        texts.append(text)
        cursor += len(text)
    out.text = "".join(texts)
    out.representations = {"text": out.text}
    out.headers = {"width": width, "height": height, "exif_orientation": orientation, "ocr_lines": len(kept)}
    if not kept:
        out.omissions.append("no legible text recognised")
    return out
