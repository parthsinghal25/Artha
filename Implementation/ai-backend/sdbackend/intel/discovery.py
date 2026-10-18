"""Collective investigator (M3): open-vocabulary issue discovery + assessment of EVERY eligible memory.

* **Discovery** (bounded model call(s) over paginated compact scans of all current eligible case memories — open,
  closed, normal and uncertain alike): propose recurring operational issues with a testable definition,
  inclusion/exclusion tests, level (symptom / mechanism / common_cause), dimension, examples, lookalikes and
  alternatives; and keep / revise / retire / merge existing definitions. No seed vocabulary or authored labels are
  given. A proposal needs at least two example cases to be retained as a candidate recurring issue; it is a
  hypothesis until assessment.
* **Registry**: stable `ISS-` ids, versioned definitions (`DEF-`), explicit lineage (revised / merged_into /
  retired) — earlier registries stay immutable so prior publications keep their taxonomy.
* **Assessment**: every eligible case memory × every active definition → included / excluded / uncertain /
  conflicting (+ unassessed when a call fails), with the case's own claim ids. Inclusion needs the case's own
  supporting claim. When memory is insufficient (`needs_evidence` or uncertain), the host retrieves the case's
  original passages by lexical search over the indexed evidence and asks once more with those passages. Reuse is
  keyed by (memory revision, definition version): unchanged pairs make no call; a new or revised definition
  reassesses all eligible memories — including closed and older cases — without rereading every case.
"""
from __future__ import annotations

import math
import re
import time

from jsonschema import Draft202012Validator

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..citations import text_ref
from ..models.base import GenRequest, Message, OutputInvalid, Part
from ..pipeline import control
from . import temporal

DISCOVERY_PROMPT = "intel-discovery-1.1.0"   # 1.1: every recurring issue across factor types
ASSESS_PROMPT = "intel-membership-1.1.0"   # 1.1: claim-subset disclosure; unique passage handles (V02)
REGISTRY_VERSION = "sdb-issue-registry-1.0.0"
SCAN_PAGE_CHARS = 16000
ASSESS_BATCH = 4
ASSESS_RETRIES = 3
MIN_EXAMPLES = 2

DISCOVERY_SYSTEM = """You are the collective investigator for complaint cases at a fictional Australian
superannuation fund (synthetic data). You see compact case memories (K-keys): each case's contributing factors as
recorded by the case investigator. Find RECURRING operational issues: the same specific way things go wrong (or
the same kind of wait/dependency) appearing in two or more cases, possibly across different products and dates.
- Define each issue precisely enough that another analyst could test a new case against it: what must be true
  (include_if) and what would rule it out (exclude_if). Describe the operational step/record/route involved.
- level: symptom (a shared effect only, e.g. delay), mechanism (a shared operational way it happens, supported in
  each case's own facts), common_cause (a single corroborated shared cause such as one route/configuration/
  instruction set — only if the memories show the SAME underlying cause). Two similar quotes do not establish a
  common cause. Settlement, escalation or dissatisfaction alone is not a failure.
- Include lookalikes (cases that look similar but differ) and alternatives (other explanations).
- Do not invent fault or intent. Do not use the product name alone as an issue.
- Existing issues (I-keys) are shown: for each say keep, revise (better definition, same issue), retire (not
  supported) or merge_into another key; then add new issues."""

DISCOVERY_ASK = """Return one JSON object:
{"existing": [{"key": "I1", "action": "keep|revise|retire|merge_into", "merge_into": null, "label": "...",
   "definition": "...", "include_if": "...", "exclude_if": "...", "level": "mechanism", "dimension": "service"}],
 "new": [{"label": "short specific label", "definition": "...", "include_if": "...", "exclude_if": "...",
   "level": "symptom|mechanism|common_cause", "dimension": "service|handling|communication|dependency|external",
   "examples": ["K1", "K4"], "lookalikes": [], "alternatives": "..."}]}
(one entry per distinct recurring issue — look across ALL factor types: service mechanisms, waits/dependencies,
handling and communication; usually several issues recur). Use [] where there is nothing. Only propose an issue
with at least two example cases, and list every case that shows it in `examples`."""

