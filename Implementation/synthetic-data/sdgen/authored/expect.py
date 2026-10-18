"""Mechanical expectations from FINAL source-backed evidence (native case/party rows + financial/work/lifecycle tables
read from the delivered bytes), never from private intent.

Rules: an event counts at a checkpoint only if it occurred by the end of that day AND at least one source carrying it
was held by the end of that day (Australia/Melbourne). "By the end of day D" is half-open for exact instants (strictly
before the next local midnight) and inclusive for day-precision clocks dated D (`model.held_by`). Known event times
(`event_time`) order same-day events; date-only same-day events stay unordered, and anything that depends on their
order is reported as unknown rather than decided by id. Events are deduplicated by their own id, so copies and
re-exports never multiply totals; a shared event counts once and lists all its case links. Cases, members and AFCA
matters are separate populations; flows (arrivals, payments, work) are separate from stocks (open backlog); paid cash,
obligations and hours are separate measures and are never added together.

Case status uses explicit lifecycle rows only: confirmed open (last visible row received/reopened), confirmed closed,
or unknown (no visible lifecycle row, or same-day rows whose order decides the state). Unknown is never counted as
open; "received without evidenced closure" is reported separately. A letter or outcome text is not a lifecycle row.
Zero recorded work/cash means none recorded in the delivered holdings, not zero burden: coverage lists the arrived
cases with no work/financial rows. The annual grain is the OBSERVATION YEAR (the whole window), not calendar years."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .events import latest_candidates, lifecycle_states
from .model import held_by, melbourne_midnight

GRAINS = ("daily", "weekly", "monthly", "quarterly", "annual")
APPROVALS = ("approved", "agreed", "awarded", "amount_revision")


def end_of_day(d: date):
    return melbourne_midnight(d + timedelta(days=1))


def bucket(grain, d: date, window):
    if grain == "daily":
        return d.isoformat()
    if grain == "weekly":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if grain == "monthly":
        return f"{d.year}-{d.month:02d}"
    if grain == "quarterly":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return f"observation-year {window[0].isoformat()}..{(window[1] - timedelta(days=1)).isoformat()}"


def _visible(item, avail, cutoff_day):
    return held_by(item["clock"], cutoff_day) and any(held_by(avail[s], cutoff_day) for s in item["source_ids"])


def snapshot(cases, seen, avail, source_afca, cutoff: date):
    """State and cumulative flows as known at the end of `cutoff`."""
    ci = end_of_day(cutoff)
    arrived = sorted(c["case_id"] for c in cases if c["receipt"] is not None and c["receipt"] <= cutoff
                     and held_by(c["available"], cutoff))
    members = sorted({m for c in cases if c["case_id"] in arrived for m in c["members"]})
    fin = [f for f in seen["financial"].values() if not f.get("conflict") and _visible(f, avail, cutoff)]
    work = [w for w in seen["work"].values() if not w.get("conflict") and _visible(w, avail, cutoff)]
    life = [x for x in seen["lifecycle"].values() if not x.get("conflict") and _visible(x, avail, cutoff)]
    by_case = defaultdict(list)
    for x in life:
        by_case[x["case_id"]].append((x["clock"], x["lifecycle_event_id"], x["status"]))
    status, ambiguous_cases = {}, []
    for c in arrived:
        if not by_case.get(c):
            status[c] = "unknown"
            continue
        ends, _, amb = lifecycle_states(by_case[c])
        status[c] = ends.pop() if len(ends) == 1 else "unknown"
        if status[c] == "unknown":
            ambiguous_cases.append(c)
    confirmed_open = sorted(c for c in arrived if status[c] == "open")
    confirmed_closed = sorted(c for c in arrived if status[c] == "closed")
    unknown = sorted(c for c in arrived if status[c] == "unknown")
    ceilings, ambiguous_obs = {}, []
    by_ob = defaultdict(list)
    for f in fin:
        if f["state"] in APPROVALS:
            by_ob[f["obligation_id"]].append((f["clock"], f["event_id"], abs(f["amount"])))
    for ob, items in by_ob.items():
        last = latest_candidates(items)          # same rule as the payment ceiling check
        if len({t[2] for t in last}) == 1:
            ceilings[ob] = (last[0][2], sorted(t[1] for t in last))
        else:
            ambiguous_obs.append(ob)          # same-day approvals with different amounts and no known order
    pay = [f for f in fin if f["state"] == "payment"]
    rev = [f for f in fin if f["state"] in ("payment_reversal", "refund")]
    rec = [w for w in work if w["basis"] == "recorded"]
    matters = sorted({f["afca_matter_id"] for f in fin + work if f.get("afca_matter_id")}
                     | {m for s, ms in source_afca.items() if held_by(avail[s], cutoff) for m in ms})
    work_cases = {c for w in rec for c in w["case_ids"]}
    fin_cases = {c for f in fin for c in f["case_ids"]}
    return {
        "cutoff": f"{cutoff.isoformat()} end of day Australia/Melbourne (exact instants before {ci.isoformat()}; day-precision clocks dated on/before {cutoff.isoformat()})",
        "cases_arrived": {"value": len(arrived), "unit": "cases", "contributors": arrived},
        "members_with_arrived_cases": {"value": len(members), "unit": "members", "contributors": members},
        "open_backlog_confirmed": {"value": len(confirmed_open), "unit": "cases (stock)", "contributors": confirmed_open,
                                   "definition": "last visible lifecycle row is received or reopened"},
        "closed_confirmed": {"value": len(confirmed_closed), "unit": "cases (stock)", "contributors": confirmed_closed},
        "status_unknown": {"value": len(unknown), "unit": "cases (stock)", "contributors": unknown,
                           "no_visible_lifecycle_rows": sorted(c for c in unknown if c not in ambiguous_cases),
                           "same_day_order_unknown": sorted(ambiguous_cases)},
        "received_without_evidenced_closure": {"value": len(confirmed_open) + len(unknown), "unit": "cases (stock)",
                                               "contributors": sorted(confirmed_open + unknown),
                                               "definition": "confirmed open plus unknown status; NOT a confirmed-open backlog"},
        "lifecycle_coverage": {"cases_with_visible_lifecycle_rows": len(arrived) - len([c for c in arrived if not by_case.get(c)]),
                               "cases_arrived": len(arrived)},
        "paid_cash_gross_minor": _sum(pay, "event_id"),
        "payment_reversals_minor": _sum(rev, "event_id"),
        "paid_cash_net_minor": {"value": sum(f["amount"] for f in pay + rev), "unit": "AUD cents",
                                "contributors": sorted(f["event_id"] for f in pay + rev)},
        "approved_obligations_minor": {"value": sum(v for v, _ in ceilings.values()), "unit": "AUD cents",
                                       "definition": "latest visible approved/agreed/awarded/amount_revision per obligation by known time; not cash",
                                       "contributors": sorted(ceilings), "events": sorted(e for _, es in ceilings.values() for e in es),
                                       "excluded_order_unknown": sorted(ambiguous_obs)},
        "invoiced_minor": _sum([f for f in fin if f["state"] == "invoiced"], "event_id"),
        "requested_minor": _sum([f for f in fin if f["state"] == "requested"], "event_id"),
        "work_recorded_seconds": {"value": sum(w["seconds"] for w in rec), "unit": "seconds",
                                  "contributors": sorted(w["work_id"] for w in rec),
                                  "coverage": {"arrived_cases_without_recorded_work_rows": sorted(set(arrived) - work_cases),
                                               "note": "no recorded rows is not zero work; it is missing recording coverage"}},
        "work_planned_seconds_excluded": {"value": sum(w["seconds"] for w in work if w["basis"] != "recorded"),
                                          "unit": "seconds", "contributors": sorted(w["work_id"] for w in work if w["basis"] != "recorded")},
        "financial_coverage": {"arrived_cases_without_financial_rows": sorted(set(arrived) - fin_cases),
                               "note": "no financial rows is not zero cost; it is missing recording coverage"},
        "afca_matters_referenced": {"value": len(matters), "unit": "AFCA matters", "contributors": matters},
        "shared_events_counted_once": sorted({f["event_id"]: f["case_ids"] for f in fin if len(f["case_ids"]) > 1}.items()),
    }


def _sum(items, key):
    return {"value": sum(i["amount"] for i in items), "unit": "AUD cents", "contributors": sorted(i[key] for i in items)}


def grains(cases, seen, window):
    """Flows by event date at the final evidence cutoff, five grains, with exact contributors."""
    flows = {
        "cases_arrived": [(c["receipt"], c["case_id"], 1) for c in cases if c["receipt"] is not None],
        "paid_cash_gross_minor": [(f["day"], f["event_id"], f["amount"]) for f in seen["financial"].values()
                                  if not f.get("conflict") and f["state"] == "payment"],
        "payment_reversals_minor": [(f["day"], f["event_id"], f["amount"]) for f in seen["financial"].values()
                                    if not f.get("conflict") and f["state"] in ("payment_reversal", "refund")],
        "approval_events_minor": [(f["day"], f["event_id"], f["amount"]) for f in seen["financial"].values()
                                  if not f.get("conflict") and f["state"] in APPROVALS],
        "invoiced_minor": [(f["day"], f["event_id"], f["amount"]) for f in seen["financial"].values()
                           if not f.get("conflict") and f["state"] == "invoiced"],
        "work_recorded_seconds": [(w["day"], w["work_id"], w["seconds"]) for w in seen["work"].values()
                                  if not w.get("conflict") and w["basis"] == "recorded"],
        "case_closures": [(x["day"], x["lifecycle_event_id"], 1) for x in seen["lifecycle"].values() if x["status"] == "closed"],
        "case_reopenings": [(x["day"], x["lifecycle_event_id"], 1) for x in seen["lifecycle"].values() if x["status"] == "reopened"],
    }
    members_of = {c["case_id"]: c["members"] for c in cases}
    out, recon = {}, []
    for g in GRAINS:
        out[g] = {}
        for metric, items in flows.items():
            bk = defaultdict(lambda: {"value": 0, "contributors": []})
            for d, i, v in items:
                b = bk[bucket(g, d, window)]
                b["value"] += v
                b["contributors"].append(i)
            for b in bk.values():
                b["contributors"].sort()
            out[g][metric] = dict(sorted(bk.items()))
            total, ids = sum(v for _, _, v in items), sorted(i for _, i, _ in items)
            got = sorted(i for b in bk.values() for i in b["contributors"])
            recon.append({"grain": g, "metric": metric, "total": total, "bucket_sum": sum(b["value"] for b in bk.values()),
                          "contributors_equal": got == ids, "ok": got == ids and total == sum(b["value"] for b in bk.values())})
        mb = defaultdict(set)
        for d, cid, _ in flows["cases_arrived"]:
            mb[bucket(g, d, window)].update(members_of[cid])
        out[g]["members_with_new_cases"] = {k: {"value": len(v), "contributors": sorted(v)} for k, v in sorted(mb.items())}
    return out, recon


def compute(cases, seen, avail, source_afca, checkpoints, window):
    final = window[1] - timedelta(days=1)
    g, recon = grains(cases, seen, window)
    return {
        "kind": "mechanical-expectations",
        "basis": "final delivered bytes: native case/party rows and financial/work/lifecycle tables; manifest availability clocks",
        "rules": __doc__.split("\n\n", 1)[1].replace("\n", " "),
        "checkpoints": {c: snapshot(cases, seen, avail, source_afca, date.fromisoformat(c)) for c in checkpoints},
        "final": snapshot(cases, seen, avail, source_afca, final),
        "grains": g,
        "grain_definitions": {"daily": "Melbourne calendar date", "weekly": "ISO week (Monday start)", "monthly": "calendar month",
                              "quarterly": "calendar quarter", "annual": "the observation year (whole window), NOT calendar years"},
        "reconciliation": recon,
        "reconciled": all(r["ok"] for r in recon),
        "not_computed": ["causal driver judgements (private review only)", "AI outputs (not run)"],
    }
