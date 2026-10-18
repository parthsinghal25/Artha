"""Combined-corpus preflight (review V08). Read-only, makes no model call, ingests nothing and opens no store.

It binds the final runtime delivery named by a combined corpus file (`config/demo/<id>.corpus.json`, `bundles` form)
and checks it statically against the same rules the runtime uses.

- Pins: each bundle's delivery-manifest digest, its runtime/transport digests where named, every original's sha256
  against the manifest and INTAKE-FILES.sha256, and each source-quality decision by file digest.
- Population: members, cases, originals and AFCA matters per bundle and combined, against the expected counts.
- Identity: zero member/case/original/party collisions across bundles. Contained snapshots (first80) are never bound
  as additional bundles. Excluded corpora (Case 11 releases, the FX fixture and other candidates) share no
  original bytes or case ids with the bound population.
- Split: member/split-only allowlists. Every delivered member is in exactly one split, the counts match, and the
  early-evaluation members are development members.
- Associations: every original's case/member/AFCA references resolve. Financial/work table rows must satisfy the
  runtime profiles' factual-link rule: the row case and linked cases belong to the original's cases, and the AFCA
  column belongs to its matters. A row that would be left unresolved is reported as a predicted omission.
- Temporal: availability inside the observation window, event ≤ availability, exact-midnight availability under the
  half-open day policy, receipt dates, and native case visibility versus the case's first held original.
- OCR: the local engine self-check plus deterministic OCR of image originals (extraction, not inference). Only
  states and counts are kept, never text.
- Forecast: actual eligible cases, narrative bytes relative to the measured pilot, assessment volume for the current
  issue count, and the selected provider. It is a serial extrapolation from measured local calls, not a commitment.

The four states are reported separately: data_delivered, runtime_merged, preflight_passed, inference_run."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import BACKEND_ROOT, ARTHA_ROOT

PREFLIGHT_VERSION = "sdb-intel-combined-preflight-1.0.0"
NARRATIVE_KINDS = {"email", "call_summary", "note", "letter", "document", "attachment_original", "afca_record",
                   "procedure", "image"}
TABLE_KINDS = {"financial_export", "work_export"}


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha(p: Path) -> str:
    return _sha_bytes(Path(p).read_bytes())


def _resolve(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    for base in (ARTHA_ROOT, BACKEND_ROOT):
        if (base / rel).exists():
            return (base / rel).resolve()
    return (ARTHA_ROOT / rel).resolve()


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _clock(o: dict, k: str):
    c = (o.get("scenario_time") or {}).get(k) or {}
    return c.get("value"), c.get("precision")


def _instant(value: str, prec: str | None, tz: ZoneInfo) -> datetime:
    """Day-precision (or offset-less) clocks are held from the END of that local day (demo-held-day-v1)."""
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        if prec == "day" or len(value) <= 10:
            return datetime.combine(d.date() + timedelta(days=1), time(0), tz)
        return d.replace(tzinfo=tz)
    return d


def _bundle(b: dict, tz: ZoneInfo, window: dict, ocr: bool) -> dict:
    root = _resolve(b["path"])
    out = {"bundle_id": b["bundle_id"], "path": b["path"], "problems": [], "notes": []}
    mpath = root / "delivery-manifest.json"
    if not mpath.is_file():
        out["problems"].append("delivery manifest missing")
        return out
    msha = _sha(mpath)
    out["pins"] = {"delivery_manifest_sha256": msha, "expected": b["delivery_manifest_sha256"],
                   "match": msha == b["delivery_manifest_sha256"]}
    if not out["pins"]["match"]:
        out["problems"].append("delivery manifest digest differs from the corpus binding")
    if b.get("runtime_zip"):
        z = _resolve(b["runtime_zip"]["path"])
        got = _sha(z) if z.is_file() else None
        out["pins"]["runtime_zip_sha256"] = {"got": got, "expected": b["runtime_zip"]["sha256"],
                                             "match": got == b["runtime_zip"]["sha256"]}
        if got != b["runtime_zip"]["sha256"]:
            out["problems"].append("runtime ZIP digest differs from Data's decision")
    m = json.loads(mpath.read_text(encoding="utf-8"))
    listed = {}
    lf = root / "INTAKE-FILES.sha256"
    if lf.is_file():
        for line in lf.read_text().splitlines():
            if line.strip():
                h, f = line.split(None, 1)
                listed[f.strip()] = h
    bad_hash, missing = [], []
    for o in m["originals"]:
        f = root / o["file"]
        if not f.is_file():
            missing.append(o["original_id"])
            continue
        h = _sha(f)
        if h != o["sha256"] or (listed and listed.get(o["file"]) not in (None, h)):
            bad_hash.append(o["original_id"])
    out["originals_hashed"] = len(m["originals"]) - len(missing)
    out["hash_mismatches"], out["missing_originals"] = bad_hash, missing
    if bad_hash or missing:
        out["problems"].append(f"{len(bad_hash)} hash mismatches, {len(missing)} missing originals")
    for nat in m.get("native_administrative_inputs", []):
        if _sha(root / nat["file"]) != nat["sha256"]:
            out["problems"].append(f"native input {nat['file']} digest mismatch")
    cases = {r["data"]["case_id"]: r for r in _jsonl(root / "native" / "case.jsonl")}
    parties = {r["data"]["party_id"]: r for r in _jsonl(root / "native" / "party.jsonl")}
    member_of_party = {pid: r["data"]["member_reference"] for pid, r in parties.items()
                       if r["data"].get("member_reference")}
    members_by_case = {cid: sorted({member_of_party[p] for p in r["data"]["party_ids"] if p in member_of_party})
                       for cid, r in cases.items()}
    members = set(member_of_party.values())
    participants = {p["actor_ref"] for p in m.get("participants", [])}
    matters = set()
    ref_problems = Counter()
    for o in m["originals"]:
        refs = o.get("references") or {}
        for c in refs.get("case_ids") or []:
            if c not in cases:
                ref_problems["case reference not a delivered case"] += 1
        for mr in refs.get("member_references") or []:
            if mr not in members:
                ref_problems["member reference not a delivered member"] += 1
        if o.get("author") and o["author"] not in participants:
            ref_problems["author not a bound participant"] += 1
        matters |= set(refs.get("afca_matter_ids") or [])
    for cid, r in cases.items():
        if not members_by_case[cid]:
            ref_problems["case without a member party"] += 1
    out["counts"] = {"members": len(members), "cases": len(cases), "originals": len(m["originals"]),
                     "afca_matters": len(matters), "parties": len(parties)}
    out["association_problems"] = dict(ref_problems)
    if ref_problems:
        out["problems"].append(f"unresolved references: {dict(ref_problems)}")
    # ---- financial/work table rows: the runtime factual-link rule (native_ledger / native_work)
    link = Counter()
    for o in m["originals"]:
        if o["kind"] not in TABLE_KINDS or o["declared_media_type"] != "text/csv":
            continue
        refs = o.get("references") or {}
        oc, om = set(refs.get("case_ids") or []), set(refs.get("afca_matter_ids") or [])
        with open(root / o["file"], newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                link["rows"] += 1
                linked = [x for x in (row.get("linked_case_ids") or "").replace(";", " ").replace(",", " ").split()
                          if x]
                afca = (row.get("afca_matter_id") or "").strip()
                if linked or afca:
                    link["full_link_occurrences"] += 1
                if row.get("case_id") and row["case_id"] not in oc:
                    link["row_case_not_linked_to_original"] += 1
                if any(x not in oc for x in linked):
                    link["linked_case_not_linked_to_original"] += 1
                if afca and afca not in om:
                    link["afca_not_linked_to_original"] += 1
    out["table_links"] = dict(link) | {
        "predicted_omissions": link["row_case_not_linked_to_original"] + link["linked_case_not_linked_to_original"]
        + link["afca_not_linked_to_original"],
        "rule": "row case, linked cases and AFCA column must belong to the original's factual associations "
                "(association_profile factual-v1; native_ledger 1.1 / native_work 1.2)"}
    if out["table_links"]["predicted_omissions"]:
        out["problems"].append(f"{out['table_links']['predicted_omissions']} table link occurrences would be "
                               "unresolved by the runtime profiles")
    # ---- temporal
    start = datetime.combine(datetime.fromisoformat(window["start"]).date(), time(0), tz)
    end = datetime.combine(datetime.fromisoformat(window["end_exclusive"]).date(), time(0), tz)
    t = Counter()
    first_avail: dict[str, datetime] = {}
    for o in m["originals"]:
        av, prec = _clock(o, "available_at")
        ev, _ = _clock(o, "event_at")
        if not av:
            t["no_available_at"] += 1
            continue
        a = _instant(av, prec, tz)
        t[f"available_precision_{prec}"] += 1
        if not (start <= a < end):
            t["available_outside_window"] += 1
        if a.astimezone(tz).time() == time(0) and prec != "day":
            t["exact_midnight_availability"] += 1
        if ev and _instant(ev, _clock(o, "event_at")[1], tz) > a:
            t["event_after_availability"] += 1
        for c in (o.get("references") or {}).get("case_ids") or []:
            first_avail[c] = min(first_avail.get(c, a), a)
    receipts = sorted(r["data"]["complaints_receipt_date"] for r in cases.values()
                      if r["data"].get("complaints_receipt_date"))
    late_native = []
    for cid, r in cases.items():
        na = r.get("envelope", {}).get("available_at")
        if na and cid in first_avail and _instant(na, None, tz) > first_avail[cid] + timedelta(days=1):
            late_native.append(cid)
    out["temporal"] = dict(t) | {"receipt_first": receipts[0] if receipts else None,
                                 "receipt_last": receipts[-1] if receipts else None,
                                 "receipt_months": len({r[:7] for r in receipts}),
                                 "native_case_visible_after_first_original": len(late_native),
                                 "native_case_visible_after_first_original_ids": sorted(late_native)[:10]}
    if t["no_available_at"] or t["event_after_availability"]:
        out["problems"].append("temporal: missing availability or event after availability")
    if t["available_outside_window"]:
        out["notes"].append(f"{t['available_outside_window']} originals available outside the observation window "
                            "(held but outside the view; the temporal policy excludes them by date)")
    if late_native:
        out["notes"].append(f"{len(late_native)} native case records become visible >1 day after the case's first "
                            "held original: replay must check early-cutoff party/case joins")
    # ---- OCR (deterministic extraction of image originals; text never kept)
    imgs = [o for o in m["originals"] if o["declared_media_type"].startswith("image/")]
    out["ocr"] = {"image_originals": len(imgs),
                  "pdf_originals": sum(o["declared_media_type"] == "application/pdf" for o in m["originals"])}
    if ocr and imgs:
        from ..extract.image import extract_image
        states = Counter()
        for o in imgs:
            res = extract_image((root / o["file"]).read_bytes())
            states[res.state] += 1
        out["ocr"]["states"] = dict(states)
        if states.get("failed"):
            out["notes"].append(f"{states['failed']} image originals fail OCR here; retained as failed extraction "
                                "(coverage disclosed), never guessed")
    # ---- population material for the forecast
    narrative = Counter()
    for o in m["originals"]:
        if o["kind"] in NARRATIVE_KINDS:
            for c in (o.get("references") or {}).get("case_ids") or []:
                narrative[c] += o["size_bytes"]
    out["_members_by_case"], out["_narrative_bytes"] = members_by_case, dict(narrative)
    out["_ids"] = {"members": members, "cases": set(cases), "originals": {o["original_id"] for o in m["originals"]},
                   "parties": set(parties), "original_sha": {o["sha256"] for o in m["originals"]},
                   "matters": matters}
    return out


def _excluded_overlap(path: str, ids: dict) -> dict:
    root = _resolve(path)
    mf = root / "delivery-manifest.json" if root.is_dir() else root
    if not mf.is_file():
        return {"path": path, "checked": False, "reason": "not present"}
    m = json.loads(mf.read_text(encoding="utf-8"))
    sha = {o["sha256"] for o in m["originals"]}
    cases = {c for o in m["originals"] for c in ((o.get("references") or {}).get("case_ids") or [])}
    return {"path": path, "checked": True, "originals": len(m["originals"]),
            "shared_original_bytes": len(sha & ids["original_sha"]), "shared_case_ids": len(cases & ids["cases"])}


def preflight_combined(corpus_path: str, *, rt=None, ocr: bool = True) -> dict:
    p = _resolve(corpus_path)
    if not p.is_file():
        return {"version": PREFLIGHT_VERSION, "state": "unrun", "corpus": corpus_path,
                "reason": "combined corpus file not present"}
    c = json.loads(p.read_text(encoding="utf-8"))
    tz = ZoneInfo(c.get("timezone", "Australia/Melbourne"))
    problems, notes = [], []
    basis = []
    for q in c["source_quality_basis"]:
        f = _resolve(q["path"])
        ok = f.is_file() and _sha(f) == q["sha256"]
        basis.append({"path": q["path"], "decision": q["decision"], "digest_match": ok})
        if not ok:
            problems.append(f"source-quality basis {q['path']} missing or changed")
    bundles = [_bundle(b, tz, c["observation_window"], ocr) for b in c["bundles"]]
    for b in bundles:
        problems += [f"{b['bundle_id']}: {x}" for x in b["problems"]]
        notes += [f"{b['bundle_id']}: {x}" for x in b["notes"]]
    # identity across bundles
    collisions = {}
    for k in ("members", "cases", "originals", "parties", "original_sha", "matters"):
        seen, dup = set(), set()
        for b in bundles:
            ids = b.get("_ids", {}).get(k, set())
            dup |= seen & ids
            seen |= ids
        collisions[k] = len(dup)
    if any(collisions.values()):
        problems.append(f"identifier collisions across bundles: {collisions}")
    union = {k: set().union(*(b.get("_ids", {}).get(k, set()) for b in bundles)) for k in
             ("members", "cases", "originals", "original_sha", "matters")}
    combined = {"members": len(union["members"]), "cases": len(union["cases"]),
                "originals": len(union["originals"]), "afca_matters": len(union["matters"])}
    exp = c["expected_counts"]
    count_match = {k: combined[k] == exp[k] for k in exp}
    if not all(count_match.values()):
        problems.append(f"combined counts {combined} differ from expected {exp}")
    excluded = []
    for x in (x for x in c.get("excluded_corpora", []) if x.get("path")):
        own = x.get("superseded_by_bundle")
        others = [b.get("_ids", {}) for b in bundles if b["bundle_id"] != own]
        other_union = {k: set().union(*(i.get(k, set()) for i in others)) for k in ("original_sha", "cases")}
        r = _excluded_overlap(x["path"], other_union) | {"label": x["label"]}
        if own and r.get("checked"):
            mine = _excluded_overlap(x["path"], next(b.get("_ids", {}) for b in bundles if b["bundle_id"] == own))
            r["superseded_by_bundle"] = own
            r["overlap_with_superseding_bundle"] = {"shared_original_bytes": mine["shared_original_bytes"],
                                                    "shared_case_ids": mine["shared_case_ids"]}
        excluded.append(r)
        if r.get("checked") and (r["shared_original_bytes"] or r["shared_case_ids"]):
            problems.append(f"excluded corpus {x['label']} overlaps the bound population")
    # split
    split = {}
    for s in c["splits"]:
        f = _resolve(s["path"])
        rows = json.loads(f.read_text(encoding="utf-8"))["members"]
        if s.get("sha256") and _sha(f) != s["sha256"]:
            problems.append(f"split allowlist {s['path']} changed")
        for r in rows:
            if r["member_reference"] in split:
                problems.append("member in more than one split allowlist")
            split[r["member_reference"]] = r["split"]
    sc = Counter(split.values())
    unsplit = union["members"] - set(split)
    foreign = set(split) - union["members"]
    early = set(c["worklist"]["early_evaluation_member_references"])
    split_res = {"development": sc.get("development", 0), "holdout": sc.get("holdout", 0),
                 "expected": c["expected_split"], "delivered_members_without_split": len(unsplit),
                 "split_members_not_delivered": len(foreign),
                 "early_evaluation_members": len(early),
                 "early_evaluation_all_development": all(split.get(m) == "development" for m in early)}
    if (split_res["development"], split_res["holdout"]) != (c["expected_split"]["development"],
                                                           c["expected_split"]["holdout"]) or unsplit or foreign \
            or not split_res["early_evaluation_all_development"]:
        problems.append(f"split does not reconcile: {split_res}")
    # forecast on the actual eligible population
    mbc = {}
    nb = {}
    for b in bundles:
        mbc |= b.get("_members_by_case", {})
        nb |= b.get("_narrative_bytes", {})
    eligible_cases = sorted(cid for cid, ms in mbc.items() if ms and all(split.get(m) == "development" for m in ms))
    holdout_cases = sorted(cid for cid, ms in mbc.items() if any(split.get(m) == "holdout" for m in ms))
    pilot_ids = set(next((b["_members_by_case"] for b in bundles if b["bundle_id"] == c.get("pilot_bundle_id")),
                         {}).keys())
    pilot_elig = [x for x in eligible_cases if x in pilot_ids]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    elig_bytes = mean([nb.get(x, 0) for x in eligible_cases])
    pilot_bytes = mean([nb.get(x, 0) for x in pilot_elig])
    fc = {"eligible_cases": len(eligible_cases), "holdout_cases_unassessed": len(holdout_cases),
          "narrative_bytes_per_eligible_case_mean": round(elig_bytes or 0),
          "pilot_narrative_bytes_per_eligible_case_mean": round(pilot_bytes or 0),
          "size_ratio_vs_pilot": round(elig_bytes / pilot_bytes, 3) if elig_bytes and pilot_bytes else None}
    if rt is not None:
        from . import discovery
        from .forecast import measured
        from .publish import current
        meas = measured(rt)
        pub = current(rt)
        n_issues = len(pub["issues"]) if pub else None
        ratio = fc["size_ratio_vs_pilot"] or 1.0
        if meas["case_tasks"]:
            mem_h = len(eligible_cases) * meas["case_seconds"]["mean"] * ratio / 3600
            batches = math.ceil(len(eligible_cases) / discovery.ASSESS_BATCH)
            coll_s = meas["collective_call_seconds_p50"] or 0
            pages = math.ceil(len(eligible_cases) * 600 / discovery.SCAN_PAGE_CHARS)
            fc |= {"measured_basis": {k: meas[k] for k in ("case_tasks", "case_seconds", "case_calls_mean",
                                                          "collective_call_seconds_p50", "providers")},
                   "memory_calls": round(len(eligible_cases) * meas["case_calls_mean"] * max(1.0, ratio)),
                   "memory_wall_hours_serial": round(mem_h, 1),
                   "issues_in_current_registry": n_issues,
                   "assessment_pairs": len(eligible_cases) * (n_issues or 0),
                   "assessment_batches": batches, "retrieval_reask_calls_max": batches,
                   "discovery_pages": pages,
                   "collective_wall_hours_serial": round((pages + 2 * batches) * coll_s / 3600, 1),
                   "total_wall_hours_serial": round(mem_h + (pages + 2 * batches) * coll_s / 3600, 1)}
        else:
            fc["measured_basis"] = "no measured calls in this store"
    fc |= {"selected_provider": c.get("selected_provider", "gemma-local"),
           "google_route": "not qualified: requires the explicitly authorised Google step (key + free-tier "
                           "attestation or approved billing); hosted latency is unmeasured",
           "note": "serial single-device extrapolation from this store's measured local calls; not a measured "
                   "commitment. The review's full-650 serial figure (~36.5 h) is likewise an extrapolation."}
    passed = not problems
    return {"version": PREFLIGHT_VERSION, "corpus_id": c["corpus_id"], "corpus": corpus_path,
            "corpus_sha256": _sha(p), "states": {
                "data_delivered": "yes: cloud480 accepted as integration data candidate with disclosed limitations; "
                                  "P4 accepted for bounded source quality",
                "runtime_merged": "no: the intake binds one bundle per demo store; combined ingestion is not built "
                                  "or run",
                "preflight_passed": "yes" if passed else "no",
                "inference_run": "no: no model call was made; full-corpus inference awaits explicit authorisation"},
            "source_quality_basis": basis, "combined_counts": combined, "expected_counts": exp,
            "count_match": count_match, "identity_collisions": collisions, "split": split_res,
            "excluded_corpora": excluded + [{"label": x["label"], "reason": x["reason"]}
                                            for x in c.get("excluded_corpora", []) if not x.get("path")],
            "bundles": [{k: v for k, v in b.items() if not k.startswith("_")} for b in bundles],
            "forecast": fc, "problems": problems, "notes": notes,
            "state": "passed" if passed else "failed"}