ASSESS_SYSTEM = """You test complaint cases (fictional superannuation fund, synthetic data) against defined
operational issues. For EVERY (case, issue) pair shown decide:
- included: the case's own claims show the issue's include_if conditions (cite the claim ids, e.g. "K2.C5");
- excluded: the claims show it does not apply, or an exclude_if condition holds;
- uncertain: possibly applies but the case's claims are insufficient or ambiguous (set needs_evidence true if the
  original documents might settle it);
- conflicting: some claims support it and others contradict it (cite both).
Absence of a mention in a summary is not exclusion: use uncertain when the evidence shown simply does not address
the issue. Cite only claim ids of THAT case. Name the case factor (F-key) it matches if any."""

ASSESS_ASK = """Return one JSON object: {"assessments": [{"case": "K1", "issue": "I1", "state":
"included|excluded|uncertain|conflicting", "factor": "F1 or null", "support": ["K1.C3"], "contrary": [],
"reason": "short", "needs_evidence": false}]}. One entry per (case, issue) pair listed under PAIRS."""

_S, _SN = {"type": "string"}, {"type": ["string", "null"]}
DISC_SCHEMA = {"type": "object", "required": ["new"], "properties": {
    "existing": {"type": "array", "items": {"type": "object", "required": ["key", "action"]}},
    "new": {"type": "array", "items": {"type": "object", "required": ["label", "definition"]}}}}
ASSESS_SCHEMA = {"type": "object", "required": ["assessments"], "properties": {"assessments": {"type": "array",
    "items": {"type": "object", "required": ["case", "issue", "state"]}}}}
_DV, _AV = Draft202012Validator(DISC_SCHEMA), Draft202012Validator(ASSESS_SCHEMA)
LEVELS = ("symptom", "mechanism", "common_cause")
DIMENSIONS = ("service", "handling", "communication", "dependency", "external")
STATES = ("included", "excluded", "uncertain", "conflicting")


# ------------------------------------------------------------------ registry
def registry_head(rt) -> dict | None:
    row = rt.db.connect().execute("SELECT registry_id FROM intel_registry_head WHERE singleton=1").fetchone()
    return rt.db.get_object("issue_registry", row["registry_id"]) if row else None


def _set_registry(rt, reg: dict) -> None:
    with rt.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO intel_registry_head VALUES (1, ?, ?)", (reg["registry_id"], utc_now_iso()))


def active_issues(reg: dict | None) -> list[dict]:
    return [i for i in (reg or {}).get("issues", []) if i["status"] == "active"]


def _definition_id(issue_id: str, version: int, body: dict) -> str:
    return derived_id("DEF", issue_id, version, canonical_sha256(body))


# ------------------------------------------------------------------ compact scans
def scan_line(key: str, mem: dict) -> str:
    st = mem["lifecycle"][-1]["state"] if mem["lifecycle"] else "unknown"
    fs = "; ".join(f"[{f['level']}/{f['prominence']}/{f['status']}] {f['label']}: {f['description'][:220]}"
                   for f in mem["factors"]) or "(no factors recorded)"
    nc = mem["native_case"]
    return (f"{key} · {nc.get('product_or_journey')} · received {nc.get('complaints_receipt_date')} · lifecycle "
            f"{st} · service {mem['service']['state']} · factors: {fs}")


def scan_pages(keys: dict[str, dict]) -> list[list[str]]:
    pages, cur, size = [], [], 0
    for k, m in keys.items():
        line = scan_line(k, m)
        if cur and size + len(line) > SCAN_PAGE_CHARS:
            pages.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        pages.append(cur)
    return pages


def _bare_array(text: str | None, key: str = "assessments"):
    """Salvage every COMPLETE element of the reply's array (under `key`, or a bare top-level array) when the model
    ended the reply with a stray token instead of closing it. Incomplete trailing elements are dropped."""
    import json as _json
    import re as _re
    t = (text or "").strip()
    m = _re.search(r'"%s"\s*:\s*\[' % _re.escape(key), t)
    i = m.end() if m else (t.find("[") + 1 if t.lstrip("`json \n").startswith("[") else 0)
    if not i:
        return None
    dec, out = _json.JSONDecoder(), []
    while True:
        while i < len(t) and t[i] in " \t\r\n,":
            i += 1
        if i >= len(t) or t[i] != "{":
            break
        try:
            val, i = dec.raw_decode(t, i)
        except ValueError:
            break
        if isinstance(val, dict):
            out.append(val)
    return out or None


