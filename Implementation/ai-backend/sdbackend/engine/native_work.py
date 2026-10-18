"""Trusted native recorded-work projection (no model): a registered work-log column profile over a source's own
CSV text or XLSX stored-cell layout (`native_tables`) → `work_entry` records with `duration_basis = recorded` and
cell-exact text citations.

This is the single path by which recorded hours come from native inputs. Model-read durations stay `reported`
and never become recorded (S04). Rules:
* The table (CSV header record, or row 1 of an XLSX sheet) must name a case column and a duration column of this
  work-log profile; otherwise the source is not a work log.
* A row is recorded work only when its basis column (if present) says recorded/actual/logged. Estimates/planned
  rows are listed as excluded estimates, never recorded.
* The row's case reference must be one of the source's own linked cases (exact crosswalk); an AFCA reference must
  be one of the source's own AFCA links. Otherwise the row is unresolved with a reason. A phase value never implies
  an AFCA link.
* Durations: seconds, minutes, or H:MM(:SS) / "1h 30m"; anything else is unresolved. Dates must be YYYY-MM-DD.
* Identity: a valid native work id is kept as the `work_id`, so the same row in a copied export or a re-import is one
  entity; without one, the id is derived from (source, row). The same id twice collapses when identical; a
  conflicting duplicate is unresolved (never summed).
* The native actor and the raw phase are preserved (cited cells + `native_rows`); an actor is never turned into a
  role and a phase outside the frozen enum stays `unknown`. No rate, cost or AFCA association is inferred.
"""
from __future__ import annotations

import re
from datetime import date

from ..canonical import derived_id
from ..citations import text_ref
from . import native_tables

PROFILE_ID = "sdb-native-worklog-1.2.0"   # 1.2: linked_case_ids shared work, exact event_time
COLUMNS = {
    "work_id": ("work_id", "entry_id", "log_id", "time_entry_id"),
    "case": ("case_id", "case_reference", "complaint_id"),
    "date": ("date", "work_date", "event_date", "entry_date"),
    "seconds": ("duration_seconds", "seconds"),
    "minutes": ("duration_minutes", "minutes"),
    "duration": ("duration", "time_spent", "hours_minutes"),
    "phase": ("phase", "activity", "work_phase"),
    "basis": ("basis", "duration_basis", "entry_type"),
    "afca": ("afca_matter_id", "afca_reference"),
    "role": ("contributor_role", "role"),
    "task": ("task_reference", "task_id"),
    "actor": ("actor", "staff_id", "user_id", "staff_reference"),
    "linked": ("linked_case_ids", "shared_case_ids"),
    "event_time": ("event_time", "event_at"),
}
RECORDED = {"recorded", "actual", "logged", "timesheet"}
ESTIMATE = {"estimate", "estimated", "planned", "forecast", "budget"}
PHASES = {"member_contact", "internal_coordination", "evidence_gathering", "approval", "submission_preparation",
          "conciliation_preparation", "conciliation_attendance", "determination_response", "follow_up", "other"}
HMS = re.compile(r"^(\d{1,3}):([0-5]\d)(?::([0-5]\d))?$")
HM_WORDS = re.compile(r"^(?:(\d{1,3})\s*h(?:ours?|rs?)?)?\s*(?:(\d{1,3})\s*m(?:in(?:ute)?s?)?)?$", re.I)


def _norm(s: str) -> str:
    return native_tables.norm(s)


def header_map(header: list[str]) -> dict[str, int] | None:
    """Column index per profile role, or None when the header is not this work-log profile."""
    out = native_tables.header_map(header, COLUMNS)
    if out is None or "case" not in out or not ({"seconds", "minutes", "duration"} & set(out)):
        return None
    return out


def parse_seconds(role: str, raw: str) -> int | None:
    v = raw.strip()
    if role in ("seconds", "minutes"):
        if not re.fullmatch(r"\d{1,7}", v):
            return None
        return int(v) * (60 if role == "minutes" else 1)
    m = HMS.match(v)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)
    m = HM_WORDS.match(v)
    if v and m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60
    return None


def _tables(source: dict):
    tables, problems = native_tables.read_tables(source)
    return [(t, cols) for t in tables if (cols := header_map(t.names())) is not None], problems


