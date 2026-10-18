"""Trusted native financial ledger projection (no model): a registered ledger column profile over a source's own
CSV text or XLSX stored-cell layout (`native_tables`) → `obligation` + `financial_event` records with the ledger's
own identifiers and cell-exact citations.

Rules:
* The table must name event id, obligation id, case, date, state/kind, integer minor-unit amount and category
  columns; otherwise the source is not a ledger and nothing is projected.
* Native identifiers are kept as `financial_event_id` / `obligation_id` (explicit native → canonical identity), so a
  copied export or a re-import is the same event, never another one.
* Kinds and categories map only through the frozen enums (`resolvers.KIND_MAP` / `CATEGORY_MAP`); anything else is
  unresolved. Approval, invoice, obligation and paid cash stay distinct kinds; nothing here is summed.
* The row's case must be one of the source's linked cases; an AFCA matter column, if present, must be one of the
  source's AFCA links. A fee category never implies an AFCA matter.
* Reversal/refund amounts are stored non-positive (frozen schema); a negative non-reversal is unresolved.
* The same event id twice collapses when identical; a conflicting duplicate is unresolved and neither is kept. An
  obligation whose rows disagree on case or category is unresolved with all its events.
"""
from __future__ import annotations

import re

from ..citations import text_ref
from . import native_tables

PROFILE_ID = "sdb-native-ledger-1.1.0"   # 1.1: shared links (factual AFCA/case), exact event_time
COLUMNS = {
    "event": ("event_id", "financial_event_id", "transaction_id"),
    "obligation": ("obligation_id",),
    "case": ("case_id", "case_reference", "complaint_id"),
    "date": ("date", "effective_date", "event_date", "transaction_date"),
    "kind": ("state", "kind", "event_kind", "transaction_type"),
    "amount": ("amount_minor", "amount_cents"),
    "category": ("category", "obligation_category"),
    "afca": ("afca_matter_id",),
    "invoice": ("invoice_reference", "invoice_number"),
    "event_time": ("event_time", "effective_at", "event_instant"),
}
REQUIRED = ("event", "obligation", "case", "date", "kind", "amount", "category")


def header_map(header: list[str]) -> dict[str, int] | None:
    out = native_tables.header_map(header, COLUMNS)
    return out if out is not None and all(r in out for r in REQUIRED) else None


def _tables(source: dict):
    tables, problems = native_tables.read_tables(source)
    return [(t, cols) for t in tables if (cols := header_map(t.names())) is not None], problems


def is_ledger(source: dict) -> bool:
    return bool(_tables(source)[0])