def _ask(gw, task_id: str, attempt: int, role: str, system: str, user: str, schema, validator, version: str,
         max_tokens: int = 4096, usage: list | None = None):
    msgs = [Message("user", [Part.t(user)])]
    last = None
    for i in range(2):
        try:
            res = gw.call(GenRequest(role=role, system=system, messages=msgs, response_schema=schema,
                                     max_output_tokens=max_tokens, prompt_version=version),
                          task_id=task_id, attempt=attempt * 10 + i)
        except OutputInvalid as exc:
            last = str(exc)[:160]
            msgs = [Message("user", [Part.t(user + "\n\nReply with the single JSON object only.")])]
            continue
        if usage is not None:
            usage.append({"role": role, "latency_ms": res.latency_ms, "input_tokens": res.usage.input_tokens,
                          "output_tokens": res.usage.output_tokens})
        data = res.data
        req = schema.get("required") or []
        if isinstance(data, dict) and req and req[0] not in data and role.startswith("membership"):
            data = _bare_array(res.text, req[0]) or data   # unterminated array: keep complete elements only
        if isinstance(data, list) and len(req) == 1 and schema["properties"][req[0]].get("type") == "array" \
                and role.startswith("membership"):
            data = {req[0]: data}          # bare array of assessments: same content, host-normalised
        if isinstance(data, dict) and not list(validator.iter_errors(data)):
            return data
        errs = [e.message[:80] for e in validator.iter_errors(data)][:2] if isinstance(data, dict) else []
        last = f"shape mismatch ({type(data).__name__}{': ' + '; '.join(errs) if errs else ''})"
        msgs = [Message("user", [Part.t(user + "\n\nYour reply did not match the required shape. Reply with the "
                                               "single JSON object only.")])]
    raise OutputInvalid(f"{role}: no valid output ({last})")


def discover(gw, memories: dict[str, dict], reg: dict | None, *, task_id: str, step_view: str,
             usage: list, heartbeat=None) -> tuple[dict, dict]:
    """→ (new registry body, discovery coverage)."""
    keys = {f"K{i}": m for i, m in enumerate(sorted(memories.values(), key=lambda m: m["case_id"]), start=1)}
    key_case = {k: m["case_id"] for k, m in keys.items()}
    existing = active_issues(reg)
    ikeys = {f"I{i}": iss for i, iss in enumerate(existing, start=1)}
    ex_txt = "\n".join(f"{k}: {i['label']} [{i['level']}/{i['dimension']}] — {i['definition'][:300]} | include_if: "
                       f"{i['include_if'][:200]} | exclude_if: {i['exclude_if'][:160]}" for k, i in ikeys.items())
    proposals_new, decisions, failed_pages = [], {}, []
    pages = scan_pages(keys)
    for n, page in enumerate(pages, start=1):
        if heartbeat:
            heartbeat()
        user = ("EXISTING ISSUES:\n" + (ex_txt or "(none yet)") + f"\n\nCASE MEMORIES (page {n}/{len(pages)}, "
                f"knowledge to end of {step_view}):\n" + "\n".join(page) + "\n\n" + DISCOVERY_ASK)
        try:
            data = _ask(gw, task_id, n, "collective_discovery", DISCOVERY_SYSTEM, user, DISC_SCHEMA, _DV,
                        DISCOVERY_PROMPT, usage=usage)
        except OutputInvalid as exc:
            failed_pages.append({"page": n, "error": str(exc)[:160]})
            continue
        for e in data.get("existing", []) or []:
            if isinstance(e, dict) and e.get("key") in ikeys:
                decisions.setdefault(e["key"], []).append(e)
        for p in data.get("new", []) or []:
            if isinstance(p, dict):
                proposals_new.append(p)
    now = utc_now_iso()
    issues, lineage_notes = [], []
    for k, iss in ikeys.items():                         # existing: keep unless an explicit decision
        ds = decisions.get(k, [])
        act = next((d for d in ds if d.get("action") in ("retire", "merge_into")), None) or \
            next((d for d in ds if d.get("action") == "revise"), None)
        if act and act["action"] == "retire":
            issues.append({**iss, "status": "retired", "lineage": iss["lineage"] + [{"event": "retired",
                                                                                     "at_view": step_view}]})
            lineage_notes.append(f"{iss['issue_id']} retired at {step_view}")
        elif act and act["action"] == "merge_into" and act.get("merge_into") in ikeys and act["merge_into"] != k:
            tgt = ikeys[act["merge_into"]]["issue_id"]
            issues.append({**iss, "status": "merged", "merged_into": tgt,
                           "lineage": iss["lineage"] + [{"event": "merged_into", "target": tgt, "at_view": step_view}]})
        elif act and act["action"] == "revise" and str(act.get("definition") or "").strip():
            body = _def_body(act, iss)
            v = iss["definition_version"] + 1
            issues.append({**iss, **body, "definition_version": v,
                           "definition_id": _definition_id(iss["issue_id"], v, body),
                           "lineage": iss["lineage"] + [{"event": "revised", "from_definition": iss["definition_id"],
                                                         "at_view": step_view}]})
        else:
            issues.append(dict(iss))
    seen_labels = {re.sub(r"\W+", " ", i["label"].lower()).strip() for i in issues}
    rejected = []
    for p in proposals_new:
        ex = [key_case[x] for x in (p.get("examples") or []) if x in key_case]
        label = re.sub(r"\s+", " ", str(p.get("label") or "")).strip()[:120]
        norm = re.sub(r"\W+", " ", label.lower()).strip()
        if len(set(ex)) < MIN_EXAMPLES or not label:
            rejected.append({"label": label, "reason": f"fewer than {MIN_EXAMPLES} example cases"})
            continue
        if norm in seen_labels:
            continue
        seen_labels.add(norm)
        body = _def_body(p, None)
        iid = derived_id("ISS", norm, step_view)
        issues.append({"issue_id": iid, **body, "definition_version": 1,
                       "definition_id": _definition_id(iid, 1, body), "status": "active", "parent_issue_id": None,
                       "proposed_examples": sorted(set(ex)),
                       "proposed_lookalikes": sorted({key_case[x] for x in (p.get("lookalikes") or [])
                                                      if x in key_case}),
                       "alternatives": str(p.get("alternatives") or "")[:400] or None,
                       "lineage": [{"event": "proposed", "at_view": step_view}], "first_view": step_view})
    coverage = {"memories_scanned": len(keys), "pages": len(pages), "failed_pages": failed_pages,
                "rejected_proposals": rejected, "complete_enumeration": not failed_pages,
                "note": "complete enumeration of eligible memories does not establish complete discovery recall"}
    body = {"registry_version": REGISTRY_VERSION, "predecessor_id": reg["registry_id"] if reg else None,
            "view_date": step_view, "issues": sorted(issues, key=lambda i: i["issue_id"]),
            "discovery": coverage, "lineage_notes": lineage_notes, "produced_at": now,
            "model_assessed": True, "human_review": "not_performed"}
    return body, coverage


