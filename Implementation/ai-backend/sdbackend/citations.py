"""Citation building and native-location mapping (Shared 1.0.0 DECISIONS §4).

* `find_quote` turns a model-proposed exact quote into a server-computed code-point span; zero or ambiguous
  matches fail instead of guessing.
* `map_citation` binds an evidence_ref to an extraction/asset and returns a Shared `locator`: `verified` with a
  parser-derived location, `unmapped` when no complete native mapping exists, or `invalid` for a bad binding.
"""
from __future__ import annotations

from typing import Any

from .canonical import derived_id, sha256_text

MAPPER_VERSION = "sdb-locator-1.0.0"


class QuoteError(ValueError):
    pass


def find_quote(text: str, quote: str, *, occurrence: int | None = None) -> tuple[int, int]:
    """Exact code-point span of `quote` in `text`. Python str indices are code points."""
    if not quote or not quote.strip():
        raise QuoteError("empty quote")
    starts, i = [], text.find(quote)
    while i != -1:
        starts.append(i)
        i = text.find(quote, i + 1)
    if not starts:
        raise QuoteError("quote not found verbatim in source")
    if occurrence is not None:
        if not 1 <= occurrence <= len(starts):
            raise QuoteError("requested occurrence does not exist")
        return starts[occurrence - 1], starts[occurrence - 1] + len(quote)
    if len(starts) > 1:
        raise QuoteError(f"quote is ambiguous ({len(starts)} matches); disambiguation required")
    return starts[0], starts[0] + len(quote)


def resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError("JSON pointer must start with '/'")
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.isdigit() or (token != "0" and token.startswith("0")):
                raise KeyError("invalid array index")
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError("pointer traverses a scalar")
    return node


def check_citation(source: dict, citation: dict) -> str | None:
    """Return None if the evidence_ref is valid for this exact source version, else a reason."""
    if citation["source_id"] != source["source_id"]:
        return "citation source differs from the requested source"
    if citation["content_sha256"] != source["content_sha256"]:
        return "citation content hash does not match this source version"
    anchor = citation["anchor"]
    if anchor["kind"] == "text":
        s, e = anchor["start_codepoint"], anchor["end_codepoint"]
        if not (0 <= s < e <= len(source["text"])):
            return "text span out of bounds or empty"
        return None
    try:
        resolve_pointer({"native_facts": source["native_facts"]}, anchor["json_pointer"])
    except (KeyError, IndexError, ValueError):
        return "JSON pointer does not resolve to a native field"
    return None


def _locator(citation, extraction, asset, state, precision, location, reason) -> dict:
    mapping_id = derived_id("MAP", citation, extraction["extraction_id"], asset["asset_version_id"], MAPPER_VERSION)
    return {
        "mapping_id": mapping_id, "citation": citation, "extraction_id": extraction["extraction_id"],
        "asset_version_id": asset["asset_version_id"], "bytes_sha256": asset["bytes_sha256"],
        "state": state, "precision": precision if state == "verified" else "none",
        "location": location if state == "verified" else None,
        "validation_id": derived_id("VAL", mapping_id, location, MAPPER_VERSION) if state == "verified" else None,
        "reason": None if state == "verified" else reason,
    }


