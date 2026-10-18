"""Demo-store intake (M1): exact corpus receipt → factual-association ingest → crosswalk, tables, OCR coverage.

Nothing is repaired in the evidence. The crosswalk records, per authored original, the canonical asset/source IDs,
content hashes, factual case/AFCA links, author and clock precision (the day-precision sidecar used by the
`demo-held-day-v1` temporal policy). Table projections (ledger/work/lifecycle) are deterministic and recomputed
from sealed sources; they are reported here for the M1 evidence only.
"""
from __future__ import annotations

import json
from collections import Counter

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..config import BACKEND_ROOT
from ..ingest.build import ingest
from ..intake.delivery import receive
from .corpus import DEMO_POINTER, Corpus, case_members, demo_release_view, eligible_cases

CROSSWALK_VERSION = "sdb-intel-crosswalk-1.0.0"


def _clock(entry: dict, key: str) -> dict | None:
    v = (entry.get("scenario_time") or {}).get(key)
    if not v or not v.get("value"):
        return None
    prec = str(v.get("precision") or "").lower()
    day = prec in ("day", "date") or len(v["value"]) == 10
    return {"value": v["value"], "precision": "day" if day else "instant"}


def build_crosswalk(rt, delivery, release_id: str) -> dict:
    rel = rt.db.get_object("release", release_id)
    ids = {(e["kind"], e["immutable_id"]) for e in rel["entries"]}
    assets = {e["immutable_id"]: rt.db.get_object("asset", e["immutable_id"]) for e in rel["entries"]
              if e["kind"] == "asset"}
    rows = []
    mapping = (rt.db.get_object("release_control", release_id) or {}).get("original_assets") or {}
    for o in delivery.originals:
        e = o.entry
        a = assets.get(mapping.get(e["original_id"]))
        src_ids = a["source_ids"] if a else []
        ext = rt.db.get_object("extraction", a["extraction_ids"][0]) if a and a["extraction_ids"] else None
        rows.append({
            "original_id": e["original_id"], "original_sha256": e["sha256"], "filename": e["filename"],
            "kind": e.get("kind"), "semantic_type": e.get("semantic_type"), "source_family": e.get("source_family"),
            "asset_version_id": a["asset_version_id"] if a else None,
            "source_ids": src_ids, "source_content_sha256": [rt.db.get_object("source", s)["data"]["content_sha256"]
                                                              for s in src_ids if ("input", s) in ids],
            "extraction_state": ext["state"] if ext else "no_asset",
            "extraction_omissions": ext["omissions"] if ext else [],
            "case_ids": sorted((e.get("references") or {}).get("case_ids") or []),
            "afca_matter_ids": sorted((e.get("references") or {}).get("afca_matter_ids") or []),
            "author": e.get("author"), "author_role": e.get("author_role"),
            "recipients": e.get("recipients", []), "cc": e.get("cc", []),
            "logical_document_id": e.get("logical_document_id"), "lineage": e.get("lineage"),
            "clocks": {k: _clock(e, k) for k in ("event_at", "created_at", "recorded_at", "available_at")},
            "resolved": a is not None})
    participants = {p["actor_ref"]: {"role": p.get("role"), "display_name": p.get("display_name")}
                    for p in delivery.manifest.get("participants", [])}
    body = {"crosswalk_version": CROSSWALK_VERSION, "release_id": release_id,
            "delivery_manifest_sha256": delivery.manifest_sha256, "rows": rows, "participants": participants,
            "unresolved_originals": [r["original_id"] for r in rows if not r["resolved"]]}
    cid = derived_id("XWALK", release_id, canonical_sha256(body))
    rt.db.put_object("intel_crosswalk", cid, body, links={"release": [release_id]})
    return {"crosswalk_id": cid, **body}


def crosswalk_for(rt, release_id: str) -> dict | None:
    ids = rt.db.linked("intel_crosswalk", "release", release_id)
    return rt.db.get_object("intel_crosswalk", ids[-1]) if ids else None


def availability_sidecar(rt, release_id: str) -> dict[str, dict]:
    """source_id → {"precision": "day", "date": D} for sources whose supplied availability had day precision.
    The envelope instant for those is next local midnight (legacy contract); this sidecar keeps the truth."""
    xw = crosswalk_for(rt, release_id) or {"rows": []}
    out = {}
    for r in xw["rows"]:
        av = (r.get("clocks") or {}).get("available_at") or (r.get("clocks") or {}).get("recorded_at")
        if av and av["precision"] == "day":
            for s in r["source_ids"]:
                out[s] = {"precision": "day", "date": av["value"][:10]}
    return out


