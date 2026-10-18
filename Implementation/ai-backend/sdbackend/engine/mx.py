"""Deterministic V11/V12 services over accepted records (MEMBER-EXPERIENCE.md; registry rules). No model.

Inputs: accepted Shared MX objects (`commitment`, `member_effort_event`, `member_outcome_response`) published in an
analysis set as `mx_record_ids`, and accepted runtime `contact_link` records. Absent evidence stays unknown.

* `classify_original_due` re-derives the six original-due states from the deadline, applicability, cancellations
  and fulfilment observations at the cutoff (never trusting a stored label): on time only if every possible
  completion instant is on time; late only if every possible instant is late; a straddle is unknown; missing
  confirmation never proves failure; a post-deadline cancellation cannot erase the opportunity.
* Repeat valid-evidence requests count an institution request occasion once when at least one child comparison is
  an eligible repeat with recognised validity before the request and a supported later order.
* Expressed positions count canonical (response occasion, identified target, dimension) relationships once;
  unidentified or uncertain links are disclosed candidates, never confirmed totals; silence creates nothing.
* Contacts: repeat contacts are supported same-episode complainant/representative repeats; post-closure returns
  need an evidenced closure strictly before the contact and a supported same-problem episode link.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .core import MELBOURNE, melbourne_midnight, parse_instant

OUTCOMES = ("on_time", "late", "explicitly_unmet", "not_yet_due", "not_applicable", "unknown")
POSITIONS = ("accept", "disagree", "unresolved", "update_only", "unknown", "conflicting")
DIMENSIONS = ("explanation", "decision", "handling", "service")


def bounds(tv: dict | None) -> tuple[datetime | None, datetime | None]:
    """time_value → (lo, hi): instant → [t, t]; day → [midnight, next midnight) (hi exclusive); interval
    inclusive; unknown → (None, None)."""
    if not tv:
        return None, None
    p = tv.get("precision")
    if p == "instant":
        t = parse_instant(tv["instant"])
        return t, t
    if p == "day":
        d = date.fromisoformat(tv["date"])
        return melbourne_midnight(d), melbourne_midnight(d + timedelta(days=1))
    if p == "interval":
        return parse_instant(tv["earliest"]), parse_instant(tv["latest"])
    return None, None


def _all_before(tv: dict, b: datetime, inclusive: bool) -> bool | None:
    """Every possible instant of tv on the 'on time' side of boundary b? None when unknown."""
    lo, hi = bounds(tv)
    if hi is None:
        return None
    if tv.get("precision") == "day":       # half-open [lo, hi): every instant is below hi
        return hi <= b
    return hi <= b if inclusive else hi < b


def _all_after(tv: dict, b: datetime, inclusive: bool) -> bool | None:
    lo, _ = bounds(tv)
    if lo is None:
        return None
    return lo > b if inclusive else lo >= b


def classify_original_due(c: dict, cutoff: datetime) -> tuple[str, str]:
    """→ (state, reason); state 'ineligible' when the original due is not interpreted (outside dated cohorts)."""
    due = c["original_due"]
    if due["interpretation_state"] != "interpreted" or not due.get("boundary"):
        return "ineligible", f"original due not interpreted ({due['interpretation_state']})"
    b = parse_instant(due["boundary"])
    inclusive = due["boundary_kind"] == "inclusive_instant"
    app = c["original_opportunity_applicability"]
    if app in ("cancelled_before_opportunity", "condition_not_activated"):
        if app == "condition_not_activated":
            return "not_applicable", "condition proven not activated"
        early = [x for x in c.get("cancellations", []) if _all_before(x["time"], b, inclusive)]
        return ("not_applicable", "positive cancellation before the opportunity") if early else \
            ("unknown", "cancellation not proven before the original opportunity")
    if app in ("unknown", "conflicting"):
        return "unknown", f"applicability {app}"
    obs = [o for o in c.get("fulfilment_observations", [])
           if (bounds(o["completion_time"])[0] is None or bounds(o["completion_time"])[0] < cutoff)]
    if any(o["fulfils_criteria"] == "conflicting" for o in obs):
        return "unknown", "material fulfilment conflict"
    done = [o for o in obs if o["fulfils_criteria"] == "supported" and o["supporting_refs"]]
    if done:
        verdicts = []
        for o in done:
            if _all_before(o["completion_time"], b, inclusive):
                verdicts.append("on_time")
            elif _all_after(o["completion_time"], b, inclusive):
                verdicts.append("late")
            else:
                verdicts.append("unknown")
        if "on_time" in verdicts:
            return "on_time", "supported fulfilment on or before the original due"
        if all(v == "late" for v in verdicts):
            return "late", "supported fulfilment after the original due"
        return "unknown", "completion time straddles the original due"
    elapsed = cutoff > b if inclusive else cutoff >= b
    if not elapsed:
        return "not_yet_due", "original due not yet elapsed at the cutoff"
    unmet = [o for o in obs if o["fulfils_criteria"] == "excluded" and o["supporting_refs"]]
    if unmet:
        return "explicitly_unmet", "explicit nonfulfilment evidence after the elapsed due"
    return "unknown", "due elapsed; fulfilment not evidenced (missing confirmation is not failure)"


def in_cohort(due: dict, start: datetime, end: datetime) -> bool:
    """Day anchors compare the promised local date with the query's local half-open dates; instant anchors
    compare the instant with the half-open window."""
    a = due.get("cohort_anchor") or {}
    if a.get("precision") == "day":
        d = date.fromisoformat(a["date"])
        return start.astimezone(MELBOURNE).date() <= d < end.astimezone(MELBOURNE).date()
    if a.get("precision") == "instant":
        return start <= parse_instant(a["instant"]) < end
    return False


def window_position(tv: dict, start: datetime, end: datetime) -> str:
    lo, hi = bounds(tv)
    if lo is None:
        return "unknown"
    if tv.get("precision") == "instant":
        return "in" if start <= lo < end else "out"
    if lo >= start and hi <= end:
        return "in"
    if hi <= start or lo >= end:
        return "out"
    return "straddle"


def request_qualifies(e: dict) -> str:
    """supported | uncertain | excluded for a repeat valid-evidence institution request occasion."""
    if e["effort_kind"] != "repeat_valid_evidence_request" or e["occasion_type"] != "institution_request":
        return "excluded"
    if e["common"]["evidence_state"] == "excluded" or e["repetition_state"] == "excluded":
        return "excluded"
    eligible = [i for i in e["item_comparisons"] if i["comparison_state"] == "eligible_repeat"
                and i["validity_state"] == "recognised_valid_before_request" and i["order_state"] == "supported_later"
                and i["item_reference"] and i["requirement_reference"]]
    if eligible and e["repetition_state"] == "supported" and e["common"]["evidence_state"] == "supported":
        return "supported"
    if any(i["comparison_state"] in ("uncertain", "eligible_repeat") or i["order_state"] == "unknown"
           or i["validity_state"] in ("not_established", "conflicting") for i in e["item_comparisons"]):
        return "uncertain"
    return "excluded"                                   # legitimate new/changed/expired/incomplete requirements


def response_status(r: dict) -> str:
    """confirmed | candidate | excluded."""
    if r["common"]["evidence_state"] == "excluded" or r["target_link_state"] == "excluded":
        return "excluded"
    if (r["target_identity_state"] == "identified" and r["target_link_state"] == "supported"
            and r["dimension"] in DIMENSIONS and r["common"]["evidence_state"] in ("supported", "conflicting")):
        return "confirmed"
    return "candidate"


def contact_day(d: dict) -> dict | None:
    if d.get("event_at"):
        return {"precision": "instant", "instant": d["event_at"]}
    if d.get("event_date"):
        return {"precision": "day", "date": d["event_date"]}
    return None
