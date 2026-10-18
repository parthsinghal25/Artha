"""Isolated diagnostic evaluator (R10.6): scores staged outputs against restricted Data evaluation rows.

Isolation: gold rows are read from an operator-supplied path, validated against the frozen Data
`evaluation.schema.json`, held in memory only and never written to the store, the passage index, a tool, a prompt or
a served payload. Only the report (counts, target IDs, outcomes) is written, to a path outside the served store.
The report is evidence FOR an independent `diagnostic_evaluation` reviewer — it is not itself an acceptance.

Evidence gold (`evidence_gold`), per target (F04):
* candidate predictions are TYPED: same subject entity, an origin able to answer the target type (TARGET_ORIGINS),
  and a value of the same type family as the acceptable values;
* every asserted candidate is scored on its own: `correct` (acceptable value AND exact-version proof per
  PROOF_POLICY), `correct_unproved`, or `wrong`; a target with right and wrong assertions is `conflicting`;
* `abstained` = no asserted candidate; abstention is correct when gold expects unknown/not_applicable;
* `contrary_retained` = gold contradictory refs the prediction kept as contrary evidence.
`assertion_precision` = correct assertions / all scored assertions; `target_hit_rate` is reported separately;
recall = correct targets / targets expecting a value. Predictions no gold row covers are `unscored`.

Metric oracle (`metric_oracle`): the supplied `answer(query)` (normally the deterministic results engine) is
compared on point state, value (exact) and included/excluded/uncertain contributor ID sets.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ..canonical import canonical_sha256, utc_now_iso

EVALUATOR_VERSION = "sdb-evaluator-1.0.0"
ABSTAIN_OK = {"unknown", "not_applicable"}


def load_gold(rt, path: Path) -> list[dict]:
    schema = json.loads((rt.settings.data_contract_dir / "evaluation.schema.json").read_text())
    v = Draft202012Validator(schema, registry=rt.contracts.registry, format_checker=FormatChecker())
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        errs = list(v.iter_errors(row))
        if errs:
            raise ValueError(f"gold row {i} is not a valid evaluation row: {errs[0].message[:200]}")
        rows.append(row)
    return rows


def _norm(v):
    if isinstance(v, list):
        return tuple(sorted(str(x).strip().lower() for x in v))
    return str(v).strip().lower()


def _overlap(a: dict, b: dict) -> bool:
    """Same source VERSION (exact content hash) and overlapping anchor. A citation of another version of the same
    source id is never proof (F04)."""
    if a["source_id"] != b["source_id"] or a.get("content_sha256") != b.get("content_sha256"):
        return False
    x, y = a["anchor"], b["anchor"]
    if x["kind"] != y["kind"]:
        return False
    if x["kind"] == "field":
        return x["json_pointer"] == y["json_pointer"]
    return x["start_codepoint"] < y["end_codepoint"] and y["start_codepoint"] < x["end_codepoint"]


PROOF_POLICY = ("every reference of at least one acceptable proof set is overlapped by a predicted reference with the "
                "same source id and exact content_sha256 (text: code-point overlap; field: identical JSON pointer). "
                "Overlap shows the cited span, not that the whole proposition is proved; meaning stays with the "
                "independent reviewer.")


def _covers(pred_refs: list[dict], proof_sets: list[list[dict]]) -> bool:
    return any(ps and all(any(_overlap(g, p) for p in pred_refs) for g in ps) for ps in proof_sets)


# typed matching: which predicted units can answer which gold target type
TARGET_ORIGINS = {"atomic_fact": {"s02", "s04"}, "membership": {"s05"}, "incident": {"s05"},
                  "relationship": {"s04", "s05"}, "temporal": {"s02", "s03"}, "clarification": {"s02"},
                  "post_closure_return": {"s02", "s03"}}


def _value_family(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, (list, tuple)):
        return "list"
    return "string"


def units(s02: list[dict] = (), s04: list[dict] = (), s05: list[dict] = ()) -> list[dict]:
    """Normalise staged outputs into predicted units: {origin, subjects, field, state, value, refs, contrary}."""
    out = []
    for st in s02:
        for f in st.get("facts", []):
            d = f["data"]
            out.append({"origin": "s02", "subjects": {d["entity_id"]}, "field": d["field_key"],
                        "state": d["value"]["state"], "value": d["value"].get("value"),
                        "refs": d["value"]["evidence_refs"], "contrary": []})
    for st in s04:
        for r in st.get("analysis_records", []):
            d = r["data"]
            subj = set(d.get("case_ids") or []) | {r["envelope"]["logical_id"]}
            val = d.get("amount_minor", d.get("duration_seconds", d.get("category")))
            out.append({"origin": "s04", "subjects": subj, "field": r["kind"], "state": "known", "value": val,
                        "refs": r["envelope"]["evidence_refs"], "contrary": []})
    for st in s05:
        for r in st.get("analysis_records", []):
            for m in r["data"]["memberships"]:
                out.append({"origin": "s05", "subjects": {m["case_id"], r["data"]["finding_id"]}, "field": "membership",
                            "state": "known" if m["state"] != "uncertain" else "unknown", "value": m["state"],
                            "refs": m["evidence_refs"], "contrary": r["data"]["contradictory_refs"]})
    return out


def _candidates(d: dict, predicted: list[dict]) -> list[tuple[int, dict]]:
    """Typed target matching: same subject entity, an origin able to answer this target type, and — when the gold
    lists acceptable values — a prediction value of the same type family (a channel string never answers an amount).
    An unknown target type is matched on subject only and flagged `untyped`."""
    ents = set(d["entity_ids"])
    origins = TARGET_ORIGINS.get(d.get("target_type"))
    families = {_value_family(v) for v in d.get("acceptable_values", [])}
    out = []
    for i, p in enumerate(predicted):
        if not p["subjects"] & ents:
            continue
        if origins is not None and p.get("origin") is not None and p["origin"] not in origins:
            continue
        if families and p["value"] is not None and _value_family(p["value"]) not in families:
            continue
        out.append((i, p))
    return out


def score_evidence(gold: list[dict], predicted: list[dict]) -> dict:
    """Per-assertion scoring. Every asserted candidate on a gold target is counted: one correct value never hides an
    additional wrong one (F04). `assertion_precision` = correct assertions / all scored assertions;
    `target_hit_rate` = targets with ≥1 correct assertion / targets asserted (reported separately, never as
    precision)."""
    targets, used = [], set()
    a_correct = a_unproved = a_wrong = 0
    for g in (r for r in gold if r["kind"] == "evidence_gold"):
        d = g["data"]
        cand = _candidates(d, predicted)
        used |= {i for i, _ in cand}
        asserted = [p for _, p in cand if p["state"] in ("known", "partial") and p["value"] is not None]
        ok_values = {_norm(v) for v in d["acceptable_values"]}
        verdicts = []
        for p in asserted:
            if d["expected_state"] in ABSTAIN_OK or _norm(p["value"]) not in ok_values:
                verdicts.append("wrong")
            elif _covers(p["refs"], d["acceptable_proof_sets"]):
                verdicts.append("correct")
            else:
                verdicts.append("correct_unproved")
        a_correct += verdicts.count("correct")
        a_unproved += verdicts.count("correct_unproved")
        a_wrong += verdicts.count("wrong")
        if not asserted:
            outcome = "abstained_correct" if d["expected_state"] in ABSTAIN_OK else "abstained"
        elif "wrong" in verdicts and ("correct" in verdicts or "correct_unproved" in verdicts):
            outcome = "conflicting"                    # right and wrong asserted together: never credited as correct
        elif "wrong" in verdicts:
            outcome = "wrong"
        elif "correct" in verdicts:
            outcome = "correct"
        else:
            outcome = "correct_unproved"
        kept = sum(1 for c in d["contradictory_refs"] if any(_overlap(c, x) for _, p in cand for x in p["contrary"]))
        targets.append({"target_id": d["target_id"], "target_type": d.get("target_type"),
                        "typed": d.get("target_type") in TARGET_ORIGINS, "expected_state": d["expected_state"],
                        "outcome": outcome, "asserted": len(asserted), "assertion_verdicts": verdicts,
                        "contrary_expected": len(d["contradictory_refs"]), "contrary_retained": kept})
    n = lambda o: sum(1 for t in targets if t["outcome"] == o)            # noqa: E731
    asserted_targets = sum(1 for t in targets if t["asserted"])
    scored = a_correct + a_unproved + a_wrong
    expect_value = sum(1 for t in targets if t["expected_state"] not in ABSTAIN_OK)
    expect_abstain = len(targets) - expect_value
    hits = sum(1 for t in targets if "correct" in t["assertion_verdicts"])
    return {"targets": targets,
            "counts": {o: n(o) for o in ("correct", "correct_unproved", "wrong", "conflicting", "abstained",
                                         "abstained_correct")},
            "assertions": {"scored": scored, "correct": a_correct, "correct_unproved": a_unproved, "wrong": a_wrong},
            "assertion_precision": round(a_correct / scored, 4) if scored else None,
            "precision": round(a_correct / scored, 4) if scored else None,        # alias: assertion precision
            "target_hit_rate": round(hits / asserted_targets, 4) if asserted_targets else None,
            "recall": round(n("correct") / expect_value, 4) if expect_value else None,
            "abstention_rate": round((n("abstained") + n("abstained_correct")) / len(targets), 4) if targets else None,
            "correct_abstention_rate": round(n("abstained_correct") / expect_abstain, 4) if expect_abstain else None,
            "contrary_retention": (round(sum(t["contrary_retained"] for t in targets) /
                                         sum(t["contrary_expected"] for t in targets), 4)
                                   if sum(t["contrary_expected"] for t in targets) else None),
            "unscored_predictions": len(predicted) - len(used), "untyped_targets": sum(1 for t in targets if not t["typed"]),
            "proof_policy": PROOF_POLICY}


def score_metrics(gold: list[dict], answer) -> dict:
    rows = []
    for g in (r for r in gold if r["kind"] == "metric_oracle"):
        d = g["data"]
        try:
            got = answer(d["query"])
        except Exception as exc:  # noqa: BLE001 - a refusal is a reported outcome, not a crash
            rows.append({"fixture_id": d["fixture_id"], "outcome": "refused", "detail": f"{type(exc).__name__}"})
            continue
        exp, pt = d["expected_result"]["point"], got["result"]["point"]
        ids = got.get("contributors", {})
        checks = {"state": exp["state"] == pt["state"], "value": exp["value"] == pt["value"],
                  "included": sorted(d["included_ids"]) == sorted(ids.get("included", [])),
                  "excluded": sorted(d["excluded_ids"]) == sorted(ids.get("excluded", [])),
                  "uncertain": sorted(d["uncertain_ids"]) == sorted(ids.get("uncertain", []))}
        rows.append({"fixture_id": d["fixture_id"], "outcome": "match" if all(checks.values()) else "mismatch",
                     "checks": checks, "expected_value": exp["value"], "actual_value": pt["value"]})
    return {"fixtures": rows, "matched": sum(1 for r in rows if r["outcome"] == "match"), "total": len(rows)}


def evaluate(rt, gold_path: Path, *, s02=(), s04=(), s05=(), answer=None, out_path: Path | None = None) -> dict:
    gold = load_gold(rt, gold_path)
    report = {"evaluator_version": EVALUATOR_VERSION, "evaluated_at": utc_now_iso(),
              "gold_sha256": canonical_sha256(Path(gold_path).read_text()), "gold_rows": len(gold),
              "stage_ids": {"s02": [s.get("stage_id") for s in s02], "s04": [s.get("stage_id") for s in s04],
                            "s05": [s.get("stage_id") for s in s05]},
              "evidence": score_evidence(gold, units(s02, s04, s05)),
              "metrics": score_metrics(gold, answer) if answer else None,
              "status": "evidence_for_independent_review_not_acceptance"}
    if out_path is not None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(report, indent=2, sort_keys=True))
    return report
