"""Server-minted passage handles over exact source text (code-point offsets)."""
from __future__ import annotations

import re

MAX_PASSAGE_CHARS = 1200


def passages(text: str) -> list[dict]:
    """Split on blank lines; split long blocks at sentence/line boundaries. Offsets are exact code points."""
    out = []
    for m in re.finditer(r"(?:[^\n]|\n(?!\s*\n))+", text):
        start, block = m.start(), m.group(0)
        if not block.strip():
            continue
        while len(block) > MAX_PASSAGE_CHARS:
            cut = max(block.rfind(". ", 0, MAX_PASSAGE_CHARS), block.rfind("\n", 0, MAX_PASSAGE_CHARS))
            cut = cut + 1 if cut > 200 else MAX_PASSAGE_CHARS
            out.append((start, start + cut))
            start, block = start + cut, block[cut:]
        out.append((start, start + len(block)))
    return [{"handle": f"P{i}", "start": s, "end": e, "text": text[s:e]} for i, (s, e) in enumerate(out, start=1)]
