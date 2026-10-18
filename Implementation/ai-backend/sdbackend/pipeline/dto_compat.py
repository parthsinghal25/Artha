"""Bounded DTO-compatibility repairs shared by S03/S05 (measured on local Gemma E4B, 15 Oct 2026).

These change only the SHAPE of what the model returned, never its evidence:
* `canonical_handle`: a citation naming a bare source id (`SRC-x`) or a decorated handle (`[SRC-x~P3]`) is mapped
  to the ONE permitted passage handle of that source whose text contains the exact quote. Zero or several matches
  → no handle (the citation is dropped with a reason). The exact-quote and read/shown-passage rules still apply.
* Omitted citation/list arrays are the empty list (no evidence → unknown), never a supported claim.
"""
from __future__ import annotations

import re

from ..citations import QuoteError, find_quote


def canonical_handle(handle, quote: str, permitted: dict[str, str]) -> tuple[str | None, str | None]:
    """permitted = {passage_handle: passage text}. → (handle, note); handle None means drop."""
    h = str(handle or "").strip().strip("[]()<>`'\" ").strip()
    if h in permitted:
        return h, (None if h == handle else f"handle {handle!r} normalised")
    if "~" in h:
        return None, None
    hits = []
    for cand, text in permitted.items():
        if cand.split("~")[0] == h:
            try:
                find_quote(text, quote)
                hits.append(cand)
            except QuoteError:
                pass
    if len(hits) == 1:
        return hits[0], f"bare source id {h} resolved to {hits[0]} by exact quote"
    return None, (f"bare source id {h}: quote matches {len(hits)} permitted passages" if h.startswith("SRC-") else None)


FINDING_KEYS = {"title", "claim", "taxonomy_label", "dimension", "members"}


def normalise_collective(data) -> tuple[object, list[str]]:
    if not isinstance(data, dict):
        return data, []
    notes = []
    if "findings" not in data and FINDING_KEYS & set(data):
        data, notes = {"findings": [data]}, ["single top-level finding wrapped in findings[]"]
    elif "findings" in data and isinstance(data["findings"], dict):
        data, notes = {**data, "findings": [data["findings"]]}, ["findings object wrapped in a list"]
    out = {"findings": []}
    for i, f in enumerate(data.get("findings") or []):
        if not isinstance(f, dict):
            out["findings"].append(f)
            continue
        f = dict(f)
        for k in ("contrary_citations", "alternatives", "missing_facts", "members"):
            if k not in f:
                f[k] = []
                notes.append(f"findings/{i}.{k} omitted → []")
        f["members"] = [dict(m, citations=m.get("citations", [])) if isinstance(m, dict) else m for m in f["members"]]
        out["findings"].append(f)
    extra = set(data) - {"findings"}
    if extra:
        return {**out, **{k: data[k] for k in extra}}, notes      # left for the validator to reject
    return out, notes
