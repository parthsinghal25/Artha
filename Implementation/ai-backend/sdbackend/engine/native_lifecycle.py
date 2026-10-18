"""Trusted native lifecycle projection (no model, A03): a registered lifecycle column profile over a source's own
CSV text or XLSX stored-cell layout (`native_tables`) → deterministic administrative transitions with cell-exact
citations.

Rules:
* The table must name a lifecycle event id, case, date and status column; otherwise nothing is projected.
* Status maps only through `STATUS_MAP` (received/open → open, awaiting, closed, reopened). Anything else is
  unresolved; a final-response heading or an external matter never becomes a lifecycle state.
* The row's case must be one of the source's factual linked cases (exact crosswalk).
* `event_time` is used only when it is an exact offset instant on the row's local date; otherwise the transition
  keeps day precision (`effective_at = null`) and is never placed by event-ID order inside that day.
* A transition is AVAILABLE when the table holding it is available (`available_at` of the source). A late-held
  export never backdates knowledge: an earlier as-known view without the export has no lifecycle evidence.
* The same lifecycle event id in copied exports is one transition (earliest-available copy kept); a conflicting
  copy is unresolved and neither is used.
"""
from __future__ import annotations

from ..citations import text_ref
from . import native_tables
from .core import parse_instant

PROFILE_ID = "sdb-native-lifecycle-1.0.0"
COLUMNS = {
    "event": ("lifecycle_event_id", "status_event_id", "event_id"),
    "case": ("case_id", "case_reference", "complaint_id"),
    "date": ("date", "status_date", "event_date"),
    "event_time": ("event_time", "status_time", "event_at"),
    "status": ("status", "lifecycle_status", "case_status"),
}
REQUIRED = ("event", "case", "date", "status")
STATUS_MAP = {"received": "open", "open": "open", "opened": "open", "awaiting": "awaiting", "on_hold": "awaiting",
              "closed": "closed", "reopened": "reopened", "re_opened": "reopened"}


def header_map(header: list[str]) -> dict[str, int] | None:
    out = native_tables.header_map(header, COLUMNS)
    if out is None or not all(r in out for r in REQUIRED):
        return None
    # a ledger (amount/obligation columns) is never a lifecycle table even if it names a status
    normed = {native_tables.norm(h) for h in header}
    if normed & {"amount_minor", "obligation_id", "duration_seconds"}:
        return None
    return out


def _tables(source: dict):
    tables, problems = native_tables.read_tables(source)
    return [(t, cols) for t in tables if (cols := header_map(t.names())) is not None], problems


def is_lifecycle(source: dict) -> bool:
    return bool(_tables(source)[0])


def project(source_record: dict) -> tuple[list[dict], list[dict]]:
    """→ (transitions, unresolved). `source_record` is the full v0.2 source record (envelope gives availability)."""
    source = source_record["data"]
    available_at = source_record["envelope"]["available_at"]
    sid, cases = source["source_id"], set(source["case_ids"])
    tables, problems = _tables(source)
    unresolved = [{"source_id": sid, "subject": None, "reason": f"{PROFILE_ID}: {p}"} for p in problems]
    out: dict[str, dict] = {}

    def bad(where, reason):
        unresolved.append({"source_id": sid, "subject": where, "reason": f"{PROFILE_ID}: {reason}"})

    for table, cols in tables:
        def cell(fields, role):
            i = cols.get(role)
            return fields[i] if i is not None and i < len(fields) else None

        def val(fields, role):
            c = cell(fields, role)
            return c[2].strip() if c else ""

        for row, fields in table.rows:
            where = f"row {row}" if table.layout == "csv" else f"sheet {table.sheet} row {row}"
            if not any(c and c[2].strip() for c in fields):
                continue
            eid = val(fields, "event")
            if not native_tables.IDENT.match(eid):
                bad(where, "lifecycle event id missing or not an identifier")
                continue
            case = val(fields, "case")
            if case not in cases:
                bad(where, "case reference is not one of this source's linked cases")
                continue
            state = STATUS_MAP.get(native_tables.norm(val(fields, "status")))
            if state is None:
                bad(where, f"status {val(fields, 'status')!r} does not map to a lifecycle state")
                continue
            day = native_tables.date_value(val(fields, "date"))
            if day is None:
                bad(where, "date is not YYYY-MM-DD")
                continue
            raw_time = val(fields, "event_time")
            at = native_tables.event_time_value(raw_time, day) if raw_time else None
            if raw_time and at is None:
                bad(where, "event_time is not an exact offset instant on the row's local date; day precision kept")
            cited = [cell(fields, r) for r in ("event", "case", "date", "event_time", "status")]
            refs = [text_ref(source, c[0], c[1]) for c in cited if c and c[1] > c[0]]
            t = {"lifecycle_event_id": eid, "case_id": case, "state": state, "raw_status": val(fields, "status"),
                 "effective_at": at, "effective_date": day, "available_at": available_at, "source_id": sid,
                 "evidence_refs": refs, "profile_id": PROFILE_ID, "row": row, "sheet": table.sheet}
            if eid in out:
                prev = out[eid]
                if (prev["case_id"], prev["state"], prev["effective_date"], prev["effective_at"]) != \
                        (case, state, day, at):
                    bad(where, f"lifecycle event {eid} repeated with different values; neither used")
                    prev["_conflict"] = True
                continue
            out[eid] = t
    return [t for t in out.values() if not t.pop("_conflict", False)], unresolved


def merge(per_source: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    """Union across exports: earliest-available copy of each lifecycle event id; conflicts unresolved."""
    best: dict[str, dict] = {}
    unresolved = []
    for rows in per_source:
        for t in rows:
            prev = best.get(t["lifecycle_event_id"])
            if prev is None:
                best[t["lifecycle_event_id"]] = t
                continue
            same = (prev["case_id"], prev["state"], prev["effective_date"], prev["effective_at"]) == \
                (t["case_id"], t["state"], t["effective_date"], t["effective_at"])
            if not same:
                unresolved.append({"source_id": t["source_id"], "subject": t["lifecycle_event_id"],
                                   "reason": f"{PROFILE_ID}: differs between exports; neither used"})
                prev["_conflict"] = True
            elif parse_instant(t["available_at"]) < parse_instant(prev["available_at"]):
                best[t["lifecycle_event_id"]] = {**t, "copies": prev.get("copies", []) + [prev["source_id"]]}
            else:
                prev.setdefault("copies", []).append(t["source_id"])
    rows = [t for t in best.values() if not t.pop("_conflict", False)]
    return sorted(rows, key=lambda t: (t["case_id"], t["effective_date"], t["effective_at"] or "",
                                       t["lifecycle_event_id"])), unresolved
