"""Deterministic issue populations (DECISIONS §issue reach; R07/R10 baseline). No model.

Inputs are accepted analysis-set objects only: `finding` records (with `data.memberships[]`), the published
`taxonomy` (nodes, parent hierarchy, permitted finding ids) and accepted Shared `membership_interval` objects.

Rules:
* Only `included` memberships of a finding whose evidence state is `supported` support inclusion. Included
  memberships of an insufficient/conflicting finding, and `uncertain` memberships, are uncertain. `excluded`
  memberships contribute nothing. Uncertain cases are disclosed, never counted as included.
* A selected node reaches its descendants (parent reach). `none` = every node; `any` = case union; `all` =
  intersection; `exact` = each case's observed supported set at one taxonomy level equals the selection, with any
  additional uncertain membership at that level making the case uncertain (never "exclusively A").
* Modes: `all_linked_to_date` keeps supported (also historical) associations available by T;
  `active_in_window` needs an accepted interval overlapping the window; `active_at_endpoint` needs supported active
  state immediately before B. Missing completion is not an open-ended interval; an unknown boundary cannot support
  inclusion merely because the case is open. Without an accepted interval an active mode yields `uncertain`.
* Availability: findings by their envelope `available_at` (= assessed snapshot) and intervals by
  `assessed_evidence_as_of`, both ≤ the cell endpoint (`Inputs.assessment_available`): the endpoint's own
  assessment counts, a later diagnosis never classifies an earlier stock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .core import melbourne_midnight, parse_instant

MODES = ("active_in_window", "all_linked_to_date", "active_at_endpoint")
OPERATORS = ("none", "any", "all", "exact")


class IssueSelectionError(ValueError):
    """Invalid issue selection (→ INVALID_REQUEST)."""


@dataclass
class IssuePopulation:
    included: dict[str, list[dict]] = field(default_factory=dict)     # case_id → supporting refs
    uncertain: set[str] = field(default_factory=set)
    assessed: set[str] = field(default_factory=set)                   # any membership row at all (incl. excluded)
    used_ids: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    discovery_gap: list[str] = field(default_factory=list)

    def gate(self, case_ids) -> str:
        """Exact crosswalk for a ledger/work/case record: in if any linked case is included, uncertain if none is
        included but one is uncertain, otherwise out."""
        cs = set(case_ids or [])
        if cs & set(self.included):
            return "in"
        return "uncertain" if cs & self.uncertain else "out"


def _bounds(tv: dict | None) -> tuple[datetime | None, datetime | None]:
    """time_value → [lo, hi] instants; unknown → (None, None)."""
    if not tv:
        return None, None
    p = tv.get("precision")
    if p == "instant":
        t = parse_instant(tv["instant"])
        return t, t
    if p == "day":
        from datetime import date
        d = date.fromisoformat(tv["date"])
        return melbourne_midnight(d), melbourne_midnight(d + timedelta(days=1))
    if p == "interval":
        return parse_instant(tv["earliest"]), parse_instant(tv["latest"])
    return None, None


def _known_through(iv: dict) -> tuple[datetime | None, datetime | None, bool]:
    """(end_lo, end_hi, ended). No end + ongoing_at → known active through ongoing_at (open, not ended).
    No end and no ongoing_at → unknown completion (never open-ended)."""
    if iv.get("end"):
        lo, hi = _bounds(iv["end"])
        return lo, hi, True
    if iv.get("ongoing_at"):
        t = parse_instant(iv["ongoing_at"])
        return t, None, False
    return None, None, False


def interval_in_window(iv: dict, start: datetime, end: datetime) -> str:
    """supported | uncertain | none: does the interval overlap [start, end)?"""
    s_lo, s_hi = _bounds(iv.get("start"))
    e_lo, e_hi, ended = _known_through(iv)
    if (s_lo is not None and s_lo >= end) or (ended and e_hi is not None and e_hi <= start):
        return "none"
    if s_hi is not None and s_hi < end and e_lo is not None and e_lo > start:
        return "supported"
    return "uncertain"


def interval_at(iv: dict, b: datetime) -> str:
    """supported | uncertain | none: active immediately before B."""
    s_lo, s_hi = _bounds(iv.get("start"))
    e_lo, e_hi, ended = _known_through(iv)
    if (s_lo is not None and s_lo >= b) or (ended and e_hi is not None and e_hi < b):
        return "none"
    if s_hi is not None and s_hi < b and e_lo is not None and e_lo >= b:
        return "supported"
    return "uncertain"


class Taxonomy:
    def __init__(self, tax: dict | None):
        self.nodes = {n["node_id"]: n for n in (tax or {}).get("nodes", [])}
        self.children: dict[str, set[str]] = {}
        for n in self.nodes.values():
            if n.get("parent_node_id"):
                self.children.setdefault(n["parent_node_id"], set()).add(n["node_id"])
        self.finding_nodes: dict[str, set[str]] = {}
        for n in self.nodes.values():
            for fid in n.get("permitted_finding_ids", []):
                self.finding_nodes.setdefault(fid, set()).add(n["node_id"])

    def reach(self, node_id: str) -> set[str]:
        out, stack = set(), [node_id]
        while stack:
            n = stack.pop()
            if n not in out:
                out.add(n)
                stack.extend(self.children.get(n, ()))
        return out

    def ancestor_at(self, node_id: str, level: str) -> str | None:
        seen, n = set(), node_id
        while n and n not in seen:
            seen.add(n)
            if self.nodes.get(n, {}).get("level") == level:
                return n
            n = self.nodes.get(n, {}).get("parent_node_id")
        return None


def validate(f: dict, tax: Taxonomy, *, issue_measure: bool) -> None:
    op, ids = f.get("selection_operator", "none"), list(f.get("issue_ids") or [])
    if op not in OPERATORS:
        raise IssueSelectionError(f"unsupported selection_operator {op}")
    if f.get("issue_population_mode") not in MODES:
        raise IssueSelectionError("unsupported issue_population_mode")
    if op == "none" and ids:
        raise IssueSelectionError("issue_ids require selection_operator any/all/exact")
    if op != "none" and not ids:
        raise IssueSelectionError(f"selection_operator {op} requires issue_ids")
    unknown = [i for i in ids if i not in tax.nodes]
    if unknown:
        raise IssueSelectionError(f"issue_ids not in the accepted taxonomy: {unknown[:3]}")
    level = f.get("taxonomy_level")
    if op == "exact" and not level:
        raise IssueSelectionError("exact selection requires taxonomy_level")
    if level is not None:
        if not any(n["level"] == level for n in tax.nodes.values()):
            raise IssueSelectionError(f"taxonomy_level {level} is not in the accepted taxonomy")
        if any(tax.nodes[i]["level"] != level for i in ids):
            raise IssueSelectionError("every issue_id must be at the selected taxonomy_level")


def selected_nodes(f: dict, tax: Taxonomy) -> list[str]:
    """Explicit issue_ids; otherwise every node at taxonomy_level (or every node: reach covers descendants)."""
    if f.get("issue_ids"):
        return sorted(f["issue_ids"])
    level = f.get("taxonomy_level")
    return sorted(n for n, v in tax.nodes.items() if level is None or v["level"] == level)


def discovery_gap(aset: dict) -> list[str]:
    """Reasons the published issue discovery was not exhaustive (E09). A bounded investigation is never presented
    as an exhaustive population: with no recorded coverage at all the gap is stated too."""
    cov = aset.get("discovery_coverage")
    if cov is None:
        return ["no recorded discovery coverage for this analysis set; issue memberships are not exhaustive"] \
            if aset.get("taxonomy_version") else []
    reasons = list(cov.get("incomplete_reasons") or [])
    if not reasons and not cov.get("exhaustive"):
        reasons = ["bounded discovery: examined passages are a selected subset, not an exhaustive search"]
    return reasons


def load_taxonomy(rt, aset: dict) -> Taxonomy:
    return Taxonomy(rt.db.get_object("taxonomy", aset["taxonomy_version"]) if aset.get("taxonomy_version") else None)


def population(inp, f: dict, *, window: tuple[datetime, datetime], at: datetime) -> IssuePopulation:
    """Issue population for one cell. `inp` is an `Inputs` at the cell's cutoff/boundary; `window` the cell's
    activity window; `at` = T (all_linked_to_date) or B (active_at_endpoint)."""
    aset, rt = inp.aset, inp.rt
    tax = load_taxonomy(rt, aset)
    mode, op = f["issue_population_mode"], f.get("selection_operator", "none")
    pop = IssuePopulation()
    heads, ambiguous = inp.records("finding", "finding_id")
    if ambiguous:
        pop.notes.append(f"{len(ambiguous)} finding(s) with unresolved revisions ignored")
    intervals: dict[tuple[str, str], list[dict]] = {}
    for iid in aset.get("membership_interval_ids", []):
        iv = rt.db.get_object("membership_interval", iid)
        if iv is None or not inp.assessment_available(iv["assessed_evidence_as_of"]):
            continue
        intervals.setdefault((iv["finding_id"], iv["case_id"]), []).append(iv)
    # per case: node → best status ("included" > "uncertain")
    status: dict[str, dict[str, str]] = {}
    refs: dict[str, list[dict]] = {}
    rank = {"uncertain": 1, "included": 2}
    for fid, rec in sorted(heads.items()):
        d = rec["data"]
        nodes = tax.finding_nodes.get(fid, set())
        if not nodes:
            continue
        pop.used_ids.add(rec["envelope"]["record_id"])
        for m in d.get("memberships") or []:
            cid = m["case_id"]
            pop.assessed.add(cid)
            if m["state"] == "excluded":
                continue
            st = "included" if (m["state"] == "included" and d["evidence_state"] == "supported") else "uncertain"
            if st == "included" and mode != "all_linked_to_date":
                verdicts = [interval_in_window(iv, *window) if mode == "active_in_window" else interval_at(iv, at)
                            for iv in intervals.get((fid, cid), []) if iv["state"] == "included"]
                if "supported" in verdicts:
                    st = "included"
                elif "uncertain" in verdicts or not verdicts:
                    st = "uncertain"                 # no accepted interval: unknown boundaries never include
                else:
                    continue                         # accepted interval proves inactive here
            for n in nodes:
                cur = status.setdefault(cid, {})
                if rank[st] > rank.get(cur.get(n, ""), 0):
                    cur[n] = st
            if st == "included":
                refs.setdefault(cid, []).extend(m.get("evidence_refs") or [])
    sel = selected_nodes(f, tax)
    level = f.get("taxonomy_level")
    for cid, by_node in sorted(status.items()):
        if op == "exact":
            inc = {tax.ancestor_at(n, level) for n, s in by_node.items() if s == "included"} - {None}
            unc = {tax.ancestor_at(n, level) for n, s in by_node.items() if s == "uncertain"} - {None} - inc
            target = set(sel)
            if inc == target and not unc:
                verdict = "included"
            elif inc <= target <= inc | unc:
                verdict = "uncertain"                # e.g. observed A with uncertain B: never "exclusively A"
            else:
                verdict = "none"
        else:
            per = []
            for s in sel:
                reach = tax.reach(s)
                vals = {by_node[n] for n in reach if n in by_node}
                per.append("included" if "included" in vals else "uncertain" if vals else "none")
            if op == "all":
                verdict = ("included" if all(p == "included" for p in per) else
                           "uncertain" if all(p != "none" for p in per) else "none")
            else:
                verdict = ("included" if "included" in per else "uncertain" if "uncertain" in per else "none")
        if verdict == "included":
            pop.included[cid] = refs.get(cid, [])[:20]
        elif verdict == "uncertain":
            pop.uncertain.add(cid)
    pop.discovery_gap = discovery_gap(aset)
    pop.notes += [f"discovery incomplete: {r}" for r in pop.discovery_gap]
    if mode != "all_linked_to_date" and not aset.get("membership_interval_ids"):
        pop.notes.append(f"{mode}: no accepted membership intervals; supported links are uncertain, not included")
    return pop
