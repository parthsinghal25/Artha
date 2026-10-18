"""Deterministic calculation primitives (no model involvement).

* Melbourne calendar arithmetic with DST (zoneinfo), never fixed 24-hour subtraction.
* R7 quantiles with total/known/missing N (DECISIONS §7).
* Population selection digest exactly per DECISIONS §6.
* Deadline outcome classification into the six accepted outcomes (AI/02 §2).
* Exact integer cents/seconds ledgers with union de-duplication by underlying IDs.
"""
from __future__ import annotations

import copy
import math
from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from ..canonical import canonical_sha256

MELBOURNE = ZoneInfo("Australia/Melbourne")
DEADLINE_OUTCOMES = ("on_time", "late", "explicitly_unmet", "unknown", "not_yet_due", "not_applicable")


# ---------------------------------------------------------------- time
def parse_instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("instants must carry an offset")
    return dt


def local_date(instant: datetime) -> date:
    return instant.astimezone(MELBOURNE).date()


def endpoint_age_date(endpoint: datetime, boundary: str) -> date:
    """Local calendar date used for age at an endpoint. Exclusive midnight → the preceding local date."""
    local = endpoint.astimezone(MELBOURNE)
    if boundary == "exclusive_stock_endpoint" and local.timetz().replace(tzinfo=None) == time(0, 0):
        return local.date() - timedelta(days=1)
    return local.date()


def age_days(receipt: date | None, endpoint: datetime, boundary: str) -> int | None:
    """Calendar-day age; missing or inconsistent (negative) endpoints are None, never clamped to zero."""
    if receipt is None:
        return None
    d = (endpoint_age_date(endpoint, boundary) - receipt).days
    return d if d >= 0 else None


def melbourne_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=MELBOURNE)


