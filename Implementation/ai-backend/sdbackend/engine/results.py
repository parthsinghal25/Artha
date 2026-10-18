"""Deterministic result service: definition + typed filters + context → immutable Shared `result` (no model).

Correctness rules (R02/R03/R10):
* **Sealed calculations.** A calculation is keyed by its full selection (analysis set, definition, normalised
  filters, frozen population restriction, evidence cutoff/boundary, analysis mode/as-of, view, permission revision)
  plus its input digest. The first computation is sealed with its clocks in one transaction; identical later
  requests (any context) reuse it, so a repeat never rewrites an immutable object. Context echoes are projected.
* **Effective records.** One effective record per logical case/matter at the cutoff: revisions available by the
  cutoff supersede earlier rows; two unsuperseded heads are unresolved and counted `uncertain`, never twice.
* **Time.** Melbourne calendar buckets. A date-only value is a whole local day: inside a bucket → member;
  straddling a non-midnight bound → uncertain; outside → excluded. Buckets after the cutoff are `unavailable`;
  truncated buckets are `partial`. Receipt cohorts bound membership.
* **Stocks** use the exclusive endpoint E = min(bucket end, cutoff): only evidence available strictly before E and
  state transitions effective before E (no current-status backfill). Each stock cell binds its own endpoint
  assessment.
* **Authority.** Any eligible record (or cited source) the principal cannot see makes the aggregate unavailable
  (`PopulationDenied`) instead of silently computing a smaller population.
* **Money/work** in exact integer cents/seconds, unions de-duplicated by underlying IDs; unknowns disclosed.
* **Issue/matter crosswalks** (R07/R10 baseline): issue populations come from `engine.issues` per cell (window and
  endpoint of that cell). A case/ledger/work record enters when a linked case is in the included population, is
  `uncertain` when it is linked only to uncertain cases, otherwise out. AFCA matter filters follow the record's own
  `afca_matter_ids` (financial events through their obligation). Every record is counted once (union by ID).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from . import issues as issue_engine
from . import mx as mx_engine
from .core import MELBOURNE, age_days, ledger_union, melbourne_midnight, parse_instant, seal_binding

ALGORITHM_VERSION = "sdb-results-2.6.0"
REG = "runtime-measures-0.2.0"
MAX_BUCKETS = 400
DEFAULT_FILTERS = {"issue_ids": [], "selection_operator": "none", "afca_matter_ids": [], "case_statuses": [],
                   "age_bands": [], "taxonomy_level": None, "parameters": {}, "combination_projection": "not_applicable",
                   "receipt_cohort_start": None, "receipt_cohort_end_exclusive": None, "case_ids": []}
OPEN_STATES = ("open", "awaiting", "reopened")
ISSUE_FILTERS = ("issue_ids", "selection_operator", "taxonomy_level")
AGE_BANDS = (("0_7", 0, 7), ("8_30", 8, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("91_180", 91, 180),
             ("over_180", 181, None))
REMEDY = ("compensation", "settlement")
EXPENSE = ("afca_fee", "external_assistance", "other_complaint_expense")
OBLIGATION_AMOUNT_KINDS = ("agreed", "awarded", "invoiced", "amount_revision")


class Unsupported(ValueError):
    """Invalid or unsupported selection for this definition (→ INVALID_REQUEST)."""


class PopulationDenied(PermissionError):
    """The principal cannot see every eligible record/source (→ neutral RESOURCE_UNAVAILABLE)."""


class HistoryUnavailable(LookupError):
    """No compatible stored result exists at the as-reported time (→ HISTORY_UNAVAILABLE)."""


@dataclass
class Item:
    entity_id: str
    case_ids: list[str]
    source_refs: list[dict]
    day: date | None = None             # flow date (date-only)
    amount: int | None = None           # cents/seconds; None = unknown
    reason: str = ""
    state: str | None = None            # stock: resolved state at endpoint
    age: int | None = None


@dataclass
class Measure:
    measure_id: str
    unit: str
    entity_unit: str
    basis: str                           # flow | stock | stock_grouped
    supported: tuple[str, ...]           # non-default filters this measure accepts
    collect: Callable                    # (inputs, sel, endpoint|None) -> (items, uncertain_ids, missing_ids, notes)
    aggregate: str = "count"             # count | sum
    cohort: bool = False
    value_scale: int = 1                 # work hours: seconds / 3600
    registry: str = REG
    version: str = "0.2.0"
    groups: tuple = ()                   # partition basis: ((key, label, selector|None), ...)
    params: tuple = ()                   # adopted `filters.parameters` keys this definition accepts


# ------------------------------------------------------------------ time
def _local_bounds(start: datetime, end: datetime, g: str) -> list[tuple[datetime, datetime]]:
    if start >= end:
        raise Unsupported("activity_start must be before activity_end_exclusive")
    s = start.astimezone(MELBOURNE)
    if g == "none":
        return [(start, end)]
    out, cur = [], s
    while cur < end:
        if g == "daily":
            nxt = melbourne_midnight(cur.date() + timedelta(days=1))
        elif g == "weekly":
            nxt = melbourne_midnight(cur.date() + timedelta(days=7 - cur.weekday()))
        elif g == "monthly":
            nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=MELBOURNE)
        elif g == "quarterly":
            q = (cur.month - 1) // 3
            y, m = (cur.year + 1, 1) if q == 3 else (cur.year, q * 3 + 4)
            nxt = datetime(y, m, 1, tzinfo=MELBOURNE)
        elif g == "annual":
            nxt = datetime(cur.year + 1, 1, 1, tzinfo=MELBOURNE)
        else:
            raise Unsupported(f"unsupported granularity {g}")
        out.append((cur, min(nxt, end)))
        cur = nxt
        if len(out) > MAX_BUCKETS:
            raise Unsupported(f"more than {MAX_BUCKETS} buckets requested")
    return out


def day_position(d: date, start: datetime, end: datetime) -> str:
    """Whole local day [d, d+1) against [start, end): in | out | straddle."""
    lo, hi = melbourne_midnight(d), melbourne_midnight(d + timedelta(days=1))
    if lo >= start and hi <= end:
        return "in"
    if hi <= start or lo >= end:
        return "out"
    return "straddle"


def _date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _local_day_of(instant: str | None, day: str | None) -> date | None:
    if instant:
        return parse_instant(instant).astimezone(MELBOURNE).date()
    return _date(day)


# ------------------------------------------------------------------ inputs
class Inputs:
    """Accepted analysis-set inputs visible at one evidence cutoff/boundary, with authority enforcement."""

    def __init__(self, rt, aset: dict, repo, cutoff: datetime, boundary: str, restriction: set[str] | None,
                 entities: dict[str, set[str]] | None = None):
        self.rt, self.aset, self.repo, self.cutoff, self.boundary = rt, aset, repo, cutoff, boundary
        self.restriction = restriction
        # Frozen contributor IDs per entity unit (e.g. financial_event_id): a requery of a selected population keeps
        # exactly those entities and never widens to every entity of the same cases.
        self.entities = entities or {}
        self.used_ids: set[str] = set()
        self.window: tuple[datetime, datetime] | None = None     # cell window / endpoint for issue populations
        self.at: datetime = cutoff
        self._issue_pop = None

    def issue_population(self, sel) -> "issue_engine.IssuePopulation":
        if self._issue_pop is None:
            self._issue_pop = issue_engine.population(self, sel.f, window=self.window or (self.cutoff, self.cutoff),
                                                      at=self.at)
            self.used_ids |= self._issue_pop.used_ids
        return self._issue_pop

    def _available(self, at: str) -> bool:
        t = parse_instant(at)
        return t < self.cutoff if self.boundary == "exclusive_stock_endpoint" else t <= self.cutoff

    def _day_available(self, day: str) -> bool:
        """demo-held-day-v1 record whose evidence was held with day precision on `day`."""
        from ..intel.temporal import end_of_day
        return self.boundary == "exclusive_stock_endpoint" and end_of_day(day) <= self.cutoff

    def assessment_available(self, assessed_as_of: str) -> bool:
        """An analysis assessment (finding / membership interval) is compatible with a cell when it assessed
        evidence up to that cell's endpoint or earlier: the endpoint's own assessment counts, a later one never
        classifies an earlier stock."""
        return parse_instant(assessed_as_of) <= self.cutoff

    def _visible(self, rec: dict, *, cited: bool = True) -> None:
        if not self.repo.auth.record_permitted(rec):
            raise PopulationDenied("eligible record outside this principal's authority")
        if cited:
            for ref in rec["envelope"].get("evidence_refs", []):
                if self.repo.source_record(ref["source_id"]) is None:
                    raise PopulationDenied("eligible record cites a source outside this principal's authority")

    def heads(self, ids: list[str], kind: str, key: str, *, restricted: bool = True) -> tuple[dict[str, dict], set[str]]:
        """Effective record per logical entity (`data[key]`) at the cutoff; ambiguous keys returned separately.
        `restricted=False` returns the unrestricted heads, for classifying a linked record (never for counting)."""
        groups: dict[str, list[dict]] = {}
        db_kind = "native_record" if kind == "case" else "analysis_record"
        recs_by_id = self.rt.db.get_objects_batched(db_kind, ids)
        for rid in ids:
            rec = recs_by_id.get(rid)
            avail = self.assessment_available if kind == "finding" else self._available
            dp = (self.aset.get("day_precision_available") or {}).get(rid)
            if rec is None or rec["kind"] != kind or not (self._day_available(dp) if dp and kind != "finding"
                                                          else avail(rec["envelope"]["available_at"])):
                continue
            if kind == "case" and not self.repo.in_release("input", rid):
                continue                                   # never a row outside the accepted release
            groups.setdefault(rec["data"][key], []).append(rec)
        heads, ambiguous = {}, set()
        for k, recs in groups.items():
            for r in recs:
                self._visible(r)
            superseded = {r["envelope"]["supersedes_record_id"] for r in recs if r["envelope"]["supersedes_record_id"]}
            top = [r for r in recs if r["envelope"]["record_id"] not in superseded]
            if len(top) == 1:
                heads[k] = top[0]
            else:
                ambiguous.add(k)
            self.used_ids |= {r["envelope"]["record_id"] for r in recs}
        if not restricted:
            return heads, ambiguous
        if key in self.entities:
            heads = {k: v for k, v in heads.items() if k in self.entities[key]}
            ambiguous = {k for k in ambiguous if k in self.entities[key]}
        if self.restriction is not None:
            heads = {k: v for k, v in heads.items() if k in self.restriction or self._in_restriction(v)}
            ambiguous = {k for k in ambiguous if k in self.restriction or k in self.entities.get(key, ())}
        return heads, ambiguous

    @staticmethod
    def _links(d: dict) -> tuple[set[str], set[str]] | None:
        """(case IDs, AFCA matter IDs) a record carries itself, or None when it has no case/matter fields at all
        (e.g. a financial event, scoped through its obligation by the collector)."""
        if not any(k in d for k in ("case_ids", "case_id", "afca_matter_ids", "afca_matter_id")):
            return None
        cases = set(d.get("case_ids") or []) | ({d["case_id"]} if d.get("case_id") else set())
        matters = set(d.get("afca_matter_ids") or []) | ({d["afca_matter_id"]} if d.get("afca_matter_id") else set())
        return cases, matters

    def in_scope(self, case_ids, matter_ids=(), entity_id: str | None = None) -> bool:
        """Whether linked case/matter IDs fall inside the frozen case restriction (always true when unrestricted).
        A case-less contributor is frozen by its own entity ID (`analysis_read.members`)."""
        return self.restriction is None or entity_id in self.restriction or \
            bool((set(case_ids or []) | set(matter_ids or [])) & self.restriction)

    def _in_restriction(self, rec: dict) -> bool:
        links = self._links(rec["data"])
        return True if links is None else self.in_scope(*links)

    def cases(self):
        return self.heads(self.aset.get("case_record_ids", []), "case", "case_id")

    def records(self, kind: str, key: str, *, restricted: bool = True):
        return self.heads(self.aset.get("record_ids", []), kind, key, restricted=restricted)

    def mx(self, kind: str) -> tuple[dict[str, dict], set[str]]:
        """Accepted Shared MX objects of one kind: one head per logical occurrence assessed by the cutoff; two
        unsuperseded heads are ambiguous. Every bound source must be visible to the principal."""
        groups: dict[str, list[dict]] = {}
        mx_ids = self.aset.get("mx_record_ids", [])
        recs_by_id = self.rt.db.get_objects_batched("mx_record", mx_ids)
        for oid in mx_ids:
            rec = recs_by_id.get(oid)
            if rec is None or rec["kind"] != kind or not self.assessment_available(rec["common"]["assessed_evidence_as_of"]):
                continue
            for b in rec["common"]["source_bindings"]:
                if self.repo.source_record(b["source_id"]) is None:
                    raise PopulationDenied("eligible MX record binds a source outside this principal's authority")
            groups.setdefault(rec["common"]["logical_occurrence_id"], []).append(rec)
            self.used_ids.add(oid)
        heads, ambiguous = {}, set()
        for k, recs in groups.items():
            sup = {r["common"]["supersedes_analysis_id"] for r in recs if r["common"]["supersedes_analysis_id"]}
            top = [r for r in recs if r["common"]["analysis_id"] not in sup]
            if len(top) == 1:
                heads[k] = top[0]
            else:
                ambiguous.add(k)
        if self.restriction is not None:
            heads = {k: v for k, v in heads.items() if set(v["common"]["case_ids"]) & self.restriction}
        return heads, ambiguous

    def transitions(self, case_id: str) -> list[dict]:
        """Accepted administrative state transitions for a case from its published case view."""
        view_id = self.aset.get("cases", {}).get(case_id)
        view = self.rt.db.get_object("case_view", view_id) if view_id else None
        if view and view.get("stage_id"):
            self.used_ids.add(f"stage:{view['stage_id']}")      # transitions depend on the accepted S03 stage
        return list(view.get("administrative_transitions", [])) if view else []


# ------------------------------------------------------------------ collectors
def _case_basis(sel) -> str:
    return "original_receipt_date" if sel.f["receipt_basis"] == "original_institution" else "complaints_receipt_date"


def _in_cohort(sel, d: date | None) -> bool | None:
    s, e = _date(sel.f["receipt_cohort_start"]), _date(sel.f["receipt_cohort_end_exclusive"])
    if s is None and e is None:
        return True
    if d is None:
        return None
    return (s is None or d >= s) and (e is None or d < e)


def _issue_selected(sel) -> bool:
    measure = getattr(sel, "measure", None)
    return sel.f["selection_operator"] != "none" or bool(measure and measure.measure_id.startswith("issues."))


def _gate(inp: Inputs, sel, case_ids, matter_ids=None) -> str:
    """in | uncertain | out for one record: case filter, AFCA matter crosswalk, then issue population."""
    if sel.f["case_ids"] and not set(case_ids or []) & set(sel.f["case_ids"]):
        return "out"
    if sel.f["afca_matter_ids"] and not set(matter_ids or []) & set(sel.f["afca_matter_ids"]):
        return "out"
    if _issue_selected(sel):
        return inp.issue_population(sel).gate(case_ids)
    return "in"


def _case_filter(sel, rec: dict) -> bool:
    return not sel.f["case_ids"] or rec["data"]["case_id"] in sel.f["case_ids"]


def collect_cases_new(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.cases()
    field_ = _case_basis(sel)
    items, missing, uncertain = [], [], sorted(ambiguous)
    for cid, rec in sorted(heads.items()):
        g = _gate(inp, sel, [cid])
        if g == "out":
            continue
        d = _date(rec["data"][field_])
        if d is None:
            missing.append(cid)
            continue
        if not _in_cohort(sel, d):
            continue
        if g == "uncertain":
            uncertain.append(cid)
            continue
        items.append(Item(cid, [cid], rec["envelope"]["evidence_refs"], day=d, reason=f"{field_} {d.isoformat()}"))
    return items, uncertain, missing, _issue_notes(inp, sel)


def _transition_available(t: dict, endpoint: datetime) -> bool:
    """Available strictly before the endpoint; a demo-policy transition whose table had day-precision availability
    (`available_precision`) is held in that day's end-of-day view instead (never an invented instant)."""
    ap = t.get("available_precision")
    if ap and ap.get("precision") == "day":
        from ..intel.temporal import end_of_day
        return end_of_day(ap["date"]) <= endpoint
    return parse_instant(t["available_at"]) < endpoint


