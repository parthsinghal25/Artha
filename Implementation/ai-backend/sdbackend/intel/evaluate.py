"""Offline evaluation adapter. Diagnostic evaluation stays `not_performed`: no independent gold exists.

What it does compute are MECHANICAL quality indicators, split into two subsets. The development subset is the one
prompts were tuned on. The early-evaluation subset was reserved before tuning, and its content is never inspected
by the builder. A large gap between the two subsets would indicate prompt overfitting. The adapter reads only
counts, never case content. Holdout members are never assessed, so they are absent here."""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import date as _date, datetime as _dt
from pathlib import Path

from ..canonical import utc_now_iso
from .worker import heads


def _subset_stats(mems: list[dict]) -> dict:
    if not mems:
        return {"cases": 0}
    def med(xs):
        return statistics.median(xs) if xs else None
    src_cov = [1 - len(m["coverage"]["sources_without_grounded_claim"]) / max(1, m["coverage"]["narrative_sources"])
               for m in mems]
    rej = [m["coverage"]["rejected_claims"] / max(1, m["coverage"]["rejected_claims"] + len(m["claims"])) for m in mems]
    return {"cases": len(mems),
            "claims_median": med([len(m["claims"]) for m in mems]),
            "source_claim_coverage_median": round(med(src_cov), 3),
            "claim_rejection_rate_median": round(med(rej), 3),
            "factors_median": med([len(m["factors"]) for m in mems]),
            "cases_with_2plus_factors": sum(len(m["factors"]) >= 2 for m in mems),
            "factor_levels": dict(Counter(f["level"] for m in mems for f in m["factors"])),
            "critique_flags_median": med([len(m["critique"]["flags"]) for m in mems]),
            "refinalised": sum(bool(m["critique"]["refinalised"]) for m in mems),
            "service_states": dict(Counter(m["service"]["state"] for m in mems)),
            "with_commitments": sum(bool(m["commitments"]) for m in mems),
            "with_member_effort": sum(bool(m["member_effort"]) for m in mems),
            "with_responses": sum(bool(m["responses"]) for m in mems),
            "with_handoffs": sum(bool(m["handoffs"]) for m in mems)}


def evaluate(rt, corpus) -> dict:
    early = set(corpus.body["worklist"]["early_evaluation_member_references"])
    dev, ev = [], []
    for cid, h in heads(rt).items():
        m = rt.db.get_object("case_memory", h["memory_id"])
        (ev if early & set(m["member_references"]) else dev).append(m)
    return {"diagnostic_evaluation": "not_performed",
            "reason": "no independent gold labels exist; private authoring labels are excluded from runtime by rule",
            "mechanical_indicators": {"development_subset": _subset_stats(dev),
                                      "early_evaluation_subset": _subset_stats(ev)},
            "holdout": "5 members reserved by Data; ingested but never assessed or used for selection",
            "note": "indicators measure grounding/structure, not correctness of diagnoses"}


# ====================================================================== M5 offline evaluator interface (V07)
"""Offline evaluator interface: frozen export → authored-format adapter → independent-judgement scoring.

1. `export_frozen` writes a frozen prediction export OUTSIDE the served store.
   - It writes predictions.jsonl and a manifest holding: publication, analysis set, taxonomy, prompt/code versions,
     the sha256 of each file, and the subset exposure.
   - Every predicted reference is mapped to a canonical reference: original_id, filename, original_sha256,
     source_id, content_sha256, char anchor and quote.
   - Only assessed (eligible) memories exist, so holdout cases can never be exported.
   - The early-evaluation subset is exported only on explicit request. Its export is never opened by the builder.
2. `adapt_judgements` maps author-format judgement rows onto the export's canonical vocabulary:
   - originals named by original_id or filename, mapped to source ids;
   - dates such as DD/MM/YYYY or "17 Oct 2026", mapped to ISO;
   - money such as "A$25.00", mapped to minor units.
   Author labels are refused, and so are rows for cases that are not in the export (holdout or ineligible).
3. `score` compares only rows whose provenance is an ADJUDICATED independent review.
   - Unadjudicated rows are `unscored`.
   - Factor/definition equivalence is a semantic judgement the REVIEWER records (`matched_factor_id`); the adapter
     never string-matches labels, so a row without that judgement is `needs_semantic_adjudication`.
   - A target type with no scored row reports agreement `unmeasured`. Precision/recall are never invented.
   - Data `metric_oracle` gold, when supplied, reuses `sdbackend.evaluation.adapter.score_metrics`.
This interface never writes to the store, a prompt, a search index or a served payload."""