def map_citation(source: dict, layout: dict, extraction: dict, asset: dict, citation: dict) -> dict:
    """Deterministic locator from the extraction layout. Never guesses coordinates."""
    def bad(reason):
        return _locator(citation, extraction, asset, "invalid", "none", None, reason)

    def unmapped(reason):
        return _locator(citation, extraction, asset, "unmapped", "none", None, reason)

    if extraction["asset_version_id"] != asset["asset_version_id"] or extraction["bytes_sha256"] != asset["bytes_sha256"]:
        return bad("extraction is not bound to this asset version/digest")
    if not any(b["source_id"] == source["source_id"] and b["content_sha256"] == source["content_sha256"]
               for b in extraction["emitted_sources"]):
        return bad("source was not emitted by this extraction")
    reason = check_citation(source, citation)
    if reason:
        return bad(reason)
    anchor = citation["anchor"]
    if anchor["kind"] == "field":
        return unmapped("native field anchor has no original-file location in this representation")
    s, e = anchor["start_codepoint"], anchor["end_codepoint"]
    segs = [seg for seg in layout["segments"] if seg["start"] < e and seg["end"] > s]
    containing = [seg for seg in segs if seg["start"] <= s and e <= seg["end"]]
    if not containing:
        return unmapped("span is not contained in a single native unit (crosses pages/cells/parts or separators)")
    seg = containing[0]
    loc, kind = seg["location"], seg["kind"]
    rel_s, rel_e = s - seg["start"] + seg.get("decoded_offset", 0), e - seg["start"] + seg.get("decoded_offset", 0)
    reps = layout.get("representation_sha256", {})

    if kind == "text":
        return _locator(citation, extraction, asset, "verified", "text_span", {
            "kind": "text", "decoded_representation_sha256": reps[loc["representation"]],
            "start_codepoint": rel_s, "end_codepoint": rel_e}, None)
    if kind == "eml":
        if loc["section"] == "attachment":
            child_sha = loc.get("child_bytes_sha256")
            if not child_sha:
                return unmapped("attachment listing has no bound child representation")
            return _locator(citation, extraction, asset, "verified", "section", {
                "kind": "eml", "mime_part_path": loc["mime_part_path"], "section": "attachment",
                "decoded_representation_sha256": child_sha, "start_codepoint": None, "end_codepoint": None}, None)
        return _locator(citation, extraction, asset, "verified", "text_span", {
            "kind": "eml", "mime_part_path": loc["mime_part_path"], "section": loc["section"],
            "decoded_representation_sha256": reps[loc["representation"]],
            "start_codepoint": rel_s, "end_codepoint": rel_e}, None)
    if kind == "pdf":
        boxes = seg.get("char_boxes")
        region = None
        if boxes is not None:
            sel = [b for b in boxes[s - seg["start"]:e - seg["start"]] if b]
            if sel:
                region = {"x0": min(b[0] for b in sel), "y0": min(b[1] for b in sel),
                          "x1": max(b[2] for b in sel), "y1": max(b[3] for b in sel)}
                if not (region["x0"] < region["x1"] and region["y0"] < region["y1"]):
                    region = None
        return _locator(citation, extraction, asset, "verified", "region" if region else "page", {
            "kind": "pdf", "page": loc["page"], "region": region,
            "coordinate_basis": "upright_cropbox_top_left_normalized",
            "rotation_applied": loc["rotation_applied"]}, None)
    if kind == "csv":
        if loc.get("header"):
            return unmapped("span is in the header record, which is not a data record")
        return _locator(citation, extraction, asset, "verified", "record_column", {
            "kind": "csv", "parser_profile_id": loc["parser_profile_id"],
            "data_record_index": loc["data_record_index"], "column_index": loc["column_index"],
            "column_name": loc["column_name"], "native_row_key": loc["native_row_key"]}, None)
    if kind == "xlsx":
        return _locator(citation, extraction, asset, "verified", "cell_range", {
            "kind": "xlsx", "sheet_id": loc["sheet_id"], "sheet_name": loc["sheet_name"],
            "a1_range": loc["a1_range"]}, None)
    if kind == "docx":
        return _locator(citation, extraction, asset, "verified", "paragraph", {
            "kind": "docx", "part_name": loc["part_name"], "paragraph_index": loc["paragraph_index"],
            "start_codepoint": rel_s, "end_codepoint": rel_e}, None)
    if kind == "image":
        return _locator(citation, extraction, asset, "verified", "region", {
            "kind": "image", "original_width": loc["original_width"], "original_height": loc["original_height"],
            "exif_orientation": loc["exif_orientation"], "coordinate_basis": "exif_normalized_top_left",
            "region": loc["line_region"]}, None)
    return unmapped(f"no mapping convention for segment kind {kind}")


def text_ref(source: dict, start: int, end: int) -> dict:
    return {"source_id": source["source_id"], "content_sha256": source["content_sha256"],
            "anchor": {"kind": "text", "start_codepoint": start, "end_codepoint": end}}


def representation_sha(text: str) -> str:
    return sha256_text(text)
