"""Isolated TEST SPECIMENS: specimen native rows, specimen delivery copies and specimen-reviewer decisions.

Everything here lives in pytest temporary directories/stores. None of it is a real review, a native register row
from the Data coder, or a finding; nothing here is ever published on the served release.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from itertools import count
from pathlib import Path

from sdbackend.canonical import canonical_sha256
from sdbackend.contracts import EXPECTED_MANIFEST_SHA256

_seq = count(1)
ACCESS = {"view_ids": ["complaints-leadership"], "classification": "standard", "redaction_state": "none"}


def case_row(cid, received, *, record_id=None, supersedes=None, available="2026-04-01T00:00:00+10:00",
             access=None, complaints=None):
    return {"schema_version": "0.2.0", "kind": "case",
            "envelope": {"record_id": record_id or f"REC-{cid}", "logical_id": cid, "supersedes_record_id": supersedes,
                         "recorded_at": available, "available_at": available, "occurred_at": None,
                         "occurrence_date": None, "access": access or ACCESS, "evidence_refs": []},
            "data": {"case_id": cid, "party_ids": [], "broad_tags": [], "original_receipt_date": received,
                     "complaints_receipt_date": complaints if complaints is not None else received,
                     "manager_reference": None, "product_or_journey": None, "channel": "unknown"}}


def specimen_bundle(tmp: Path, base: Path, natives: list[dict], name="bundle") -> Path:
    """Copy of a delivery bundle plus specimen native JSONL files per record kind (manifest + INTAKE-FILES updated
    in the copy only)."""
    out = tmp / name
    shutil.copytree(base, out)
    (out / "native").mkdir(exist_ok=True)
    m = json.loads((out / "delivery-manifest.json").read_text())
    m["native_administrative_inputs"] = []
    files = {}
    for kind in sorted({r["kind"] for r in natives}):
        data = "".join(json.dumps(r, sort_keys=True) + "\n" for r in natives if r["kind"] == kind).encode()
        rel = f"native/{kind}.jsonl"
        (out / rel).write_bytes(data)
        files[rel] = data
        m["native_administrative_inputs"].append({"file": rel, "sha256": hashlib.sha256(data).hexdigest(),
                                                  "size_bytes": len(data), "record_kind": kind, "schema_version": "0.2.0"})
    raw = json.dumps(m, indent=1).encode()
    (out / "delivery-manifest.json").write_bytes(raw)
    sums = out / "INTAKE-FILES.sha256"
    lines = [l for l in sums.read_text().splitlines() if l.strip() and not l.endswith("delivery-manifest.json")]
    lines += [f"{hashlib.sha256(raw).hexdigest()}  delivery-manifest.json"]
    lines += [f"{hashlib.sha256(d).hexdigest()}  {rel}" for rel, d in files.items()]
    sums.write_text("\n".join(lines) + "\n")
    return out


def mini_bundle(tmp: Path, originals: list[dict], name="mini") -> Path:
    """Minimal specimen delivery: originals = [{original_id, rel, data, media, case, available, precision?}]."""
    out = tmp / name
    entries, lines = [], []
    for o in originals:
        f = out / o["rel"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(o["data"])
        digest = hashlib.sha256(o["data"]).hexdigest()
        lines.append(f"{digest}  {o['rel']}")
        st = {"available_at": {"value": o["available"], "precision": o.get("precision", "second")}}
        if o.get("created"):
            st["created_at"] = {"value": o["created"], "precision": o.get("precision", "second")}
        entries.append({"original_id": o["original_id"], "kind": o.get("kind", "attachment_original"), "file": o["rel"],
                        "filename": Path(o["rel"]).name, "declared_media_type": o["media"], "size_bytes": len(o["data"]),
                        "sha256": digest, "version": 1, "source_family": "S04",
                        "references": {"case": o["case"]}, "scenario_time": st})
    m = {"manifest_kind": "pre-extraction-delivery-manifest", "manifest_version": "0.1.0",
         "batch": {"batch_id": f"specimen-{name}", "package_version": 1,
                   "contract_pin": {"manifest_content_sha256": EXPECTED_MANIFEST_SHA256}},
         "participants": [], "originals": entries, "attachment_occurrences": [], "advertised_unheld": [],
         "previews": [], "native_administrative_inputs": [],
         "scope": {"observation_window": {"start": "2025-10-01", "end_exclusive": "2026-10-01"}}}
    raw = json.dumps(m, indent=1).encode()
    (out / "delivery-manifest.json").write_bytes(raw)
    lines.append(f"{hashlib.sha256(raw).hexdigest()}  delivery-manifest.json")
    (out / "INTAKE-FILES.sha256").write_text("\n".join(lines) + "\n")
    return out


def decision(gate, subjects, *, result="pass", reviewer="specimen-reviewer", decided_at="2026-10-14T10:00:00+11:00",
             decision_id=None):
    return {"decision_format": "sdb-review-decision", "format_version": "1.0.0",
            "decision_id": decision_id or f"DEC-SPECIMEN-{next(_seq)}", "reviewer_id": reviewer, "independent": True,
            "decided_at": decided_at, "contract_baseline": EXPECTED_MANIFEST_SHA256, "gate": gate,
            "evidence_report": {"name": "pytest specimen report", "sha256": "a" * 64},
            "subjects": [{**s, "result": s.get("result", result), "limitations": ["test specimen; not a real review"]}
                         for s in subjects]}


def decide(rt, gate, subjects, **kw):
    from sdbackend.acceptance import import_decision
    return import_decision(rt, decision(gate, subjects, **kw))


def approve_required(rt, prepared, *, skip=()):
    for r in prepared["required_decisions"]:
        if r["gate"] in skip:
            continue
        decide(rt, r["gate"], [{k: r[k] for k in ("subject_kind", "subject_id", "subject_sha256")}])


def approve_originals(rt, delivery, reviewer="specimen-reviewer"):
    subs = [{"subject_kind": "supplied_original", "subject_id": o.entry["original_id"],
             "subject_sha256": hashlib.sha256(o.data).hexdigest()} for o in delivery.originals]
    return decide(rt, "source", subs, reviewer=reviewer)


def ingest_specimen(rt, tmp, base, natives, *, approve_natives=True):
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    bundle = specimen_bundle(tmp, base, natives)
    delivery = receive(bundle, rt=rt)
    if approve_natives:
        decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                     "subject_sha256": n.record_sha256} for n in delivery.natives])
    return ingest(rt, receive(bundle, rt=rt)), bundle


def publish_native(rt, release_id, *, assessed="2026-10-01T00:00:00+10:00", stages=()):
    from sdbackend.engine.publish import prepare_closure, publish_analysis_set
    prepared = prepare_closure(rt, release_id=release_id, assessed_evidence_as_of=assessed, stage_ids=list(stages))
    approve_required(rt, prepared)
    return publish_analysis_set(rt, candidate_id=prepared["candidate_id"], notes=["pytest specimen"]), prepared


def record_sha(rec):
    return canonical_sha256(rec)
