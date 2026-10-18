"""Deterministic source-grounded producers (E07, no model): grounded S02 typed facts, source metadata and staged S03
administrative transitions → AFCA matters/links, episodes and contact continuity, commitments and repeat
valid-evidence request occasions. Every output cites the exact evidence it rests on; anything not established
stays unknown/uncertain. Nothing here reads Data's private plans, gold or review crosswalks.

Rules (MEMBER-EXPERIENCE.md, DECISIONS §AFCA/§contacts):
* AFCA identity: a grounded `afca_reference` (the external body's own matter reference) names one matter per case
  set; `afca_stage`/`afca_closure_route` facts on a source carrying that reference add dated stages. A matter is
  versioned by evidence availability (each later-available stage supersedes the earlier version), so an earlier
  cutoff never sees a later stage or closure. Received date = earliest registration/referral stage date (never the
  case's own receipt). A reference without any stage evidence stays unresolved (no matter, no link); a matter is
  never inferred from a fee category, a phase label or a work row.
* Episodes: per case, from S03 administrative transitions with cited dates: an episode opens at the first open/
  reopened transition and ends at the next closure (exclusive end = next local midnight of a date-only closure).
  Closure without independent service-completion evidence is `closure_without_completion_evidence`.
* Contacts: every member/representative-authored communication source (by the source's own author role) is one
  contact; reference-coded contacts from S02 are kept and the same source is never counted twice. Repeat state:
  first member contact in an episode `first`, later ones `repeat`; outside any evidenced episode or without a date
  `uncertain`. A contact after a closure keeps the closed episode (same case) for post-closure review signals.
* Commitments: a grounded `commitment` (undertaking text) on a source; due from a grounded `commitment_due_date`
  (day precision → exclusive next-local-day boundary, day cohort anchor); otherwise uninterpreted. Promisor from
  the source's author: institution staff → direct words; an external dispute body's record → captured institution
  undertaking; a member's account → report-only. Scope defaults to member-facing; internal only when every
  addressee is an institution address. One occurrence per (case, promisor side, due date | normalised action);
  later mentions add citations. Fulfilment is not inferred from wording similarity (missing confirmation is never
  failure), so outcomes are re-derived by `mx.classify_original_due` at query time.
* Repeat valid-evidence requests: an institution `dependency` fact naming an item that already has a grounded earlier
  `evidence_received` for the same item. Recognised validity needs an earlier grounded `evidence_validity` = valid;
  otherwise the child stays `uncertain`/`not_established`. Item/requirement references are the source's own codes
  (subject / `routing_reference`), never manufactured; without them the child cannot be an eligible repeat.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..canonical import derived_id, utc_now_iso
from ..policy import record_access
from .core import MELBOURNE, melbourne_midnight, parse_instant

PRODUCERS_VERSION = "sdb-producers-1.1.1"
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
OPEN_STATES = ("open", "reopened")
AFCA_STAGES = ("registration_referral", "rules_assessment", "case_management", "conciliation",
               "preliminary_assessment", "decision", "closed", "unknown")
CLOSURE_ROUTES = ("negotiation_agreement", "conciliation_agreement", "determination", "withdrawn", "other")
MEMBER_ROLES = ("member", "complainant", "claimant")


# ------------------------------------------------------------------ helpers
def _norm(v) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def _known(facts: dict, key: str) -> list[dict]:
    return [f for f in facts.get(key, []) if f["data"]["value"]["state"] in ("known", "partial")]


def _one(facts: dict, key: str):
    vals = _known(facts, key)
    distinct = {repr(f["data"]["value"]["value"]) for f in vals}
    return vals[0] if len(distinct) == 1 else None


def _ref(f: dict) -> dict:
    return f["data"]["value"]["evidence_refs"][0]


def _day(f: dict, src: dict | None) -> str | None:
    return f["data"].get("event_date") or _source_day(src)


def _source_day(src: dict | None) -> str | None:
    if src is None:
        return None
    at = src["envelope"].get("occurred_at") or src["envelope"].get("available_at")
    return parse_instant(at).astimezone(MELBOURNE).date().isoformat() if at else None


def author_side(src: dict | None) -> str:
    """institution | member | representative | external | unknown, from the source's own recorded author."""
    if src is None:
        return "unknown"
    d = src["data"]
    ref, role = (d.get("author_reference") or ""), _norm(d.get("author_role"))
    if ref.startswith("STAFF-") or any(w in role for w in ("case manager", "administration", "finance", "delegate",
                                                             "complaints team", "staff", "intake")):
        return "institution"
    if "representative" in role:
        return "representative"
    if any(w in role.split() for w in MEMBER_ROLES):
        return "member"
    if "external" in role:
        return "external"
    return "unknown"