def _def_body(p: dict, prev: dict | None) -> dict:
    def g(k, n):
        v = str(p.get(k) or (prev or {}).get(k) or "").strip()
        return v[:n]
    lvl = p.get("level") if p.get("level") in LEVELS else (prev or {}).get("level", "symptom")
    dim = p.get("dimension") if p.get("dimension") in DIMENSIONS else (prev or {}).get("dimension", "service")
    return {"label": g("label", 120), "definition": g("definition", 800), "include_if": g("include_if", 500),
            "exclude_if": g("exclude_if", 500), "level": lvl, "dimension": dim}


# ------------------------------------------------------------------ assessment
MAX_CLAIMS_SHOWN = 24
RETRIEVAL_PAIRS, RETRIEVAL_HITS = 6, 4


def _case_block(key: str, mem: dict, max_claims: int = MAX_CLAIMS_SHOWN) -> tuple[str, dict]:
    alias = {}
    total = len(mem["claims"])
    shown = min(total, max_claims)
    lines = [f"CASE {key} · {mem['native_case'].get('product_or_journey')} · received "
             f"{mem['native_case'].get('complaints_receipt_date')} · claims shown {shown} of {total}" +
             (" (SUBSET: claims not shown may still support or contradict an issue; their absence here is not "
              "exclusion — use uncertain with needs_evidence true when the shown claims do not settle it)"
              if shown < total else "")]
    for i, f in enumerate(mem["factors"], start=1):
        lines.append(f"  F{i}: [{f['level']}/{f['status']}] {f['label']} — {f['description'][:200]}")
    important = sorted(mem["claims"], key=lambda c: (c["kind"] in ("other", "acknowledgement"), c["source_day"]))
    for i, c in enumerate(important[:max_claims], start=1):
        alias[f"{key}.C{i}"] = c["claim_id"]
        lines.append(f"  {key}.C{i} [{c['source_day']}] {c['kind']}/{c['modality']} {c['actor']}: "
                     f"\"{c['quote'][:180]}\"")
    return "\n".join(lines), alias


