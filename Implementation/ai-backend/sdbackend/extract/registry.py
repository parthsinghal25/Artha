"""Semantic-format → extractor dispatch, with byte/MIME agreement checks."""
from __future__ import annotations

from typing import Callable

from .base import MAX_ASSET_BYTES, ExtractionOutput, ExtractorProfile, failed
from .eml import EML_PROFILE, extract_eml
from .image import IMAGE_PROFILE, extract_image
from .jsondoc import JSON_PROFILE, extract_json
from .pdf import PDF_PROFILE, extract_pdf
from .sniff import DOCX, XLSX, agrees, detect_media_type
from .textual import CSV_PROFILE, DOCX_PROFILE, TXT_PROFILE, extract_csv, extract_docx, extract_txt
from .xlsx import XLSX_PROFILE, extract_xlsx

EXTRACTORS: dict[str, tuple[ExtractorProfile, Callable[[bytes], ExtractionOutput]]] = {
    "application/pdf": (PDF_PROFILE, extract_pdf),
    "message/rfc822": (EML_PROFILE, extract_eml),
    "text/csv": (CSV_PROFILE, extract_csv),
    "text/plain": (TXT_PROFILE, extract_txt),
    "application/json": (JSON_PROFILE, extract_json),
    XLSX: (XLSX_PROFILE, extract_xlsx),
    DOCX: (DOCX_PROFILE, extract_docx),
    "image/png": (IMAGE_PROFILE, extract_image),
    "image/jpeg": (IMAGE_PROFILE, extract_image),
}


def validate_bytes(data: bytes, declared_media_type: str) -> tuple[bool, str | None, str]:
    """Return (ok, detected_media_type, reason)."""
    if len(data) > MAX_ASSET_BYTES:
        return False, None, "asset exceeds the 20 MiB per-asset limit"
    detected = detect_media_type(data)
    if declared_media_type not in EXTRACTORS and declared_media_type != "text/html":
        return False, detected, f"declared media type {declared_media_type} is not a supported first-build format"
    if not agrees(declared_media_type, detected):
        return False, detected, f"declared {declared_media_type} disagrees with detected signature {detected}"
    return True, detected, "declared media type agrees with byte signature/container"


def extract(data: bytes, media_type: str) -> tuple[ExtractorProfile | None, ExtractionOutput]:
    entry = EXTRACTORS.get(media_type)
    if entry is None:
        return None, failed(f"no extractor registered for {media_type}")
    profile, fn = entry
    return profile, fn(data)