def _addressees(text: str) -> list[str] | None:
    """Domains of To/Cc addressees from an email header block; None when the source has no such header."""
    head = text.split("\n\n", 1)[0]
    lines = [l for l in head.splitlines() if l.lower().startswith(("to:", "cc:"))]
    if not lines:
        return None
    return [m.group(1).lower() for l in lines for m in EMAIL.finditer(l)]


def institution_domains(sources: list[dict]) -> set[str]:
    """Sender domains of institution-authored emails (the declared institution's own addresses)."""
    out = set()
    for s in sources:
        if author_side(s) == "institution":
            head = s["data"]["text"].split("\n\n", 1)[0]
            for l in head.splitlines():
                if l.lower().startswith("from:"):
                    out |= {m.group(1).lower() for m in EMAIL.finditer(l)}
    return out


def _record(kind: str, rid: str, logical: str, available_at: str, refs: list, data: dict,
            supersedes: str | None = None) -> dict:
    return {"schema_version": "0.2.0", "kind": kind,
            "envelope": {"record_id": rid, "logical_id": logical, "supersedes_record_id": supersedes,
                         "recorded_at": available_at, "available_at": available_at, "occurred_at": None,
                         "occurrence_date": None, "access": record_access(), "evidence_refs": refs}, "data": data}


def _bindings(refs: list[dict]) -> list[dict]:
    out = []
    for r in refs:
        b = {"source_id": r["source_id"], "content_sha256": r["content_sha256"]}
        if b not in out:
            out.append(b)
    return out


def _common(analysis_id: str, logical: str, cases: list[str], event_day: str | None, refs: list[dict], *,
            evidence_state: str, attribution: str, assessed: str, now: str) -> dict:
    return {"analysis_id": analysis_id, "logical_occurrence_id": logical, "version": "v1",
            "supersedes_analysis_id": None, "case_ids": sorted(cases), "member_reference": None,
            "member_identity_state": "unknown", "member_life_state": "unknown", "problem_reference": None,
            "problem_link_state": "uncertain",
            "event_time": {"precision": "day", "date": event_day} if event_day else
            {"precision": "unknown", "reason": "no evidenced date"},
            "source_bindings": _bindings(refs), "supporting_refs": refs, "contradictory_refs": [],
            "evidence_state": evidence_state, "attribution": attribution, "context_id": "STAGED-S04",
            "assessed_evidence_as_of": assessed, "computed_at": now, "published_at": now,
            "policy": {"access": record_access(), "entitlement_policy_id": "sdb-local-policy",
                       "permission_revision": "staged"},
            "review_state": "not_reviewed"}


def _unknown_due(wording: str, refs: list, reason: str) -> dict:
    return {"wording": wording[:300], "evidence_refs": refs, "interpretation_state": "unknown", "boundary": None,
            "boundary_kind": "unknown", "cohort_anchor": None, "timezone": "Australia/Melbourne",
            "precision": "unknown", "calendar_id": None, "calendar_version": None, "interpretation_basis": reason}


def _day_due(wording: str, refs: list, day: str) -> dict:
    d = date.fromisoformat(day)
    return {"wording": wording[:300], "evidence_refs": refs, "interpretation_state": "interpreted",
            "boundary": melbourne_midnight(d + timedelta(days=1)).isoformat(),
            "boundary_kind": "exclusive_next_local_day", "cohort_anchor": {"precision": "day", "date": day},
            "timezone": "Australia/Melbourne", "precision": "day", "calendar_id": None, "calendar_version": None,
            "interpretation_basis": "absolute local date written in the cited undertaking/due quote"}


