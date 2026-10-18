"""Deterministic, evidence-cited AFCA procedure and problem-continuity producers (A09/A10, review V07). No model.

AFCA (A10). A matter exists only through a HELD factual association: an original whose delivery metadata names the
external matter id. Stage evidence comes only from exact, conservative phrases in those associated sources, each
cited by its exact span:
- registration_referral: the external body's notice of a received complaint or referral;
- decision: a determination notice;
- closed: the external body's own closure record;
- the closure route is withdrawn or determination only when the closing source says so.

A member's withdrawal email is a notice, not a closure. Threats or mentions without the factual association are
never matters. Unknown stage or route is retained as unknown.

Continuity (A09).
- Administrative intervals come from lifecycle transitions.
- A closed interval is completed only when a recorded completion claim for a NAMED service action falls inside it.
  Otherwise it is a closure without completion evidence.
- Reopening keeps the case. The same underlying problem continues across a reopen only when one factor is
  supported on both sides of it; otherwise continuity is uncertain.
- Separate cases of the same member share a problem only on runtime evidence: a shared external matter, an
  original associated with both cases, or one case's claim naming the other case's id. Otherwise continuity is
  uncertain, and is never counted as a new episode. Private episode ids or totals are never used."""
from __future__ import annotations

import re

from ..canonical import derived_id
from ..citations import text_ref

PROCEDURES_VERSION = "sdb-intel-procedures-1.0.0"
STAGE_RULES = (
    ("registration_referral", re.compile(r"we have received [^.]{0,80}?complaint|is notified of (?:an )?external "
                                         r"matter|\breferral notice\b|\bnew matter notice\b|notice to financial firm",
                                         re.I)),
    ("decision", re.compile(r"\bthe determination (?:is|awards|finds|requires)\b|\bdetermination notification\b", re.I)),
    ("closed", re.compile(r"\b(?:our |the )?file is closed\b|\bmatter (?:is|has been) closed\b|\bclosure following "
                          r"withdrawal\b", re.I)),
)
WITHDRAWAL_NOTICE = re.compile(r"\bI am withdrawing this matter\b|\bhas withdrawn the matter\b", re.I)
ROUTE_WITHDRAWN = re.compile(r"\bclosure following withdrawal\b|\bhas withdrawn the matter\b", re.I)
ROUTE_DETERMINATION = re.compile(r"\bclosed\b[^.]{0,60}\bdetermination\b(?![^.]{0,40}\bnot\b)", re.I)
NOT_DETERMINATION = re.compile(r"\bnot a determination\b", re.I)


def _span(src: dict, m: re.Match) -> dict:
    d = src["data"]
    return {"ref": text_ref(d, m.start(), m.end()), "quote": d["text"][m.start():m.end()]}


