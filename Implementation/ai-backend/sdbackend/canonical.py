"""Canonical JSON, digests and opaque identifiers.

One sealing convention is used everywhere (Shared 1.0.0 DECISIONS §6 and AI/03 §2): UTF-8 JSON with sorted
object keys, compact separators and preserved Unicode. The algorithm identifier is recorded with sealed objects so a
later convention change cannot silently alter an existing digest.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

CANONICAL_JSON_ALGORITHM = "sha256-json-sorted-compact-utf8-v1"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derived_id(prefix: str, *parts: Any, length: int = 24) -> str:
    """Deterministic opaque ID from canonical inputs (idempotent re-ingestion yields the same ID)."""
    return f"{prefix}-{canonical_sha256(list(parts))[:length]}"


def random_id(prefix: str, nbytes: int = 12) -> str:
    return f"{prefix}-{secrets.token_hex(nbytes)}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Actual (real) clock, used only for ingestion/analysis/publication audit times."""
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def source_content_sha256(source: dict) -> str:
    """Data v0.2 canonical source-content hash (Data/tools/check_contracts.py::source_digest).

    Covers text, provenance fields and the exact native-fact values; differs from the raw asset byte digest.
    """
    fields = ("text", "source_family", "subtype", "author_role", "author_reference", "case_ids", "afca_matter_ids")
    payload = {key: source[key] for key in fields}
    payload["native_facts"] = [
        {"field_key": fact["field_key"], "entity_id": fact["entity_id"],
         "value": {key: fact["value"][key] for key in ("state", "value", "missing_reason")}}
        for fact in source["native_facts"]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()