# ------------------------------------------------------------------ AFCA
def afca(groups: dict, sources: dict, unresolved: list) -> list[dict]:
    """→ afca_matter (versioned by evidence availability) + afca_link records."""
    matters: dict[str, dict] = {}
    for (sid, entity), g in sorted(groups.items()):
        ref_f = _one(g["facts"], "afca_reference")
        if ref_f is None:
            continue
        reference = str(ref_f["data"]["value"]["value"]).strip()
        if not IDENT.match(reference):
            unresolved.append({"source_id": sid, "subject": entity, "reason": f"{PRODUCERS_VERSION}: AFCA reference "
                               "is not a usable identifier; no matter record"})
            continue
        src = sources.get(sid)
        m = matters.setdefault(reference, {"cases": set(), "events": [], "refs": []})
        m["cases"] |= set(g["case_ids"])
        m["refs"].append({"at": g["available_at"], "ref": _ref(ref_f)})
        for f in _known(g["facts"], "afca_stage"):
            stage = str(f["data"]["value"]["value"]).strip().lower()
            if stage not in AFCA_STAGES:
                continue
            m["events"].append({"stage": stage, "day": f["data"].get("event_date"), "at": g["available_at"],
                                "ref": _ref(f)})
        route_f = _one(g["facts"], "afca_closure_route")
        if route_f is not None:
            route = str(route_f["data"]["value"]["value"]).strip().lower()
            if route in CLOSURE_ROUTES:
                m["events"].append({"route": route, "day": route_f["data"].get("event_date") or _day(route_f, src),
                                    "at": g["available_at"], "ref": _ref(route_f)})
    out = []
    for reference, m in sorted(matters.items()):
        if not any("stage" in e for e in m["events"]):
            continue                    # a reference without receipt/stage evidence is not a matter (R10 ruling)
        mid = reference
        steps = sorted({x["at"] for x in m["refs"]} | {e["at"] for e in m["events"]})
        prev = None
        for at in steps:
            evs = [e for e in m["events"] if e["at"] <= at]
            refs = [x["ref"] for x in m["refs"] if x["at"] <= at]
            if not refs:
                continue                                     # identity not yet evidenced at this availability
            history = []
            for e in sorted((e for e in evs if "stage" in e), key=lambda e: (e["day"] or "", e["stage"])):
                h = {"stage": e["stage"], "effective_at": None, "effective_date": e["day"], "available_at": e["at"],
                     "source_refs": [e["ref"]]}
                if h not in history:
                    history.append(h)
            routes = sorted({e["route"] for e in evs if "route" in e})
            closed = [h for h in history if h["stage"] == "closed" and h["effective_date"]]
            regs = [h["effective_date"] for h in history if h["stage"] == "registration_referral" and h["effective_date"]]
            data = {"afca_matter_id": mid, "external_reference": reference, "party_ids": [],
                    "received_date": min(regs) if regs else None, "assigned_manager": None, "stream": "unknown",
                    "stage_history": history,
                    "closure_route": routes[0] if len(routes) == 1 else ("unknown" if closed or routes else "not_closed"),
                    "closed_date": min(h["effective_date"] for h in closed) if closed else None,
                    "evidence_refs": refs + [e["ref"] for e in evs]}
            if prev is not None and prev["data"] == data:
                continue
            rid = derived_id("AFCAV", mid, at)
            rec = _record("afca_matter", rid, mid, at, data["evidence_refs"][:20], data,
                          supersedes=prev["envelope"]["record_id"] if prev else None)
            out.append(rec)
            prev = rec
        first_at = min(x["at"] for x in m["refs"])
        for case in sorted(m["cases"]):
            refs = [x["ref"] for x in m["refs"]]
            lid = derived_id("AFCALINK", mid, case)
            out.append(_record("afca_link", lid, lid, first_at, refs[:20],
                               {"afca_matter_id": mid, "case_id": case, "link_state": "supported",
                                "evidence_refs": refs[:20]}))
    return out


