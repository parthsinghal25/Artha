"""S04 deterministic ledger/work resolution (no model): staged S02 typed facts → candidate v0.2 analysis records.

Rules (R10.1, R10.4):
* Only grounded typed facts (S02 host-validated quote + value grounding) are used; nothing is inferred.
* A financial event needs, on the SAME source and subject: an amount, a financial kind that maps exactly to a frozen
  financial_event kind, and a category that maps exactly to a frozen obligation category. Requests, offers,
  approvals and invoices stay distinct kinds and never become cash.
* Reversal/refund amounts are stored non-positive (frozen schema); payments non-negative.
* Recorded work comes ONLY from the trusted native work-log projection (`native_work`, registered column profile,
  cell-exact citations) over the sources of the consumed S02 stages. Model-read durations — including a model's
  `documented_native` label — are listed unresolved and never recorded; a work-log source is read natively only,
  so the same row can never be counted twice.
* Native ledgers (E06) come ONLY from the trusted ledger profile (`native_ledger`): the ledger's own event and
  obligation IDs are the canonical identities. A model-read financial mention whose (cases, category) is covered by a
  native ledger is reconciled to that ledger: an exact (kind, amount, date) match becomes corroborating evidence of
  the same event — or, when the mention was available before the ledger snapshot, an earlier version of that same
  logical event which the ledger version supersedes — and anything else is listed unresolved, never a second event.
* Native tables are read from the S02-bound sources and from `native_source_ids` (release sources that match a
  registered profile), so recorded work and ledgers never depend on a model call. A table source's own cells are
  never also read as model facts. The same native row in copied/re-imported sources is one entity (earliest
  availability wins); conflicting copies are unresolved and neither is counted.
* Contacts: a grounded `contact_reference` becomes one `contact_link` per (case set, reference) — source copies and
  repeated mentions collapse; initiator comes only from the source's recorded author role; repeat state and episode
  stay `uncertain`/null (continuity is never inferred here). AFCA references without receipt/stage evidence are
  listed unresolved, never turned into matters.
* Identity: obligation = (case set, category, subject); event = (obligation, kind, date, amount, source span).
  Repeated mentions of the same event (same identity) collapse to one record, so portfolio sums are unions.
* Everything unresolvable is listed with a reason. The output is a staged S04 stage: it needs an independent
  `diagnostic_evaluation` pass before any publication.
"""
from __future__ import annotations

import re

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..citations import text_ref
from ..policy import record_access
from . import native_ledger, native_work, producers

RESOLVER_VERSION = "sdb-ledger-resolver-1.5.1"
KIND_MAP = {"payment": "payment", "paid": "payment", "payment_reversal": "payment_reversal", "reversal": "payment_reversal",
            "refund": "refund", "requested": "requested", "request": "requested", "offered": "offered", "offer": "offered",
            "approved": "approved", "approval": "approved", "agreed": "agreed", "awarded": "awarded",
            "invoiced": "invoiced", "invoice": "invoiced", "amount_revision": "amount_revision"}
CATEGORY_MAP = {"afca_fee": "afca_fee", "afca fee": "afca_fee", "external_assistance": "external_assistance",
                "other_complaint_expense": "other_complaint_expense", "compensation": "compensation",
                "settlement": "settlement", "benefit_principal": "benefit_principal",
                "refund_entitlement": "refund_entitlement"}
WORK_PHASES = {"member_contact", "internal_coordination", "evidence_gathering", "approval", "submission_preparation",
               "conciliation_preparation", "conciliation_attendance", "determination_response", "follow_up", "other"}


def _norm(v) -> str:
    return str(v or "").strip().lower().replace("-", "_")


def native_candidates(rt, source_ids) -> list[str]:
    """Release sources that parse under a registered native work-log or ledger profile."""
    out = []
    for sid in sorted(set(source_ids)):
        src = rt.db.get_object("source", sid)
        if src and (native_work.is_work_log(src["data"]) or native_ledger.is_ledger(src["data"])):
            out.append(sid)
    return out