def _validate(a: dict, alias: dict, key_case: dict, issue_keys: dict, factor_ids: dict,
              shown: dict | None = None) -> dict | None:
    case = key_case.get(str(a.get("case")))
    iss = issue_keys.get(str(a.get("issue")))
    if case is None or iss is None:
        return None
    own = {k: v for k, v in alias.items() if k.startswith(str(a["case"]) + ".")}
    sup = [own[x] for x in (a.get("support") or []) if x in own]
    con = [own[x] for x in (a.get("contrary") or []) if x in own]
    state = a.get("state") if a.get("state") in STATES else "uncertain"
    notes = []
    if state == "included" and not sup:
        state, notes = "uncertain", ["included without the case's own supporting claim → uncertain"]
    if state == "conflicting" and not (sup and con):
        state, notes = ("uncertain", ["conflicting without both supporting and contrary claims → uncertain"])
    seen, total = (shown or {}).get(case, (None, None))
    needs = bool(a.get("needs_evidence"))
    if state == "excluded" and not con and seen is not None and seen < total:
        # V02: the assessor saw only a subset of this memory's claims; omission is not exclusion
        state, needs = "uncertain", True
        notes = notes + [f"excluded without a contrary claim while only {seen} of {total} claims were shown → "
                         "uncertain (needs evidence)"]
    f = str(a.get("factor") or "")
    fid = factor_ids.get(case, {}).get(f)
    return {"case_id": case, "issue_id": iss["issue_id"], "definition_id": iss["definition_id"],
            "state": state, "factor_id": fid, "support_claim_ids": sup, "contrary_claim_ids": con,
            "reason": str(a.get("reason") or "")[:300], "needs_evidence": needs,
            "claims_shown": seen, "claims_total": total,
            "retrieved_support_refs": [], "retrieved_contrary_refs": [], "retrieval": None,
            "host_notes": notes}


def assess(gw, rt, repo_search, memories: dict[str, dict], pairs: list[tuple[str, dict]], *, task_id: str,
           usage: list, log=None) -> tuple[list[dict], list[dict]]:
    """pairs = [(case_id, issue)] needing assessment → (assessments, unassessed)."""
    by_case: dict[str, list[dict]] = {}
    for cid, iss in pairs:
        by_case.setdefault(cid, []).append(iss)
    cases = sorted(by_case)
    out, unassessed = [], []
    for b in range(0, len(cases), ASSESS_BATCH):
        batch = cases[b:b + ASSESS_BATCH]
        keys = {f"K{j}": cid for j, cid in enumerate(batch, start=1)}
        issues = {}
        for cid in batch:
            for iss in by_case[cid]:
                issues.setdefault(iss["issue_id"], iss)
        ikeys = {f"I{j}": iss for j, iss in enumerate(issues.values(), start=1)}
        inv = {v["issue_id"]: k for k, v in ikeys.items()}
        blocks, alias, fids, shown = [], {}, {}, {}
        for k, cid in keys.items():
            txt, al = _case_block(k, memories[cid])
            blocks.append(txt)
            alias.update(al)
            shown[cid] = (len(al), len(memories[cid]["claims"]))
            fids[cid] = {f"F{i}": f["factor_id"] for i, f in enumerate(memories[cid]["factors"], start=1)}
        defs = "\n".join(f"{k}: {i['label']} [{i['level']}] — {i['definition'][:400]}\n    include_if: "
                         f"{i['include_if'][:300]}\n    exclude_if: {i['exclude_if'][:240]}" for k, i in ikeys.items())
        pair_txt = ", ".join(f"({k},{inv[i['issue_id']]})" for k, cid in keys.items() for i in by_case[cid])
        user = "ISSUES:\n" + defs + "\n\nCASES:\n" + "\n\n".join(blocks) + "\n\nPAIRS: " + pair_txt + "\n\n" + ASSESS_ASK
        try:
            data = _ask(gw, task_id, 100 + b, "membership_assessor", ASSESS_SYSTEM, user, ASSESS_SCHEMA, _AV,
                        ASSESS_PROMPT, usage=usage)
        except OutputInvalid as exc:
            unassessed += [{"case_id": cid, "issue_id": i["issue_id"], "reason": str(exc)[:160]}
                           for cid in batch for i in by_case[cid]]
            continue
        got = {}
        for a in data.get("assessments", []) or []:
            if isinstance(a, dict):
                v = _validate(a, alias, keys, ikeys, fids, shown)
                if v and (v["case_id"], v["issue_id"]) not in got:
                    got[(v["case_id"], v["issue_id"])] = v
        # targeted original-evidence retrieval for insufficient memory (one bounded follow-up per batch)
        weak = [v for v in got.values() if v["state"] == "uncertain" and v["needs_evidence"]]
        if weak and repo_search is not None:
            extra = _retrieve_and_reassess(gw, repo_search, weak, memories, issues, keys, task_id, b, usage)
            for v in extra:
                got[(v["case_id"], v["issue_id"])] = v
        for cid in batch:
            for i in by_case[cid]:
                v = got.get((cid, i["issue_id"]))
                if v is None:
                    unassessed.append({"case_id": cid, "issue_id": i["issue_id"], "reason": "no assessment returned"})
                else:
                    out.append(v)
        if log:
            log(f"  assessed batch {b // ASSESS_BATCH + 1}: {len(got)} pairs")
    return out, unassessed


