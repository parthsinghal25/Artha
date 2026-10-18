"""Event/state coherence: private authored events, and the financial/work/lifecycle tables that are runtime evidence.

Profiles mirror the ACTIVE backend projections (`ai-backend/sdbackend/engine/native_ledger.py`, `native_work.py`,
`resolvers.KIND_MAP/CATEGORY_MAP`); a test re-reads those files and fails if they drift. Only canonical enum values
are accepted here (no aliases), so one written value means one thing.

Nothing is inferred from equal amounts: an obligation link is the row's `obligation_id`, a shared charge is one
`event_id` with several `linked_case_ids`, a reversal reduces the payments already recorded on the SAME obligation."""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date

from .model import IDENT, TABLE_PROFILE, Clock, Report, definitely_after, parse_clock

FIN_STATES = ("requested", "offered", "approved", "agreed", "awarded", "invoiced", "payment", "payment_reversal",
              "refund", "amount_revision")
FIN_CATEGORIES = ("afca_fee", "external_assistance", "other_complaint_expense", "compensation", "settlement",
                  "benefit_principal", "refund_entitlement")
APPROVAL_STATES = ("approved", "agreed", "awarded", "invoiced", "amount_revision")
NEGATIVE_STATES = ("payment_reversal", "refund")
WORK_PHASES = ("member_contact", "internal_coordination", "evidence_gathering", "approval", "submission_preparation",
               "conciliation_preparation", "conciliation_attendance", "determination_response", "follow_up", "other")
WORK_BASES = ("recorded", "planned", "estimate")
LIFECYCLE_STATUSES = ("received", "closed", "reopened")
LIFECYCLE_KIND = {"case_received": "received", "case_closed": "closed", "case_reopened": "reopened"}
PROCESS_KINDS = {"instruction_received", "evidence_received", "evidence_requested", "validity_assessed",
                 "approval_requested", "approval_granted", "approval_declined", "routed", "service_attempted",
                 "service_completed", "member_contact", "promise_made", "afca_threatened", "afca_lodged",
                 "afca_outcome", "afca_closed", "incident", "procedure_change", "other"}
EVENT_KINDS = PROCESS_KINDS | {"financial", "work"} | set(LIFECYCLE_KIND)
CASELESS_KINDS = {"incident", "procedure_change"}

PROFILES = {
    "financial": {"required": ("event_id", "obligation_id", "case_id", "date", "state", "amount_minor", "category"),
                  "optional": ("event_time", "linked_case_ids", "invoice_reference", "afca_matter_id", "currency", "note")},
    "work": {"required": ("work_id", "case_id", "date", "duration_seconds", "basis", "contributor_role", "phase"),
             "optional": ("event_time", "linked_case_ids", "actor", "task_reference", "afca_matter_id", "note")},
    "lifecycle": {"required": ("lifecycle_event_id", "case_id", "date", "status"),
                  "optional": ("event_time", "linked_case_ids", "note")},
}
# Receiver column aliases (copied from the active backend; see tests/test_authored_contract.py).
BACKEND_LEDGER_COLUMNS = {
    "event": ("event_id", "financial_event_id", "transaction_id"), "obligation": ("obligation_id",),
    "case": ("case_id", "case_reference", "complaint_id"),
    "date": ("date", "effective_date", "event_date", "transaction_date"),
    "kind": ("state", "kind", "event_kind", "transaction_type"), "amount": ("amount_minor", "amount_cents"),
    "category": ("category", "obligation_category"), "afca": ("afca_matter_id",),
    "invoice": ("invoice_reference", "invoice_number")}
BACKEND_WORK_COLUMNS = {
    "work_id": ("work_id", "entry_id", "log_id", "time_entry_id"), "case": ("case_id", "case_reference", "complaint_id"),
    "date": ("date", "work_date", "event_date", "entry_date"), "seconds": ("duration_seconds", "seconds"),
    "minutes": ("duration_minutes", "minutes"), "duration": ("duration", "time_spent", "hours_minutes"),
    "phase": ("phase", "activity", "work_phase"), "basis": ("basis", "duration_basis", "entry_type"),
    "afca": ("afca_matter_id", "afca_reference"), "role": ("contributor_role", "role"),
    "task": ("task_reference", "task_id"), "actor": ("actor", "staff_id", "user_id", "staff_reference")}
