"""Deterministic portfolio intelligence results (M3; no model).

Every cell states value, unit, numerator/denominator where a share, exact contributor IDs, uncertain and unassessed
IDs and a coverage note. Grains follow `demo-held-day-v1` (day, Monday week, calendar month, calendar quarter,
observation year Oct–Sep; calendar_year kept distinct). Rules:
* Arrivals are distinct case IDs by native complaints receipt date; members are canonical member references (set
  union, never a sum of bucket counts).
* Issue incidence uses the SAME cohort for numerator and denominator: included cases among ASSESSED eligible
  arrivals in the bucket. Unassessed/not-eligible cases are reported separately, never as excluded. Shares of
  different issues overlap and can sum above 100%.
* Onset is "first evidenced" (earliest supporting claim day) — not proof of when the mechanism began.
* Backlog/stock uses lifecycle transitions held at the endpoint; unknown lifecycle stays unknown. Administrative
  closure is not service completion (service state reported separately).
* Burden: cash as integer cents by category kind (remedy / complaint expense / principal-entitlement kept apart),
  hours as recorded seconds; event union across cases. Issue burden is ASSOCIATED (all events of included cases),
  with shared (cases in ≥2 issues) and unallocated (cases in no issue) partitions; issue amounts are not additive.
  Associated burden is not causal or avoidable cost.
* Alerts are descriptors (current 28 local days vs the preceding 84), with an explicit threshold version; small or
  missing baselines return insufficient_baseline. Priorities expose reach/change/persistence/burden/uncertainty
  without a hidden score.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from ..canonical import canonical_sha256, derived_id
from ..engine.core import MELBOURNE, melbourne_midnight, parse_instant
from . import temporal

MEASURES_VERSION = "sdb-intel-measures-1.3.0"   # 1.1 breadth; 1.2 overlap; 1.3 AFCA procedure + continuity (V07)
BROAD_SHARE, BROAD_MIN = 0.85, 8
ALERT_RULE = {"rule_version": "intel-alert-heuristic-1.0.0", "current_days": 28, "baseline_days": 84,
              "min_current_new_cases": 3, "min_baseline_assessed_arrivals": 3,
              "note": "configurable demo heuristic; not statistical significance or production incident detection"}
REMEDY = ("compensation", "settlement")
EXPENSE = ("afca_fee", "external_assistance", "other_complaint_expense")
PRINCIPAL = ("benefit_principal", "refund_entitlement")
OPEN = ("open", "awaiting", "reopened")


def _cell(value, unit: str, contributors: list[str], **kw) -> dict:
    c = {"value": value, "unit": unit, "contributors": sorted(set(contributors))}
    c.update(kw)
    return c


class Portfolio:
    """Snapshot of everything held/assessed at one knowledge view."""

    def __init__(self, *, view_date: date, knowledge_end, cases: dict[str, dict], issues: list[dict],
                 memberships: dict[tuple[str, str], dict], records: list[dict], memories: dict[str, dict]):
        self.view_date, self.knowledge_end = view_date, knowledge_end
        self.cases, self.issues, self.memberships = cases, issues, memberships
        self.records, self.memories = records, memories
        self.obligations = {r["data"]["obligation_id"]: r for r in records if r["kind"] == "obligation"}

    # ---- helpers
    def receipt(self, cid: str) -> date | None:
        d = self.cases[cid]["receipt_date"]
        return date.fromisoformat(d) if d else None

    def state_at(self, cid: str, end) -> tuple[str, list]:
        best = None
        for t in self.cases[cid]["transitions"]:
            if not temporal.available(t["available_at"], t.get("available_precision"), end):
                continue
            eff = parse_instant(t["effective_at"]) if t.get("effective_at") else melbourne_midnight(
                date.fromisoformat(t["effective_date"]))
            if eff >= end:
                continue
            key = (t["effective_date"], t.get("effective_at") or "")
            if best is None or key > best[0]:
                best = (key, [t])
            elif key == best[0]:
                best[1].append(t)
        if best is None:
            return "unknown", []
        states = {t["state"] for t in best[1]}
        if len(states) > 1:
            return "conflicting", best[1]
        return best[1][0]["state"], best[1]

    def included(self, issue_id: str) -> list[str]:
        return sorted(c for (c, i), m in self.memberships.items() if i == issue_id and m["state"] == "included")

    def assessed_eligible(self) -> list[str]:
        return sorted(c for c, v in self.cases.items() if v["eligible"] and c in self.memories)

    def event_cases(self, r: dict) -> list[str]:
        if r["kind"] == "financial_event":
            ob = self.obligations.get(r["data"]["obligation_id"])
            return sorted((ob or {}).get("data", {}).get("case_ids") or [])
        return sorted(r["data"].get("case_ids") or [])

    def event_day(self, r: dict) -> date | None:
        d = r["data"]
        if r["kind"] == "financial_event":
            return temporal.local_day(d.get("effective_at"), d.get("effective_date"))
        return temporal.local_day(d.get("event_at"), d.get("event_date"))


# ------------------------------------------------------------------ measures
def arrivals(p: Portfolio, grain: str) -> list[dict]:
    out = []
    for label, s, e in temporal.buckets(temporal.OBSERVATION_START, min(temporal.OBSERVATION_END_EXCLUSIVE,
                                                                       p.view_date + timedelta(days=1)), grain):
        ids = [c for c in p.cases if (d := p.receipt(c)) and s <= d < e]
        members = sorted({m for c in ids for m in p.cases[c]["members"]})
        assessed = [c for c in ids if p.cases[c]["eligible"] and c in p.memories]
        out.append({"bucket": label, "start": s.isoformat(), "end_exclusive": e.isoformat(),
                    "cases": _cell(len(ids), "cases", ids), "members": _cell(len(members), "members", members),
                    "assessed_cases": _cell(len(assessed), "cases", assessed),
                    "unassessed_cases": sorted(set(ids) - set(assessed))})
    return out


def backlog(p: Portfolio) -> dict:
    end = p.knowledge_end
    rows = {"open": [], "closed": [], "unknown": [], "conflicting": []}
    ages, silent, received_no_closure = {}, [], []
    for cid in sorted(p.cases):
        d = p.receipt(cid)
        if d is None or melbourne_midnight(d) >= end:
            continue
        st, _ = p.state_at(cid, end)
        bucket = "open" if st in OPEN else st if st in ("closed", "conflicting") else "unknown"
        rows[bucket].append(cid)
        if bucket == "open":
            ages[cid] = (p.view_date - d).days
            last = max((parse_instant(s) for s in p.cases[cid]["evidence_available"]), default=None)
            if last is None or (end - last).days > 30:
                silent.append(cid)
        if bucket != "closed" and not any(t["state"] == "closed" for t in p.cases[cid]["transitions"]
                                          if temporal.available(t["available_at"], t.get("available_precision"), end)):
            received_no_closure.append(cid)
    svc = {}
    for cid in rows["closed"]:
        m = p.memories.get(cid)
        svc.setdefault(m["service"]["state"] if m else "not_assessed", []).append(cid)
    vals = sorted(ages.values())
    return {"open": _cell(len(rows["open"]), "cases", rows["open"]),
            "closed": _cell(len(rows["closed"]), "cases", rows["closed"]),
            "lifecycle_unknown": _cell(len(rows["unknown"]), "cases", rows["unknown"]),
            "lifecycle_conflicting": _cell(len(rows["conflicting"]), "cases", rows["conflicting"]),
            "open_age_days": {"median": statistics.median(vals) if vals else None, "max": max(vals) if vals else None,
                              "by_case": ages},
            "open_without_evidence_last_30_days": _cell(len(silent), "cases", silent),
            "received_without_evidenced_closure": _cell(len(received_no_closure), "cases", received_no_closure),
            "closed_by_service_state": {k: _cell(len(v), "cases", v) for k, v in sorted(svc.items())},
            "note": "administrative closure is not service completion; unknown lifecycle is neither open nor closed"}


def _cash(p: Portfolio, events: list[dict], cats: tuple[str, ...]) -> dict:
    ids, total = [], 0
    for r in events:
        if r["kind"] != "financial_event" or r["data"]["kind"] not in ("payment", "payment_reversal", "refund"):
            continue
        ob = p.obligations.get(r["data"]["obligation_id"])
        if ob is None or ob["data"]["category"] not in cats:
            continue
        ids.append(r["data"]["financial_event_id"])
        total += r["data"]["amount_minor"]
    return _cell(total, "AUD_cents", ids)


def _hours(events: list[dict]) -> dict:
    ids = [r["data"]["work_id"] for r in events if r["kind"] == "work_entry"]
    secs = sum(r["data"]["duration_seconds"] or 0 for r in events if r["kind"] == "work_entry")
    return _cell(secs, "seconds", ids, hours=round(secs / 3600, 4))


def burden(p: Portfolio, cases: set[str] | None = None) -> dict:
    ev = [r for r in p.records if r["kind"] in ("financial_event", "work_entry")
          and (cases is None or set(p.event_cases(r)) & cases)]
    return {"remedies_paid": _cash(p, ev, REMEDY), "complaint_expenses_paid": _cash(p, ev, EXPENSE),
            "principal_or_entitlement_paid": _cash(p, ev, PRINCIPAL), "recorded_work": _hours(ev),
            "note": "event union: a shared charge/work entry linked to several cases counts once; principal/"
                    "entitlement is not complaint expense; hours are not cash; holdings are partial (missing "
                    "records are not zero)"}


def burden_series(p: Portfolio, grain: str) -> list[dict]:
    out = []
    for label, s, e in temporal.buckets(temporal.OBSERVATION_START, min(temporal.OBSERVATION_END_EXCLUSIVE,
                                                                       p.view_date + timedelta(days=1)), grain):
        ev = [r for r in p.records if r["kind"] in ("financial_event", "work_entry") and
              (d := p.event_day(r)) is not None and s <= d < e]
        out.append({"bucket": label, "remedies_paid": _cash(p, ev, REMEDY),
                    "complaint_expenses_paid": _cash(p, ev, EXPENSE), "recorded_work": _hours(ev)})
    return out


def issue_view(p: Portfolio, iss: dict) -> dict:
    iid = iss["issue_id"]
    elig = [c for c, v in p.cases.items() if v["eligible"]]
    states = {}
    for c in elig:
        m = p.memberships.get((c, iid))
        states.setdefault(m["state"] if m else ("unassessed" if c in p.memories else "no_memory"), []).append(c)
    inc = sorted(states.get("included", []))
    members = sorted({m for c in inc for m in p.cases[c]["members"]})
    open_inc = [c for c in inc if p.state_at(c, p.knowledge_end)[0] in OPEN]
    onset = {}
    for c in inc:
        m = p.memberships[(c, iid)]
        onset[c] = m.get("first_evidenced")
    ages = [(p.view_date - p.receipt(c)).days for c in open_inc if p.receipt(c)]
    others = {i["issue_id"]: set(p.included(i["issue_id"])) for i in p.issues if i["issue_id"] != iid}
    shared_cases = sorted(c for c in inc if any(c in s for s in others.values()))
    overlaps = sorted(({"issue_id": j, "shared_cases": len(set(inc) & s_), "jaccard": round(len(set(inc) & s_) /
                                                                                         len(set(inc) | s_), 3)}
                       for j, s_ in others.items() if inc and s_ and len(set(inc) & s_) / len(set(inc) | s_) >= 0.6),
                      key=lambda x: -x["jaccard"])
    return {"issue_id": iid, "definition_id": iss["definition_id"], "label": iss["label"], "level": iss["level"],
            "dimension": iss["dimension"],
            "membership": {k: _cell(len(v), "cases", v) for k, v in sorted(states.items())},
            "affected_members": _cell(len(members), "members", members),
            "open_included": _cell(len(open_inc), "cases", open_inc),
            "open_included_age_days": {"median": statistics.median(ages) if ages else None,
                                       "max": max(ages) if ages else None},
            "first_evidenced_by_case": onset,
            "discrimination": _discrimination(len(inc), len(elig) - len(states.get("unassessed", []))
                                              - len(states.get("no_memory", []))),
            "associated_burden": burden(p, set(inc)),
            "burden_shared_with_other_issues_cases": shared_cases,
            "overlapping_issues": overlaps,
            "overlap_note": ("largely the same included cases as another issue (Jaccard >= 0.6): possibly one "
                             "problem described twice, or two facets of one case population") if overlaps else None,
            "assessment_scope": {"eligible_cases": len(elig), "assessed": len(elig) - len(states.get("unassessed", []))
                                 - len(states.get("no_memory", [])),
                                 "not_eligible_cases_unassessed": sorted(c for c, v in p.cases.items()
                                                                         if not v["eligible"])}}


def _discrimination(included: int, assessed: int) -> dict:
    share = included / assessed if assessed else None
    broad = share is not None and assessed >= BROAD_MIN and share >= BROAD_SHARE
    return {"included_share": round(share, 3) if share is not None else None, "assessed": assessed,
            "state": "non_discriminating" if broad else "ok",
            "note": ("included in nearly every assessed case: the definition is probably too broad to separate an "
                     "operational problem; treat as a theme, not a specific issue") if broad else None}


def issue_series(p: Portfolio, iss: dict, grain: str) -> list[dict]:
    inc = set(p.included(iss["issue_id"]))
    out = []
    for label, s, e in temporal.buckets(temporal.OBSERVATION_START, min(temporal.OBSERVATION_END_EXCLUSIVE,
                                                                       p.view_date + timedelta(days=1)), grain):
        arr = [c for c in p.cases if (d := p.receipt(c)) and s <= d < e]
        assessed = [c for c in arr if p.cases[c]["eligible"] and c in p.memories
                    and (c, iss["issue_id"]) in p.memberships]
        num = sorted(c for c in assessed if c in inc)
        unc = sorted(c for c in assessed if p.memberships[(c, iss["issue_id"])]["state"] in ("uncertain", "conflicting"))
        first = sorted(c for c in inc if (fe := p.memberships[(c, iss["issue_id"])].get("first_evidenced"))
                       and s <= date.fromisoformat(fe) < e)
        out.append({"bucket": label, "affected_new_cases": _cell(len(num), "cases", num),
                    "share_of_assessed_arrivals": {"numerator": len(num), "denominator": len(assessed),
                                                   "value": round(len(num) / len(assessed), 4) if assessed else None,
                                                   "contributors": num, "uncertain": unc,
                                                   "unassessed": sorted(set(arr) - set(assessed))},
                    "first_evidenced": _cell(len(first), "cases", first)})
    return out


def alerts(p: Portfolio, iss: dict) -> dict:
    cur_s = p.view_date - timedelta(days=ALERT_RULE["current_days"] - 1)
    base_s = cur_s - timedelta(days=ALERT_RULE["baseline_days"])
    inc = set(p.included(iss["issue_id"]))

    def window(s, e):
        arr = [c for c in p.cases if (d := p.receipt(c)) and s <= d < e and p.cases[c]["eligible"]
               and (c, iss["issue_id"]) in p.memberships]
        return arr, sorted(c for c in arr if c in inc)
    cur_arr, cur_inc = window(cur_s, p.view_date + timedelta(days=1))
    base_arr, base_inc = window(base_s, cur_s)
    base_rate = len(base_inc) / (ALERT_RULE["baseline_days"] / ALERT_RULE["current_days"])
    if base_s < temporal.OBSERVATION_START or len(base_arr) < ALERT_RULE["min_baseline_assessed_arrivals"]:
        state = "insufficient_baseline"
    elif len(cur_inc) >= ALERT_RULE["min_current_new_cases"] and len(cur_inc) > base_rate:
        state = "emerging_concentration"
    else:
        state = "no_alert"
    persistent = [c for c in inc if p.state_at(c, p.knowledge_end)[0] in OPEN and p.receipt(c)
                  and (p.view_date - p.receipt(c)).days > 90]
    return {"issue_id": iss["issue_id"], "state": state, "rule": ALERT_RULE,
            "current_window": {"start": cur_s.isoformat(), "end_inclusive": p.view_date.isoformat(),
                               "assessed_arrivals": len(cur_arr), "affected": _cell(len(cur_inc), "cases", cur_inc)},
            "baseline_window": {"start": base_s.isoformat(), "end_exclusive": cur_s.isoformat(),
                                "assessed_arrivals": len(base_arr), "affected": _cell(len(base_inc), "cases", base_inc),
                                "affected_per_28_days": round(base_rate, 3)},
            "absolute_change": len(cur_inc) - round(base_rate, 3),
            "persistent_open_over_90_days": _cell(len(persistent), "cases", sorted(persistent)),
            "note": "newly discovered older cases are not new arrivals; windows use receipt dates"}


def experience(p: Portfolio) -> dict:
    contacts = [dict(c, case_id=cid) for cid, m in p.memories.items() for c in m.get("contacts", [])]
    effort = [dict(e, case_id=cid) for cid, m in p.memories.items() for e in m.get("member_effort", [])]
    cmts = [dict(c, case_id=cid) for cid, m in p.memories.items() for c in m.get("commitments", [])]
    resp = [dict(r, case_id=cid) for cid, m in p.memories.items() for r in m.get("responses", [])]
    handoffs = [dict(h, case_id=cid) for cid, m in p.memories.items() for h in m.get("handoffs", [])]

    def group(rows, key, idk):
        g = {}
        for r in rows:
            g.setdefault(r[key], []).append(r[idk])
        return {k: _cell(len(v), "occurrences", v) for k, v in sorted(g.items())}
    inbound = {}
    for c in contacts:
        if c["direction"] == "inbound":
            inbound.setdefault(c["case_id"], []).append(c["contact_id"])
    repeat = sorted(cid for cid, v in inbound.items() if len(v) >= 2)
    passed = [c["commitment_id"] for c in cmts if c["status"] in ("unknown", "pending") and c.get("due_date")
              and date.fromisoformat(c["due_date"]) < p.view_date]
    return {"contacts": {"by_outcome": group(contacts, "outcome", "contact_id"),
                         "by_channel": group(contacts, "channel", "contact_id"),
                         "by_direction": group(contacts, "direction", "contact_id"),
                         "cases_with_repeat_inbound_contact": _cell(len(repeat), "cases", repeat),
                         "note": "occasions from held correspondence and call summaries; copies are one occasion; "
                                 "an attempt or no-answer is not a completed call; no member minutes are inferred"},
            "member_effort": group(effort, "kind", "effort_id"),
            "commitments": {"by_status": group(cmts, "status", "commitment_id"),
                            "due_passed_without_confirmation": _cell(len(passed), "commitments", passed),
                            "note": "missing confirmation is unknown, not breach"},
            "responses": group([dict(r, key=f"{r['dimension']}:{r['position']}",
                                     rid=derived_id("RSP", r["case_id"], r["target"], r["claim_ids"])) for r in resp],
                               "key", "rid"),
            "handoffs": {"by_acknowledgement": group([dict(h) for h in handoffs], "acknowledged", "handoff_id"),
                         "by_outcome": group([dict(h) for h in handoffs], "outcome", "handoff_id"),
                         "note": "a forward does not prove acceptance; unknown ownership is not team fault"}}


def priorities(p: Portfolio, views: list[dict], alert_rows: dict[str, dict]) -> list[dict]:
    rows = []
    for v in views:
        a = alert_rows[v["issue_id"]]
        b = v["associated_burden"]
        rows.append({"issue_id": v["issue_id"], "label": v["label"], "level": v["level"],
                     "reach": {"cases": v["membership"].get("included", {"value": 0})["value"],
                               "members": v["affected_members"]["value"]},
                     "recent_change": {"state": a["state"], "current": a["current_window"]["affected"]["value"],
                                       "baseline_per_28_days": a["baseline_window"]["affected_per_28_days"]},
                     "persistence": {"open_included": v["open_included"]["value"],
                                     "median_open_age_days": v["open_included_age_days"]["median"],
                                     "open_over_90_days": a["persistent_open_over_90_days"]["value"]},
                     "recorded_burden": {"remedies_paid_cents": b["remedies_paid"]["value"],
                                         "complaint_expenses_paid_cents": b["complaint_expenses_paid"]["value"],
                                         "recorded_work_hours": b["recorded_work"]["hours"]},
                     "uncertainty": {"uncertain": v["membership"].get("uncertain", {"value": 0})["value"],
                                     "conflicting": v["membership"].get("conflicting", {"value": 0})["value"],
                                     "unassessed": v["membership"].get("unassessed", {"value": 0})["value"],
                                     "not_eligible": len(v["assessment_scope"]["not_eligible_cases_unassessed"])},
                     "discrimination": v["discrimination"]["state"],
                     "ordering_note": "specific (discriminating) issues first, then reach, then persistence; no "
                                      "hidden monetary score"})
    return sorted(rows, key=lambda r: (r["discrimination"] != "ok", -r["reach"]["cases"],
                                       -r["persistence"]["open_included"], r["issue_id"]))


def compute(p: Portfolio) -> dict:
    views = [issue_view(p, i) for i in p.issues]
    al = {i["issue_id"]: alerts(p, i) for i in p.issues}
    grains = ("day", "week", "month", "quarter", "observation_year")
    inc_cases = {c for i in p.issues for c in p.included(i["issue_id"])}
    elig = {c for c, v in p.cases.items() if v["eligible"]}
    res = {
        "measures_version": MEASURES_VERSION, "view_date": p.view_date.isoformat(),
        "knowledge_end_exclusive": p.knowledge_end.isoformat(), "temporal_policy": temporal.POLICY_ID,
        "overview": {"cases_received_to_date": _cell(len([c for c in p.cases if p.receipt(c)
                                                          and p.receipt(c) <= p.view_date]), "cases",
                                                     [c for c in p.cases if p.receipt(c) and p.receipt(c) <= p.view_date]),
                     "members": _cell(len({m for c in p.cases.values() for m in c["members"]}), "members",
                                      [m for c in p.cases.values() for m in c["members"]]),
                     "assessed_cases": _cell(len(p.memories), "cases", list(p.memories)),
                     "not_eligible_unassessed_cases": sorted(c for c, v in p.cases.items() if not v["eligible"]),
                     "backlog": backlog(p), "burden_total": burden(p),
                     "burden_unallocated_to_any_issue": burden(p, (elig - inc_cases) | {c for c, v in p.cases.items()
                                                                                      if not v["eligible"]})},
        "arrivals": {g: arrivals(p, g) for g in grains},
        "burden_series": {g: burden_series(p, g) for g in ("week", "month", "quarter", "observation_year")},
        "issues": views,
        "issue_series": {v["issue_id"]: {g: issue_series(p, i, g) for g in ("week", "month", "quarter",
                                                                           "observation_year")}
                         for v, i in zip(views, p.issues)},
        "alerts": list(al.values()), "priorities": priorities(p, views, al), "experience": experience(p),
        "calendar_year_note": "observation_year (1 Oct 2025 – 30 Sep 2026) is distinct from calendar_year; a single "
                              "observation year supports an annual total, not a year-on-year claim"}
    afca = getattr(p, "afca", None)
    if afca is not None:
        mcases = sorted({c for m in afca["matters"] for c in m["case_ids"]})
        by_stage: dict[str, list[str]] = {}
        for m in afca["matters"]:
            by_stage.setdefault(m["current_stage"], []).append(m["afca_matter_id"])
        res["afca"] = {**afca, "cases_with_external_matter": _cell(len(mcases), "cases", mcases),
                       "matters_by_current_stage": {k: _cell(len(v), "matters", v) for k, v in sorted(by_stage.items())},
                       "multi_case_matters": sorted(m["afca_matter_id"] for m in afca["matters"] if m["multi_case"]),
                       "burden_of_cases_with_external_matter": burden(p, set(mcases))}
    cont = getattr(p, "continuity", None)
    if cont is not None:
        res["continuity"] = cont
    res["results_digest"] = canonical_sha256(res)
    return res