def is_work_log(source: dict) -> bool:
    return bool(_tables(source)[0])


def project(source: dict, make_record) -> tuple[list[dict], list[dict]]:
    """→ (work_entry records, unresolved). `make_record(kind, rid, available_at, refs, data)` builds the envelope.
    Each record carries a private `_native` sidecar (actor, raw phase, sheet/row) that the resolver moves into the
    stage's `native_rows` before sealing; it is never part of the frozen record."""
    sid, cases, matters = source["source_id"], set(source["case_ids"]), set(source.get("afca_matter_ids") or [])
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
            basis = val(fields, "basis").lower()
            if basis in ESTIMATE:
                bad(where, "estimated/planned duration; never recorded work")
                continue
            if basis and basis not in RECORDED:
                bad(where, f"unknown duration basis {basis!r}")
                continue
            case_c = cell(fields, "case")
            if case_c is None or case_c[2].strip() not in cases:
                bad(where, "case reference is not one of this source's linked cases")
                continue
            dur_role = next(r for r in ("seconds", "minutes", "duration") if r in cols)
            dur_c = cell(fields, dur_role)
            secs = parse_seconds(dur_role, dur_c[2]) if dur_c else None
            if secs is None or secs <= 0:
                bad(where, "duration missing or not in a registered format")
                continue
            row_cases = [case_c[2].strip()]
            if val(fields, "linked"):
                linked = native_tables.linked_cases(val(fields, "linked"))
                if linked is None or any(c not in cases for c in linked):
                    bad(where, "linked case reference is not one of this source's linked cases")
                    continue
                row_cases = sorted(set(row_cases) | set(linked))
            date_c = cell(fields, "date")
            day = None
            if date_c and date_c[2].strip():
                day = native_tables.date_value(date_c[2])
                if day is None:
                    bad(where, "date is not YYYY-MM-DD")
                    continue
            afca = val(fields, "afca")
            if afca and afca not in matters:
                bad(where, "AFCA reference is not one of this source's AFCA links")
                continue
            raw_phase = val(fields, "phase")
            phase = _norm(raw_phase) if raw_phase else "unknown"
            native_key = val(fields, "work_id")
            if native_key and native_tables.IDENT.match(native_key):
                wid = native_key
            elif native_key:
                bad(where, "native work id is not a valid identifier")
                continue
            else:
                wid = derived_id("WRK", sid, PROFILE_ID, f"{table.sheet or ''}:{row}")
            cited = [dur_c, case_c, date_c, cell(fields, "work_id"), cell(fields, "actor"), cell(fields, "phase"),
                     cell(fields, "linked"), cell(fields, "event_time")]
            refs = [text_ref(source, c[0], c[1]) for c in cited if c and c[1] > c[0]]
            raw_time = val(fields, "event_time")
            event_at = native_tables.event_time_value(raw_time, day) if raw_time else None
            if raw_time and event_at is None:
                bad(where, "event_time is not an exact offset instant on the row's local date; date precision kept")
            data = {"work_id": wid, "case_ids": row_cases, "afca_matter_ids": [afca] if afca else [],
                    "contributor_role": val(fields, "role") or "unknown",
                    "phase": phase if phase in PHASES else "unknown",
                    "task_reference": (val(fields, "task") or None),
                    "event_at": event_at, "event_date": day, "duration_seconds": secs, "duration_basis": "recorded",
                    "assumption_id": None, "duplicate_of_work_id": None, "evidence_refs": refs}
            if wid in out:
                prev = out[wid]["data"]
                if (prev["duration_seconds"], prev["event_date"], prev["case_ids"]) != (secs, day, data["case_ids"]):
                    bad(where, f"work id {native_key or wid} repeated with different values; neither counted")
                    out[wid]["_conflict"] = True
                continue
            rec = make_record("work_entry", wid, None, refs, data)
            rec["_native"] = {"work_id": wid, "source_id": sid, "layout": table.layout, "sheet": table.sheet,
                              "row": row, "actor": val(fields, "actor") or None, "raw_phase": raw_phase or None,
                              "profile_id": PROFILE_ID}
            out[wid] = rec
    records = [r for r in out.values() if not r.pop("_conflict", False)]
    return records, unresolved