def _state_at(transitions: list[dict], endpoint: datetime) -> tuple[str | None, list[dict]]:
    """Latest accepted transition effective before endpoint and available strictly before it."""
    eligible = []
    for t in transitions:
        if not _transition_available(t, endpoint):
            continue
        eff = parse_instant(t["effective_at"]) if t.get("effective_at") else \
            melbourne_midnight(_date(t["effective_date"])) if t.get("effective_date") else None
        if eff is None or eff >= endpoint:
            continue
        eligible.append((eff, t))
    if not eligible:
        return None, []
    eligible.sort(key=lambda x: x[0])
    latest_time = eligible[-1][0]
    latest = [t for e, t in eligible if e == latest_time]
    states = {t["state"] for t in latest}
    if len(states) > 1:
        return "conflicting", [r for t in latest for r in t.get("evidence_refs", [])]
    return latest[-1]["state"], latest[-1].get("evidence_refs", [])


def collect_cases_open(inp: Inputs, sel, endpoint: datetime):
    heads, ambiguous = inp.cases()
    field_ = _case_basis(sel)
    items, uncertain, missing = [], list(sorted(ambiguous)), []
    wanted = set(sel.f["case_statuses"]) or set(OPEN_STATES)
    if wanted - set(OPEN_STATES):
        raise Unsupported(f"{sel.measure.measure_id} filters only open/awaiting/reopened")
    for cid, rec in sorted(heads.items()):
        g = _gate(inp, sel, [cid])
        if g == "out":
            continue
        d = _date(rec["data"][field_])
        if d is not None and melbourne_midnight(d) >= endpoint:
            continue                                    # not yet received at this endpoint
        state, refs = _state_at(inp.transitions(cid), endpoint)
        if state is None or state in ("unknown", "conflicting"):
            uncertain.append(cid)                       # unknown historical state: separate, never backfilled
            continue
        if state in wanted:
            if g == "uncertain":
                uncertain.append(cid)                   # open, but its issue link is uncertain
                continue
            items.append(Item(cid, [cid], refs or rec["envelope"]["evidence_refs"], state=state,
                              age=age_days(d, endpoint, "exclusive_stock_endpoint"),
                              reason=f"administrative state {state} at endpoint"))
    return items, uncertain, missing, _issue_notes(inp, sel)