def project(source: dict, make_record) -> tuple[list[dict], list[dict]]:
    """→ (obligation + financial_event records, unresolved)."""
    from .resolvers import CATEGORY_MAP, KIND_MAP, _norm
    sid, cases, matters = source["source_id"], set(source["case_ids"]), set(source.get("afca_matter_ids") or [])
    tables, problems = _tables(source)
    unresolved = [{"source_id": sid, "subject": None, "reason": f"{PROFILE_ID}: {p}"} for p in problems]
    events: dict[str, dict] = {}
    obligations: dict[str, dict] = {}
    conflicted_events, conflicted_obs = set(), set()

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
            eid, oid = val(fields, "event"), val(fields, "obligation")
            if not (native_tables.IDENT.match(eid) and native_tables.IDENT.match(oid)):
                bad(where, "event/obligation id missing or not a valid identifier")
                continue
            case = val(fields, "case")
            if case not in cases:
                bad(where, "case reference is not one of this source's linked cases")
                continue
            kind = KIND_MAP.get(_norm(val(fields, "kind")))
            cat = CATEGORY_MAP.get(_norm(val(fields, "category")))
            if kind is None or cat is None:
                bad(where, "state/kind or category does not map exactly to a frozen financial kind/category")
                continue
            raw_amount = val(fields, "amount")
            if not re.fullmatch(r"-?\d{1,12}", raw_amount):
                bad(where, "amount is not an integer minor-unit value")
                continue
            amount = int(raw_amount)
            if kind in ("payment_reversal", "refund"):
                amount = -abs(amount)
            elif amount < 0:
                bad(where, "negative non-reversal amount")
                continue
            day = native_tables.date_value(val(fields, "date"))
            if day is None:
                bad(where, "date is not YYYY-MM-DD")
                continue
            afca = val(fields, "afca")
            if afca and afca not in matters:
                bad(where, "AFCA reference is not one of this source's AFCA links")
                continue
            raw_time = val(fields, "event_time")
            effective_at = native_tables.event_time_value(raw_time, day) if raw_time else None
            if raw_time and effective_at is None:
                bad(where, "event_time is not an exact offset instant on the row's local date; date precision kept")
            invoice = val(fields, "invoice") or None
            if invoice and not native_tables.IDENT.match(invoice):
                invoice = None
            amount_c, kind_c, date_c = cell(fields, "amount"), cell(fields, "kind"), cell(fields, "date")
            cited = [amount_c, kind_c, date_c, cell(fields, "event"), cell(fields, "obligation"),
                     cell(fields, "category"), cell(fields, "case")]
            refs = [text_ref(source, c[0], c[1]) for c in cited if c and c[1] > c[0]]
            ob_refs = [text_ref(source, c[0], c[1]) for c in (cell(fields, "obligation"), cell(fields, "category"),
                                                               cell(fields, "case")) if c and c[1] > c[0]]
            ob = {"obligation_id": oid, "category": cat, "currency": "AUD", "case_ids": [case],
                  "afca_matter_ids": [afca] if afca else [], "creditor_role": "unknown", "source_ids": [sid],
                  "duplicate_of_obligation_id": None}
            if oid in obligations:
                prev = obligations[oid]["data"]
                if (prev["category"], prev["case_ids"]) != (cat, ob["case_ids"]):
                    conflicted_obs.add(oid)
                elif ob["afca_matter_ids"] and ob["afca_matter_ids"] != prev["afca_matter_ids"]:
                    if prev["afca_matter_ids"]:
                        conflicted_obs.add(oid)
                    else:
                        prev["afca_matter_ids"] = ob["afca_matter_ids"]
                obligations[oid]["envelope"]["evidence_refs"] += [r for r in ob_refs
                                                                  if r not in obligations[oid]["envelope"]["evidence_refs"]]
            else:
                obligations[oid] = make_record("obligation", oid, None, list(ob_refs), ob)
            ev = {"financial_event_id": eid, "obligation_id": oid, "kind": kind, "amount_minor": amount,
                  "gst_minor": None, "currency": "AUD", "amount_basis": "unknown", "value_basis": "synthetic_recorded",
                  "effective_at": effective_at, "effective_date": day, "invoice_reference": invoice, "adjusts_event_id": None,
                  "evidence_refs": refs}
            if eid in events:
                prev = events[eid]["data"]
                if {k: v for k, v in prev.items() if k != "evidence_refs"} != \
                        {k: v for k, v in ev.items() if k != "evidence_refs"}:
                    bad(where, f"event id {eid} repeated with different values; neither counted")
                    conflicted_events.add(eid)
                continue
            rec = make_record("financial_event", eid, None, list(refs), ev)
            rec["_native"] = {"financial_event_id": eid, "obligation_id": oid, "source_id": sid, "layout": table.layout,
                              "sheet": table.sheet, "row": row, "raw_state": val(fields, "kind"),
                              "raw_category": val(fields, "category"), "profile_id": PROFILE_ID}
            events[eid] = rec
    for oid in sorted(conflicted_obs):
        bad(oid, f"obligation {oid} rows disagree on case/category/matter; its events are not counted")
    keep_events = [r for e, r in sorted(events.items()) if e not in conflicted_events
                   and r["data"]["obligation_id"] not in conflicted_obs]
    keep_obs = [r for o, r in sorted(obligations.items()) if o not in conflicted_obs]
    return keep_obs + keep_events, unresolved