def receive_corpus(rt, corpus: Corpus):
    """The corpus's exact delivery. A composite corpus (`bundles`) receives each named bundle through the normal
    receiver (each manifest digest verified), then joins them without changing any byte, original id, native row or
    participant; any identity collision refuses the merge. The composite identity is the ordered bundle digests."""
    from ..config import ARTHA_ROOT
    from ..intake.delivery import Delivery
    from .corpus import composite_manifest_sha256
    if not corpus.composite:
        return receive(corpus.bundle, rt=rt)
    parts = []
    for b in corpus.body["bundles"]:
        d = receive((ARTHA_ROOT / b["path"]).resolve(), rt=rt)
        if d.manifest_sha256 != b["delivery_manifest_sha256"]:
            raise SystemExit(f"bundle {b['bundle_id']}: received manifest differs from the corpus file")
        if d.manifest.get("previews"):
            raise SystemExit(f"bundle {b['bundle_id']}: previews are not supported in a composite delivery")
        parts.append((b["bundle_id"], d))
    seen_o, seen_n, people = {}, {}, {}
    for bid, d in parts:
        for o in d.originals:
            if o.entry["original_id"] in seen_o:
                raise SystemExit(f"original id collision {o.entry['original_id']} ({seen_o[o.entry['original_id']]}, {bid})")
            seen_o[o.entry["original_id"]] = bid
        for n in d.natives:
            rid = n.record["envelope"]["record_id"]
            if rid in seen_n:
                raise SystemExit(f"native record id collision {rid} ({seen_n[rid]}, {bid})")
            seen_n[rid] = bid
        for p in d.manifest.get("participants", []):
            if p["actor_ref"] in people and people[p["actor_ref"]] != p:
                raise SystemExit(f"participant {p['actor_ref']} differs between bundles")
            people[p["actor_ref"]] = p
    windows = {json.dumps(d.manifest.get("scope", {}).get("observation_window"), sort_keys=True) for _, d in parts}
    if len(windows) != 1:
        raise SystemExit("bundles declare different observation windows")
    first = parts[0][1].manifest
    sha = composite_manifest_sha256(corpus.body)
    manifest = {"manifest_kind": first["manifest_kind"], "manifest_version": first["manifest_version"],
                "note": "composite of exact received bundles; see composite_of",
                "batch": {"batch_id": corpus.corpus_id, "package_version": sha[:16],
                          "contract_pin": first["batch"]["contract_pin"], "purpose": "corpus"},
                "scope": {**first.get("scope", {}), "cases": sum(d.manifest.get("scope", {}).get("cases", 0)
                                                                 for _, d in parts)},
                "participants": list(people.values()),
                "advertised_unheld": [a for _, d in parts for a in d.manifest.get("advertised_unheld", [])],
                "previews": [],
                "coverage": {"note": " | ".join(d.manifest.get("coverage", {}).get("note", "") for _, d in parts),
                             "native_inputs_note": " | ".join(d.manifest.get("coverage", {}).get(
                                 "native_inputs_note", "") for _, d in parts)},
                "composite_of": [{"bundle_id": bid, "manifest_sha256": d.manifest_sha256,
                                  "batch_id": d.manifest["batch"]["batch_id"],
                                  "package_version": d.manifest["batch"]["package_version"]} for bid, d in parts]}
    receipt = {"receipt_kind": "sdb-composite-intake-receipt", "receipt_version": "1.0.0",
               "receipt_id": derived_id("RCPT", sha, *[d.receipt["receipt_id"] for _, d in parts]),
               "received_at": utc_now_iso(), "composite_manifest_sha256": sha,
               "bundles": [{"bundle_id": bid, **d.receipt} for bid, d in parts],
               "summary": {"originals_listed": len(seen_o), "native_records": len(seen_n),
                           "participants": len(people)}}
    return Delivery(parts[0][1].bundle_dir, manifest, sha, [o for _, d in parts for o in d.originals], receipt,
                    [n for _, d in parts for n in d.natives])


def run_intake(rt, corpus: Corpus) -> dict:
    """Receive and ingest the exact corpus bundle into this (demo) store; idempotent for identical inputs."""
    if rt.settings.publication_policy != "synthetic-demo-v1":
        raise SystemExit("demo intake requires SDB_PUBLICATION_POLICY=synthetic-demo-v1 (use scripts/demo.sh)")
    if "var/demo/" not in str(rt.settings.data_dir).replace("\\", "/"):
        raise SystemExit(f"refusing demo intake outside a var/demo store: {rt.settings.data_dir}")
    delivery = receive_corpus(rt, corpus)
    if delivery.manifest_sha256 != corpus.body["delivery_manifest_sha256"]:
        raise SystemExit("received manifest differs from the corpus file")
    summary = ingest(rt, delivery, associations=corpus.body.get("association_profile", "factual-v1"),
                     demo=corpus.demo_binding())
    xw = build_crosswalk(rt, delivery, summary["release_id"])
    rv = demo_release_view(rt)
    members = case_members(rt, rv) if rv else {}
    elig, not_elig = eligible_cases(rt, rv, corpus) if rv else ([], [])
    from .corpus import reports_dir
    reports = reports_dir(corpus)
    receipt = reports / "INTAKE-RECEIPT.json"
    receipt.write_text(json.dumps(delivery.receipt, indent=1, ensure_ascii=False), encoding="utf-8")
    from .tables import project_release
    tables = project_release(rt, rv) if rv else {}
    out = {"generated_at": utc_now_iso(), "corpus_id": corpus.corpus_id, "pointer": DEMO_POINTER,
           "served": rv is not None, "release": {k: summary[k] for k in summary if k != "limitations"},
           "limitations": summary["limitations"], "crosswalk_id": xw["crosswalk_id"],
           "unresolved_originals": xw["unresolved_originals"],
           "originals_by_extraction_state": dict(Counter(r["extraction_state"] for r in xw["rows"])),
           "failed_or_partial": [{k: r[k] for k in ("original_id", "filename", "extraction_state",
                                                     "extraction_omissions")}
                                 for r in xw["rows"] if r["extraction_state"] != "complete"],
           "population": {"cases": len(members), "members": len({m for ms in members.values() for m in ms}),
                          "eligible_cases": len(elig), "not_eligible_cases": len(not_elig)},
           "tables": tables.get("summary", {})}
    (reports / "INTAKE-SUMMARY.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