def resolve(rt, s02_stage_ids: list[str], *, run_id: str | None = None, persist: bool = True,
            native_source_ids: list[str] = (), release_source_ids: list[str] = (),
            s03_stage_ids: list[str] = ()) -> dict:
    """Build the S04 stage body from staged S02 outputs (deterministic; reads only).

    `persist=False` is the WORKER path: the body is returned unsealed and the runner seals it, with its dependency
    edges, only inside a valid fenced `control.finish` (F03). `persist=True` is an operator/test convenience that
    seals immediately and must never be used by a worker."""
    groups: dict[tuple[str, str], dict] = {}
    native_logs: dict[str, dict] = {}
    native_ledgers: dict[str, dict] = {}

    def native(src):
        if native_work.is_work_log(src["data"]):
            native_logs[src["data"]["source_id"]] = src
        if native_ledger.is_ledger(src["data"]):
            native_ledgers[src["data"]["source_id"]] = src

    for nsid in sorted(set(native_source_ids)):
        src = rt.db.get_object("source", nsid)
        if src is not None:
            native(src)
    for sid in sorted(s02_stage_ids):
        stage = rt.db.get_object("stage_s02", sid)
        if stage is None:
            continue
        bound = rt.db.get_object("source", (stage.get("source_binding") or {}).get("source_id") or "")
        if bound and bound["data"]["content_sha256"] == stage["source_binding"]["content_sha256"]:
            native(bound)
        for f in stage["facts"]:
            d = f["data"]
            ref = d["value"]["evidence_refs"][0]
            src = rt.db.get_object("source", ref["source_id"])
            key = (ref["source_id"], d["entity_id"])
            g = groups.setdefault(key, {"facts": {}, "case_ids": sorted(src["data"]["case_ids"]) if src else [],
                                        "available_at": src["envelope"]["available_at"] if src else None,
                                        "author_role": (src["data"].get("author_role") or "") if src else ""})
            g["facts"].setdefault(d["field_key"], []).append(f)
    records, unresolved, seen = [], [], set()
    native_rows: list[dict] = []
    table_sources = set(native_logs) | set(native_ledgers)

    def mk(src):
        return lambda k, rid, _a, refs, data, at=src["envelope"]["available_at"]: _record(k, rid, at, refs, data)

    # ---- native ledgers first: canonical financial identities
    ledger_recs: dict[str, dict] = {}
    for source_id, src in sorted(native_ledgers.items(), key=lambda kv: (kv[1]["envelope"]["available_at"], kv[0])):
        recs, unres = native_ledger.project(src["data"], mk(src))
        unresolved += unres
        for r in recs:
            rid = r["envelope"]["record_id"]
            if rid not in ledger_recs:
                ledger_recs[rid] = r                     # earliest-available copy of the same native row wins
                continue
            prev = ledger_recs[rid]
            same = {k: v for k, v in prev["data"].items() if k not in ("evidence_refs", "source_ids")} == \
                {k: v for k, v in r["data"].items() if k not in ("evidence_refs", "source_ids")}
            if not same:
                unresolved.append({"source_id": source_id, "subject": rid,
                                   "reason": f"{native_ledger.PROFILE_ID}: {rid} differs between ledger copies; "
                                             f"neither counted"})
                prev["_conflict"] = True
            elif r["kind"] == "obligation":
                prev["data"]["source_ids"] = sorted(set(prev["data"]["source_ids"]) | set(r["data"]["source_ids"]))
    conflicted_obs = {k for k, r in ledger_recs.items() if r["kind"] == "obligation" and r.get("_conflict")}
    ledger_recs = {k: r for k, r in ledger_recs.items() if not r.get("_conflict")
                   and not (r["kind"] == "financial_event" and r["data"]["obligation_id"] in conflicted_obs)}
    ledger_obs = {k: r for k, r in ledger_recs.items() if r["kind"] == "obligation"}
    covered = {(tuple(sorted(o["data"]["case_ids"])), o["data"]["category"]) for o in ledger_obs.values()}
    ledger_index: dict[tuple, str] = {}
    for k, r in ledger_recs.items():
        if r["kind"] == "financial_event":
            ob = ledger_obs.get(r["data"]["obligation_id"])
            if ob is not None:
                d = r["data"]
                ledger_index[(tuple(sorted(ob["data"]["case_ids"])), ob["data"]["category"], d["kind"],
                              d["amount_minor"], d["effective_date"])] = k
    prose_matches: dict[str, list[dict]] = {}

    for (source_id, entity), g in sorted(groups.items()):
        if source_id in table_sources:
            continue                                      # a native table is read natively only, never as model facts
        facts = g["facts"]

        def one(key):
            vals = [f for f in facts.get(key, []) if f["data"]["value"]["state"] == "known"]
            return vals[0] if len({canonical_sha256(v["data"]["value"]["value"]) for v in vals}) == 1 else None

        amount, kind_f, cat_f = one("financial_amount_minor"), one("financial_kind"), one("financial_category")
        if amount or kind_f or cat_f:
            kind = KIND_MAP.get(_norm(kind_f["data"]["value"]["value"])) if kind_f else None
            cat = CATEGORY_MAP.get(_norm(cat_f["data"]["value"]["value"])) if cat_f else None
            if not (amount and kind and cat):
                unresolved.append({"source_id": source_id, "subject": entity,
                                   "reason": "financial fact lacks an exact amount, mapped kind or mapped category"})
            else:
                value = int(amount["data"]["value"]["value"])
                if kind in ("payment_reversal", "refund"):
                    value = -abs(value)
                elif value < 0:
                    unresolved.append({"source_id": source_id, "subject": entity, "reason": "negative non-reversal amount"})
                    continue
                refs = [x["data"]["value"]["evidence_refs"][0] for x in (amount, kind_f, cat_f)]
                day = amount["data"]["event_date"] or (kind_f["data"]["event_date"] if kind_f else None)
                if (tuple(sorted(g["case_ids"])), cat) in covered:
                    native_id = ledger_index.get((tuple(sorted(g["case_ids"])), cat, kind, value, day))
                    if native_id is None:
                        unresolved.append({"source_id": source_id, "subject": entity, "reason":
                                           "financial mention in a case/category covered by a native ledger does not "
                                           "match a ledger event (kind, amount, date); not counted separately"})
                    else:
                        prose_matches.setdefault(native_id, []).append(
                            {"source_id": source_id, "available_at": g["available_at"], "refs": refs})
                    continue
                ob_id = derived_id("OBL", g["case_ids"], cat, entity)
                if ob_id not in seen:
                    seen.add(ob_id)
                    records.append(_record("obligation", ob_id, g["available_at"], refs, {
                        "obligation_id": ob_id, "category": cat, "currency": "AUD", "case_ids": g["case_ids"],
                        "afca_matter_ids": [], "creditor_role": "unknown", "source_ids": [source_id],
                        "duplicate_of_obligation_id": None}))
                ev_id = derived_id("FEV", ob_id, kind, day, value)
                if ev_id in seen:
                    continue                                  # repeated mention of the same event: union
                seen.add(ev_id)
                records.append(_record("financial_event", ev_id, g["available_at"], refs, {
                    "financial_event_id": ev_id, "obligation_id": ob_id, "kind": kind, "amount_minor": value,
                    "gst_minor": None, "currency": "AUD", "amount_basis": "unknown",
                    "value_basis": "synthetic_recorded", "effective_at": None, "effective_date": day,
                    "invoice_reference": None, "adjusts_event_id": None, "evidence_refs": refs}))
        ref_f = one("contact_reference")
        if ref_f and g["case_ids"]:
            ref = str(ref_f["data"]["value"]["value"]).strip()
            kid = derived_id("CON", g["case_ids"], ref.lower())
            role = g["author_role"].lower()
            initiator = ("complainant" if role in ("member", "complainant") else
                         "representative" if "representative" in role else "unknown")
            if kid not in seen:
                seen.add(kid)
                chan = one("channel")
                records.append(_record("contact_link", kid, g["available_at"], [ref_f["data"]["value"]["evidence_refs"][0]], {
                    "contact_id": kid, "case_ids": g["case_ids"], "episode_id": None, "event_at": None,
                    "event_date": ref_f["data"]["event_date"] or (chan["data"]["event_date"] if chan else None),
                    "initiator": initiator, "repeat_state": "uncertain", "duplicate_of_contact_id": None,
                    "source_ids": [source_id], "evidence_refs": [ref_f["data"]["value"]["evidence_refs"][0]]}))
            else:
                for r in records:
                    if r["envelope"]["record_id"] == kid and source_id not in r["data"]["source_ids"]:
                        r["data"]["source_ids"] = sorted(r["data"]["source_ids"] + [source_id])
        if one("afca_reference"):
            unresolved.append({"source_id": source_id, "subject": entity,
                               "reason": "AFCA reference without receipt/stage evidence; not a matter record"})
        if one("work_duration_seconds") and source_id not in native_logs:
            unresolved.append({"source_id": source_id, "subject": entity,
                               "reason": "model-read duration (prose or unregistered table); never recorded work"})
    # ---- recorded work: native work logs only; identical copies collapse, conflicting copies count for neither
    work: dict[str, dict] = {}
    for source_id, src in sorted(native_logs.items(), key=lambda kv: (kv[1]["envelope"]["available_at"], kv[0])):
        recs, unres = native_work.project(src["data"], mk(src))
        unresolved += unres
        for r in recs:
            rid = r["envelope"]["record_id"]
            if rid in seen:
                continue
            if rid not in work:
                work[rid] = r
                continue
            a = {k: v for k, v in work[rid]["data"].items() if k != "evidence_refs"}
            if a != {k: v for k, v in r["data"].items() if k != "evidence_refs"}:
                unresolved.append({"source_id": source_id, "subject": rid,
                                   "reason": f"{native_work.PROFILE_ID}: work id {rid} differs between copies; "
                                             f"neither counted"})
                work[rid]["_conflict"] = True
    for rid, r in sorted(work.items()):
        if r.pop("_conflict", False):
            continue
        native_rows.append({"kind": "work_entry", **r.pop("_native")})
        records.append(r)
        seen.add(rid)

    # ---- native ledger events/obligations, with prose reconciled to them (earlier versions or corroboration)
    early_obligation: dict[str, dict] = {}
    for eid, matches in sorted(prose_matches.items()):
        led = ledger_recs[eid]
        first = min(matches, key=lambda m: (m["available_at"] or "9999", m["source_id"]))
        extra = [ref for m in matches for ref in m["refs"]]
        if first["available_at"] and first["available_at"] < led["envelope"]["available_at"]:
            ver = _record("financial_event", derived_id("FEVP", eid, first["source_id"]), first["available_at"],
                          first["refs"], {**led["data"], "evidence_refs": first["refs"]})
            ver["envelope"]["logical_id"] = eid
            led["envelope"]["supersedes_record_id"] = ver["envelope"]["record_id"]
            records.append(ver)
            oid = led["data"]["obligation_id"]
            if oid not in early_obligation or first["available_at"] < early_obligation[oid]["available_at"]:
                early_obligation[oid] = {"available_at": first["available_at"], "source_id": first["source_id"],
                                         "refs": first["refs"]}
        for ref in extra:
            if ref not in led["data"]["evidence_refs"]:
                led["data"]["evidence_refs"].append(ref)
                led["envelope"]["evidence_refs"].append(ref)
    for oid, e in sorted(early_obligation.items()):
        led = ledger_recs[oid]
        ver = _record("obligation", derived_id("OBLP", oid, e["source_id"]), e["available_at"], e["refs"],
                      {**led["data"], "source_ids": [e["source_id"]]})
        ver["envelope"]["logical_id"] = oid
        led["envelope"]["supersedes_record_id"] = ver["envelope"]["record_id"]
        records.append(ver)
    for rid, r in sorted(ledger_recs.items(), key=lambda kv: (kv[1]["kind"] != "obligation", kv[0])):
        if rid in seen:
            continue
        if "_native" in r:
            native_rows.append({"kind": r["kind"], **r.pop("_native")})
        records.append(r)
        seen.add(rid)
    # ---- E07 producers: AFCA identity/stages, episodes + contact continuity, commitments, repeat requests
    srcmap = {}
    for sid in sorted({k[0] for k in groups} | set(release_source_ids)):
        src = rt.db.get_object("source", sid)
        if src is not None:
            srcmap[sid] = src
    prod_groups = {k: g for k, g in groups.items() if k[0] not in table_sources}
    s03_bodies = [b for b in (rt.db.get_object("stage_s03", x) for x in sorted(set(s03_stage_ids))) if b]
    records += producers.afca(prod_groups, srcmap, unresolved)
    ep_records, spans = producers.episodes(s03_bodies)
    records += ep_records
    producers.contacts(records, {k: v for k, v in srcmap.items() if k in set(release_source_ids)}, spans)
    mx_records = []
    for m in (producers.commitments(prod_groups, srcmap, producers.institution_domains(list(srcmap.values())),
                                    unresolved) + producers.effort(prod_groups, srcmap, unresolved)):
        errs = rt.contracts.errors(m["kind"], m)
        if errs:
            unresolved.append({"source_id": m["common"]["source_bindings"][0]["source_id"], "subject": m["kind"],
                               "reason": f"{producers.PRODUCERS_VERSION}: {m['kind']} failed schema: {errs[0][:160]}"})
            continue
        mx_records.append(m)
    # Preserve a narrower earlier position when a later ledger snapshot has the canonical identity.
    # Conditional authorization is not an agreed/awarded/invoiced debt or a payment. Exact native ID
    # and conditional wording in the original source are necessary; no category/amount is inferred.
    history_uncertainties = []
    for sid, src in sorted(srcmap.items()):
        if sid in table_sources:
            continue
        text = src["data"]["text"]
        if not re.search(r"\bconditional\b|\bsubject to (?:its|the) conditions\b", text, re.I):
            continue
        for oid, obligation in sorted(ledger_obs.items()):
            if not set(src["data"]["case_ids"]) & set(obligation["data"]["case_ids"]):
                continue
            for line in re.finditer(r"[^\n]+", text):
                if oid not in line.group() or not re.search(r"authoris|authoriz|approv", line.group(), re.I):
                    continue
                history_uncertainties.append({"obligation_id": oid, "state": "uncertain",
                    "case_ids": sorted(src["data"]["case_ids"]), "available_at": src["envelope"]["available_at"],
                    "evidence_refs": [text_ref(src["data"], line.start(), line.end())],
                    "reason": "earlier conditional authorization; applicability/amount-setting conditions not "
                              "established; approval alone is excluded from outstanding debt"})
    for r in records:
        rt.contracts.check("analysis_record", r, document="runtime")
    cited = sorted({ref["source_id"] for r in records for ref in r["envelope"]["evidence_refs"]} |
                   {b["source_id"] for m in mx_records for b in m["common"]["source_bindings"]} |
                   {r["source_id"] for u in history_uncertainties for r in u["evidence_refs"]})
    body = {"kind": "s04_ledger_resolution",
            "consumed_sources": [{"source_id": s, "object_sha256": rt.db.object_sha("source", s)} for s in cited], "resolver_version": RESOLVER_VERSION, "run_id": run_id,
            "consumed_s02_stages": sorted(s02_stage_ids), "analysis_records": records, "unresolved": unresolved,
            "native_work_logs": [{"source_id": s, "profile_id": native_work.PROFILE_ID} for s in sorted(native_logs)],
            "native_ledgers": [{"source_id": s, "profile_id": native_ledger.PROFILE_ID} for s in sorted(native_ledgers)],
            "native_rows": native_rows, "mx_records": mx_records, "producers_version": producers.PRODUCERS_VERSION,
            "financial_history_uncertainties": history_uncertainties,
            "assessment_coverage": {"mx.commitments.follow_through": {
                "state": "partial", "case_ids": sorted({c for s in srcmap.values() for c in s["data"]["case_ids"]}),
                "limitations": ["bounded source interpretation does not establish exhaustive commitment coverage; "
                                "missing extracted promises are not evidence of no undertaking"]}},
            "consumed_s03_stages": sorted(set(s03_stage_ids)),
            "prose_reconciliation": [{"financial_event_id": e, "sources": sorted({m["source_id"] for m in ms})}
                                     for e, ms in sorted(prose_matches.items())],
            "produced_at": utc_now_iso(), "draft_status": "staged_not_accepted"}
    natives = sorted(set(native_source_ids))
    extra = ([[(n, rt.db.object_sha("source", n)) for n in natives]] if natives else [])
    if release_source_ids or s03_stage_ids:
        extra += [sorted(set(release_source_ids)), [(x, rt.db.object_sha("stage_s03", x)) for x in
                                                     sorted(set(s03_stage_ids))]]
    stage_id = derived_id("STAGE", "s04", RESOLVER_VERSION, sorted(s02_stage_ids), *extra)
    body["stage_id"] = stage_id
    existing = rt.db.get_object("stage_s04", stage_id)
    if existing is not None:
        return existing                                   # identical inputs already sealed: reuse, never rewrite
    if persist:
        rt.db.put_object("stage_s04", stage_id, body)
        return rt.db.get_object("stage_s04", stage_id)
    return body


def dependency_edges(stage: dict) -> list:
    return ([("source", x["source_id"], x["object_sha256"], None) for x in stage["consumed_sources"]] +
            [("stage_s02", x, None, None) for x in stage["consumed_s02_stages"]] +
            [("stage_s03", x, None, None) for x in stage.get("consumed_s03_stages", [])])


def _record(kind: str, rid: str, available_at: str | None, refs: list, data: dict) -> dict:
    at = available_at or utc_now_iso()
    return {"schema_version": "0.2.0", "kind": kind,
            "envelope": {"record_id": rid, "logical_id": rid, "supersedes_record_id": None, "recorded_at": at,
                         "available_at": at, "occurred_at": None, "occurrence_date": None, "access": record_access(),
                         "evidence_refs": refs}, "data": data}