BACKEND_ALIASES = {"financial": BACKEND_LEDGER_COLUMNS, "work": BACKEND_WORK_COLUMNS}
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INT = re.compile(r"^-?\d{1,12}$")


def norm_header(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")


def split_links(v):
    return [x.strip() for x in str(v or "").split(";") if x.strip()]


def _day(v):
    if not isinstance(v, str) or not ISO_DAY.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


# ---------------------------------------------------------------- tables from final bytes
def table_from_bytes(fmt: str, data: bytes):
    """(columns, [row dict of str]) from a CSV or the FIRST sheet of an XLSX, reading stored values only."""
    if fmt == "CSV":
        recs = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
        if not recs:
            return [], []
        cols = recs[0]
        return cols, [{c: (r[i] if i < len(r) else "") for i, c in enumerate(cols)} for r in recs[1:] if any(x.strip() for x in r)]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    ws = wb.worksheets[0]
    rows = [["" if v is None else _cell_text(v) for v in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not rows:
        return [], []
    cols = rows[0]
    return cols, [{c: (r[i] if i < len(r) else "") for i, c in enumerate(cols)} for r in rows[1:] if any(str(x).strip() for x in r)]


def _cell_text(v):
    from datetime import datetime
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time().isoformat() == "00:00:00" else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def normalise_rows(columns, rows):
    """Input table rows may be lists (in column order) or dicts; values become strings ('' for None)."""
    out = []
    for r in rows:
        if isinstance(r, list):
            r = {c: (r[i] if i < len(r) else None) for i, c in enumerate(columns)}
        out.append({c: "" if r.get(c) is None else str(r.get(c)) for c in columns})
    return out


# ---------------------------------------------------------------- snapshots from authored events
def snapshot_table(inp, sid):
    """Correctly dated export: the events of this profile linked to the source's cases/matters that had occurred by
    the export's created_at (an event whose day/instant may fall after it is left out, never moved)."""
    src = inp.sources[sid]
    profile = TABLE_PROFILE[src["kind"]]
    as_of = inp.clocks[sid].get("created_at")
    cases, matters = set(src.get("case_ids") or []), set(src.get("afca_matter_ids") or [])
    picked = []
    for eid, e in inp.events.items():
        c = inp.event_clocks.get(eid)
        if c is None or not c.known:
            continue
        kind_ok = ((profile == "financial" and e["kind"] == "financial") or (profile == "work" and e["kind"] == "work")
                   or (profile == "lifecycle" and e["kind"] in LIFECYCLE_KIND))
        linked = cases & set(e.get("case_ids") or []) or matters & set(e.get("afca_matter_ids") or [])
        if kind_ok and linked and c.end() <= as_of.end():
            picked.append((c.local_day(), eid, e))
    picked.sort(key=lambda t: (t[0], t[1]))
    rows = []
    for day, eid, e in picked:
        c = inp.event_clocks[eid]
        when = c.value if c.precision in ("second", "minute") else ""   # known instants survive into the export
        lead = next(c for c in e["case_ids"] if c in cases) if cases & set(e["case_ids"]) else e["case_ids"][0]
        links = ";".join(e["case_ids"]) if len(e["case_ids"]) > 1 else ""
        afca = (e.get("afca_matter_ids") or [""])[0]
        if profile == "financial":
            rows.append({"event_id": eid, "obligation_id": e.get("obligation_id"), "case_id": lead, "linked_case_ids": links,
                         "date": day.isoformat(), "event_time": when, "state": e.get("state"), "amount_minor": e.get("amount_minor"),
                         "category": e.get("category"), "invoice_reference": e.get("invoice_reference") or "",
                         "afca_matter_id": afca})
        elif profile == "work":
            rows.append({"work_id": eid, "case_id": lead, "linked_case_ids": links, "date": day.isoformat(), "event_time": when,
                         "duration_seconds": e.get("duration_seconds"), "basis": e.get("basis"),
                         "contributor_role": e.get("contributor_role"), "phase": e.get("phase"),
                         "actor": e.get("actor") or "", "task_reference": e.get("task_reference") or "",
                         "afca_matter_id": afca})
        else:
            rows.append({"lifecycle_event_id": eid, "case_id": lead, "linked_case_ids": links, "date": day.isoformat(),
                         "event_time": when, "status": LIFECYCLE_KIND[e["kind"]]})
    spec = PROFILES[profile]
    cols = list(spec["required"]) + [c for c in spec["optional"] if any(str(r.get(c) or "") for r in rows)]
    cols = [c for c in _ordered(profile) if c in cols]
    return cols, normalise_rows(cols, rows)


def _ordered(profile):
    order = {"financial": ["event_id", "obligation_id", "case_id", "linked_case_ids", "date", "event_time", "state", "amount_minor",
                           "category", "invoice_reference", "afca_matter_id", "currency", "note"],
             "work": ["work_id", "case_id", "linked_case_ids", "date", "event_time", "duration_seconds", "basis", "contributor_role",
                      "phase", "actor", "task_reference", "afca_matter_id", "note"],
             "lifecycle": ["lifecycle_event_id", "case_id", "linked_case_ids", "date", "event_time", "status", "note"]}
    return order[profile]


# ---------------------------------------------------------------- private events
def check_events(inp, rep: Report):
    by_case_lifecycle = defaultdict(list)
    fin = []
    end = inp.window[1]
    for eid, e in inp.events.items():
        s = f"event:{eid}"
        kind = e.get("kind")
        if kind not in EVENT_KINDS:
            rep.add("event_kind", s, f"kind {kind!r} is not a known event kind", f"use one of {sorted(EVENT_KINDS)}")
            continue
        cases = e.get("case_ids") or []
        if not cases and kind not in CASELESS_KINDS:
            rep.add("event_no_case", s, "event has no case link", "list the case(s) the event belongs to")
        for c in cases:
            if c not in inp.cases:
                rep.add("dangling_case_ref", s, f"case {c} is not in runtime/cases.jsonl", "add the case or fix the id")
        clock = inp.event_clocks.get(eid)
        if clock is None:
            continue
        if clock.known and end and clock.local_day() >= end:
            rep.add("event_outside_window", s, f"occurs {clock.value}, after the observation window",
                    "remove it from this batch; events are never clamped to the window end")
        for pid in e.get("prerequisite_event_ids") or []:
            if pid == eid or pid not in inp.events:
                rep.add("missing_prerequisite", s, f"prerequisite {pid} does not exist", "reference an existing event id")
            elif definitely_after(inp.event_clocks.get(pid), clock):
                rep.add("prerequisite_after_event", s, f"prerequisite {pid} occurs after this event",
                        "fix one of the dates; a later event cannot enable an earlier one")
        if kind == "financial":
            ok = True
            if e.get("state") not in FIN_STATES:
                rep.add("financial_state", s, f"state {e.get('state')!r} not in {FIN_STATES}",
                        "a lifecycle state is not a category; e.g. approved/payment with category compensation")
                ok = False
            if e.get("category") not in FIN_CATEGORIES:
                rep.add("financial_category", s, f"category {e.get('category')!r} not in {FIN_CATEGORIES}", "use a frozen category")
                ok = False
            if not isinstance(e.get("amount_minor"), int) or isinstance(e.get("amount_minor"), bool):
                rep.add("amount_not_cents", s, "amount_minor must be integer AUD cents", "e.g. 250000 for A$2,500.00")
                ok = False
            if not IDENT.match(str(e.get("obligation_id") or "")):
                rep.add("missing_obligation", s, "financial event needs obligation_id", "link the obligation explicitly")
                ok = False
            if ok:
                fin.append({"event_id": eid, "obligation_id": e["obligation_id"], "case_ids": cases, "day": clock.local_day(),
                            "clock": clock, "state": e["state"], "amount": e["amount_minor"], "category": e["category"],
                            "invoice": e.get("invoice_reference") or "", "prerequisite_unknown": e.get("prerequisite_unknown"),
                            "where": s})
        elif kind == "work":
            d = e.get("duration_seconds")
            if not isinstance(d, int) or isinstance(d, bool) or d <= 0:
                rep.add("work_duration", s, "duration_seconds must be a positive integer", "integer seconds, e.g. 1800")
            if e.get("basis") not in WORK_BASES:
                rep.add("work_basis", s, f"basis {e.get('basis')!r} not in {WORK_BASES}", "recorded for actual work")
            if e.get("phase") not in WORK_PHASES:
                rep.add("work_phase", s, f"phase {e.get('phase')!r} not in {WORK_PHASES}", "use a frozen phase")
            if not e.get("contributor_role"):
                rep.add("work_role", s, "contributor_role is required", "the function that did the work")
        elif kind in LIFECYCLE_KIND:
            for c in cases:
                by_case_lifecycle[c].append((clock, eid, LIFECYCLE_KIND[kind]))
    for c, items in by_case_lifecycle.items():
        _lifecycle_sequence(c, items, rep, "event")
    check_obligations(fin, rep, "event")


def clusters(items):
    """Group (clock, ...) items into runs whose relative order is not fully known: a new run starts only when the next
    item is certainly after everything in the current run. Known instants therefore order events; two date-only
    events on one day (or a date-only and a timed event that day) stay unordered."""
    out = []
    for it in sorted(items, key=lambda t: (t[0].start(), t[0].end())):
        if out and not (it[0].start() > max(x[0].end() for x in out[-1])):
            out[-1].append(it)
        else:
            out.append([it])
    return out


def latest_candidates(items, at=None):
    """(clock, id, amount) approvals → those that may be the latest in effect (at the clock `at`, or overall when None).
    One is excluded only when another is certainly later (and, with `at`, certainly still before `at`); approvals
    possibly after `at` are ignored. Several candidates with different amounts mean the order is unknown."""
    pool = [t for t in items if at is None or not definitely_after(t[0], at)]
    return [a for a in pool
            if not any(definitely_after(b[0], a[0]) and (at is None or definitely_after(at, b[0])) for b in pool)]


LIFE_NEXT = {None: {"received": "open"}, "open": {"closed": "closed"}, "closed": {"reopened": "open"}}
MAX_PERMUTE = 7


def lifecycle_states(items):
    """Possible states after (clock, id, status) items, honouring known order and trying every order inside an
    unordered run. → (possible end states, [(run ids, reason)] problems, [run ids] ambiguous runs)."""
    from itertools import permutations
    states, problems, ambiguous = {None}, [], []
    for run in clusters([t for t in items if t[0] is not None and t[0].known]):
        ids = [t[1] for t in run]
        if len(run) > MAX_PERMUTE:
            ambiguous.append(ids)
            states = {"open", "closed"}
            continue
        ends, valid, total = set(), 0, 0
        for st0 in states:
            for perm in permutations(run):
                total += 1
                cur, ok = st0, True
                for t in perm:
                    cur = LIFE_NEXT.get(cur, {}).get(t[2])
                    if cur is None:
                        ok = False
                        break
                if ok:
                    valid += 1
                    ends.add(cur)
        if not ends:
            problems.append((ids, f"{[t[2] for t in run]} cannot follow state {sorted(map(str, states))} in any order "
                                  f"consistent with the known times"))
            last = run[-1][2]
            ends = {"closed" if last == "closed" else "open"}
        elif len(run) > 1 and (valid < total or len(ends) > 1):
            ambiguous.append(ids)
        states = ends
    return states, problems, ambiguous


def _lifecycle_sequence(case, items, rep, origin):
    """items: (Clock, id, status). Rejects only what is impossible under every order the known times allow."""
    _, problems, ambiguous = lifecycle_states(items)
    for ids, why in problems:
        rep.add("lifecycle_order", f"{origin}:{ids[0]}", f"case {case}: {why}",
                "received → closed → reopened → closed; give times when several changes share a day")
    for ids in ambiguous:
        rep.warn("ordering_ambiguous", f"{origin}:{ids[0]}",
                 f"case {case}: same-day lifecycle changes {ids} have no known order; state between them is unknown",
                 "add event times (event_time column / instant clocks) if the order is known")
    return ambiguous


def check_obligations(fin, rep: Report, origin: str):
    """fin: deduplicated financial events (event_id, obligation_id, clock, state, amount, category, invoice,
    prerequisite_unknown, where). Uses known instants for order; rejects only what is certain under every ordering
    the clocks allow (a same-day date-only pair is unordered, never given an invented intraday order).
    → [(event ids, reason)] ambiguities that affected a check."""
    by_ob = defaultdict(list)
    invoices = defaultdict(set)
    ambiguities = []
    for f in fin:
        by_ob[f["obligation_id"]].append(f)
        if f["state"] == "invoiced" and f["invoice"]:
            invoices[f["invoice"]].add(f["event_id"])
    for inv, ids in invoices.items():
        if len(ids) > 1:
            rep.add("shared_charge_duplicated", f"{origin}:invoice {inv}",
                    f"invoice {inv} is recorded as {len(ids)} separate invoiced events {sorted(ids)}",
                    "record one invoiced event and list every case in linked_case_ids")
    before = lambda a, b: definitely_after(b["clock"], a["clock"])          # noqa: E731  a certainly before b
    maybe_before = lambda a, b: not definitely_after(a["clock"], b["clock"])  # noqa: E731  a possibly not after b
    for ob, items in by_ob.items():
        cats = {f["category"] for f in items}
        if len(cats) > 1:
            rep.add("obligation_conflict", f"{origin}:obligation {ob}", f"rows disagree on category {sorted(cats)}",
                    "one obligation has one category")
        approvals = [f for f in items if f["state"] in APPROVAL_STATES]
        pays = [f for f in items if f["state"] == "payment"]
        revs = [f for f in items if f["state"] in NEGATIVE_STATES]
        for f in items:
            if f["amount"] < 0 and f["state"] not in NEGATIVE_STATES:
                rep.add("negative_amount", f["where"], f"negative amount for state {f['state']}",
                        "only payment_reversal/refund are negative")
        for p in pays:
            prior = [a for a in approvals if maybe_before(a, p)]
            if not prior and not p.get("prerequisite_unknown"):
                if approvals:
                    rep.add("payment_before_approval", p["where"],
                            f"payment {p['event_id']} ({p['clock'].value}) on obligation {ob} is before every approval "
                            f"{[(a['event_id'], a['clock'].value) for a in approvals]}",
                            "a payment follows its approval; fix the times or the obligation link")
                else:
                    rep.add("unmatched_payment", p["where"],
                            f"payment {p['event_id']} on obligation {ob} has no earlier approved/agreed/awarded/invoiced event",
                            "add the approval event (and its source) or state prerequisite_unknown with the reason it is not held")
            elif prior and not any(before(a, p) for a in prior):
                ambiguities.append(([a["event_id"] for a in prior] + [p["event_id"]], "approval and payment share a day; order unknown"))
            if prior:
                # the ceiling is the latest approval/revision in effect at this payment, never the historical maximum
                certain_paid = sum(abs(q["amount"]) for q in pays if q is p or before(q, p))
                possible_rev = sum(abs(r["amount"]) for r in revs if maybe_before(r, p))
                net = certain_paid - possible_rev
                latest = latest_candidates([(a["clock"], a["event_id"], abs(a["amount"])) for a in prior], p["clock"])
                ceilings = {t[2] for t in latest}
                if net > max(ceilings):
                    rep.add("payment_exceeds_obligation", p["where"],
                            f"net payments {net} exceed the approved {max(ceilings)} in effect at payment "
                            f"{p['event_id']} ({p['clock'].value}) on obligation {ob} "
                            f"(latest approval/revision {sorted(t[1] for t in latest)})",
                            "record an amount_revision or correct the amounts")
                elif net > min(ceilings):
                    ambiguities.append(([t[1] for t in latest] + [p["event_id"]],
                                        f"approvals/revisions {sorted(ceilings)} share a day with no known order; net payments "
                                        f"{net} exceed some of them, so whether payment was within the ceiling is unknown"))
        for r in revs:
            prior = [q for q in pays if maybe_before(q, r)]
            if not prior:
                if pays:
                    rep.add("reversal_before_payment", r["where"],
                            f"{r['state']} {r['event_id']} ({r['clock'].value}) on obligation {ob} is before every payment",
                            "a reversal follows the payment it reverses")
                else:
                    rep.add("unmatched_reversal", r["where"],
                            f"{r['state']} {r['event_id']} on obligation {ob} has no earlier payment to reverse",
                            "link it to the obligation whose payment it reverses, or remove it")
                continue
            if not any(before(q, r) for q in prior):
                ambiguities.append(([q["event_id"] for q in prior] + [r["event_id"]], "payment and reversal share a day; order unknown"))
            certain_rev = sum(abs(x["amount"]) for x in revs if x is r or before(x, r))
            possible_paid = sum(abs(q["amount"]) for q in prior)
            if certain_rev > possible_paid:
                rep.add("over_reversal", r["where"],
                        f"reversals {certain_rev} exceed payments {possible_paid} on obligation {ob}",
                        "a reversal cannot exceed what was paid")
    for ids, why in ambiguities:
        rep.warn("ordering_ambiguous", f"{origin}:{ids[-1]}", f"{why}: {ids}",
                 "accepted because some order is valid; add event times if the order is known")
    return ambiguities


# ---------------------------------------------------------------- runtime tables
def check_tables(tables, meta, inp, rep: Report):
    """tables: {sid: (profile, columns, rows)} as held in the FINAL originals; meta: {sid: {"as_of": Clock,
    "case_ids": [...], "afca_matter_ids": [...]}}. Returns the deduplicated runtime events per profile."""
    seen = {"financial": {}, "work": {}, "lifecycle": {}}
    for sid, (profile, cols, rows) in tables.items():
        s = f"source:{sid}"
        spec = PROFILES[profile]
        missing = [c for c in spec["required"] if c not in cols]
        if missing:
            rep.add("ledger_profile", s, f"{profile} table lacks required column(s) {missing}",
                    f"columns: {list(spec['required'])} (+ optional {list(spec['optional'])})")
            continue
        extra = [c for c in cols if c not in spec["required"] + spec["optional"]]
        if extra:
            rep.warn("extra_column", s, f"columns {extra} are kept as evidence but no projection reads them", "")
        if profile in BACKEND_ALIASES:
            normed = [norm_header(c) for c in cols]
            for role, aliases in BACKEND_ALIASES[profile].items():
                hits = [cols[i] for i, n in enumerate(normed) if n in aliases]
                if len(hits) > 1:
                    rep.add("ledger_profile", s, f"columns {hits} are ambiguous for the receiver's {role} role",
                            "keep one of them")
        m = meta[sid]
        cases, matters, as_of = set(m["case_ids"]), set(m.get("afca_matter_ids") or []), m["as_of"]
        for n, r in enumerate(rows, start=2):
            w = f"{s} row {n}"
            if any("\n" in v or "\r" in v for v in r.values()):
                rep.add("cell_newline", w, "a cell contains a line break", "single-line cells only (the receiver's XLSX layout)")
            rid = r[spec["required"][0]]
            if not IDENT.match(rid):
                rep.add("row_identity", w, f"{spec['required'][0]} {rid!r} is not a stable identifier", "every row has an id")
                continue
            day = _day(r["date"])
            if day is None:
                rep.add("row_date", w, f"date {r['date']!r} is not YYYY-MM-DD", "ISO calendar date")
                continue
            clock = Clock("day", day.isoformat())
            if r.get("event_time", "").strip():
                c = parse_clock(r["event_time"].strip(), w, "event_time", rep)
                if c is None or c.precision not in ("second", "minute") or c.local_day() != day:
                    rep.add("row_time", w, f"event_time {r['event_time']!r} is not an offset instant on {day}",
                            "ISO date-time with the Melbourne offset, on the row's date")
                    continue
                clock = c
            if r["case_id"] not in cases:
                rep.add("row_case_unlinked", w, f"case {r['case_id']} is not one of the source's case_ids",
                        "add the case to the source's case_ids")
            links = split_links(r.get("linked_case_ids"))
            if links and (r["case_id"] not in links or not set(links) <= cases):
                rep.add("row_links", w, "linked_case_ids must include case_id and only cases linked to the source",
                        "list every case the shared event belongs to, separated by ;")
            afca = r.get("afca_matter_id", "")
            if afca and afca not in matters:
                rep.add("row_afca_unlinked", w, f"AFCA matter {afca} is not one of the source's afca_matter_ids", "link it")
            if definitely_after(clock, as_of):
                rep.add("future_row", w, f"{profile} row at {clock.value} is later than the export's own clock {as_of.value}",
                        "issue a later dated export (or a snapshot) for later events; never report them early")
            norm = _norm_row(profile, r, day, links)
            if norm is None:
                rep.add("row_values", w, f"row values outside the {profile} profile: {_why(profile, r)}", _fix(profile))
                continue
            norm["clock"] = clock
            norm["where"] = w
            norm["source_ids"] = [sid]
            prev = seen[profile].get(rid)
            if prev is not None:
                if {k: v for k, v in prev.items() if k not in ("where", "source_ids")} != \
                        {k: v for k, v in norm.items() if k not in ("where", "source_ids")}:
                    rep.add("conflicting_duplicate", w, f"{rid} also appears as {prev['where']} with different values",
                            "a repeated event id must carry identical values")
                    prev["conflict"] = True
                else:
                    prev["source_ids"].append(sid)
                continue
            seen[profile][rid] = norm
            if inp is not None:
                _against_event(profile, rid, norm, inp, rep, w)
    fin = [f for f in seen["financial"].values() if not f.get("conflict")]
    ambiguous = [ids for ids, _ in check_obligations(fin, rep, "runtime")]
    life = defaultdict(list)
    for rid, f in seen["lifecycle"].items():
        life[f["case_id"]].append((f["clock"], rid, f["status"]))
    for c, items in life.items():
        ambiguous += _lifecycle_sequence(c, items, rep, "runtime")
    if inp is not None:
        for ids in ambiguous:
            cl = [inp.event_clocks.get(i) for i in ids]
            if all(c is not None and c.precision in ("second", "minute") for c in cl):
                rep.warn("runtime_order_lost", f"runtime:{ids[-1]}",
                         f"authored events {ids} have known times but the delivered rows carry only dates",
                         "add an event_time column to the authored table (snapshots add it automatically)")
    return seen


def _norm_row(profile, r, day, links):
    if profile == "financial":
        if r["state"] not in FIN_STATES or r["category"] not in FIN_CATEGORIES or not INT.match(r["amount_minor"].strip()):
            return None
        amt = int(r["amount_minor"])
        if amt < 0 and r["state"] not in NEGATIVE_STATES:
            return None
        if not IDENT.match(r["obligation_id"]):
            return None
        return {"event_id": r["event_id"], "obligation_id": r["obligation_id"], "case_id": r["case_id"],
                "case_ids": links or [r["case_id"]], "day": day, "state": r["state"],
                "amount": -abs(amt) if r["state"] in NEGATIVE_STATES else amt, "category": r["category"],
                "invoice": r.get("invoice_reference", ""), "afca_matter_id": r.get("afca_matter_id", ""),
                "prerequisite_unknown": None}
    if profile == "work":
        if (r["basis"] not in WORK_BASES or r["phase"] not in WORK_PHASES or not re.fullmatch(r"\d{1,7}", r["duration_seconds"].strip())
                or int(r["duration_seconds"]) <= 0 or not r["contributor_role"].strip()):
            return None
        return {"work_id": r["work_id"], "case_id": r["case_id"], "case_ids": links or [r["case_id"]], "day": day,
                "seconds": int(r["duration_seconds"]), "basis": r["basis"], "phase": r["phase"],
                "contributor_role": r["contributor_role"], "afca_matter_id": r.get("afca_matter_id", "")}
    if r["status"] not in LIFECYCLE_STATUSES:
        return None
    return {"lifecycle_event_id": r["lifecycle_event_id"], "case_id": r["case_id"], "case_ids": links or [r["case_id"]],
            "day": day, "status": r["status"]}


def _why(profile, r):
    if profile == "financial":
        return f"state={r.get('state')!r} category={r.get('category')!r} amount_minor={r.get('amount_minor')!r}"
    if profile == "work":
        return f"basis={r.get('basis')!r} phase={r.get('phase')!r} duration_seconds={r.get('duration_seconds')!r}"
    return f"status={r.get('status')!r}"


def _fix(profile):
    return {"financial": f"state in {FIN_STATES}; category in {FIN_CATEGORIES}; integer cents (negative only for reversal/refund)",
            "work": f"basis in {WORK_BASES}; phase in {WORK_PHASES}; positive integer seconds",
            "lifecycle": f"status in {LIFECYCLE_STATUSES}"}[profile]


def _against_event(profile, rid, row, inp, rep, w):
    e = inp.events.get(rid)
    if e is None:
        rep.add("dangling_event_ref", w, f"{rid} is not an authored event in private/events.jsonl",
                "every ledger/work/lifecycle row records an authored event; add it there")
        return
    clock = inp.event_clocks.get(rid)
    want_kind = {"financial": {"financial"}, "work": {"work"}, "lifecycle": set(LIFECYCLE_KIND)}[profile]
    if e["kind"] not in want_kind:
        rep.add("row_event_kind", w, f"{rid} is a {e['kind']} event, not {profile}", "an approval is not a payment; use the right event")
        return
    diffs = []
    if clock is not None and clock.known and clock.local_day() != row["day"]:
        diffs.append(f"date {row['day']} vs event {clock.local_day()}")
    elif clock is not None and clock.known and row["clock"].precision != "day" and \
            not (clock.start() <= row["clock"].start() <= clock.end()):
        diffs.append(f"event_time {row['clock'].value} vs event {clock.value}")
    if row["case_id"] not in e["case_ids"]:
        diffs.append(f"case {row['case_id']} not in event cases {e['case_ids']}")
    if len(e["case_ids"]) > 1 and sorted(row["case_ids"]) != sorted(e["case_ids"]):
        diffs.append(f"shared event links {e['case_ids']} not all carried (linked_case_ids)")
    if profile == "financial":
        if row["state"] != e.get("state"):
            diffs.append(f"state {row['state']} vs event {e.get('state')}")
        if abs(row["amount"]) != abs(e.get("amount_minor") or 0):
            diffs.append(f"amount {row['amount']} vs event {e.get('amount_minor')}")
        if row["obligation_id"] != e.get("obligation_id") or row["category"] != e.get("category"):
            diffs.append("obligation/category differ from the event")
        row["prerequisite_unknown"] = e.get("prerequisite_unknown")
    elif profile == "work":
        if row["seconds"] != e.get("duration_seconds") or row["basis"] != e.get("basis"):
            diffs.append("duration/basis differ from the event")
    elif row["status"] != LIFECYCLE_KIND[e["kind"]]:
        diffs.append(f"status {row['status']} vs event {e['kind']}")
    if diffs:
        rep.add("row_event_mismatch", w, "; ".join(diffs), "the source row must record the authored event exactly")


def check_reports(inp, as_of_of, rep: Report):
    """A source's `reports_events` must have occurred by the source's own as-of clock."""
    for sid, s in inp.sources.items():
        as_of = as_of_of(sid)
        for eid in s.get("reports_events") or []:
            if eid not in inp.events:
                rep.add("dangling_event_ref", f"source:{sid}", f"reports_events names unknown event {eid}", "fix the id")
            elif definitely_after(inp.event_clocks.get(eid), as_of):
                rep.add("future_knowledge", f"source:{sid}",
                        f"reports {eid} ({inp.event_clocks[eid].value}) which occurs after the source's as-of {as_of.value}",
                        "a source can only report what has happened by its own date; use a later source")