def collect_age_bands(inp: Inputs, sel, endpoint: datetime):
    items, uncertain, missing, notes = collect_cases_open(inp, sel, endpoint)
    return items, uncertain, missing, notes


def _band(age: int | None) -> str:
    if age is None:
        return "unknown"
    for name, lo, hi in AGE_BANDS:
        if age >= lo and (hi is None or age <= hi):
            return name
    return "unknown"


def _obligations(inp: Inputs, *, restricted: bool = True) -> dict[str, dict]:
    heads, _ = inp.records("obligation", "obligation_id", restricted=restricted)
    return {k: v for k, v in heads.items() if v["data"]["duplicate_of_obligation_id"] is None}


def _collect_cash(categories: tuple[str, ...]):
    def collect(inp: Inputs, sel, endpoint=None):
        obligations = _obligations(inp, restricted=False)
        events, ambiguous = inp.records("financial_event", "financial_event_id")
        items, uncertain, missing = [], sorted(ambiguous), []
        for eid, rec in sorted(events.items()):
            d = rec["data"]
            if d["kind"] not in ("payment", "payment_reversal"):
                continue                                 # requests/offers/approvals/invoices are not cash
            ob = obligations.get(d["obligation_id"])
            if ob is None:
                uncertain.append(eid)                    # category unknown without its obligation
                continue
            if ob["data"]["category"] not in categories:
                continue
            if not inp.in_scope(ob["data"]["case_ids"], ob["data"]["afca_matter_ids"], eid):
                continue                                 # outside the frozen population: out, not uncertain
            g = _gate(inp, sel, ob["data"]["case_ids"], ob["data"]["afca_matter_ids"])
            if g == "out":
                continue
            if g == "uncertain":
                uncertain.append(eid)
                continue
            day = _local_day_of(d["effective_at"], d["effective_date"])
            if day is None:
                missing.append(eid)
                continue
            # frozen schema: payments ≥ 0, reversals ≤ 0 — the recorded signed amount is used as is
            items.append(Item(eid, ob["data"]["case_ids"], d["evidence_refs"], day=day, amount=d["amount_minor"],
                              reason=f"{d['kind']} for {ob['data']['category']} {d['obligation_id']}"))
        return items, uncertain, missing, ["cash only: requests, offers, approvals and invoices excluded"] + \
            _issue_notes(inp, sel)
    return collect


