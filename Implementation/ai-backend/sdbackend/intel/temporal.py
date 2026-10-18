"""`demo-held-day-v1` temporal policy (A04) for the new demo store only; legacy inclusive contexts are unchanged.

* Knowledge views are half-open: an end-of-day view for local date D has `knowledge_end_exclusive` = Melbourne
  midnight starting D+1. Exact evidence counts iff its instant is strictly before that midnight; an exact
  next-midnight arrival belongs to the next day, never to D.
* Day-precision availability on D counts in D's end-of-day view WITHOUT inventing an intraday instant. The legacy
  envelope converts such a day to next local midnight; the crosswalk sidecar keeps the supplied precision, and this
  policy consults the sidecar (never guesses day precision from a midnight timestamp).
* Unknown same-day order stays unknown: `same_day_order` returns None unless both instants are exact.
* Grains: local day, Monday week, calendar month, calendar quarter and the observation year 1 Oct 2025 – 30 Sep 2026
  (`observation_year`). The legacy calendar-year grain is distinct (`calendar_year`) and never relabelled.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..engine.core import MELBOURNE, melbourne_midnight, parse_instant

POLICY_ID = "demo-held-day-v1"
GRAINS = ("day", "week", "month", "quarter", "observation_year", "calendar_year")
OBSERVATION_START = date(2025, 10, 1)
OBSERVATION_END_EXCLUSIVE = date(2026, 10, 1)


def end_of_day(d: date | str) -> datetime:
    """knowledge_end_exclusive for the end-of-day view of local date `d`."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return melbourne_midnight(d + timedelta(days=1))


def view_date(knowledge_end_exclusive: datetime) -> date:
    """The local date whose end-of-day view this is (exclusive midnight → preceding date)."""
    local = knowledge_end_exclusive.astimezone(MELBOURNE)
    if (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0):
        return local.date() - timedelta(days=1)
    return local.date()


def available(envelope_available_at: str, sidecar: dict | None, knowledge_end_exclusive: datetime) -> bool:
    if sidecar and sidecar.get("precision") == "day":
        return end_of_day(sidecar["date"]) <= knowledge_end_exclusive
    return parse_instant(envelope_available_at) < knowledge_end_exclusive


def availability_day(envelope_available_at: str, sidecar: dict | None) -> date:
    """Local day on which the evidence became held (day precision honoured)."""
    if sidecar and sidecar.get("precision") == "day":
        return date.fromisoformat(sidecar["date"])
    return parse_instant(envelope_available_at).astimezone(MELBOURNE).date()


def same_day_order(a: str | None, b: str | None) -> int | None:
    """-1/0/1 when both are exact instants; None (unknown order) otherwise. Never falls back to ID order."""
    if not a or not b:
        return None
    x, y = parse_instant(a), parse_instant(b)
    return (x > y) - (x < y)


def bucket(d: date, grain: str) -> tuple[str, date, date]:
    """(label, start, end_exclusive) of the local-date bucket containing `d`."""
    if grain == "day":
        return d.isoformat(), d, d + timedelta(days=1)
    if grain == "week":
        start = d - timedelta(days=d.weekday())               # Monday
        return f"{start.isoformat()}/W", start, start + timedelta(days=7)
    if grain == "month":
        start = d.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
        return start.strftime("%Y-%m"), start, nxt
    if grain == "quarter":
        q = (d.month - 1) // 3
        start = date(d.year, 3 * q + 1, 1)
        nxt = date(d.year + 1, 1, 1) if q == 3 else date(d.year, 3 * q + 4, 1)
        return f"{d.year}-Q{q + 1}", start, nxt
    if grain == "observation_year":
        y = d.year if d.month >= 10 else d.year - 1
        start = date(y, 10, 1)
        return f"OY{y}-{(y + 1) % 100:02d}", start, date(y + 1, 10, 1)
    if grain == "calendar_year":
        return str(d.year), date(d.year, 1, 1), date(d.year + 1, 1, 1)
    raise ValueError(f"unknown grain {grain}")


def buckets(start: date, end_exclusive: date, grain: str) -> list[tuple[str, date, date]]:
    out, d = [], start
    while d < end_exclusive:
        b = bucket(d, grain)
        out.append((b[0], max(b[1], start), min(b[2], end_exclusive)))
        d = b[2]
    return out


def local_day(instant: str | None, day: str | None) -> date | None:
    if instant:
        return parse_instant(instant).astimezone(MELBOURNE).date()
    if day:
        return date.fromisoformat(day[:10])
    return None