def _retrieve_and_reassess(gw, search, weak, memories, issues, keys, task_id, b, usage) -> list[dict]:
    """One bounded re-ask with original passages for insufficient memories. Passage handles are unique across the
    whole request (V02): K1.P1, K1.P2, … never repeat for a second issue of the same case."""
    inv_keys = {cid: k for k, cid in keys.items()}
    blocks, alias, pairs, prov = [], {}, [], {}
    counter: dict[str, int] = {}
    for v in weak[:RETRIEVAL_PAIRS]:
        iss = issues[v["issue_id"]]
        terms = " ".join(re.findall(r"[A-Za-z]{5,}", iss["include_if"] + " " + iss["label"]))[:300]
        hits = search(v["case_id"], terms, RETRIEVAL_HITS)
        prov[(v["case_id"], v["issue_id"])] = {"terms": terms, "hits": len(hits), "limit": RETRIEVAL_HITS,
                                               "truncated": len(hits) >= RETRIEVAL_HITS}
        if not hits:
            continue
        k = inv_keys[v["case_id"]]
        lines = [f"ORIGINAL PASSAGES for case {k} (issue {iss['label']}):"]
        for h in hits:
            counter[k] = counter.get(k, 0) + 1
            pk = f"{k}.P{counter[k]}"
            alias[pk] = h
            lines.append(f"  {pk}: \"{h['text'][:500]}\"")
        blocks.append("\n".join(lines))
        pairs.append((k, iss, v))
    skipped = weak[RETRIEVAL_PAIRS:]
    if not blocks:
        return []
    ikeys = {f"I{j}": i for j, (_, i, _) in enumerate(pairs, start=1)}
    defs = "\n".join(f"{k}: {i['label']} — include_if: {i['include_if'][:300]} | exclude_if: {i['exclude_if'][:200]}"
                     for k, i in ikeys.items())
    user = ("ISSUES:\n" + defs + "\n\n" + "\n\n".join(blocks) + "\n\nPAIRS: " +
            ", ".join(f"({k},I{j})" for j, (k, _, _) in enumerate(pairs, start=1)) +
            "\n\nDecide using these original passages; cite passage ids (e.g. K1.P2) in support/contrary.\n" + ASSESS_ASK)
    try:
        data = _ask(gw, task_id, 500 + b, "membership_retrieval", ASSESS_SYSTEM, user, ASSESS_SCHEMA, _AV,
                    ASSESS_PROMPT, usage=usage)
    except OutputInvalid:
        return []
    out = []
    for a in data.get("assessments", []) or []:
        if not isinstance(a, dict):
            continue
        k, ik = str(a.get("case")), str(a.get("issue"))
        if k not in keys or ik not in ikeys:
            continue
        orig = next((v for (kk, i, v) in pairs if kk == k and i["issue_id"] == ikeys[ik]["issue_id"]), None)
        if orig is None:
            continue
        sup_h = [x for x in (a.get("support") or []) if x in alias and x.startswith(k + ".")]
        con_h = [x for x in (a.get("contrary") or []) if x in alias and x.startswith(k + ".") and x not in sup_h]
        state = a.get("state") if a.get("state") in STATES else "uncertain"
        notes = ["reassessed with retrieved original passages"]
        if state == "included" and not (sup_h or orig["support_claim_ids"]):
            state = "uncertain"
            notes.append("included without supporting passage or claim → uncertain")
        if state == "conflicting" and not ((sup_h or orig["support_claim_ids"]) and (con_h or orig["contrary_claim_ids"])):
            state = "uncertain"
            notes.append("conflicting without both sides → uncertain")
        out.append({**orig, "state": state, "reason": str(a.get("reason") or "")[:300],
                    "needs_evidence": state == "uncertain",
                    "retrieved_support_refs": [dict(alias[x]["ref"], passage_handle=x) for x in sup_h],
                    "retrieved_contrary_refs": [dict(alias[x]["ref"], passage_handle=x) for x in con_h],
                    "retrieved_evidence_refs": [alias[x]["ref"] for x in sup_h],      # support only (legacy name)
                    "retrieval": {**prov[(orig["case_id"], orig["issue_id"])],
                                  "pairs_skipped_by_cap": len(skipped)},
                    "host_notes": orig["host_notes"] + notes})
    return out


