"""Truthful application/json support (portal submissions; M0).

The exact original bytes are retained and bound as the asset. The extraction text is the VERBATIM decoded UTF-8
of those bytes (no reformatting, no projection), so every citation is an exact text span of the original and the
full-source view shows the submission as received. Validation is strict RFC 8259: BOM-free UTF-8, one JSON value,
no duplicate object keys, no NaN/Infinity. JSON-pointer spans of string values are recorded as navigation
metadata only; field values are never re-encoded or normalised.
"""
from __future__ import annotations

import json

from .base import ExtractionOutput, ExtractorProfile, Segment, failed

JSON_PROFILE = ExtractorProfile("sdb-json-rfc8259", "1.0.0", {
    "encoding": "utf-8", "bom": "rejected", "duplicate_keys": "rejected", "non_finite_numbers": "rejected",
    "representation": "verbatim decoded original text", "execution": "none"})


def _no_dupes(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate object key")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f"non-finite number {name}")


def parse_strict(text: str):
    return json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_reject_constant)


def _pointer_spans(text: str, value, pointer: str = "") -> list[tuple[str, int, int]]:
    """(json_pointer, start, end) for string leaves, located by scanning the verbatim text in document order."""
    out, pos = [], [0]

    def esc(k):
        return str(k).replace("~", "~0").replace("/", "~1")

    def walk(v, ptr):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{ptr}/{esc(k)}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{ptr}/{i}")
        elif isinstance(v, str) and v:
            enc = json.dumps(v, ensure_ascii=False)[1:-1]       # the literal as it would appear unescaped-ascii
            i = text.find(enc, pos[0])
            if i != -1:
                out.append((ptr, i, i + len(enc)))
                pos[0] = i + len(enc)
    walk(value, pointer)
    return out


def extract_json(data: bytes) -> ExtractionOutput:
    if data.startswith(b"\xef\xbb\xbf"):
        return failed("JSON has a BOM; rejected by the registered profile")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return failed("JSON is not UTF-8")
    try:
        value = parse_strict(text)
    except ValueError as exc:
        return failed(f"JSON rejected by the registered profile: {exc}")
    out = ExtractionOutput(state="complete", text=text, representations={"text": text})
    if text:
        out.segments.append(Segment(0, len(text), "text", {"representation": "text"}, decoded_offset=0))
    out.headers = {"json_pointers": [{"pointer": p, "start": s, "end": e} for p, s, e in _pointer_spans(text, value)],
                   "top_level_type": type(value).__name__}
    return out


PROJECTION_VERSION = "sdb-json-projection-1.0.0"


def projection_bytes(original_id: str, original_sha256: str, data: bytes) -> tuple[bytes, int]:
    """Deterministic labelled derived text: a fixed header, then the original JSON reproduced byte-for-byte.
    Returns (bytes, header length in code points); original offset = projection offset − header length."""
    header = (f"[Derived text projection {PROJECTION_VERSION} of supplied original {original_id}. "
              f"Original media type application/json, SHA-256 {original_sha256}; the original bytes are held "
              "unchanged. The JSON below is reproduced verbatim.]\n\n")
    return header.encode("utf-8") + data, len(header)