OFFLINE_EVALUATOR_VERSION = "sdb-intel-offline-evaluator-1.0.0"
TARGETS = ("membership", "service_state", "service_action", "commitment", "factor_present")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _versions() -> dict:
    from . import discovery, measures, memory, procedures, publish, worker
    code = {p.name: _sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    return {"prompts": {"reader": memory.READER_PROMPT, "synthesis": memory.SYNTH_PROMPT,
                        "factors": memory.FACTOR_PROMPT, "discovery": discovery.DISCOVERY_PROMPT,
                        "assessment": discovery.ASSESS_PROMPT},
            "components": {"memory": memory.MEMORY_VERSION, "critic": memory.CRITIC_VERSION,
                           "registry": discovery.REGISTRY_VERSION, "measures": measures.MEASURES_VERSION,
                           "procedures": procedures.PROCEDURES_VERSION, "publisher": publish.PUBLISHER_VERSION,
                           "worker": worker.WORKER_VERSION},
            "code_sha256": code}


def _canon(claim: dict, xw_by_source: dict) -> dict:
    row = xw_by_source.get(claim["source_id"]) or {}
    return {"original_id": row.get("original_id"), "filename": row.get("filename"),
            "original_sha256": row.get("original_sha256"), "source_id": claim["source_id"],
            "content_sha256": claim["content_sha256"], "anchor": claim["ref"].get("anchor") if isinstance(
                claim.get("ref"), dict) else None, "quote": claim["quote"], "claim_id": claim["claim_id"]}


def export_frozen(rt, corpus, out_dir: Path, *, subset: str = "development",
                  allow_early_evaluation: bool = False) -> dict:
    from ..canonical import derived_id
    from .discovery import ASSESS_PROMPT
    from .intake import crosswalk_for
    from .publish import current
    out_dir = Path(out_dir).resolve()
    store = Path(rt.settings.data_dir).resolve()
    if out_dir == store or store in out_dir.parents:
        raise ValueError("the frozen export must be written outside the served store")
    if subset not in ("development", "early_evaluation"):
        raise ValueError("subset must be development or early_evaluation; the holdout is never exported")
    if subset == "early_evaluation" and not allow_early_evaluation:
        raise PermissionError("early-evaluation export needs explicit authorisation (allow_early_evaluation=True)")
    pub = current(rt)
    if pub is None:
        raise ValueError("no sealed publication to export")
    early = set(corpus.early_evaluation_members)
    xw = crosswalk_for(rt, pub["closure"]["release_id"]) or {"rows": []}   # the release this publication sealed
    by_source = {s: r for r in xw["rows"] for s in r["source_ids"]}
    out_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    for cid, mid in sorted(pub["memory_by_case"].items()):
        m = rt.db.get_object("case_memory", mid)
        in_early = bool(early & set(m["member_references"]))
        if (subset == "early_evaluation") != in_early:
            continue
        claims = {c["claim_id"]: c for c in m["claims"]}
        refs = lambda ids: [_canon(claims[i], by_source) for i in ids if i in claims]  # noqa: E731
        ships = []
        for iss in pub["issues"]:
            a = rt.db.get_object("membership_assessment", derived_id("MBA", mid, iss["definition_id"], ASSESS_PROMPT))
            ships.append({"issue_id": iss["issue_id"], "definition_id": iss["definition_id"],
                          "label": iss["label"], "state": a["state"] if a else "not_assessed",
                          "support": refs(a["support_claim_ids"]) if a else [],
                          "contrary": refs(a["contrary_claim_ids"]) if a else [],
                          "retrieved_support": [{k: v for k, v in r.items() if k != "passage_handle"}
                                                for r in (a or {}).get("retrieved_support_refs") or []],
                          "claims_shown": (a or {}).get("claims_shown"), "claims_total": (a or {}).get("claims_total")})
        rows.append({"case_id": cid, "memory_id": mid, "coverage": m["coverage"],
                     "factors": [{"factor_id": f["factor_id"], "label": f["label"], "level": f["level"],
                                  "status": f["status"], "support": refs(f["support_claim_ids"]),
                                  "contrary": refs(f.get("contrary_claim_ids") or [])} for f in m["factors"]],
                     "service": {"state": m["service"]["state"],
                                 "items": [{"action": i["action"], "state": i["state"],
                                            "completion": refs(i.get("completion_claim_ids") or [])}
                                           for i in m["service"].get("items", [])]},
                     "commitments": [{"commitment_id": c["commitment_id"], "action": c["action"], "status": c["status"],
                                      "promise": refs([c["promise_claim_id"]]), "evidence": refs(c["evidence_claim_ids"])}
                                     for c in m["commitments"]],
                     "memberships": ships})
    pred = out_dir / "predictions.jsonl"
    pred.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    manifest = {"evaluator_version": OFFLINE_EVALUATOR_VERSION, "exported_at": utc_now_iso(),
                "publication_id": pub["publication_id"], "analysis_set_version": pub["analysis_set_version"],
                "taxonomy_version": pub["taxonomy_version"], "corpus_id": pub["corpus_id"],
                "subset": subset, "cases": len(rows),
                "exposure": ("builder-visible development subset" if subset == "development" else
                             "early-evaluation subset: exported on authorisation; not inspected by the builder"),
                "holdout": "never exported (no assessed memory exists for holdout cases)",
                "versions": _versions(), "files": {"predictions.jsonl": _sha(pred)}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_export(export_dir: Path) -> tuple[dict, dict]:
    export_dir = Path(export_dir)
    manifest = json.loads((export_dir / "manifest.json").read_text())
    for name, sha in manifest["files"].items():
        if _sha(export_dir / name) != sha:
            raise ValueError(f"export file {name} changed after freezing")
    preds = {r["case_id"]: r for r in (json.loads(x) for x in (export_dir / "predictions.jsonl").read_text()
                                       .splitlines() if x.strip())}
    return manifest, preds


def norm_date(v) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return _dt.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    mt = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", s)
    if mt and mt.group(2).lower() in _MONTHS:
        return _date(int(mt.group(3)), _MONTHS[mt.group(2).lower()], int(mt.group(1))).isoformat()
    raise ValueError(f"unrecognised date {s!r}")


def norm_money(v) -> int | None:
    """'A$25', 'AUD 1,234.50', 25.5 → minor units (cents)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return round(float(v) * 100)
    s = re.sub(r"(?i)\b(?:aud|a\$|\$)|,|\s", "", str(v))
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", s):
        raise ValueError(f"unrecognised amount {v!r}")
    return round(float(s) * 100)


def adapt_judgements(rows: list[dict], manifest: dict, preds: dict, crosswalk: dict) -> dict:
    """Author-format rows → canonical judgements. Returns {"judgements", "refused"}."""
    by_orig = {r["original_id"]: r for r in crosswalk.get("rows", [])}
    by_name = {r["filename"]: r for r in crosswalk.get("rows", []) if r.get("filename")}
    out, refused = [], []
    for i, r in enumerate(rows):
        jid = r.get("judgement_id") or f"row-{i + 1}"
        prov = r.get("provenance") or {}
        why = None
        if prov.get("kind") != "independent_review":
            why = "not an independent review (author/planted labels are never gold)"
        elif r.get("target") not in TARGETS:
            why = f"unknown target {r.get('target')!r}"
        elif r.get("case_id") not in preds:
            why = "case not in this frozen export (holdout, ineligible or other subset)"
        if why:
            refused.append({"judgement_id": jid, "reason": why})
            continue
        ev = []
        for e in r.get("evidence") or []:
            row = by_orig.get(e.get("original_id")) or by_name.get(e.get("filename"))
            ev.append({"original_id": row["original_id"] if row else e.get("original_id"),
                       "source_ids": row["source_ids"] if row else [], "quote": e.get("quote") or "",
                       "resolved": row is not None})
        try:
            expected = r.get("expected")
            if r["target"] == "commitment" and isinstance(expected, dict):
                expected = {**expected, "due_date": norm_date(expected.get("due_date"))}
            facts = {k: norm_date(v) for k, v in (r.get("dates") or {}).items()}
            amounts = {k: norm_money(v) for k, v in (r.get("amounts") or {}).items()}
        except ValueError as exc:
            refused.append({"judgement_id": jid, "reason": f"unit/temporal crosswalk failed: {exc}"})
            continue
        out.append({"judgement_id": jid, "case_id": r["case_id"], "target": r["target"],
                    "issue_id": r.get("issue_id"), "action": r.get("action"), "expected": expected,
                    "matched_factor_id": r.get("matched_factor_id"), "dates": facts, "amounts": amounts,
                    "evidence": ev, "adjudicated": bool(prov.get("adjudicated")),
                    "reviewer": prov.get("reviewer")})
    return {"judgements": out, "refused": refused, "export_publication_id": manifest["publication_id"]}


def _evidence_found(ev: list[dict], refs: list[dict]) -> bool | None:
    if not ev:
        return None
    for e in ev:
        for r in refs:
            if r["source_id"] in e["source_ids"] and e["quote"] and (e["quote"] in r["quote"] or r["quote"] in e["quote"]):
                return True
    return False


def _predict(j: dict, p: dict):
    if j["target"] == "membership":
        s = next((x for x in p["memberships"] if x["issue_id"] == j["issue_id"]), None)
        return (s["state"], s["support"] + s["contrary"]) if s else ("not_predicted", [])
    if j["target"] == "service_state":
        return p["service"]["state"], [c for it in p["service"]["items"] for c in it["completion"]]
    if j["target"] == "service_action":
        from .memory import same_action
        it = next((x for x in p["service"]["items"]
                   if same_action(j.get("action") or "", {"quote": x["action"], "item": x["action"], "note": ""})), None)
        return (it["state"], it["completion"]) if it else ("not_predicted", [])
    if j["target"] == "commitment":
        from .memory import same_action
        exp_action = (j["expected"] or {}).get("action", "") if isinstance(j["expected"], dict) else ""
        c = next((x for x in p["commitments"] if same_action(exp_action or j.get("action") or "",
                                                              {"quote": x["action"], "item": x["action"], "note": ""})),
                 None)
        return (c["status"], c["promise"] + c["evidence"]) if c else ("not_predicted", [])
    f = next((x for x in p["factors"] if x["factor_id"] == j["matched_factor_id"]), None)
    return ("present" if f else "absent", f["support"] if f else [])


def score(adapted: dict, preds: dict, *, data_gold=None, answer=None) -> dict:
    rows = []
    for j in adapted["judgements"]:
        p = preds[j["case_id"]]
        if not j["adjudicated"]:
            rows.append({"judgement_id": j["judgement_id"], "target": j["target"], "outcome": "unscored",
                         "reason": "review not adjudicated"})
            continue
        if j["target"] == "factor_present" and not j["matched_factor_id"] and j["expected"] == "present":
            rows.append({"judgement_id": j["judgement_id"], "target": j["target"],
                         "outcome": "needs_semantic_adjudication",
                         "reason": "reviewer did not record which predicted factor (if any) is equivalent"})
            continue
        got, refs = _predict(j, p)
        exp = j["expected"]["status"] if isinstance(j["expected"], dict) else j["expected"]
        rows.append({"judgement_id": j["judgement_id"], "target": j["target"], "case_id": j["case_id"],
                     "expected": exp, "predicted": got, "outcome": "agree" if got == exp else "disagree",
                     "evidence_found_in_prediction": _evidence_found(j["evidence"], refs)})
    by = {}
    for t in TARGETS:
        rs = [r for r in rows if r["target"] == t]
        sc = [r for r in rs if r["outcome"] in ("agree", "disagree")]
        by[t] = {"judgements": len(rs), "scored": len(sc), "agree": sum(r["outcome"] == "agree" for r in sc),
                 "unscored": sum(r["outcome"] == "unscored" for r in rs),
                 "needs_semantic_adjudication": sum(r["outcome"] == "needs_semantic_adjudication" for r in rs),
                 "agreement": round(sum(r["outcome"] == "agree" for r in sc) / len(sc), 3) if sc else "unmeasured"}
    metrics = None
    if data_gold is not None and answer is not None:
        from ..evaluation.adapter import score_metrics
        metrics = score_metrics(data_gold, answer)
    scored = sum(v["scored"] for v in by.values())
    return {"evaluator_version": OFFLINE_EVALUATOR_VERSION, "evaluated_at": utc_now_iso(),
            "export_publication_id": adapted["export_publication_id"],
            "diagnostic_evaluation": "performed_on_adjudicated_subset" if scored else
            ("not_performed" if not adapted["judgements"] and not adapted["refused"] else "unscored"),
            "by_target": by, "rows": rows, "refused": adapted["refused"], "metric_oracle": metrics,
            "note": "agreement is over adjudicated independent judgements only; unscored/unmeasured targets carry "
                    "no accuracy claim; this report is evidence for review, not acceptance"}


def evaluate_offline(export_dir: Path, judgements_path: Path | None, crosswalk: dict, *, out_path: Path | None = None,
                     data_gold=None, answer=None) -> dict:
    manifest, preds = load_export(export_dir)
    rows = []
    if judgements_path is not None:
        rows = [json.loads(x) for x in Path(judgements_path).read_text().splitlines() if x.strip()]
    if not rows and data_gold is None:
        rep = {"evaluator_version": OFFLINE_EVALUATOR_VERSION, "export_publication_id": manifest["publication_id"],
               "diagnostic_evaluation": "not_performed",
               "reason": "no independent adjudicated judgements were supplied", "by_target": {
                   t: {"judgements": 0, "scored": 0, "agreement": "unmeasured"} for t in TARGETS}}
    else:
        rep = score(adapt_judgements(rows, manifest, preds, crosswalk), preds, data_gold=data_gold, answer=answer)
    rep["export_manifest_sha256"] = _sha(Path(export_dir) / "manifest.json")
    if out_path is not None:
        Path(out_path).write_text(json.dumps(rep, indent=2, sort_keys=True))
    return rep