# ----------------------------------------------------------- quantiles
def r7(sorted_values: list[float], p: float) -> float | None:
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    h = 1 + (n - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    x_lo, x_hi = sorted_values[lo - 1], sorted_values[hi - 1]
    return x_lo + (h - lo) * (x_hi - x_lo)


def distribution(values: Iterable[float | None]) -> dict:
    vals = list(values)
    known = sorted(v for v in vals if v is not None)
    stats = {k: r7(known, p) for k, p in (("min", 0.0), ("p25", 0.25), ("median", 0.5), ("p75", 0.75),
                                         ("p90", 0.9), ("max", 1.0))}
    return {"total_n": len(vals), "known_n": len(known), "missing_n": len(vals) - len(known), **stats}


# ------------------------------------------------------- population digest
SELF_REF = ("population_id", "population_version", "population_sha256")


def selection_digest(binding: dict) -> str:
    """DECISIONS §6: canonical JSON of the selection fields with the predicate's self-referential identity
    fields removed; actual computed/published clocks excluded."""
    predicate = {k: v for k, v in copy.deepcopy(binding["predicate"]).items() if k not in SELF_REF}
    payload = {"predicate": predicate, "selectors": binding["selectors"],
               "effective_evidence_as_of": binding["effective_evidence_as_of"],
               "evidence_boundary": binding["evidence_boundary"], "versions": binding["versions"],
               "input_set_sha256": binding["input_set_sha256"], "assessment_id": binding["assessment_id"],
               "assessed_evidence_as_of": binding["assessed_evidence_as_of"], "analysis_mode": binding["analysis_mode"],
               "analysis_as_of": binding["analysis_as_of"]}
    return canonical_sha256(payload)


def seal_binding(binding: dict) -> dict:
    b = copy.deepcopy(binding)
    digest = selection_digest(b)
    b["predicate_sha256"] = digest
    b["predicate"]["population_sha256"] = digest
    return b


def binding_consistent(binding: dict) -> bool:
    p = binding["predicate"]
    return (binding["predicate_sha256"] == p["population_sha256"] == selection_digest(binding)
            and binding["population_id"] == p["population_id"]
            and binding["binding_version"] == p["population_version"]
            and p["evidence_cutoff"] == binding["effective_evidence_as_of"])


# --------------------------------------------------------- deadlines
def _eligible_completion(completion: dict | None, cutoff: datetime) -> dict | None:
    """Completion evidence counts only if it was available by the cutoff AND the completion itself cannot lie after
    the cutoff. A completion wholly or partly after the cutoff is unknown at that cutoff (never `late`)."""
    if completion is None:
        return None
    if completion.get("available_at") and parse_instant(completion["available_at"]) > cutoff:
        return None
    prec = completion.get("precision")
    if prec == "instant":
        latest = parse_instant(completion["instant"])
    elif prec == "day":
        latest = melbourne_midnight(date.fromisoformat(completion["date"]) + timedelta(days=1)) - timedelta(microseconds=1)
    elif prec == "interval":
        latest = parse_instant(completion["latest"])
    else:
        return completion
    return None if latest > cutoff else completion


def original_due(revisions: list[dict], cutoff: datetime) -> dict | None:
    """Original due opportunity: the earliest-recorded due commitment available by the cutoff. Later extensions
    or revisions never replace it for on-time classification (they are reported separately)."""
    known = [r for r in revisions if parse_instant(r["available_at"]) <= cutoff]
    if not known:
        return None
    return sorted(known, key=lambda r: (parse_instant(r["recorded_at"]), r.get("revision_id", "")))[0]


def deadline_outcome(*, applicable: bool | None, due_boundary: datetime | None, boundary_rule: str | None,
                     completion: dict | None, explicitly_unmet: bool, cutoff: datetime,
                     unmet_available_at: str | None = None) -> str:
    """Six-outcome classification.

    `due_boundary` is the interpreted comparison boundary; `boundary_rule` is `inclusive_instant` (done at or
    before the instant is on time) or `exclusive_next_day` (done before local midnight starting the next day).
    `completion` is a time_value-like dict: {"precision": "instant", "instant"} | {"precision": "day", "date"} |
    {"precision": "interval", "earliest", "latest"} | None (no evidenced completion), optionally with
    `available_at` (when the completion evidence became available). Completion after the cutoff is ignored.
    """
    if applicable is False:
        return "not_applicable"
    if applicable is None or due_boundary is None or boundary_rule not in ("inclusive_instant", "exclusive_next_day"):
        return "unknown"
    completion = _eligible_completion(completion, cutoff)
    if explicitly_unmet and unmet_available_at is not None and parse_instant(unmet_available_at) > cutoff:
        explicitly_unmet = False                      # unmet evidence recorded after the cutoff is not known yet

    def on_time(t: datetime) -> bool:
        return t <= due_boundary if boundary_rule == "inclusive_instant" else t < due_boundary

    if completion is None:
        if explicitly_unmet:
            return "explicitly_unmet"
        if (cutoff <= due_boundary) if boundary_rule == "inclusive_instant" else (cutoff < due_boundary):
            return "not_yet_due"
        return "unknown"            # missing confirmation alone is not explicitly unmet
    prec = completion["precision"]
    if prec == "instant":
        return "on_time" if on_time(parse_instant(completion["instant"])) else "late"
    if prec == "day":
        d = date.fromisoformat(completion["date"])
        start, end = melbourne_midnight(d), melbourne_midnight(d + timedelta(days=1))
        earliest, latest = start, end - timedelta(microseconds=1)
    elif prec == "interval":
        earliest, latest = parse_instant(completion["earliest"]), parse_instant(completion["latest"])
    else:
        return "unknown"
    if on_time(latest):
        return "on_time"
    if not on_time(earliest):
        return "late"
    return "unknown"                # completion interval straddles the due boundary


# ------------------------------------------------------------- ledgers
def ledger_union(entries: Iterable[dict], id_key: str, amount_key: str) -> dict:
    """Exact integer sum over unique underlying IDs (linked groups overlap; portfolio totals use unions).
    Unknown amounts are disclosed, never counted as zero."""
    seen: dict[str, int | None] = {}
    for e in entries:
        eid = e[id_key]
        amount = e.get(amount_key)
        if eid in seen and seen[eid] != amount:
            raise ValueError(f"conflicting amounts for {eid}")
        seen[eid] = amount
    known = [v for v in seen.values() if v is not None]
    if any(not isinstance(v, int) for v in known):
        raise TypeError("ledger amounts must be integers (cents/seconds)")
    return {"entity_ids": sorted(seen), "known_n": len(known), "unknown_n": len(seen) - len(known),
            "sum": sum(known) if known else None, "state": "known" if len(known) == len(seen) else
            ("partial" if known else "unknown")}
