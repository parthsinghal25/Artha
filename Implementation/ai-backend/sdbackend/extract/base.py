"""Common extraction model.

An extractor turns exact original bytes into a *decoded representation* (the v0.2 source `text`) plus a layout of
segments. Each segment binds a half-open code-point range of that text to a native location in the original
(PDF page geometry, CSV record/column, EML MIME part/section, XLSX cell, DOCX paragraph, image region).
Locators are always derived from this parser layout and the actual bytes, never from model output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..canonical import canonical_sha256

# Proposed implementation limits from AI/01 §3 (not Google limits, not full-format claims).
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_TOTAL_CHILD_BYTES = 50 * 1024 * 1024
MAX_NESTING_DEPTH = 5
MAX_CHILD_ENTRIES = 100
MAX_TABLE_ROWS = 100_000

SUPPORTED_MEDIA_TYPES = {
    "application/pdf", "message/rfc822", "text/csv", "image/png", "image/jpeg", "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class Segment:
    start: int                     # code point offset in source text (inclusive)
    end: int                       # exclusive
    kind: str                      # pdf | csv | xlsx | eml | text | docx | image
    location: dict[str, Any]       # native-location fields fixed for this segment
    char_boxes: list[list[float]] | None = None   # pdf/image: per-code-point normalised [x0,y0,x1,y1] or None
    decoded_offset: int = 0        # eml/text/docx: offset of segment start inside its decoded representation


@dataclass
class ChildPart:
    mime_part_path: str
    filename: str | None
    content_type: str
    data: bytes
    disposition: str | None


@dataclass
class ExtractionOutput:
    state: str                                   # complete | partial | failed
    text: str = ""
    segments: list[Segment] = field(default_factory=list)
    omissions: list[str] = field(default_factory=list)
    children: list[ChildPart] = field(default_factory=list)
    headers: dict[str, Any] = field(default_factory=dict)
    representations: dict[str, str] = field(default_factory=dict)   # name -> decoded text, hashed for locators
    failure_reason: str | None = None

    def layout(self) -> dict:
        return {"segments": [asdict(s) for s in self.segments],
                "representation_sha256": {k: _sha(v) for k, v in self.representations.items()}}


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractorProfile:
    name: str
    version: str
    config: dict

    @property
    def configuration_sha256(self) -> str:
        return canonical_sha256({"name": self.name, "version": self.version, "config": self.config})


def failed(reason: str) -> ExtractionOutput:
    return ExtractionOutput(state="failed", failure_reason=reason, omissions=[reason])