# ------------------------------------------------------------------ episodes and contacts
def episodes(s03_bodies: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """→ (episode records, case_id → [episode spans]) from S03 administrative transitions."""
    out, spans = [], {}
    for body in sorted(s03_bodies, key=lambda b: b["case_id"]):
        case = body["case_id"]
        service = (body.get("state_projection_candidate") or {}).get("service") or []
        completed = any(s.get("value") == "confirmed_complete" for s in service)
        trans = sorted((t for t in body.get("administrative_transitions", []) if t.get("effective_date")),
                       key=lambda t: (t.get("effective_at") or t["effective_date"], t["available_at"]))
        cur = None
        for t in trans:
            if t["state"] in OPEN_STATES and cur is None:
                cur = {"start": t["effective_date"], "start_instant": t.get("effective_at"),
                       "start_at": t["available_at"], "refs": list(t["evidence_refs"])}
            elif t["state"] == "closed" and cur is not None:
                cur.update(end=t["effective_date"], end_instant=t.get("effective_at"),
                           end_at=t["available_at"], refs=cur["refs"] + t["evidence_refs"])
                spans.setdefault(case, []).append(cur)
                cur = None
        if cur is not None:
            spans.setdefault(case, []).append(cur)
        for sp in spans.get(case, []):
            eid = derived_id("EPI", case, sp["start"])
            sp["episode_id"] = eid
            ended = sp.get("end") is not None
            data = {"episode_id": eid, "party_ids": [], "case_ids": [case], "link_state": "supported",
                    "start_at": sp.get("start_instant") or melbourne_midnight(date.fromisoformat(sp["start"])).isoformat(),
                    "end_at_exclusive": (sp.get("end_instant") or
                        melbourne_midnight(date.fromisoformat(sp["end"]) + timedelta(days=1)).isoformat()) if ended else None,
                    "interval_state": "bounded" if ended else "unknown",
                    "completion_state": ("recorded_completion" if completed else "closure_without_completion_evidence")
                    if ended else "unknown",
                    "evidence_refs": sp["refs"][:20]}
            out.append(_record("episode", eid, eid, sp.get("end_at") or sp["start_at"], data["evidence_refs"], data))
    return out, spans


def contacts(records: list[dict], sources: dict, spans: dict[str, list[dict]]) -> list[dict]:
    """Add source-authored member contacts and assign episodes/repeat states to every contact record (in place)."""
    have = {s for r in records if r["kind"] == "contact_link" for s in r["data"]["source_ids"]}
    for sid, src in sorted(sources.items()):
        side = author_side(src)
        if side not in ("member", "representative") or sid in have or src["data"].get("copy_of_source_id"):
            continue
        if src["data"].get("subtype") in ("certificate_copy", "certified_identity_copy", "relationship_form"):
            continue                                       # supplied documents are not contacts
        if not src["data"]["case_ids"]:
            continue
        cid = derived_id("CON", sorted(src["data"]["case_ids"]), "source", sid)
        text = src["data"]["text"]
        ref = {"source_id": sid, "content_sha256": src["data"]["content_sha256"],
               "anchor": {"kind": "text", "start_codepoint": 0, "end_codepoint": min(len(text), 200)}}
        records.append(_record("contact_link", cid, cid, src["envelope"]["available_at"], [ref], {
            "contact_id": cid, "case_ids": sorted(src["data"]["case_ids"]), "episode_id": None,
            "event_at": src["envelope"].get("occurred_at"), "event_date": None,
            "initiator": "complainant" if side == "member" else "representative", "repeat_state": "uncertain",
            "duplicate_of_contact_id": None, "source_ids": [sid], "evidence_refs": [ref]}))
    by_case: dict[str, list[dict]] = {}
    for r in records:
        if r["kind"] == "contact_link":
            for c in r["data"]["case_ids"]:
                by_case.setdefault(c, []).append(r)

    def day_of(d):
        if d.get("event_at"):
            return parse_instant(d["event_at"])
        return melbourne_midnight(date.fromisoformat(d["event_date"])) if d.get("event_date") else None

    for case, rs in by_case.items():
        eps = spans.get(case, [])
        seen_in: dict[str, int] = {}
        for r in sorted(rs, key=lambda r: (day_of(r["data"]) is None, day_of(r["data"]) or 0, r["data"]["contact_id"])):
            d = r["data"]
            t = day_of(d)
            if t is None or len(d["case_ids"]) != 1:
                d["repeat_state"] = "uncertain"
                continue
            ep = None
            for sp in eps:
                start = melbourne_midnight(date.fromisoformat(sp["start"]))
                if t >= start:
                    ep = sp                                # latest episode started before the contact
            if ep is None:
                d["episode_id"], d["repeat_state"] = None, "uncertain"
                continue
            d["episode_id"] = ep["episode_id"]
            if d["initiator"] not in ("complainant", "representative"):
                d["repeat_state"] = "not_applicable"
                continue
            n = seen_in.get(ep["episode_id"], 0)
            d["repeat_state"] = "first" if n == 0 else "repeat"
            seen_in[ep["episode_id"]] = n + 1
    return records


# ------------------------------------------------------------------ commitments
def commitments(groups: dict, sources: dict, domains: set[str], unresolved: list) -> list[dict]:
    now = utc_now_iso()
    occ: dict[tuple, dict] = {}
    for (sid, entity), g in sorted(groups.items(), key=lambda kv: (kv[1]["available_at"] or "", kv[0])):
        c_f = _one(g["facts"], "commitment")
        if c_f is None or not g["case_ids"]:
            continue
        src = sources.get(sid)
        side = author_side(src)
        wording = str(c_f["data"]["value"]["value"]).strip()
        # A typed model label is not proof of an undertaking. Approval conditions and requirements
        # without promissory words are quarantined, even for internal scope. The source remains intact.
        anchor = _ref(c_f)["anchor"]
        text = (src["data"]["text"][anchor["start_codepoint"]:anchor["end_codepoint"]]
                if src and anchor.get("kind") == "text" else "")
        if not re.search(r"\b(?:will|shall|promise[ds]?|undertak\w*|agreed? to|committ?\w* to)\b|\b(?:I|we)['’]ll\b", text, re.I):
            unresolved.append({"source_id": sid, "subject": entity,
                               "reason": "no evidenced undertaking language; commitment candidate quarantined"})
            continue
        due_f = _one(g["facts"], "commitment_due_date")
        due_day = str(due_f["data"]["value"]["value"]) if due_f else None
        if side == "institution":
            promisor = {"party_reference": src["data"].get("author_reference") if src and IDENT.match(
                src["data"].get("author_reference") or "") else None, "role": "staff",
                "represented_member_reference": None, "authority_state": "not_applicable", "evidence_refs": [_ref(c_f)]}
            basis, attribution = "direct", "direct_words"
        elif side == "external":
            promisor = {"party_reference": None, "role": "institution", "represented_member_reference": None,
                        "authority_state": "not_applicable", "evidence_refs": [_ref(c_f)]}
            basis, attribution = "captured", "captured_undertaking"
        elif side in ("member", "representative"):
            promisor = {"party_reference": None, "role": "institution", "represented_member_reference": None,
                        "authority_state": "not_applicable", "evidence_refs": []}
            basis, attribution = "reported_only", "attributed_report"
        else:
            promisor = {"party_reference": None, "role": "unknown", "represented_member_reference": None,
                        "authority_state": "uncertain", "evidence_refs": []}
            basis, attribution = "uncertain", "attributed_report"
        addressees = _addressees(src["data"]["text"]) if src else None
        internal = (src is not None and src["data"].get("subtype") == "note") or \
            (addressees is not None and addressees and domains and all(a in domains for a in addressees))
        scope = "internal" if internal else "member_facing"
        side_key = "institution" if promisor["role"] in ("staff", "institution") else promisor["role"]
        key = (tuple(sorted(g["case_ids"])), side_key, due_day or _norm(wording))
        refs = [_ref(c_f)] + ([_ref(due_f)] if due_f else [])
        cur = occ.get(key)
        if cur is not None:
            cur["refs"] += [r for r in refs if r not in cur["refs"]]
            if cur["basis"] in ("reported_only", "uncertain") and basis in ("direct", "captured"):
                cur.update(basis=basis, attribution=attribution, promisor=promisor)
            continue
        occ[key] = {"cases": sorted(g["case_ids"]), "wording": wording, "due_day": due_day, "refs": refs,
                    "promisor": promisor, "basis": basis, "attribution": attribution, "scope": scope,
                    "day": _day(c_f, src), "at": g["available_at"]}
    out = []
    for key, o in sorted(occ.items(), key=lambda kv: repr(kv[0])):
        logical = derived_id("COMMIT", key)
        target = derived_id("TGT", o["cases"], "commitment", _norm(o["wording"]))
        due = _day_due(o["wording"], o["refs"][1:2] or o["refs"], o["due_day"]) if o["due_day"] else \
            _unknown_due(o["wording"], o["refs"][:1], "no absolute due date written in the cited evidence")
        rec = {"kind": "commitment",
               "common": _common(logical, logical, o["cases"], o["day"], o["refs"],
                                 evidence_state="supported" if o["basis"] in ("direct", "captured") else "uncertain",
                                 attribution=o["attribution"], assessed=o["at"], now=now),
               "commitment_id": logical, "promisor": o["promisor"],
               "beneficiary": {"party_reference": None, "role": "member", "represented_member_reference": None,
                               "authority_state": "uncertain", "evidence_refs": []},
               "scope": o["scope"], "action": o["wording"][:500], "action_target_reference": target,
               "action_target_state": "identified", "action_target_reason": "cited undertaking text",
               "fulfilment_criteria": o["wording"][:500], "undertaking_basis": o["basis"], "original_due": due,
               "conditions": [], "original_opportunity_applicability": "applicable" if o["due_day"] else "unknown",
               "applicability_refs": o["refs"][:1], "cancellations": [], "revisions": [],
               "fulfilment_observations": [], "original_due_outcome": "unknown",
               "classification_reason": "re-derived at query time from the original due and fulfilment evidence",
               "classification_refs": [], "revised_due_outcome": None}
        out.append(rec)
    return out


# ------------------------------------------------------------------ repeat valid-evidence requests
def effort(groups: dict, sources: dict, unresolved: list) -> list[dict]:
    now = utc_now_iso()
    received: dict[tuple, list] = {}
    valid: dict[tuple, list] = {}
    requests = []
    for (sid, entity), g in sorted(groups.items()):
        src = sources.get(sid)
        cases = tuple(sorted(g["case_ids"]))
        for f in _known(g["facts"], "evidence_received"):
            received.setdefault((cases, entity), []).append((_day(f, src), f))
        for f in _known(g["facts"], "evidence_validity"):
            v = _norm(f["data"]["value"]["value"])
            valid.setdefault((cases, entity), []).append((_day(f, src), v, f))
        if author_side(src) == "institution":
            for f in _known(g["facts"], "dependency"):
                routing = _one(g["facts"], "routing_reference")
                requests.append({"cases": cases, "entity": entity, "day": _day(f, src), "fact": f, "sid": sid,
                                 "at": g["available_at"], "routing": routing})
    out = []
    for q in sorted(requests, key=lambda q: (q["day"] or "", q["sid"])):
        prior = [(d, f) for d, f in received.get((q["cases"], q["entity"]), []) if d and q["day"] and d < q["day"]]
        if not prior:
            continue                                    # no earlier receipt: a first/legitimate request
        vals = [(d, v, f) for d, v, f in valid.get((q["cases"], q["entity"]), []) if d and d < q["day"]]
        recognised = [x for x in vals if x[1] in ("valid", "accepted", "met", "satisfied")]
        item_code = str(q["fact"]["data"]["value"]["value"]).strip()
        item_ref = item_code if IDENT.match(item_code) else None
        req_ref = str(q["routing"]["data"]["value"]["value"]).strip() if q["routing"] else None
        req_ref = req_ref if req_ref and IDENT.match(req_ref) else None
        eligible = bool(recognised and item_ref and req_ref)
        child = {"item_reference": item_ref, "item_version_reference": item_ref if eligible else None,
                 "requirement_reference": req_ref, "prior_occurrence_reference": None,
                 "receipt_state": "received",
                 "validity_state": "recognised_valid_before_request" if recognised else "not_established",
                 "comparison_state": "uncertain", "order_state": "supported_later" if prior else "unknown",
                 "supporting_refs": [_ref(prior[0][1])] + ([_ref(recognised[0][2])] if recognised else []),
                 "contradictory_refs": [],
                 "reason": "earlier grounded receipt of the same item" + (
                     "; validity recognised before the request" if recognised else
                     "; no recognised validity before the request (later assessment cannot substitute)")}
        if eligible:
            child.update(comparison_state="eligible_repeat",
                         prior_occurrence_reference=derived_id("OCC", q["cases"], q["entity"], prior[0][0]))
        occasion = derived_id("RQOCC", q["cases"], q["sid"])
        aid = derived_id("EFF", occasion, q["entity"])
        refs = [_ref(q["fact"])]
        out.append({"kind": "member_effort_event",
                    "common": _common(aid, aid, list(q["cases"]), q["day"], refs,
                                      evidence_state="supported" if eligible else "uncertain",
                                      attribution="observed_record", assessed=q["at"], now=now),
                    "effort_kind": "repeat_valid_evidence_request", "occasion_id": occasion,
                    "occasion_type": "institution_request",
                    "initiator": {"party_reference": None, "role": "institution", "represented_member_reference": None,
                                  "authority_state": "not_applicable", "evidence_refs": refs},
                    "prior_occurrence_ids": [child["prior_occurrence_reference"]] if eligible else [],
                    "continuity_state": "supported", "repetition_state": "supported" if eligible else "uncertain",
                    "receipt_state": "not_applicable", "repeated_concern": None, "item_comparisons": [child],
                    "qualifiers": [] if eligible else ["necessity_unknown"]})
    return out


# ------------------------------------------------------------------ membership intervals (S05)
def intervals(records: list[dict], node_of: dict[str, str], source_day, *, assessed: str, now: str) -> list[dict]:
    """Shared membership intervals for included memberships of findings: start = earliest cited supporting
    evidence day; `ongoing_at` only for an `active` association (latest supporting evidence availability); no
    invented end. `source_day(source_id) -> (day, available_at)`; `node_of` maps finding → taxonomy node. The
    taxonomy version is bound at publication (the staged value is a placeholder replaced by the publisher)."""
    out = []
    for rec in records:
        if rec["kind"] != "finding" or rec["data"]["finding_id"] not in node_of:
            continue
        d = rec["data"]
        for m in d.get("memberships") or []:
            if m["state"] != "included" or not m.get("evidence_refs"):
                continue
            seen = [source_day(r["source_id"]) for r in m["evidence_refs"]]
            seen = [s for s in seen if s and s[0]]
            if not seen:
                continue
            start = min(s[0] for s in seen)
            ongoing = max(s[1] for s in seen) if m.get("association") == "active" else None
            vid = derived_id("MIV", d["finding_id"], m["case_id"], start, ongoing)
            out.append({"membership_version_id": vid, "finding_id": d["finding_id"], "finding_version": d["version"],
                        "taxonomy_node_id": node_of[d["finding_id"]], "taxonomy_version": "STAGED",
                        "case_id": m["case_id"], "episode_id": m.get("episode_id"), "state": "included",
                        "start": {"precision": "day", "date": start}, "end": None, "ongoing_at": ongoing,
                        "endpoint_state": "active" if ongoing else "unknown",
                        "supporting_refs": m["evidence_refs"][:20], "contradictory_refs": [],
                        "assessed_evidence_as_of": assessed, "published_at": now})
    return out