def make_search(rt, ctx):
    """Lexical search over the case's eligible indexed passages (FTS5), returning exact text refs."""
    from ..pipeline.passages import passages
    from ..pipeline.tools import build_index
    index_version = build_index(rt, ctx.rv.release_id)

    def search(case_id: str, terms: str, limit: int) -> list[dict]:
        allowed = {s["data"]["source_id"]: s for s in ctx.inputs(case_id).narrative}
        if not allowed or not terms.strip():
            return []
        q = " OR ".join(sorted({t.lower() for t in re.findall(r"[A-Za-z]{5,}", terms)})[:12])
        rows = rt.db.connect().execute(
            "SELECT passage_id, source_id FROM passages_fts WHERE passages_fts MATCH ? AND index_version=? "
            "ORDER BY rank LIMIT 200", (q, index_version)).fetchall()
        out = []
        for r in rows:
            if r["source_id"] not in allowed:
                continue
            src = allowed[r["source_id"]]["data"]
            h = r["passage_id"].split("~")[1]
            p = next((x for x in passages(src["text"]) if x["handle"] == h), None)
            if p:
                out.append({"source_id": src["source_id"], "text": p["text"],
                            "ref": text_ref(src, p["start"], p["end"])})
            if len(out) >= limit:
                break
        return out
    return search