def afca_matters(ctx, records: list[dict]) -> dict:
    """→ {"matters": [...], "not_assessed_matters": n, "note"} at the context's knowledge view."""
    rows = [r for r in ctx.crosswalk["rows"] if r["afca_matter_ids"]]
    eligible = set(ctx.eligible)
    held: dict[str, dict] = {}
    for cid in sorted(eligible):
        if not ctx.in_view(cid):
            continue
        inp = ctx.inputs(cid)
        for s in inp.narrative:
            held[s["data"]["source_id"]] = {"src": s, "day": inp.source_day(s)}
    matters: dict[str, dict] = {}
    not_assessed = set()
    for r in rows:
        for mid in r["afca_matter_ids"]:
            cases = set(r["case_ids"])
            if cases and not cases <= eligible:
                not_assessed.add(mid)
            sids = [s for s in r["source_ids"] if s in held]
            if not sids and not (cases & eligible):
                continue
            m = matters.setdefault(mid, {"afca_matter_id": mid, "case_ids": set(), "association_refs": [],
                                         "events": []})
            m["case_ids"] |= cases & eligible
            for sid in sids:
                h = held[sid]
                src, day = h["src"], h["day"]
                m["association_refs"].append({"source_id": sid, "original_id": r["original_id"], "day": day})
                text = src["data"]["text"]
                for stage, rx in STAGE_RULES:
                    mt = rx.search(text)
                    if mt:
                        ev = {"stage": stage, "date": day, "available_at": src["envelope"]["available_at"],
                              "source_id": sid, **_span(src, mt)}
                        if stage == "closed":
                            ev["route"] = ("withdrawn" if ROUTE_WITHDRAWN.search(text) else
                                           "determination" if ROUTE_DETERMINATION.search(text) and
                                           not NOT_DETERMINATION.search(text) else "unknown")
                        m["events"].append(ev)
                wn = WITHDRAWAL_NOTICE.search(text)
                if wn:
                    m["events"].append({"stage": "withdrawal_notice", "date": day, "source_id": sid,
                                        "available_at": src["envelope"]["available_at"], **_span(src, wn)})
    out = []
    for mid, m in sorted(matters.items()):
        if mid in not_assessed and not m["case_ids"]:
            continue
        evs = sorted(m["events"], key=lambda e: (e["date"] or "", e["stage"]))
        stages = [e for e in evs if e["stage"] in ("registration_referral", "decision", "closed")]
        closed = [e for e in stages if e["stage"] == "closed"]
        regs = [e["date"] for e in stages if e["stage"] == "registration_referral"]
        cases = sorted(m["case_ids"])
        out.append({"afca_matter_id": mid, "case_ids": cases, "multi_case": len(cases) > 1,
                    "received_date": min(regs) if regs else None,
                    "current_stage": stages[-1]["stage"] if stages else "unknown",
                    "closed_date": closed[0]["date"] if closed else None,
                    "closure_route": closed[0]["route"] if closed else "not_closed" if stages else "unknown",
                    "stage_history": [{k: e[k] for k in ("stage", "date", "available_at", "source_id", "ref", "quote")
                                       if k in e} | ({"route": e["route"]} if "route" in e else {}) for e in evs],
                    "association_refs": sorted(m["association_refs"], key=lambda x: (x["day"] or "", x["source_id"])),
                    "stage_evidence": "cited" if stages else "association_only_stage_unknown"})
    return {"version": PROCEDURES_VERSION, "matters": out, "not_assessed_matters": len(not_assessed - set(matters)),
            "holdout_or_ineligible_matters_unassessed": sorted(not_assessed),
            "note": "matters exist only through held factual associations; stages only from cited source phrases; "
                    "a member's withdrawal email is a notice, not a closure; unknown stays unknown"}


