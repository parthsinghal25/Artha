"""Trusted native tables (no model): a source's own extracted text → header + rows of exact code-point cell spans.

Two layouts are recognised, both produced by the production extractors over the stored original:
* CSV (RFC 4180, header record) via `extract.textual.tokenize_csv`.
* XLSX via `extract.xlsx` (profile `sdb-xlsx-openpyxl`): `## Sheet: <name>` then one `A1: value` line per non-empty
  stored cell, row-major. Row 1 of each sheet is its header. A sheet is not trusted when a line is not a cell line
  (e.g. a multi-line cell value), when coordinates repeat or run backwards, or when a data row has a cell outside the
  header columns.

A cell is `(start, end, value)` in the source text, so every projected value cites the exact stored cell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..extract.textual import tokenize_csv

TABLES_VERSION = "sdb-native-tables-1.0.0"
SHEET = "## Sheet: "
CELL_LINE = re.compile(r"([A-Z]{1,3})([1-9][0-9]*): ")


@dataclass
class Table:
    layout: str                                   # csv | xlsx
    sheet: str | None
    header: list[tuple[int, int, str]]
    rows: list[tuple[int, list[tuple[int, int, str] | None]]] = field(default_factory=list)   # (native row, cells)

    def names(self) -> list[str]:
        return [c[2] for c in self.header]


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xlsx_tables(text: str) -> tuple[list[Table], list[str]]:
    tables, problems = [], []
    pos = 0
    blocks = []
    while True:
        i = text.find(SHEET, pos)
        if i < 0:
            break
        j = text.find("\n" + SHEET, i + 1)
        end = len(text) if j < 0 else j
        blocks.append((i, end))
        pos = end + 1 if j >= 0 else len(text)
        if j < 0:
            break
    for start, end in blocks:
        nl = text.find("\n", start, end)
        name = text[start + len(SHEET): nl if nl >= 0 else end]
        cells: dict[int, dict[int, tuple[int, int, str]]] = {}
        last = (0, -1)
        ok = True
        k = nl
        while 0 <= k < end:
            nxt = text.find("\n", k + 1, end)
            line_end = end if nxt < 0 else nxt
            m = CELL_LINE.match(text, k + 1, line_end)
            if m is None:
                ok = False
                problems.append(f"sheet {name}: non-cell line (multi-line or unrecognised cell); not trusted")
                break
            row, col = int(m.group(2)), _col(m.group(1))
            if (row, col) <= last:
                ok = False
                problems.append(f"sheet {name}: cell order is not row-major; not trusted")
                break
            last = (row, col)
            vs = m.end()
            cells.setdefault(row, {})[col] = (vs, line_end, text[vs:line_end])
            k = nxt
        if not ok or 1 not in cells:
            continue
        hdr_cols = sorted(cells[1])
        if hdr_cols != list(range(len(hdr_cols))):
            problems.append(f"sheet {name}: header row has gaps; not trusted")
            continue
        t = Table("xlsx", name, [cells[1][c] for c in hdr_cols])
        bad = False
        for r in sorted(cells):
            if r == 1:
                continue
            if any(c >= len(hdr_cols) for c in cells[r]):
                problems.append(f"sheet {name}: row {r} has a cell outside the header; not trusted")
                bad = True
                break
            t.rows.append((r, [cells[r].get(c) for c in range(len(hdr_cols))]))
        if not bad:
            tables.append(t)
    return tables, problems


def read_tables(source: dict) -> tuple[list[Table], list[str]]:
    """(tables, problems) for a source's text; [] when the text is neither a CSV nor the XLSX cell layout."""
    text = source.get("text") or ""
    if text.startswith(SHEET):
        return _xlsx_tables(text)
    try:
        recs = tokenize_csv(text)
    except ValueError as exc:
        return [], [f"CSV rejected: {exc}"]
    if not recs:
        return [], []
    t = Table("csv", None, list(recs[0]))
    for i, fields in enumerate(recs[1:], start=2):
        t.rows.append((i, list(fields) + [None] * max(0, len(t.header) - len(fields))))
    return [t], []


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


def header_map(names: list[str], columns: dict[str, tuple[str, ...]]) -> dict[str, int] | None:
    """Column index per profile role; None when a role alias is ambiguous."""
    normed = [norm(h) for h in names]
    out = {}
    for role, aliases in columns.items():
        hits = [i for i, n in enumerate(normed) if n in aliases]
        if len(hits) > 1:
            return None
        if hits:
            out[role] = hits[0]
    return out


def date_value(raw: str) -> str | None:
    """YYYY-MM-DD, or an XLSX stored midnight datetime rendered as YYYY-MM-DDT00:00:00; anything else is None."""
    v = raw.strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:T00:00:00)?", v)
    if not m:
        return None
    from datetime import date
    try:
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        return None


IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def event_time_value(raw: str, day: str | None) -> str | None:
    """Exact ISO-8601 instant with an explicit offset whose Melbourne local date equals the row's date; else None
    (a contradictory or offset-less time is never guessed into an instant)."""
    v = (raw or "").strip()
    if not v:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    from .core import MELBOURNE
    if day is not None and dt.astimezone(MELBOURNE).date().isoformat() != day:
        return None
    return dt.isoformat()


def linked_cases(raw: str) -> list[str] | None:
    """`a;b` / `a,b` / `a b` list of case identifiers; None when any element is not an identifier."""
    parts = [x for x in re.split(r"[;,\s]+", (raw or "").strip()) if x]
    return parts if all(IDENT.match(x) for x in parts) else None
