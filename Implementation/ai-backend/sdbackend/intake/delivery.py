"""Receiving validator for the Data coder's pre-extraction delivery (`pre-extraction-delivery-manifest` 0.1.x).

Only files enumerated by the manifest are read; nothing is ingested recursively. Every byte is re-hashed; manifest
sizes/hashes are never trusted. Restricted/private/gold material is never opened.

Independent review decisions are NOT read from the bundle or from an ad-hoc list. They are imported separately
into the acceptance registry (`sdbackend/acceptance.py`, `python -m sdbackend import-decision`) and bound to the
exact received bytes (originals) or exact canonical record digest (native rows). The receiver looks dispositions up
by (original_id, actual sha256), so changed bytes can never inherit an earlier approval.

Native administrative inputs (v0.2 `case`/`party`/`procedure` input records as JSONL) are received through the
same allowlisted path: re-hashed, schema-validated against the frozen runtime schema, and staged pending their own
`native_input` review. They never arrive by a side-door publication argument.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..canonical import canonical_sha256, derived_id, utc_now_iso
from ..contracts import EXPECTED_MANIFEST_SHA256
from ..extract.registry import validate_bytes

SUPPORTED_MANIFEST = ("pre-extraction-delivery-manifest", ("0.1.0", "0.2.0"))
NATIVE_KINDS = ("case", "party", "procedure")
MAX_NATIVE_FILE_BYTES = 5 * 1024 * 1024
FORBIDDEN_PATH_PARTS = {"restricted", "private", "private-controls", "gold", "world", "conversations", "archive",
                        "review", "evaluation", "holdout"}
RECEIVER_VERSION = "sdb-intake-1.1.0"

KIND_MEDIA = {"email": "message/rfc822", "call_summary": "text/plain", "letter": "application/pdf",
              "portal_submission": "application/json"}
FORMAT_MEDIA = {"PDF": "application/pdf", "JPG": "image/jpeg", "JPEG": "image/jpeg", "PNG": "image/png",
                "CSV": "text/csv", "TXT": "text/plain", "EML": "message/rfc822", "JSON": "application/json",
                "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class DeliveryRejected(RuntimeError):
    pass


@dataclass
class ReceivedOriginal:
    entry: dict
    path: Path
    data: bytes
    media_type: str
    disposition: str          # staged_pending_review | accepted_by_review | rejected
    reasons: list[str] = field(default_factory=list)


@dataclass
class ReceivedNative:
    record: dict
    record_sha256: str
    file: str
    disposition: str          # staged_pending_review | accepted_by_review | rejected
    acceptance_id: str | None = None


@dataclass
class Delivery:
    bundle_dir: Path
    manifest: dict
    manifest_sha256: str
    originals: list[ReceivedOriginal]
    receipt: dict
    natives: list[ReceivedNative] = field(default_factory=list)


def _safe_relpath(bundle: Path, rel: str) -> Path:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise DeliveryRejected(f"unsafe path in manifest: {rel}")
    if FORBIDDEN_PATH_PARTS & {part.lower() for part in p.parts}:
        raise DeliveryRejected(f"manifest references a restricted/private location: {rel}")
    full = (bundle / rel).resolve()
    if bundle.resolve() not in full.parents:
        raise DeliveryRejected(f"path escapes the intake bundle: {rel}")
    return full


def _media_type(entry: dict) -> str:
    if entry.get("declared_media_type"):
        return entry["declared_media_type"]
    if entry.get("format") and entry["format"].upper() in FORMAT_MEDIA:
        return FORMAT_MEDIA[entry["format"].upper()]
    if entry.get("kind") in KIND_MEDIA:
        return KIND_MEDIA[entry["kind"]]
    return entry.get("detected_media_type") or "application/octet-stream"


def _disposition(rt, gate: str, kind: str, sid: str, sha: str | None) -> tuple[str, str | None, str | None]:
    """(decision, reviewer, acceptance_id) from the acceptance registry for the exact digest."""
    if rt is None or sha is None:
        return "pending", None, None
    from ..acceptance import effective
    rec = effective(rt, gate, kind, sid, sha)
    if rec is None:
        return "pending", None, None
    return {"pass": "accepted", "fail": "rejected", "repair_required": "repair_required",
            "withdrawn": "withdrawn"}.get(rec["result"], "pending"), rec["checker"], rec["acceptance_id"]


def receive(bundle_dir: Path, *, rt=None, review_decisions: Path | None = None) -> Delivery:
    """`rt` (optional) supplies imported review decisions. `review_decisions` (legacy unbound list) is refused."""
    if review_decisions is not None:
        raise DeliveryRejected("unbound review-decision lists are no longer accepted; import an sdb-review-decision "
                               "document bound to exact byte digests with `python -m sdbackend import-decision`")
    bundle = Path(bundle_dir).resolve()
    manifest_path = bundle / "delivery-manifest.json"
    raw = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    problems: list[str] = []

    kind, versions = SUPPORTED_MANIFEST
    if manifest.get("manifest_kind") != kind or manifest.get("manifest_version") not in versions:
        raise DeliveryRejected(f"unsupported delivery manifest {manifest.get('manifest_kind')}@{manifest.get('manifest_version')}")
    pin = manifest.get("batch", {}).get("contract_pin", {})
    if pin.get("manifest_content_sha256") != EXPECTED_MANIFEST_SHA256:
        raise DeliveryRejected("delivery is pinned to a different Shared contract")

    listed_hashes: dict[str, str] = {}
    sums = bundle / "INTAKE-FILES.sha256"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, rel = line.split(None, 1)
                listed_hashes[rel.strip()] = digest
        if listed_hashes.get("delivery-manifest.json") != manifest_sha:
            raise DeliveryRejected("delivery-manifest.json digest differs from INTAKE-FILES.sha256")
    else:
        problems.append("INTAKE-FILES.sha256 absent; manifest digest not independently listed")

    originals: list[ReceivedOriginal] = []
    entries = []
    seen_ids = set()
    for entry in manifest.get("originals", []):
        oid = entry["original_id"]
        reasons: list[str] = []
        if oid in seen_ids:
            raise DeliveryRejected(f"duplicate original_id {oid}")
        seen_ids.add(oid)
        path = _safe_relpath(bundle, entry["file"])
        data = path.read_bytes() if path.is_file() else b""
        actual = hashlib.sha256(data).hexdigest() if path.is_file() else None
        media = _media_type(entry)
        if actual is None:
            reasons.append("file listed but not present")
        else:
            if actual != entry.get("sha256"):
                reasons.append("actual SHA-256 differs from manifest")
            if listed_hashes and listed_hashes.get(entry["file"]) != actual:
                reasons.append("actual SHA-256 differs from INTAKE-FILES.sha256")
            if len(data) != entry.get("size_bytes"):
                reasons.append("actual byte size differs from manifest")
            ok, detected, why = validate_bytes(data, media)
            if not ok:
                reasons.append(why)
        decision, reviewer, acc_id = _disposition(rt, "source", "supplied_original", oid, actual)
        review = {"decision": decision, "reviewer": reviewer, "acceptance_id": acc_id}
        if reasons:
            disposition = "rejected"
        elif decision == "accepted":
            disposition = "accepted_by_review"
        elif decision in ("rejected", "withdrawn"):
            disposition = "rejected"
            reasons.append(f"{decision} by independent source review")
        else:
            disposition = "staged_pending_review"
            if decision == "repair_required":
                reasons.append("independent source review requires repair; staged only")
        if disposition != "rejected":
            originals.append(ReceivedOriginal(entry, path, data, media, disposition, reasons))
        entries.append({"original_id": oid, "file": entry["file"], "received_bytes_sha256": actual,
                        "received_byte_size": len(data) if actual else None, "media_type": media,
                        "byte_checks": "pass" if not [r for r in reasons if "independent source review" not in r] else "fail",
                        "source_review": review["decision"], "reviewer": review.get("reviewer"),
                        "review_acceptance_id": review.get("acceptance_id"),
                        "disposition": disposition, "reasons": reasons})

    by_id = {o.entry["original_id"]: o for o in originals}
    occurrence_checks = []
    for occ in manifest.get("attachment_occurrences", []):
        target = by_id.get(occ.get("references_original"))
        ok = target is not None and target.entry["sha256"] == occ.get("sha256")
        occurrence_checks.append({"parent_message_file": occ["parent_message_file"],
                                  "manifest_mime_part_path": occ.get("mime_part_path"),
                                  "references_original": occ.get("references_original"),
                                  "check": "bytes_reference_consistent" if ok else "unresolved_or_mismatched",
                                  "note": "MIME path re-derived from the actual EML during extraction"})
    unheld = []
    for adv in manifest.get("advertised_unheld", []):
        if adv.get("file") or adv.get("sha256") or adv.get("size_bytes"):
            problems.append(f"advertised_unheld {adv.get('staging_asset_ref')} carries bytes/hash; rejected")
            continue
        unheld.append({"staging_asset_ref": adv.get("staging_asset_ref"), "advertised_in": adv.get("advertised_in"),
                       "state": "recorded_unheld_no_bytes"})

    natives, native_rows = _receive_natives(bundle, manifest, listed_hashes, rt)

    listed_files = ({e["file"] for e in manifest.get("originals", [])} | {p["preview_file"] for p in manifest.get("previews", [])}
                    | {n.get("file") for n in manifest.get("native_administrative_inputs", []) if isinstance(n, dict)})
    on_disk = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()}
    unlisted = sorted(on_disk - listed_files - {"delivery-manifest.json", "INTAKE-FILES.sha256"})

    received_at = utc_now_iso()   # each receipt is a distinct actual receiving event
    receipt = {
        "receipt_kind": "sdb-intake-receipt", "receipt_version": "1.0.0", "receiver": RECEIVER_VERSION,
        "receipt_id": derived_id("RCPT", manifest_sha, canonical_sha256(entries), received_at),
        "received_at": received_at,
        "delivery": {"batch_id": manifest["batch"]["batch_id"], "package_version": manifest["batch"]["package_version"],
                     "manifest_kind": manifest["manifest_kind"], "manifest_version": manifest["manifest_version"],
                     "manifest_sha256": manifest_sha, "contract_pin_verified": True},
        "originals": entries,
        "attachment_occurrences": occurrence_checks,
        "advertised_unheld": unheld,
        "previews": [{"preview_file": p["preview_file"], "preview_of": p["preview_of"],
                      "treatment": "display aid only; not evidence; served sandboxed if registered"} for p in manifest.get("previews", [])],
        "unlisted_files_ignored": unlisted,
        "native_administrative_inputs": native_rows,
        "problems": problems,
        "summary": {
            "originals_listed": len(entries),
            "byte_checks_passed": sum(e["byte_checks"] == "pass" for e in entries),
            "accepted_by_independent_review": sum(e["disposition"] == "accepted_by_review" for e in entries),
            "staged_pending_review": sum(e["disposition"] == "staged_pending_review" for e in entries),
            "rejected": sum(e["disposition"] == "rejected" for e in entries),
        },
        "statement": ("Receipt of bytes and mechanical checks only. Source acceptance, extraction fidelity and "
                      "diagnostic gold are separate gates; no item is accepted by this receipt."),
    }
    return Delivery(bundle, manifest, manifest_sha, originals, receipt, natives)


def _receive_natives(bundle: Path, manifest: dict, listed_hashes: dict, rt) -> tuple[list[ReceivedNative], list[dict]]:
    """Native v0.2 input rows (JSONL). Each file is re-hashed; each row is schema-validated and individually staged
    pending a `native_input` review bound to its canonical digest. Invalid rows are rejected with reasons."""
    from ..canonical import canonical_sha256 as _csha
    from ..contracts import load_contracts
    contracts = load_contracts()
    out, rows = [], []
    for spec in manifest.get("native_administrative_inputs", []) or []:
        if not isinstance(spec, dict) or not spec.get("file"):
            rows.append({"file": None, "state": "rejected", "reasons": ["native input entry without a file"]})
            continue
        path = _safe_relpath(bundle, spec["file"])
        reasons = []
        data = path.read_bytes() if path.is_file() else None
        if data is None:
            reasons.append("file listed but not present")
        elif len(data) > MAX_NATIVE_FILE_BYTES:
            reasons.append("native input file exceeds 5 MiB")
        else:
            actual = hashlib.sha256(data).hexdigest()
            if actual != spec.get("sha256"):
                reasons.append("actual SHA-256 differs from manifest")
            if listed_hashes and listed_hashes.get(spec["file"]) != actual:
                reasons.append("actual SHA-256 differs from INTAKE-FILES.sha256")
            rec_kind = spec.get("record_kind") or spec.get("kind")
            schema_ver = spec.get("schema_version") or "0.2.0"
            if schema_ver != "0.2.0" or rec_kind not in NATIVE_KINDS:
                reasons.append("native input must declare schema_version 0.2.0 and record_kind case|party|procedure")
        file_rows = []
        if not reasons:
            for n, line in enumerate(data.decode("utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    file_rows.append({"line": n, "state": "rejected", "reasons": ["not JSON"]})
                    continue
                errs = contracts.errors("input_record", rec, document="runtime")
                if not errs and rec.get("kind") != rec_kind:
                    errs = [f"row kind {rec.get('kind')} differs from declared {rec_kind}"]
                if errs:
                    file_rows.append({"line": n, "state": "rejected", "reasons": errs[:3]})
                    continue
                sha = _csha(rec)
                rid = rec["envelope"]["record_id"]
                decision, reviewer, acc_id = _disposition(rt, "native_input", "native_record", rid, sha)
                disposition = ("accepted_by_review" if decision == "accepted" else
                               "rejected" if decision in ("rejected", "withdrawn") else "staged_pending_review")
                file_rows.append({"line": n, "record_id": rid, "record_sha256": sha, "state": disposition,
                                  "reviewer": reviewer})
                if disposition != "rejected":
                    out.append(ReceivedNative(rec, sha, spec["file"], disposition, acc_id))
        rows.append({"file": spec["file"], "record_kind": spec.get("record_kind"), "state": "rejected" if reasons else "received",
                     "reasons": reasons, "rows": file_rows})
    ids = [n.record["envelope"]["record_id"] for n in out]
    if len(ids) != len(set(ids)):
        raise DeliveryRejected("duplicate native record_id across native input files")
    return out, rows