def _intervals(transitions: list[dict]) -> list[dict]:
    trans = sorted((t for t in transitions if t.get("effective_date")),
                   key=lambda t: (t.get("effective_at") or t["effective_date"], t["available_at"]))
    out, cur = [], None
    for t in trans:
        if t["state"] in ("open", "reopened", "awaiting") and cur is None:
            cur = {"start": t["effective_date"], "opened_as": t["state"], "end": None,
                   "evidence_refs": list(t["evidence_refs"])}
        elif t["state"] == "closed" and cur is not None:
            cur.update(end=t["effective_date"], evidence_refs=cur["evidence_refs"] + list(t["evidence_refs"]))
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def continuity(cases: dict, memories: dict, crosswalk: dict, matters: list[dict]) -> dict:
    """→ per-case administrative intervals with completion matching, reopen continuity, and member-level
    evidence-linked vs uncertain repeat-case continuity."""
    per_case = {}
    for cid, c in sorted(cases.items()):
        if not c.get("eligible"):
            continue                         # holdout/ineligible: lifecycle stays in its own case view only
        m = memories.get(cid)
        ivs = _intervals(c["transitions"])
        claims = {x["claim_id"]: x for x in (m or {}).get("claims", [])}
        completions = []
        for it in ((m or {}).get("service") or {}).get("items", []):
            for clid in it.get("completion_claim_ids") or []:
                if clid in claims:
                    completions.append({"action": it["action"], "claim_id": clid,
                                        "day": claims[clid]["business_date"] or claims[clid]["source_day"]})
        for iv in ivs:
            inside = [x for x in completions if x["day"] and iv["start"] <= x["day"] and
                      (iv["end"] is None or x["day"] <= iv["end"])]
            iv["completion"] = ("recorded_completion" if inside else
                                "closure_without_completion_evidence" if iv["end"] else "open_or_unknown")
            iv["completed_actions"] = [{"action": x["action"], "claim_id": x["claim_id"]} for x in inside]
        reopens = []
        for a, b in zip(ivs, ivs[1:]):
            if a["end"] is None:
                continue
            both = []
            for f in (m or {}).get("factors", []):
                days = sorted(claims[i]["business_date"] or claims[i]["source_day"] for i in f["support_claim_ids"]
                              if i in claims)
                if days and days[0] <= a["end"] and days[-1] >= b["start"]:
                    both.append({"factor_id": f["factor_id"], "label": f["label"]})
            reopens.append({"closed": a["end"], "reopened": b["start"],
                            "continuity": "same_problem_continued" if both else "uncertain",
                            "spanning_factors": both})
        per_case[cid] = {"intervals": ivs, "reopens": reopens, "assessed": m is not None}
    # member level: repeat cases
    by_member: dict[str, list[str]] = {}
    for cid, c in cases.items():
        if not c.get("eligible"):
            continue                         # holdout/ineligible cases stay outside derived continuity
        for mem in c["members"]:
            by_member.setdefault(mem, []).append(cid)
    narrative = {cid: {s["source_id"] for s in m["inputs"]["narrative_sources"]} for cid, m in memories.items()}
    # only narrative originals count; a financial/work export listing many cases is not shared-problem evidence
    shared_rows = [r for r in crosswalk.get("rows", []) if len(r["case_ids"]) > 1 and r["source_family"] != "S10"
                   and "export" not in str(r.get("semantic_type") or "").lower()]
    matter_cases = [set(m["case_ids"]) for m in matters]
    links = []
    for mem, cids in sorted(by_member.items()):
        cids = sorted(cids, key=lambda x: (cases[x]["receipt_date"] or "", x))
        for i, a in enumerate(cids):
            for b in cids[i + 1:]:
                basis = []
                for mc, mt in zip(matter_cases, matters):
                    if {a, b} <= mc:
                        basis.append({"basis": "shared_external_matter", "afca_matter_id": mt["afca_matter_id"]})
                for r in shared_rows:
                    held = set(r["source_ids"]) & (narrative.get(a, set()) | narrative.get(b, set()))
                    if {a, b} <= set(r["case_ids"]) and held:
                        basis.append({"basis": "original_associated_with_both", "original_id": r["original_id"],
                                      "source_ids": r["source_ids"]})
                for x, y in ((a, b), (b, a)):
                    for cl in (memories.get(x) or {}).get("claims", []):
                        if y in cl["quote"]:
                            basis.append({"basis": "claim_names_other_case", "case_id": x,
                                          "claim_id": cl["claim_id"]})
                links.append({"continuity_id": derived_id("CONT", a, b), "member_reference": mem,
                              "case_ids": [a, b], "state": "evidence_linked" if basis else "uncertain",
                              "basis": basis[:10]})
    repeat_members = sorted(m for m, c in by_member.items() if len(c) > 1)
    return {"version": PROCEDURES_VERSION, "cases": per_case, "repeat_case_links": links,
            "summary": {
                "members_with_repeat_cases": len(repeat_members),
                "repeat_case_pairs_evidence_linked": sum(l["state"] == "evidence_linked" for l in links),
                "repeat_case_pairs_uncertain": sum(l["state"] == "uncertain" for l in links),
                "reopened_cases": sorted(c for c, v in per_case.items() if v["reopens"]),
                "closed_intervals_without_completion_evidence": sorted(
                    c for c, v in per_case.items() if any(i["completion"] == "closure_without_completion_evidence"
                                                          for i in v["intervals"])),
                "closed_intervals_with_recorded_completion": sorted(
                    c for c, v in per_case.items() if any(i["completion"] == "recorded_completion"
                                                          for i in v["intervals"]))},
            "note": "continuity is linked only on runtime evidence; uncertain pairs are not new problems; no "
                    "private episode identifiers or totals are used"}