# ------------------------------------------------------------------ phase (worker)
def collective_phase(rt, ctx, *, gw, prov, reason, budget, log, assess_shard: tuple[int, int] | None = None) -> dict:
    from .worker import _run, heads
    mems = {cid: m for cid, m in heads(rt).items() if cid in set(ctx.eligible) and ctx.in_view(cid)}
    out = {"run_ids": [], "memories": len(mems)}
    if not mems:
        return out
    reg = registry_head(rt)
    versions = {"discovery": DISCOVERY_PROMPT, "assess": ASSESS_PROMPT, "registry": REGISTRY_VERSION}
    disc_key = canonical_sha256([sorted(m["memory_id"] for m in mems.values()),
                                 reg["registry_id"] if reg else None, DISCOVERY_PROMPT])
    run = _run(rt, ctx, "intel_collective", [disc_key], prov, reason, versions)
    gw.run_id = run["run_id"]
    out["run_ids"].append(run["run_id"])
    usage: list = []
    # ---- discovery (reuse when the memory set and registry are unchanged)
    task = control.ensure_task(rt, run, "intel_discovery", "portfolio", [disc_key])
    if task["state"] in ("staged", "accepted"):
        reg = rt.db.get_object("issue_registry", task["output_object_id"]) or reg
        out["discovery"] = "reused"
    elif reg is not None and (reg.get("discovery_key") == disc_key or (
            reg.get("memory_ids") == sorted(m["memory_id"] for m in mems.values())
            and reg.get("discovery_prompt") == DISCOVERY_PROMPT)):
        out["discovery"] = "reused"          # fixpoint: the head registry already reflects these exact memories
    else:
        if assess_shard is not None:
            raise RuntimeError("assessment shards never run discovery: run it once unsharded first")
        budget.check(len(scan_pages({f"K{i}": m for i, m in enumerate(mems.values())})))
        token = control.claim(rt, task["task_id"])
        log(f"discovery over {len(mems)} memories")
        try:
            body, cov = discover(gw, mems, reg, task_id=task["task_id"], step_view=ctx.view_date.isoformat(),
                                 usage=usage, heartbeat=lambda: control.heartbeat(rt, task["task_id"], token))
        except BaseException:
            control.release_lease(rt, task["task_id"], token, "queued", "interrupted (budget/stop); resumable")
            raise
        body["discovery_key"] = disc_key
        body["memory_ids"] = sorted(m["memory_id"] for m in mems.values())
        body["discovery_prompt"] = DISCOVERY_PROMPT
        rid = derived_id("REG", canonical_sha256({k: v for k, v in body.items() if k != "produced_at"}))
        body["registry_id"] = rid
        if control.finish(rt, task["task_id"], token, "staged", output_object_id=rid,
                          outputs=[("issue_registry", rid, body, {"run": [run["run_id"]]})]):
            _set_registry(rt, body)
            reg = body
        out["discovery"] = {"issues_active": len(active_issues(reg)), **cov}
        log(f"  registry {rid}: {len(active_issues(reg))} active issues")
    # ---- assessment of every eligible memory × every active definition
    issues = active_issues(reg)
    need, reused = [], 0
    for cid, m in sorted(mems.items()):
        for iss in issues:
            aid = derived_id("MBA", m["memory_id"], iss["definition_id"], ASSESS_PROMPT)
            if rt.db.get_object("membership_assessment", aid) is not None:
                reused += 1
            else:
                need.append((cid, iss))
    out["assessment"] = {"pairs_total": len(mems) * len(issues), "reused": reused, "to_assess": len(need)}
    if need:
        # one fenced task per batch: each batch persists on its own (a long pass can never outlive one lease),
        # and an interrupted pass resumes at the first unpersisted batch
        by_case: dict[str, list] = {}
        from .worker import in_shard
        for cid, iss in need:
            if in_shard(cid, assess_shard):     # disjoint by CASE: stable however much other shards have persisted
                by_case.setdefault(cid, []).append(iss)
        cases = sorted(by_case)
        search = make_search(rt, ctx)
        results_all, unassessed_all, persisted = [], [], 0
        log(f"assessing {len(need)} (case, issue) pairs in {math.ceil(len(cases) / ASSESS_BATCH)} batches")
        for b in range(0, len(cases), ASSESS_BATCH):
            pairs = [(c, i) for c in cases[b:b + ASSESS_BATCH] for i in by_case[c]]
            bkey = canonical_sha256(sorted((c, i["definition_id"], mems[c]["memory_id"]) for c, i in pairs))
            token = task = None
            for attempt in range(ASSESS_RETRIES):      # bounded retries of an identical pending batch
                task = control.ensure_task(rt, run, "intel_membership", "batch", [bkey, attempt])
                token = control.claim(rt, task["task_id"])
                if token is None and control.recover_dead_lease(rt, task["task_id"]):
                    token = control.claim(rt, task["task_id"])
                if token is not None:
                    break
            if token is None:
                unassessed_all += [{"case_id": c, "issue_id": i["issue_id"],
                                    "reason": f"retry limit ({ASSESS_RETRIES}) reached"} for c, i in pairs]
                continue
            try:
                budget.check(1)
                results, unassessed = assess(gw, rt, search, mems, pairs, task_id=task["task_id"], usage=usage,
                                             log=None)
            except BaseException:
                control.release_lease(rt, task["task_id"], token, "queued", "interrupted (budget/stop); resumable")
                raise
            outputs = []
            for v in results:
                m = mems[v["case_id"]]
                aid = derived_id("MBA", m["memory_id"], v["definition_id"], ASSESS_PROMPT)
                outputs.append(("membership_assessment", aid,
                                {**v, "assessment_id": aid, "memory_id": m["memory_id"],
                                 "memory_revision": m["revision"], "assess_prompt": ASSESS_PROMPT,
                                 "provider": prov, "assessed_view": ctx.view_date.isoformat(),
                                 "model_assessed": True, "human_review": "not_performed",
                                 "assessed_at": utc_now_iso()},
                                {"case": [v["case_id"]], "issue": [v["issue_id"]]}))
            ok = control.finish(rt, task["task_id"], token, "staged", output_object_id=None,
                                coverage={"assessed": len(results), "unassessed": len(unassessed)}, outputs=outputs)
            if ok:
                persisted += len(results)
                results_all += results
                unassessed_all += unassessed
            else:
                unassessed_all += [{"case_id": v["case_id"], "issue_id": v["issue_id"],
                                    "reason": "fenced persist refused (lease lost); rerun to resume"} for v in results]
            log(f"  batch {b // ASSESS_BATCH + 1}: {len(results)} assessed, persisted={ok}")
        out["assessment"].update(assessed=persisted, unassessed=unassessed_all)
    out["usage_calls"] = len(usage)
    out["registry_id"] = reg["registry_id"] if reg else None
    return out