def collect_work(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.records("work_entry", "work_id")
    items, uncertain, missing = [], sorted(ambiguous), []
    for wid, rec in sorted(heads.items()):
        d = rec["data"]
        if d["duplicate_of_work_id"] or d["duration_basis"] != "recorded":
            continue                                     # estimates never enter recorded hours
        g = _gate(inp, sel, d["case_ids"], d["afca_matter_ids"])
        if g == "out":
            continue
        if g == "uncertain":
            uncertain.append(wid)
            continue
        day = _local_day_of(d["event_at"], d["event_date"])
        if day is None or d["duration_seconds"] is None:
            missing.append(wid)
            continue
        items.append(Item(wid, d["case_ids"], d["evidence_refs"], day=day, amount=int(d["duration_seconds"]),
                          reason=f"recorded {d['phase']} work"))
    return items, uncertain, missing, ["recorded durations only; estimates excluded"] + _issue_notes(inp, sel)


def collect_obligations(inp: Inputs, sel, endpoint: datetime):
    obligations = _obligations(inp)
    events, ambiguous = inp.records("financial_event", "financial_event_id")
    by_ob: dict[str, list[dict]] = {}
    for rec in events.values():
        d = rec["data"]
        eff = d["effective_at"] or (melbourne_midnight(_date(d["effective_date"])).isoformat() if d["effective_date"] else None)
        if eff is None or parse_instant(eff) >= endpoint:
            continue
        by_ob.setdefault(d["obligation_id"], []).append({**d, "_eff": parse_instant(eff)})
    items, uncertain, notes = [], sorted(ambiguous), []
    for u in inp.aset.get("financial_history_uncertainties", []):
        if parse_instant(u["available_at"]) >= endpoint or _gate(inp, sel, u["case_ids"]) == "out":
            continue
        oid = u["obligation_id"]
        if u.get("stage_id"):
            inp.used_ids.add(f"stage:{u['stage_id']}")
        evs = by_ob.get(oid, [])
        if any(e["kind"] in OBLIGATION_AMOUNT_KINDS or e["kind"] == "payment" for e in evs):
            continue
        if not all(inp.repo.source_record(r["source_id"]) for r in u["evidence_refs"]):
            raise PopulationDenied("conditional-authorization evidence unavailable")
        if oid not in uncertain:
            uncertain.append(oid)
        notes.append(f"{oid}: {u['reason']}")
    for oid, ob in sorted(obligations.items()):
        g = _gate(inp, sel, ob["data"]["case_ids"], ob["data"]["afca_matter_ids"])
        if g == "out":
            continue
        if g == "uncertain":
            uncertain.append(oid)
            continue
        evs = sorted(by_ob.get(oid, []), key=lambda e: (e["_eff"], e["financial_event_id"]))
        amounts = [e for e in evs if e["kind"] in OBLIGATION_AMOUNT_KINDS]
        if not amounts:
            continue                                     # offers/requests alone are not obligations
        latest = [e for e in amounts if e["_eff"] == amounts[-1]["_eff"]]
        if len({e["amount_minor"] for e in latest}) > 1:
            uncertain.append(oid)
            continue
        paid = sum(e["amount_minor"] for e in evs if e["kind"] in ("payment", "payment_reversal", "refund"))
        outstanding = latest[-1]["amount_minor"] - paid
        if outstanding < 0:
            notes.append(f"overpayment flagged for {oid}")
            outstanding = 0
        if outstanding:
            items.append(Item(oid, ob["data"]["case_ids"], [r for e in evs for r in e["evidence_refs"]][:20],
                              amount=outstanding, reason=f"{ob['data']['category']} outstanding at endpoint"))
    return items, uncertain, [], notes + _issue_notes(inp, sel)


def collect_afca_new(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.records("afca_matter", "afca_matter_id")
    items, missing = [], []
    for mid, rec in sorted(heads.items()):
        d = rec["data"]
        if sel.f["afca_matter_ids"] and mid not in sel.f["afca_matter_ids"]:
            continue
        day = _date(d["received_date"])
        if day is None:
            missing.append(mid)
            continue
        items.append(Item(mid, [], d["evidence_refs"], day=day, reason=f"formal AFCA receipt {day.isoformat()}"))
    return items, sorted(ambiguous), missing, ["formal external receipts only; threats to escalate excluded"]


def collect_afca_open(inp: Inputs, sel, endpoint: datetime):
    heads, ambiguous = inp.records("afca_matter", "afca_matter_id")
    items, uncertain = [], sorted(ambiguous)
    for mid, rec in sorted(heads.items()):
        d = rec["data"]
        if sel.f["afca_matter_ids"] and mid not in sel.f["afca_matter_ids"]:
            continue
        if d["received_date"] and melbourne_midnight(_date(d["received_date"])) >= endpoint:
            continue
        transitions = [{"state": h["stage"], "effective_at": h["effective_at"], "effective_date": h["effective_date"],
                        "available_at": h["available_at"], "evidence_refs": h["source_refs"]} for h in d["stage_history"]]
        state, refs = _state_at(transitions, endpoint)
        if state is None or state in ("unknown", "conflicting"):
            uncertain.append(mid)
            continue
        if state != "closed":
            items.append(Item(mid, [], refs, state=state, reason=f"AFCA stage {state} at endpoint"))
    return items, uncertain, [], []


def _issue_notes(inp: Inputs, sel) -> list[str]:
    if not _issue_selected(sel):
        return []
    pop = inp.issue_population(sel)
    mode = sel.f["issue_population_mode"]
    return [f"issue population: {sel.f['selection_operator']} over {len(sel.f['issue_ids']) or 'all'} node(s), "
            f"mode {mode}; included memberships of supported findings only; issue-linked burden may overlap "
            f"across issues and is not additive"] + pop.notes


LINKED_STATUSES = ("open", "awaiting", "reopened", "closed", "unknown")


def collect_issue_linked_cases(inp: Inputs, sel, endpoint: datetime):
    """issues.linked_cases: distinct accepted cases in the cell's issue population (recomputed, never summed).
    Optional receipt cohort and administrative status at the cell endpoint (`unknown` = no sourced state)."""
    pop = inp.issue_population(sel)
    heads, ambiguous = inp.cases()
    wanted = set(sel.f["case_statuses"])
    if wanted - set(LINKED_STATUSES):
        raise Unsupported("issues.linked_cases case_statuses must be open/awaiting/reopened/closed/unknown")
    field_ = _case_basis(sel)
    items, uncertain, missing, notes = [], sorted(ambiguous), [], _issue_notes(inp, sel)
    orphan = sorted((set(pop.included) | pop.uncertain) - set(heads) - set(ambiguous))
    if orphan:
        notes.append(f"{len(orphan)} linked case(s) without an accepted register row excluded")
    for cid in sorted((set(pop.included) | pop.uncertain) & set(heads)):
        rec = heads[cid]
        if sel.f["case_ids"] and cid not in sel.f["case_ids"]:
            continue
        cohort = _in_cohort(sel, _date(rec["data"][field_]))
        if cohort is False:
            continue
        state = None
        if wanted:
            state, _ = _state_at(inp.transitions(cid), endpoint)
            seg = state if state in ("open", "awaiting", "reopened", "closed") else "unknown"
            if seg not in wanted:
                continue
        if cohort is None:
            missing.append(cid)                          # receipt date unknown: cohort cannot be decided
        elif cid in pop.included:
            items.append(Item(cid, [cid], pop.included[cid] or rec["envelope"]["evidence_refs"], state=state,
                              reason=f"supported {sel.f['issue_population_mode']} issue membership"))
        else:
            uncertain.append(cid)
    return items, sorted(set(uncertain)), missing, notes


def _window(inp: Inputs):
    return inp.window or (inp.cutoff, inp.cutoff)


def collect_follow_through(inp: Inputs, sel, endpoint=None):
    """mx.commitments.follow_through: original-due cohort in the window, classified at the cutoff."""
    heads, ambiguous = inp.mx("commitment")
    start, end = parse_instant(sel.f["activity_start"]), parse_instant(sel.f["activity_end_exclusive"])
    scope = (sel.f.get("parameters") or {}).get("commitment_scope", "member_facing")
    items, unc, missing, notes = [], sorted(ambiguous), [], ["original due only; revised-due performance not shown",
                                                             f"commitment scope: {scope}"]
    other = 0
    for lid, c in sorted(heads.items()):
        if c["scope"] != scope:
            other += 1
            continue
        if c["common"]["evidence_state"] == "excluded" or c["undertaking_basis"] == "reported_only":
            continue                                    # a report-only allegation is not a captured undertaking
        g = _gate(inp, sel, c["common"]["case_ids"])
        if g == "out":
            continue
        state, reason = mx_engine.classify_original_due(c, inp.cutoff)
        if state == "ineligible":
            missing.append(lid)                         # unknown due: outside dated cohorts, disclosed
            continue
        if not mx_engine.in_cohort(c["original_due"], start, end):
            continue
        if g == "uncertain" or c["common"]["evidence_state"] in ("uncertain", "conflicting") \
                or c["undertaking_basis"] == "uncertain":
            unc.append(lid)
            continue
        items.append(Item(lid, c["common"]["case_ids"], c["common"]["supporting_refs"], state=state, reason=reason))
    if other:
        notes.append(f"{other} commitment(s) outside the selected {scope} scope (a separately selected scope)")
    return items, unc, missing, notes + _issue_notes(inp, sel)


def _flow_partition(inp: Inputs, sel, tv, oid, cases, refs, reason, *, state="all", confirmed=True):
    start, end = parse_instant(sel.f["activity_start"]), min(parse_instant(sel.f["activity_end_exclusive"]), inp.cutoff)
    pos = mx_engine.window_position(tv, start, end) if tv else "unknown"
    if pos == "out":
        return None, None
    g = _gate(inp, sel, cases)
    if g == "out":
        return None, None
    if pos in ("unknown",):
        return None, ("missing", oid)
    if pos == "straddle" or g == "uncertain" or not confirmed:
        return None, ("uncertain", oid)
    return Item(oid, cases, refs, state=state, reason=reason), None


def _partition_collect(inp, sel, rows):
    items, unc, missing = [], [], []
    for it, other in rows:
        if it is not None:
            items.append(it)
        elif other is not None:
            (unc if other[0] == "uncertain" else missing).append(other[1])
    return items, sorted(set(unc)), sorted(set(missing))


def collect_repeat_valid_requests(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.mx("member_effort_event")
    seen, rows = set(), []
    for lid, e in sorted(heads.items()):
        q = mx_engine.request_qualifies(e)
        if q == "excluded" or e["occasion_id"] in seen:
            continue
        seen.add(e["occasion_id"])                       # one request occasion, however many items/copies
        rows.append(_flow_partition(inp, sel, e["common"]["event_time"], e["occasion_id"], e["common"]["case_ids"],
                                    e["common"]["supporting_refs"], "later request for previously valid evidence",
                                    confirmed=q == "supported"))
    items, unc, missing = _partition_collect(inp, sel, rows)
    return items, sorted(set(unc) | ambiguous), missing, ["request occasions counted once across items"] + \
        _issue_notes(inp, sel)


def collect_expressed_positions(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.mx("member_outcome_response")
    rows, seen, candidates = [], set(), 0
    dim = (sel.f.get("parameters") or {}).get("dimension")
    for lid, r in sorted(heads.items()):
        status = mx_engine.response_status(r)
        if status == "excluded":
            continue
        if dim is not None and status == "confirmed" and r["dimension"] != dim:
            continue                                    # another communicated-target dimension
        if status == "candidate":
            candidates += 1
            rows.append(_flow_partition(inp, sel, r["common"]["event_time"], lid, r["common"]["case_ids"], [], "",
                                        confirmed=False))
            continue
        key = (r["response_occasion_id"], r["target_id"], r["target_version"], r["dimension"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(_flow_partition(inp, sel, r["common"]["event_time"], lid, r["common"]["case_ids"],
                                    r["common"]["supporting_refs"], f"{r['expressed_position']} on {r['dimension']}",
                                    state=f"{r['expressed_position']}:{r['dimension']}"))
    items, unc, missing = _partition_collect(inp, sel, rows)
    notes = ["silence creates no response; update-only is distinct from acceptance"] + \
        ([f"dimension: {dim}"] if dim else ["all dimensions"])
    if candidates:
        notes.append(f"{candidates} unidentified/uncertain target candidate(s) outside confirmed totals")
    return items, sorted(set(unc) | ambiguous), missing, notes + _issue_notes(inp, sel)


MEMBER_INITIATORS = ("complainant", "representative")


def collect_contacts_repeat(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.records("contact_link", "contact_id")
    rows = []
    for cid, rec in sorted(heads.items()):
        d = rec["data"]
        if d["duplicate_of_contact_id"] or d["initiator"] not in MEMBER_INITIATORS or \
                d["repeat_state"] in ("first", "not_applicable"):
            continue
        rows.append(_flow_partition(inp, sel, mx_engine.contact_day(d), cid, d["case_ids"], d["evidence_refs"],
                                    "supported same-episode repeat contact", confirmed=d["repeat_state"] == "repeat"
                                    and d["episode_id"] is not None))
    items, unc, missing = _partition_collect(inp, sel, rows)
    return items, sorted(set(unc) | ambiguous), missing, ["copies de-duplicated; uncertain continuity separate"] + \
        _issue_notes(inp, sel)


def collect_post_closure_return(inp: Inputs, sel, endpoint=None):
    heads, ambiguous = inp.records("contact_link", "contact_id")
    episodes, _ = inp.records("episode", "episode_id")
    rows = []
    for cid, rec in sorted(heads.items()):
        d = rec["data"]
        if d["duplicate_of_contact_id"] or d["initiator"] not in MEMBER_INITIATORS:
            continue
        tv = mx_engine.contact_day(d)
        k_lo, k_hi = mx_engine.bounds(tv)
        closed_before, maybe = False, False
        for case in d["case_ids"]:
            for t in inp.transitions(case):
                if t["state"] != "closed" or not (_transition_available(t, inp.cutoff)
                                                  if t.get("available_precision") else inp._available(t["available_at"])):
                    continue
                ct = {"precision": "instant", "instant": t["effective_at"]} if t.get("effective_at") else \
                    {"precision": "day", "date": t["effective_date"]} if t.get("effective_date") else None
                c_lo, c_hi = mx_engine.bounds(ct)
                if c_lo is None or k_lo is None:
                    maybe = True                          # unknown timing: never proves a return
                elif (c_hi <= k_lo) if ct["precision"] == "day" else (c_hi < k_lo):
                    closed_before = True                  # every possible closure instant strictly earlier
                elif c_lo < k_hi:
                    maybe = True                          # overlapping timing: uncertain order
        if not closed_before and not maybe:
            continue
        ep = episodes.get(d["episode_id"]) if d["episode_id"] else None
        continuing = ep is not None and ep["data"]["link_state"] == "supported"
        rows.append(_flow_partition(inp, sel, tv, cid, d["case_ids"], d["evidence_refs"],
                                    "return contact after an evidenced closure, same continuing problem",
                                    confirmed=closed_before and continuing))
    items, unc, missing = _partition_collect(inp, sel, rows)
    return items, sorted(set(unc) | ambiguous), missing, [
        "review signal, not proof of defective resolution; absence under missing history is not zero"] + \
        _issue_notes(inp, sel)


SHARED = "shared-measures-1.0.0"
FT_GROUPS = tuple((o, o.replace("_", " "), {"kind": "commitment_outcome", "outcome": o, "due_basis": "original"})
                  for o in mx_engine.OUTCOMES)
POS_GROUPS = tuple((f"{p}:{d}", f"{p.replace('_', ' ')} · {d}",
                    {"kind": "response_position", "position": p, "dimension": d})
                   for d in mx_engine.DIMENSIONS for p in mx_engine.POSITIONS)
MEASURES: dict[tuple[str, str, str], Measure] = {}
for m in (
    Measure("cases.new", "cases", "case_id", "flow", ("receipt_cohort", "case_ids", "issues"), collect_cases_new, cohort=True),
    Measure("cases.open", "cases", "case_id", "stock", ("case_ids", "case_statuses", "issues"), collect_cases_open),
    Measure("cases.age_distribution", "cases", "case_id", "stock_grouped", ("case_ids", "age_bands", "issues"), collect_age_bands),
    Measure("cash.remedies_paid", "AUD_minor", "financial_event_id", "flow", ("case_ids", "afca_matter_ids", "issues"), _collect_cash(REMEDY), "sum"),
    Measure("cash.expenses_paid", "AUD_minor", "financial_event_id", "flow", ("case_ids", "afca_matter_ids", "issues"), _collect_cash(EXPENSE), "sum"),
    Measure("cash.expenses_and_remedies_paid", "AUD_minor", "financial_event_id", "flow",
            ("case_ids", "afca_matter_ids", "issues"),
            _collect_cash(REMEDY + EXPENSE), "sum"),
    Measure("work.recorded_hours", "hours", "work_id", "flow", ("case_ids", "afca_matter_ids", "issues"), collect_work, "sum", value_scale=3600),
    Measure("obligations.outstanding", "AUD_minor", "obligation_id", "stock", ("case_ids", "afca_matter_ids", "issues"), collect_obligations, "sum"),
    Measure("afca.new", "matters", "afca_matter_id", "flow", ("afca_matter_ids",), collect_afca_new),
    Measure("afca.open", "matters", "afca_matter_id", "stock", ("afca_matter_ids",), collect_afca_open),
    Measure("issues.linked_cases", "cases", "case_id", "stock",
            ("issues", "case_ids", "receipt_cohort", "case_statuses"), collect_issue_linked_cases),
    Measure("issues.open_linked_cases", "cases", "case_id", "stock", ("issues", "case_ids", "case_statuses"),
            collect_cases_open),
    Measure("contacts.repeat", "contacts", "contact_id", "partition", ("case_ids", "issues"), collect_contacts_repeat),
    Measure("contacts.post_closure_return", "contacts", "contact_id", "partition", ("case_ids", "issues"),
            collect_post_closure_return),
    Measure("mx.commitments.follow_through", "commitments", "commitment_id", "partition", ("case_ids", "issues"),
            collect_follow_through, registry=SHARED, version="1.0.0", groups=FT_GROUPS, params=("commitment_scope",)),
    Measure("mx.effort.repeat_valid_evidence_requests", "request occasions", "request_event_id", "partition",
            ("case_ids", "issues"), collect_repeat_valid_requests, registry=SHARED, version="1.0.0"),
    Measure("mx.responses.expressed_positions", "response relationships", "response_relationship_id", "partition",
            ("case_ids", "issues"), collect_expressed_positions, registry=SHARED, version="1.0.0",
            groups=POS_GROUPS, params=("dimension",)),
):
    MEASURES[(m.registry, m.measure_id, m.version)] = m
IMPLEMENTED = set(MEASURES)


# ------------------------------------------------------------------ selection
@dataclass
class Sel:
    f: dict
    measure: Measure
    restriction: list[str] | None
    origin: dict | None = None
    notes: list[str] = field(default_factory=list)
    entities: dict[str, set[str]] | None = None
    discovery_gap: list[str] = field(default_factory=list)
    assessment_gap: list[str] = field(default_factory=list)


def entity_restriction(rt, origin: list[dict] | None) -> dict[str, set[str]] | None:
    """Exact frozen contributor IDs per entity unit for the (already validated) origin population references;
    several references intersect within the same unit."""
    if not origin:
        return None
    from ..api.analysis_read import resolve_reference
    out: dict[str, set[str]] = {}
    for ref in origin:
        found = resolve_reference(rt, ref)
        if found is None:
            raise PopulationDenied("origin population unavailable")
        cset = found[2]
        unit = cset["population"]["predicate"]["entity_unit"]
        ids = {i["entity_id"] for i in cset["items"]}
        out[unit] = ids if unit not in out else out[unit] & ids
    return out


def validate_filters(measure: Measure, f: dict) -> None:
    for k, default in DEFAULT_FILTERS.items():
        if k in ("receipt_cohort_start", "receipt_cohort_end_exclusive"):
            if f.get(k) is not None and "receipt_cohort" not in measure.supported:
                raise Unsupported(f"{measure.measure_id} does not support a receipt cohort")
            continue
        if k == "parameters":
            extra = sorted(set(f.get("parameters") or {}) - set(measure.params))
            if extra:
                raise Unsupported(f"{measure.measure_id}@{measure.version} does not support parameter(s) {extra}")
            continue
        cap = "issues" if k in ISSUE_FILTERS else k
        if f.get(k, default) != default and cap not in measure.supported:
            raise Unsupported(f"{measure.measure_id}@{measure.version} does not support filter {k}")
    s, e = _date(f.get("receipt_cohort_start")), _date(f.get("receipt_cohort_end_exclusive"))
    if s and e and s >= e:
        raise Unsupported("receipt_cohort_start must be before receipt_cohort_end_exclusive")
    if measure.basis == "stock_grouped" and f["granularity"] != "none":
        raise Unsupported(f"{measure.measure_id} is a single-window partition in this build; use granularity none")
    parse_instant(f["activity_start"]), parse_instant(f["activity_end_exclusive"])


def selection_key(aset: dict, ctx: dict, req: dict, restriction: list[str] | None, origin: dict | None) -> str:
    """Identity of a calculation independent of context/request IDs and clocks, including authority scope."""
    return canonical_sha256({"aset": aset["analysis_set_version"], "definition": req["definition"],
                             "filters": {**DEFAULT_FILTERS, **req["filters"]}, "restriction": restriction,
                             "origin": origin, "evidence_as_of": ctx["evidence_as_of"],
                             "boundary": ctx["evidence_boundary"], "view_id": ctx["view_id"],
                             "permission_revision": ctx["permission_revision"], "algorithm": ALGORITHM_VERSION})


# ------------------------------------------------------------------ computation
def _binding(sel: Sel, ctx: dict, aset: dict, versions: dict, input_set: str, key: str, start: datetime,
             end: datetime, *, endpoint: datetime | None, now: str, calc_key: str, selectors=()) -> dict:
    m, f = sel.measure, sel.f
    stock = endpoint is not None
    eff = endpoint.isoformat() if stock else ctx["evidence_as_of"]
    pop_id = derived_id("POP", calc_key, key)
    predicate = {"population_id": pop_id, "population_version": "v1", "population_sha256": "0" * 64,
                 "entity_unit": m.entity_unit, "definition": {"registry_id": m.registry, "measure_id": m.measure_id,
                                                              "definition_version": m.version},
                 "activity_start": start.isoformat(), "activity_end_exclusive": end.isoformat(),
                 "receipt_basis": f["receipt_basis"], "receipt_cohort_start": f["receipt_cohort_start"],
                 "receipt_cohort_end_exclusive": f["receipt_cohort_end_exclusive"],
                 "stock_endpoint": eff if stock else None, "evidence_cutoff": eff,
                 "knowledge_rule": "stock_at_endpoint" if stock else "flow_at_selected_cutoff",
                 "taxonomy_level": f["taxonomy_level"],
                 "taxonomy_version": aset.get("taxonomy_version") if (f["issue_ids"] or f["selection_operator"] != "none"
                                                                      or m.measure_id.startswith("issues.")) else None,
                 "issue_ids": sorted(f["issue_ids"]), "selection_operator": f["selection_operator"],
                 "issue_population_mode": f["issue_population_mode"],
                 "case_ids": sorted(sel.restriction if sel.restriction is not None else f["case_ids"]),
                 "afca_matter_ids": sorted(f["afca_matter_ids"]), "case_statuses": sorted(f["case_statuses"]),
                 "age_bands": sorted(f["age_bands"]), "membership_states": ["included"],
                 "combination_projection": "not_applicable",
                 "extension_parameters": {k: v for k, v in sorted((f.get("parameters") or {}).items())}}
    return seal_binding({
        "population_id": pop_id, "binding_version": "v1", "predicate": predicate, "selectors": list(selectors),
        "predicate_sha256": "0" * 64, "effective_evidence_as_of": eff,
        "evidence_boundary": "exclusive_stock_endpoint" if stock else ctx["evidence_boundary"], "versions": versions,
        "input_set_sha256": input_set,
        "assessment_id": derived_id("ASSESS", aset["analysis_set_version"], eff) if stock else aset["assessment_id"],
        "assessed_evidence_as_of": eff if stock else aset["assessed_evidence_as_of"],
        "analysis_mode": ctx["analysis_mode"], "analysis_as_of": ctx["analysis_as_of"], "computed_at": now,
        "published_at": now})


def _cell(sel: Sel, binding: dict, key: str, label: str, members: list[Item], uncertain: list[str],
          missing: list[str], eligible: int, start, end, effective_end, partial: bool, future: bool, calc_key: str,
          notes: list[str]) -> tuple[dict, dict, dict]:
    m = sel.measure
    gap = list(sel.discovery_gap)
    assessment_gap = list(sel.assessment_gap)
    incomplete = bool(gap or assessment_gap)
    cset_id = derived_id("CSET", calc_key, key)
    calc_ref = derived_id("CALC", calc_key)
    cov_id = derived_id("COVR", calc_key, key)
    if future:
        value, state = None, "unavailable"
    elif m.aggregate == "count":
        value = len(members)
        state = "partial" if (partial or uncertain or missing or incomplete) else "known"
    else:
        led = ledger_union([{"id": i.entity_id, "amount": i.amount} for i in members], "id", "amount")
        total = led["sum"] or 0
        value = total / m.value_scale if m.value_scale != 1 else total
        state = "partial" if (partial or uncertain or missing or led["unknown_n"] or incomplete) else "known"
    reasons = [r for r in [("bucket starts after the evidence cutoff; not observed" if future else None),
                           ("bucket truncated at evidence cutoff" if partial and not future else None),
                           (f"{len(uncertain)} unresolved/uncertain entity(ies) excluded" if uncertain else None),
                           (f"{len(missing)} entity(ies) with unknown date/amount excluded" if missing else None),
                           ("issue discovery incomplete; not an exhaustive affected population" if gap and not future
                            else None), ("assessment incomplete; captured subtotal only" if assessment_gap and not future
                                         else None)] if r]
    point = {"value": value, "state": state, "unit": m.unit, "reason": "; ".join(reasons) or None,
             "contributor_set_reference": cset_id, "included_count": 0 if future else len(members),
             "excluded_count": 0 if future else max(0, eligible - len(members) - len(uncertain) - len(missing)),
             "uncertain_count": 0 if future else len(uncertain), "coverage_ids": [cov_id], "calculation_reference": calc_ref}
    coverage = {"coverage_ids": [cov_id], "eligible_count": eligible, "included_count": point["included_count"],
                "excluded_count": point["excluded_count"], "uncertain_count": point["uncertain_count"],
                "missing_count": 0 if future else len(missing),
                "assessment_state": "unknown" if future else ("partial" if (uncertain or missing or partial or incomplete)
                                                              else "complete_for_declared_release"),
                "limitations": ["selected complaints corpus; not institution-wide"] + notes + assessment_gap +
                [f"discovery incomplete: {r}" for r in gap if f"discovery incomplete: {r}" not in notes] +
                ([f"frozen population restriction of {len(sel.restriction)} case(s)"] if sel.restriction is not None else []) +
                [f"frozen {u} set of {len(ids)} entity(ies) from the originating population" for u, ids in
                 sorted((sel.entities or {}).items())] +
                (["empty frozen population: no overlap with the originating cell"] if sel.restriction == [] else []),
                "entity_unit": m.entity_unit,
                "dimension": "time"}
    cell = {"key": key, "label": label, "point": point, "distribution": None,
            "contributor_set_id": cset_id, "population_id": binding["population_id"], "population": binding,
            "coverage": coverage, "additional_coverage": [], "interval_start": start.isoformat(),
            "interval_end_exclusive": end.isoformat(), "effective_end": effective_end.isoformat() if effective_end else None,
            "partial_bucket": bool(partial or future)}
    cset = {"contributor_set_id": cset_id, "population_id": binding["population_id"],
            "predicate_sha256": binding["predicate_sha256"], "population": binding,
            "total_count": point["included_count"], "calc_key": calc_key,
            "items": [] if future else [{"entity_id": i.entity_id, "case_ids": sorted(i.case_ids),
                                         "source_refs": i.source_refs, "inclusion_reason": i.reason,
                                         "observation_reference": None} for i in members]}
    covrec = {"coverage_record_id": cov_id, "calc_key": calc_key, "cell_key": key, **coverage}
    return cell, cset, covrec


def _label(start: datetime, g: str) -> str:
    s = start.astimezone(MELBOURNE)
    return {"monthly": s.strftime("%b %Y"), "annual": str(s.year),
            "quarterly": f"Q{(s.month - 1) // 3 + 1} {s.year}"}.get(g, s.strftime("%Y-%m-%d"))


def _groups(sel: Sel) -> tuple:
    """Partition cells for this selection: an explicit response `dimension` keeps only that dimension's cells."""
    groups = sel.measure.groups or (("all", "All", None),)
    dim = (sel.f.get("parameters") or {}).get("dimension")
    if dim is not None:
        groups = tuple(g for g in groups if not g[2] or g[2].get("dimension") in (None, dim))
    return groups


def calculate(rt, aset: dict, repo, ctx: dict, req: dict, sel: Sel, calc_key: str) -> tuple[dict, list[tuple]]:
    """Pure computation → (result body, [(kind, id, body, links)] objects to seal)."""
    f, m = sel.f, sel.measure
    cutoff = parse_instant(ctx["evidence_as_of"])
    start, end = parse_instant(f["activity_start"]), parse_instant(f["activity_end_exclusive"])
    buckets = _local_bounds(start, end, f["granularity"])
    restriction = set(sel.restriction) if sel.restriction is not None else None
    entities = sel.entities
    versions = {"source_set_version": ctx["source_set_version"], "analysis_set_version": aset["analysis_set_version"],
                "taxonomy_version": aset.get("taxonomy_version"), "algorithm_version": ALGORITHM_VERSION}
    now = utc_now_iso()
    cells, objects, used = [], [], set()
    coverage = aset.get("assessment_coverage", {}).get(m.measure_id, {})
    if sel.assessment_gap and coverage.get("stage_id"):
        used.add(f"stage:{coverage['stage_id']}")
    flow_inputs = None
    if m.basis == "flow":
        flow_inputs = Inputs(rt, aset, repo, cutoff, ctx["evidence_boundary"], restriction, entities)
        flow_inputs.window, flow_inputs.at = (start, min(end, cutoff)), min(end, cutoff)
        flow_items, f_unc, f_miss, notes = m.collect(flow_inputs, sel)
        if _issue_selected(sel) and f["granularity"] != "none":
            notes = notes + ["issue population evaluated once over the whole activity window, not per bucket"]
        used |= flow_inputs.used_ids
    if m.basis == "partition":
        # Each period re-runs the measure's own collector on that period, so grouping follows the measure's clock
        # (original-due cohort, response/request/contact activity), never another measure's date.
        periods = [(start, end, None)] if f["granularity"] == "none" else \
            [(bs, be, bs.astimezone(MELBOURNE).strftime("%Y-%m-%d")) for bs, be in buckets]
        for p_start, p_end, pkey in periods:
            sel_p = sel if pkey is None else Sel(**{**sel.__dict__, "f": {**f, "activity_start": p_start.isoformat(),
                                                                          "activity_end_exclusive": p_end.isoformat()}})
            inp = Inputs(rt, aset, repo, cutoff, ctx["evidence_boundary"], restriction, entities)
            inp.window, inp.at = (p_start, min(p_end, cutoff)), min(p_end, cutoff)
            future, partial = p_start >= cutoff, cutoff < p_end and not p_start >= cutoff
            items, unc, miss, notes = ([], [], [], ["period starts after the evidence cutoff"]) if future else \
                m.collect(inp, sel_p)
            used |= inp.used_ids
            input_set = canonical_sha256([calc_key, pkey, sorted(inp.used_ids)])
            for gkey, glabel, selector in _groups(sel):
                members = items if gkey == "all" else [i for i in items if i.state == gkey]
                key = gkey if pkey is None else f"{pkey}.{gkey}"   # frozen key pattern: period.group
                label = glabel if pkey is None else f"{_label(p_start, f['granularity'])} · {glabel}"
                binding = _binding(sel_p, ctx, aset, versions, input_set, key, p_start, p_end, endpoint=None, now=now,
                                   calc_key=calc_key, selectors=[selector] if selector else [])
                cell, cset, cov = _cell(sel_p, binding, key, label, members, unc, miss,
                                        len(items) + len(unc) + len(miss), p_start, p_end, min(p_end, cutoff),
                                        partial, future, calc_key, notes)
                cells.append(cell)
                objects += [("contributor_set", cset["contributor_set_id"], cset, None),
                            ("coverage_record", cov["coverage_record_id"], cov, None)]
        buckets = []
    for b_start, b_end in buckets:
        key = b_start.astimezone(MELBOURNE).strftime("%Y-%m-%d") if f["granularity"] != "none" else "all"
        future = b_start >= cutoff
        partial = cutoff < b_end and not future
        if m.basis == "flow":
            # Unknown-date and unresolved entities could belong to any bucket: every cell discloses them.
            members, unc = [], list(f_unc)
            for it in flow_items:
                if melbourne_midnight(it.day) >= cutoff:
                    continue                         # dated after the evidence cutoff: never counted
                pos = day_position(it.day, b_start, b_end)
                if pos == "in":
                    members.append(it)
                elif pos == "straddle":
                    unc.append(it.entity_id)
            eligible = len(flow_items) + len(f_unc) + len(f_miss)
            missing = list(f_miss)
            unc = sorted(set(unc))
            binding = _binding(sel, ctx, aset, versions, canonical_sha256([calc_key, sorted(used)]), key, b_start,
                               b_end, endpoint=None, now=now, calc_key=calc_key)
            cell, cset, cov = _cell(sel, binding, key, _label(b_start, f["granularity"]), members, unc, missing,
                                    eligible, b_start, b_end, min(b_end, cutoff), partial, future, calc_key, notes)
            cells.append(cell)
            objects += [("contributor_set", cset["contributor_set_id"], cset, None),
                        ("coverage_record", cov["coverage_record_id"], cov, None)]
            continue
        endpoint = min(b_end, cutoff)
        inp = Inputs(rt, aset, repo, endpoint, "exclusive_stock_endpoint", restriction, entities)
        inp.window, inp.at = (b_start, endpoint), endpoint
        items, unc, miss, notes = m.collect(inp, sel, endpoint)
        used |= inp.used_ids
        input_set = canonical_sha256([calc_key, endpoint.isoformat(), sorted(inp.used_ids)])
        if m.basis == "stock_grouped":
            groups = {name: [] for name, _, _ in AGE_BANDS} | {"unknown": []}
            for it in items:
                groups[_band(it.age)].append(it)
            wanted = set(f["age_bands"]) or set(groups)
            for band, members in groups.items():
                if band not in wanted:
                    continue
                selector = {"kind": "age_band", "band": band}
                bkey = f"band-{band}"
                binding = _binding(sel, ctx, aset, versions, input_set, bkey, b_start, b_end, endpoint=endpoint,
                                   now=now, calc_key=calc_key, selectors=[selector])
                cell, cset, cov = _cell(sel, binding, bkey, band.replace("_", "–"), members, unc, miss,
                                        len(items) + len(unc) + len(miss), b_start, b_end, endpoint, partial, future,
                                        calc_key, notes + [f"age at exclusive endpoint {endpoint.isoformat()}"])
                cells.append(cell)
                objects += [("contributor_set", cset["contributor_set_id"], cset, None),
                            ("coverage_record", cov["coverage_record_id"], cov, None)]
            continue
        binding = _binding(sel, ctx, aset, versions, input_set, key, b_start, b_end, endpoint=endpoint, now=now,
                           calc_key=calc_key)
        cell, cset, cov = _cell(sel, binding, key, _label(b_start, f["granularity"]), items, unc, miss,
                                len(items) + len(unc) + len(miss), b_start, b_end, endpoint, partial, future,
                                calc_key, notes + [f"stock at exclusive endpoint {endpoint.isoformat()}"])
        cells.append(cell)
        objects += [("contributor_set", cset["contributor_set_id"], cset, None),
                    ("coverage_record", cov["coverage_record_id"], cov, None)]
    input_set = canonical_sha256([calc_key, sorted(used)])
    result_id = derived_id("RES", calc_key)
    common = dict(cells[0]["population"]["predicate"]) if cells else {}
    common.update({"activity_start": f["activity_start"], "activity_end_exclusive": f["activity_end_exclusive"]})
    calc_id = derived_id("CALC", calc_key)
    state = "partial" if any(c["point"]["state"] != "known" for c in cells) else "ready"
    result = {"result_id": result_id, "contract_version": "1.0.0",
              "result_context": {"context": ctx, "predicate": common, "versions": versions, "computed_at": now,
                                 "published_at": now, "calculation_id": calc_id, "input_set_sha256": input_set},
              "computation_state": state,
              "shape": ("matrix" if m.basis == "partition" and f["granularity"] != "none" else "partition")
              if m.basis == "stock_grouped" or m.groups else ("series" if len(cells) > 1 else "scalar"),
              "cells": cells, "legacy_result_reference": None, "synthetic_demo": True}
    objects.append(("calculation", calc_id, {"calculation_id": calc_id, "calc_key": calc_key, "result_id": result_id,
                                             "algorithm_version": ALGORITHM_VERSION, "definition": req["definition"],
                                             "input_record_ids": sorted(used), "input_set_sha256": input_set,
                                             "computed_at": now}, None))
    return result, objects


def project(result: dict, ctx: dict) -> dict:
    """Echo the requesting context without rewriting the sealed calculation."""
    return {**result, "result_context": {**result["result_context"], "context": ctx}}


def compute(rt, aset: dict, repo, ctx: dict, req: dict, *, restriction: list[str] | None = None,
            origin: dict | None = None) -> dict | None:
    d = req["definition"]
    key = (d["registry_id"], d["measure_id"], d["definition_version"])
    if key not in MEASURES or key not in {(x["registry_id"], x["measure_id"], x["definition_version"])
                                          for x in aset.get("supported_definitions", [])}:
        return None
    measure = MEASURES[key]
    f = {**DEFAULT_FILTERS, **req["filters"]}
    validate_filters(measure, f)
    dim = (f.get("parameters") or {}).get("dimension")
    if dim is not None and dim not in aset.get("supported_response_dimensions", []):
        raise Unsupported(f"response dimension {dim} is not supported by this analysis set (never substituted)")
    if f["selection_operator"] != "none" or f["issue_ids"] or measure.measure_id.startswith("issues."):
        try:
            issue_engine.validate(f, issue_engine.load_taxonomy(rt, aset), issue_measure=True)
        except issue_engine.IssueSelectionError as e:
            raise Unsupported(str(e)) from e
    sel = Sel(f=f, measure=measure, restriction=None if restriction is None else sorted(restriction), origin=origin,
              entities=entity_restriction(rt, origin),
              discovery_gap=issue_engine.discovery_gap(aset) if _issue_selected(Sel(f=f, measure=measure,
                                                                                    restriction=None)) else [])
    coverage = aset.get("assessment_coverage", {}).get(measure.measure_id, {})
    if coverage.get("state") != "complete" and coverage:
        selected = set(f["case_ids"] or coverage.get("case_ids", []))
        if restriction is not None:
            selected &= set(restriction)
        if selected & set(coverage.get("case_ids", [])):
            sel.assessment_gap = list(coverage.get("limitations", []))
    calc_key = selection_key(aset, ctx, req, sel.restriction, origin)
    result_id = derived_id("RES", calc_key)
    if ctx["analysis_mode"] == "as_reported":
        return as_reported(rt, aset, ctx, req, restriction=sel.restriction, origin=origin)
    from ..api.analysis_read import dependencies_ok
    stored = rt.db.get_object("result", result_id)
    if stored is not None:
        if not dependencies_ok(rt, aset, result_dependency_ids(rt, stored)):
            raise PopulationDenied("an accepted input this result depends on is no longer effective")
        return project(stored, ctx)
    result, objects = calculate(rt, aset, repo, ctx, req, sel, calc_key)
    calc = next(b for k, _, b, _ in objects if k == "calculation")
    if not dependencies_ok(rt, aset, calc["input_record_ids"]):
        raise PopulationDenied("an accepted input this result depends on is no longer effective")
    with rt.db.tx() as c:          # all-or-nothing: concurrent identical requests converge on the first seal
        existing = c.execute("SELECT body FROM objects WHERE kind='result' AND object_id=?", (result_id,)).fetchone()
        if existing is None:
            for kind, oid, body, links in objects:
                if c.execute("SELECT 1 FROM objects WHERE kind=? AND object_id=?", (kind, oid)).fetchone() is None:
                    rt.db.put_object(kind, oid, body, links=links, conn=c)
            rt.db.put_object("result", result_id, result, conn=c,
                             links={"analysis_set": [aset["analysis_set_version"]],
                                    "selection": [history_key(aset, ctx, req, sel.restriction, origin)]})
    return project(rt.db.get_object("result", result_id), ctx)


def result_dependency_ids(rt, result: dict) -> list[str]:
    calc = rt.db.get_object("calculation", result["result_context"]["calculation_id"])
    return list(calc["input_record_ids"]) if calc else ["<missing calculation>"]


def history_key(aset: dict, ctx: dict, req: dict, restriction, origin) -> str:
    """Selection identity WITHOUT the analysis mode (the calculation key never includes it), used to find stored
    results for as-reported reads."""
    return selection_key(aset, ctx, req, restriction, origin)


def as_reported(rt, aset: dict, ctx: dict, req: dict, *, restriction=None, origin=None) -> dict:
    """Latest stored compatible result published at or before analysis_as_of (parsed instants). Never computes."""
    hk = history_key(aset, ctx, req, None if restriction is None else sorted(restriction), origin)
    cutoff = parse_instant(ctx["analysis_as_of"])
    best = None
    for rid in rt.db.linked("result", "selection", hk):
        res = rt.db.get_object("result", rid)
        published = parse_instant(res["result_context"]["published_at"])
        if published <= cutoff and (best is None or published > parse_instant(best["result_context"]["published_at"])):
            best = res
    if best is None:
        raise HistoryUnavailable("no compatible result was published by the requested as-reported time")
    from ..api.analysis_read import dependencies_ok
    if not dependencies_ok(rt, aset, result_dependency_ids(rt, best)):
        raise PopulationDenied("an accepted input this stored result depends on has since been withdrawn")
    return project(best, ctx)
