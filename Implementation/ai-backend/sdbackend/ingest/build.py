"""Canonical ingestion: received delivery → sealed assets/extractions/sources/attachments/locators → release.

The single production path for source identity and content hashes (Data readiness sign-off, AI/02 §1). Every
object is schema-checked against the frozen contracts before sealing. A release is published to the `current`
pointer only when every included item has an effective independent pass bound to its exact bytes/record digest
(acceptance registry); otherwise it is sealed as an `unreviewed_candidate` that the read service serves only when
the operator explicitly enables it.

Review promotion never rewrites sealed objects: asset/extraction/source identities and bodies are independent of
review state (the asset's `acceptance_id` is its intake record). The review state lives in the release entries'
`acceptance_id` and the release's own acceptance, so staged → reviewed creates a NEW release over the same objects.
Identical bytes supplied as separate originals for a different case or time keep their own asset occurrence (and
case links); only true duplicates within one occurrence context collapse to one asset.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..canonical import canonical_bytes, canonical_sha256, derived_id, sha256_bytes, source_content_sha256, utc_now_iso
from ..citations import QuoteError, find_quote, map_citation, text_ref
from ..acceptance import effective as _effective_decision, is_pass, mechanical
from ..contracts import (BASELINE_REGISTRY_ID, COMPANION_REGISTRY_ID, CONTRACT_VERSION, EXPECTED_MANIFEST_SHA256,
                         RUNTIME_SCHEMA_VERSION)
from ..extract.base import ExtractionOutput
from ..extract.registry import extract
from ..engine.core import MELBOURNE, melbourne_midnight
from ..extract.base import MAX_NESTING_DEPTH, MAX_TOTAL_CHILD_BYTES
from ..intake.delivery import RECEIVER_VERSION, Delivery, ReceivedOriginal
from ..policy import policy_binding, record_access
from ..runtime import Runtime
from ..store.blobs import blob_reference

INGEST_VERSION = "sdb-ingest-1.1.0"
CURRENT_POINTER = "release:current"
CANDIDATE_POINTER = "release:candidate"
UNHELD_CUES = ("not attached", "none is held", "is not held", "not held", "unable to attach", "could not attach")


@dataclass
class _AssetBuild:
    asset: dict
    data: bytes
    profile: Any = None
    output: ExtractionOutput | None = None
    extraction: dict | None = None
    source: dict | None = None
    source_record: dict | None = None
    entry: dict | None = None            # manifest entry when separately supplied
    parent: "_AssetBuild | None" = None
    children: list[tuple[str, "_AssetBuild"]] = field(default_factory=list)


DAY_PRECISIONS = ("day", "date")


def _scenario_raw(entry: dict, *keys: str) -> tuple[str, str] | None:
    """(value, precision) of the first present scenario time; precision defaults to instant for offset values."""
    st = entry.get("scenario_time", {})
    for k in keys:
        if st.get(k) and st[k].get("value"):
            v = st[k]["value"]
            prec = str(st[k].get("precision") or "").lower()
            if prec in DAY_PRECISIONS or (len(v) == 10 and v[4] == "-" and v[7] == "-"):
                return v[:10], "day"
            return v, "instant"
    return None


def _time_value(raw: tuple[str, str] | None, reason: str) -> dict:
    """Shared time_value preserving supplied precision: a day-only value never becomes an invented instant."""
    if raw is None:
        return {"precision": "unknown", "reason": reason}
    value, prec = raw
    return {"precision": "day", "date": value} if prec == "day" else {"precision": "instant", "instant": value}


def _available_instant(raw: tuple[str, str] | None) -> str | None:
    """Envelope instants must be date-times. A day-only availability is NOT assumed before that day ends: it is
    available from the next Melbourne local midnight (DECISIONS: date-only availability on B is not before B)."""
    if raw is None:
        return None
    value, prec = raw
    if prec != "day":
        return value
    from datetime import date as _date, timedelta as _td
    return melbourne_midnight(_date.fromisoformat(value) + _td(days=1)).isoformat()


def _scenario(entry: dict, *keys: str) -> str | None:
    raw = _scenario_raw(entry, *keys)
    return None if raw is None else _available_instant(raw)


def _acceptance(rt: Runtime, *, subject_kind: str, subject_id: str, subject_sha256: str, gate: str, result: str,
                checker: str, independent: bool, limitations: list[str], decided_at: str | None = None) -> str:
    """Mechanical (code-produced) records only; independent passes come from the acceptance registry."""
    if independent:
        raise ValueError("independent acceptance cannot be minted by ingest; import a reviewer decision")
    return mechanical(rt, subject_kind=subject_kind, subject_id=subject_id, subject_sha256=subject_sha256, gate=gate,
                      result=result, checker=checker, limitations=limitations, decided_at=decided_at)


def _occurrence_signature(e: dict) -> tuple:
    return (e.get("case_id") or (e.get("references") or {}).get("case"), _scenario(e, "available_at", "recorded_at", "created_at", "event_at") or "")


def _participants(manifest: dict) -> dict[str, dict]:
    return {p["actor_ref"]: p for p in manifest.get("participants", [])}


def _thread_id(headers: dict) -> str | None:
    refs = (headers.get("references") or "").split() or ([headers["in-reply-to"]] if headers.get("in-reply-to") else [])
    root = refs[0] if refs else headers.get("message-id")
    return derived_id("THR", root) if root else None


def _factual_cases(e: dict) -> list[str]:
    refs = e.get("references") or {}
    return sorted(set(refs.get("case_ids") or []) | ({refs["case"]} if refs.get("case") else set())
                  | ({e["case_id"]} if e.get("case_id") else set()))


def _factual_matters(e: dict) -> list[str]:
    return sorted(set((e.get("references") or {}).get("afca_matter_ids") or []))


def ingest(rt: Runtime, delivery: Delivery, *, associations: str = "legacy", demo: dict | None = None) -> dict:
    """Build and seal one source release from a received delivery. Idempotent for identical inputs.

    `associations="factual-v1"` (new demo stores, A02/F13): every factual case and AFCA link the delivery declares
    for an original is kept on its asset/source, embedded copies bind to an overlapping held original, and authored
    lineage (logical document, exact copy, supersession, derivation) is consumed with conflict/cycle checks. The
    default `legacy` keeps the historical single-case behaviour so existing stores re-import identically."""
    factual = associations == "factual-v1"
    if associations not in ("legacy", "factual-v1"):
        raise ValueError(f"unknown association profile {associations}")
    manifest = delivery.manifest
    people = _participants(manifest)
    ingested_at = utc_now_iso()
    builds: dict[str, _AssetBuild] = {}           # by asset_version_id
    by_sha: dict[str, _AssetBuild] = {}
    limitations: list[str] = []
    case_ids_all = sorted(({o.entry.get("case_id") or o.entry.get("references", {}).get("case") for o in delivery.originals}
                           | {n.record["data"].get("case_id") for n in getattr(delivery, "natives", [])
                              if n.record["kind"] == "case"}) - {None})

    # ---- held originals → assets (attachments first so embedded bytes can bind to them)
    ordered = sorted(delivery.originals, key=lambda o: (o.entry.get("kind") != "attachment_original", o.entry["original_id"]))
    src_acc: dict[str, str] = {}           # intake (mechanical) record per original: part of the immutable asset
    review_acc: dict[str, str] = {}        # effective independent decision per asset version (release entries)
    reviewed_ids: set[str] = set()
    held_originals: list[dict] = []
    for rec in ordered:
        e = rec.entry
        if rec.media_type == "application/json":
            # The frozen Shared asset media enum has no application/json. Hold the exact JSON original (hash-bound
            # to its source decision) and serve a labelled DERIVED text projection that reproduces it verbatim
            # after a fixed declared header (DECISIONS §37: a conversion is a new labelled representation).
            from ..extract.jsondoc import extract_json, projection_bytes
            orig_sha = sha256_bytes(rec.data)
            if extract_json(rec.data).state != "complete":
                limitations.append(f"original {e['original_id']} declared JSON but fails the strict profile; not held")
                continue
            rt.blobs.put(rec.data)
            proj, header_len = projection_bytes(e["original_id"], orig_sha, rec.data)
            held_originals.append({"original_id": e["original_id"], "media_type": "application/json",
                                   "bytes_sha256": orig_sha, "byte_size": len(rec.data),
                                   "blob_reference": blob_reference(orig_sha), "projection_sha256": sha256_bytes(proj),
                                   "projection_header_codepoints": header_len,
                                   "reason": "frozen Shared asset media_type enum has no application/json"})
            rec = type(rec)(**{**rec.__dict__, "data": proj, "media_type": "text/plain"})
            e = {**e, "filename": f"{e['filename']}.projection.txt", "_json_original_sha256": orig_sha}
        sha = sha256_bytes(rec.data)
        if sha in by_sha and _occurrence_signature(by_sha[sha].entry or {}) == _occurrence_signature(e):
            # identical bytes supplied twice in the same case/time context (e.g. letter also embedded): one asset
            limitations.append(f"original {e['original_id']} has bytes identical to an earlier original in the same "
                               "case/time context; bound to one asset version")
            builds_entry = by_sha[sha]
            builds_entry.entry = builds_entry.entry or e
            continue
        rt.blobs.put(rec.data)
        orig_sha = e.get("_json_original_sha256") or sha        # the source decision binds the ORIGINAL bytes
        decision = _effective_decision(rt, "source", "supplied_original", e["original_id"], orig_sha)
        if e.get("_json_original_sha256") and is_pass(decision):
            # the derived projection is served only with its own independent extraction-fidelity decision
            proj_dec = _effective_decision(rt, "extraction", "derived_projection", e["original_id"], sha)
            if not is_pass(proj_dec):
                decision = {"acceptance_id": None, "result": "pending"}
            else:
                decision = proj_dec
        if is_pass(decision):
            reviewed_ids.add(e["original_id"])
        src_acc[e["original_id"]] = _acceptance(
            rt, subject_kind="supplied_original", subject_id=e["original_id"], subject_sha256=orig_sha, gate="source",
            result="pending", checker=RECEIVER_VERSION, independent=False,
            limitations=["pending independent source review; mechanical byte checks only"])
        byte_val = _acceptance(rt, subject_kind="asset_bytes", subject_id=e["original_id"], subject_sha256=sha,
                               gate="byte_validation", result="pass", checker=RECEIVER_VERSION, independent=False,
                               limitations=["mechanical signature/digest/size agreement"])
        av_id = derived_id("AV", sha, e["original_id"])
        review_acc[av_id] = decision["acceptance_id"] if e["original_id"] in reviewed_ids else src_acc[e["original_id"]]
        doc_time = _scenario_raw(e, "created_at", "event_at")
        asset = {
            "asset_version_id": av_id, "logical_document_id": derived_id("DOC", e["original_id"]),
            "version": f"v{e.get('version', 1)}",
            "representation_role": "preview" if e.get("_json_original_sha256") else "supplied_original",
            "filename": e["filename"], "media_type": rec.media_type, "byte_size": len(rec.data),
            "bytes_sha256": sha, "byte_validation_id": byte_val, "blob_reference": blob_reference(sha),
            "scenario_document_time": _time_value(doc_time, "no scenario document time supplied"),
            "scenario_available_at": _scenario(e, "available_at", "recorded_at", "created_at", "event_at") or ingested_at, "ingested_at": ingested_at,
            "case_ids": _factual_cases(e) if factual else ([e["case_id"]] if e.get("case_id") else ([e["references"]["case"]] if e.get("references", {}).get("case") else [])),
            "afca_matter_ids": _factual_matters(e) if factual else [], "policy": policy_binding(), "source_ids": [], "extraction_ids": [],
            "preview_ids": [], "derived_from_asset_version_ids": [], "supersedes_asset_version_id": None,
            "lineage_reason": "preview" if e.get("_json_original_sha256") else "initial",
            "acceptance_id": src_acc[e["original_id"]]}
        b = _AssetBuild(asset=asset, data=rec.data, entry=e)
        if sha in by_sha:
            limitations.append(f"original {e['original_id']} repeats earlier bytes in another case/time context; kept "
                               "as its own asset occurrence (shared blob)")
        builds[av_id] = b
        by_sha.setdefault(sha, b)

    # ---- extraction (outside any DB transaction)
    def run_extraction(b: _AssetBuild) -> None:
        profile, out = extract(b.data, b.asset["media_type"])
        b.profile, b.output = profile, out
        cfg = profile.configuration_sha256 if profile else "none"
        primary = by_sha.get(b.asset["bytes_sha256"])
        ext_id = (derived_id("EXT", b.asset["bytes_sha256"], cfg) if primary is None or primary is b
                  else derived_id("EXT", b.asset["bytes_sha256"], cfg, b.asset["asset_version_id"]))
        b.extraction = {
            "extraction_id": ext_id, "version": "v1", "asset_version_id": b.asset["asset_version_id"],
            "bytes_sha256": b.asset["bytes_sha256"], "extractor": profile.name if profile else "none",
            "extractor_version": profile.version if profile else "0", "configuration_sha256":
            profile.configuration_sha256 if profile else canonical_sha256({}), "processed_at": utc_now_iso(),
            "state": out.state, "omissions": list(out.omissions), "emitted_sources": [], "acceptance_id": None}
        b.asset["extraction_ids"] = [ext_id]

    for b in list(builds.values()):
        run_extraction(b)

    # ---- parser/profile upgrade: never rewrite a sealed asset; mint a superseding asset version instead
    def _cmp(a: dict) -> dict:          # superseding-ID basis (frozen: changing it breaks reimport idempotence)
        return {k: v for k, v in a.items() if k not in ("ingested_at",)}
    LATER = ("ingested_at", "source_ids", "preview_ids", "asset_version_id", "supersedes_asset_version_id",
             "lineage_reason", "version")

    def _receipt(a: dict) -> dict:      # fields fixed at receipt time (later-filled links excluded)
        return {k: v for k, v in a.items() if k not in LATER}
    for old_id, b in list(builds.items()):
        sealed = rt.db.get_object("asset", old_id)
        if sealed is None or _receipt(sealed) == _receipt(b.asset):
            continue
        what = ("a new extractor profile" if sealed["extraction_ids"] != b.asset["extraction_ids"] else
                "a new receiver version (different intake record)" if sealed["acceptance_id"] != b.asset["acceptance_id"]
                else "changed receipt metadata")
        new_id = derived_id("AV", b.asset["bytes_sha256"], b.entry["original_id"], b.asset["extraction_ids"],
                            b.asset["acceptance_id"], canonical_sha256(_cmp({**b.asset, "asset_version_id": None})))
        b.asset.update(asset_version_id=new_id, supersedes_asset_version_id=old_id,
                       lineage_reason="new_document_version",
                       version=f"{b.asset['version']}-x{new_id[-8:]}")
        b.extraction["asset_version_id"] = new_id
        sealed_ext = rt.db.get_object("extraction", b.extraction["extraction_id"])
        if sealed_ext is not None and sealed_ext["asset_version_id"] != new_id:
            b.extraction["extraction_id"] = derived_id("EXT", b.asset["bytes_sha256"], b.extraction["configuration_sha256"],
                                                       new_id)          # bound to the superseding asset version
            b.asset["extraction_ids"] = [b.extraction["extraction_id"]]
        limitations.append(f"asset {old_id} re-received under {what}; superseded by {new_id} (identical bytes; "
                           "the sealed version is never rewritten; frozen lineage enum has no 're-receipt' reason)")
        review_acc[new_id] = review_acc.pop(old_id)
        builds[new_id] = builds.pop(old_id)

    # ---- embedded children: bind to supplied originals by exact bytes, else register embedded asset
    attachments: list[dict] = []
    depth_of = {id(b): 0 for b in builds.values()}
    child_bytes = 0
    queue = [b for b in builds.values()]
    while queue:
        b = queue.pop(0)
        if b.asset["media_type"] != "message/rfc822" or b.output.state == "failed":
            continue
        if depth_of.get(id(b), 0) >= MAX_NESTING_DEPTH:
            b.output.omissions.append("attached message nesting limit reached; inner parts not extracted")
            b.extraction["omissions"] = list(b.output.omissions)
            b.extraction["state"] = b.output.state = "partial"
            continue
        for child in b.output.children:
            child_sha = sha256_bytes(child.data)
            child_bytes += len(child.data)
            if child_bytes > MAX_TOTAL_CHILD_BYTES:
                raise RuntimeError("decoded embedded bytes exceed the 50 MiB delivery limit")
            target = by_sha.get(child_sha)
            if target is not None and (not set(target.asset["case_ids"]) & set(b.asset["case_ids"]) if factual
                                       else sorted(target.asset["case_ids"]) != sorted(b.asset["case_ids"])):
                target = None          # same bytes, other case: separate embedded occurrence, not a cross-case bind
            if target is None:
                rt.blobs.put(child.data)
                byte_val = _acceptance(rt, subject_kind="asset_bytes", subject_id=f"{b.asset['asset_version_id']}:{child.mime_part_path}",
                                       subject_sha256=child_sha, gate="byte_validation", result="pass",
                                       checker=RECEIVER_VERSION, independent=False,
                                       limitations=["embedded MIME part decoded from parent; mechanical checks only"])
                av_id = derived_id("AV", child_sha, b.asset["asset_version_id"], child.mime_part_path)
                target = _AssetBuild(asset={
                    **b.asset, "asset_version_id": av_id,
                    "logical_document_id": derived_id("DOC", b.asset["logical_document_id"], child.mime_part_path),
                    "version": "v1", "filename": child.filename or f"part-{child.mime_part_path}",
                    "media_type": child.content_type, "byte_size": len(child.data), "bytes_sha256": child_sha,
                    "byte_validation_id": byte_val, "blob_reference": blob_reference(child_sha),
                    "source_ids": [], "extraction_ids": [], "preview_ids": [],
                    "derived_from_asset_version_ids": [b.asset["asset_version_id"]], "lineage_reason": "copy"},
                    data=child.data, parent=b)
                builds[av_id] = target
                by_sha.setdefault(child_sha, target)
                run_extraction(target)
                depth_of[id(target)] = depth_of.get(id(b), 0) + 1
                queue.append(target)
            b.children.append((child.mime_part_path, target))

    # ---- sources (one per non-failed extraction); children before parents for attachment_source_ids
    def build_source(b: _AssetBuild) -> None:
        if b.source is not None or b.output.state == "failed":
            return
        for _, child in b.children:
            build_source(child)
        anc = b
        while anc.entry is None and anc.parent is not None:
            anc = anc.parent                     # nearest supplied ancestor (nested embedded parts)
        e = b.entry or (anc.entry if anc is not b else None) or {}
        author = e.get("author") or e.get("owner") or (e.get("sender", {}).get("name") if isinstance(e.get("sender"), dict) else e.get("sender"))
        role = people.get(author, {}).get("role") if author else None
        auth_ref = author if author and re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", str(author)) else None
        family = e.get("source_family") or (anc.entry or {}).get("source_family") or "S04"
        source = {
            "source_id": None, "source_family": family,
            "subtype": e.get("semantic_type") or e.get("kind") or b.asset["media_type"],
            "case_ids": list(b.asset["case_ids"]), "afca_matter_ids": list(b.asset["afca_matter_ids"]) if factual else [],
            "author_role": role or "unknown", "author_reference": auth_ref,
            "text": b.output.text, "content_sha256": "0" * 64, "copy_of_source_id": None,
            "thread_id": _thread_id(b.output.headers) if b.asset["media_type"] == "message/rfc822" else None,
            "attachment_source_ids": sorted({c.source["source_id"] for _, c in b.children if c.source}),
            "native_facts": [], "clarification_request_id": None}
        source["content_sha256"] = source_content_sha256(source)
        source["source_id"] = derived_id("SRC", source["content_sha256"], b.asset["asset_version_id"])
        b.source = source
        b.extraction["emitted_sources"] = [{"source_id": source["source_id"], "content_sha256": source["content_sha256"]}]
        b.asset["source_ids"] = [source["source_id"]]
        occ_raw = _scenario_raw(e, "event_at", "created_at")
        occurred = None if occ_raw is None or occ_raw[1] == "day" else occ_raw[0]
        occurrence_date = occ_raw[0] if occ_raw and occ_raw[1] == "day" else None
        recorded = _scenario(e, "recorded_at", "created_at", "event_at")
        b.source_record = {
            "schema_version": RUNTIME_SCHEMA_VERSION, "kind": "source",
            "envelope": {"record_id": source["source_id"], "logical_id": derived_id("SRCL", b.asset["logical_document_id"]),
                         "supersedes_record_id": None, "recorded_at": recorded or b.asset["scenario_available_at"],
                         "available_at": b.asset["scenario_available_at"], "occurred_at": occurred,
                         "occurrence_date": occurrence_date, "access": record_access(), "evidence_refs": []},
            "data": source}

    lineage = _lineage(builds, limitations) if factual else {}
    for oid_, rel in lineage.items():
        b = next(x for x in builds.values() if x.entry and x.entry["original_id"] == oid_)
        if rel.get("logical"):
            b.asset["logical_document_id"] = derived_id("DOC", rel["logical"])
        if rel.get("supersedes"):
            b.asset["supersedes_asset_version_id"] = rel["supersedes"].asset["asset_version_id"]
            b.asset["lineage_reason"] = "new_document_version"
        if rel.get("derived_from"):
            # frozen lineage enum has no "derived": the derivation edge is kept; the reason stays as received
            b.asset["derived_from_asset_version_ids"] = [rel["derived_from"].asset["asset_version_id"]]
        if rel.get("copy_of"):
            b.asset["derived_from_asset_version_ids"] = [rel["copy_of"].asset["asset_version_id"]]
            b.asset["lineage_reason"] = "copy"
    # sources of lineage targets first: an exact copy/successor needs its target's source id
    for b in sorted(builds.values(), key=lambda x: _lineage_depth(x, lineage)):
        build_source(b)
        rel = lineage.get((b.entry or {}).get("original_id"))
        if rel and b.source and (rel.get("copy_of") or rel.get("supersedes")):
            tgt = rel.get("copy_of") or rel.get("supersedes")
            if tgt.source is not None:
                if rel.get("copy_of"):
                    b.source["copy_of_source_id"] = tgt.source["source_id"]
                b.source["content_sha256"] = source_content_sha256(b.source)
                old_id = b.source["source_id"]
                b.source["source_id"] = derived_id("SRC", b.source["content_sha256"], b.asset["asset_version_id"])
                b.extraction["emitted_sources"] = [{"source_id": b.source["source_id"],
                                                    "content_sha256": b.source["content_sha256"]}]
                b.asset["source_ids"] = [b.source["source_id"]]
                env = b.source_record["envelope"]
                env["record_id"] = b.source["source_id"]
                if rel.get("supersedes"):
                    env["supersedes_record_id"] = tgt.source["source_id"]
                    env["logical_id"] = tgt.source_record["envelope"]["logical_id"]
                limitations.append(f"original {b.entry['original_id']}: authored lineage "
                                   f"{'exact copy of' if rel.get('copy_of') else 'supersedes'} "
                                   f"{tgt.entry['original_id']} (source {old_id} re-identified)")

    # ---- attachment occurrences (embedded edges) with citations to the parent's attachment listing
    locators: list[dict] = []
    for b in builds.values():
        if not b.children or b.source is None:
            continue
        for path, child in b.children:
            seg = next(s for s in b.output.segments if s.kind == "eml" and s.location.get("section") == "attachment"
                       and s.location.get("mime_part_path") == path)
            ref = text_ref(b.source, seg.start, seg.end)
            att = {"attachment_occurrence_id": derived_id("ATT", b.source["source_id"], path),
                   "parent_source_id": b.source["source_id"], "parent_source_sha256": b.source["content_sha256"],
                   "business_occurrence_id": None, "child_asset_version_id": child.asset["asset_version_id"],
                   "relation": "embedded", "mime_part_path": path, "advertised_filename": None,
                   "scenario_available_at": b.asset["scenario_available_at"], "policy": policy_binding(),
                   "copy_of_attachment_occurrence_id": None, "supporting_refs": [ref]}
            attachments.append(att)
            locators.append(map_citation(b.source, b.output.layout(), b.extraction, b.asset, ref))

    # ---- advertised-but-unheld edges: only with an exact permitted citation in the advertising source
    for adv in manifest.get("advertised_unheld", []):
        parents = [b for b in builds.values() if b.entry and b.entry.get("staging_card_ref") in adv.get("advertised_in", [])
                   and b.source and b.asset["media_type"] == "message/rfc822"]
        bound = False
        for p in parents:
            sentences = [m for m in re.finditer(r"[^\n.]*(?:" + "|".join(map(re.escape, UNHELD_CUES)) + r")[^\n.]*\.?",
                                                p.source["text"], flags=re.IGNORECASE)]
            if len(sentences) != 1:
                continue
            quote = sentences[0].group(0).strip()
            try:
                s, e = find_quote(p.source["text"], quote)
            except QuoteError:
                continue
            ref = text_ref(p.source, s, e)
            attachments.append({
                "attachment_occurrence_id": derived_id("ATT", p.source["source_id"], "unheld", adv.get("staging_asset_ref")),
                "parent_source_id": p.source["source_id"], "parent_source_sha256": p.source["content_sha256"],
                "business_occurrence_id": None, "child_asset_version_id": None, "relation": "advertised_unheld",
                "mime_part_path": None, "advertised_filename": None,
                "scenario_available_at": p.asset["scenario_available_at"], "policy": policy_binding(),
                "copy_of_attachment_occurrence_id": None, "supporting_refs": [ref]})
            locators.append(map_citation(p.source, p.output.layout(), p.extraction, p.asset, ref))
            bound = True
        if not bound:
            limitations.append(f"advertised_unheld {adv.get('staging_asset_ref')}: no unique citable passage; edge not published")
        else:
            limitations.append(f"advertised_unheld {adv.get('staging_asset_ref')}: staging filename not evidenced in "
                               "the source, so advertised_filename is null")

    # ---- previews (display aids; validated HTML only)
    preview_assets: list[dict] = []
    for p in manifest.get("previews", []):
        orig = next((b for b in builds.values() if b.entry and b.entry["original_id"] == p["preview_of"]), None)
        path = (delivery.bundle_dir / p["preview_file"]).resolve()
        if orig is None or delivery.bundle_dir.resolve() not in path.parents or not path.is_file():
            limitations.append(f"preview {p['preview_file']} not registered (unresolved original or path)")
            continue
        data = path.read_bytes()
        lowered = data.lower()
        if b"<script" in lowered or re.search(rb"(src|href)\s*=\s*[\"']?\s*(https?:|//)", lowered):
            limitations.append(f"preview {p['preview_file']} rejected: active or external content")
            continue
        sha = rt.blobs.put(data)
        byte_val = _acceptance(rt, subject_kind="asset_bytes", subject_id=p["preview_file"], subject_sha256=sha,
                               gate="byte_validation", result="pass", checker=RECEIVER_VERSION, independent=False,
                               limitations=["HTML preview: no script/external loads detected; served sandboxed"])
        pv = {**orig.asset, "asset_version_id": derived_id("AV", sha, "preview", orig.asset["asset_version_id"]),
              "logical_document_id": orig.asset["logical_document_id"], "version": orig.asset["version"],
              "representation_role": "preview", "filename": path.name, "media_type": "text/html",
              "byte_size": len(data), "bytes_sha256": sha, "byte_validation_id": byte_val,
              "blob_reference": blob_reference(sha), "source_ids": [], "extraction_ids": [], "preview_ids": [],
              "derived_from_asset_version_ids": [orig.asset["asset_version_id"]], "lineage_reason": "preview"}
        orig.asset["preview_ids"] = sorted(set(orig.asset["preview_ids"]) | {pv["asset_version_id"]})
        preview_assets.append(pv)

    # ---- coverage input from the delivery's declared boundary
    window = manifest.get("scope", {}).get("observation_window", {})
    obs_start, obs_end = window.get("start", "2025-10-01"), window.get("end_exclusive", "2026-10-01")
    coverage_records = []
    for case_id in case_ids_all:
        from datetime import date as _date
        cov = {"coverage_id": derived_id("COV", delivery.manifest_sha256, case_id), "view_id": "complaints-leadership",
               "source_family": None, "metric_id": None,
               "start": melbourne_midnight(_date.fromisoformat(obs_start)).isoformat(),
               "end_exclusive": melbourne_midnight(_date.fromisoformat(obs_end)).isoformat(), "state": "partial",
               "basis": ("Delivered rehearsal batch covers selected cards only; "
                         + manifest.get("coverage", {}).get("note", "") + " "
                         + manifest.get("coverage", {}).get("native_inputs_note", "")).strip(),
               "source_ids": sorted(b.source["source_id"] for b in builds.values()
                                    if b.source and case_id in b.source["case_ids"])}
        coverage_records.append({"schema_version": RUNTIME_SCHEMA_VERSION, "kind": "coverage", "envelope": {
            "record_id": cov["coverage_id"], "logical_id": derived_id("COVL", case_id, "delivery-boundary"),
            "supersedes_record_id": None, "recorded_at": ingested_at, "available_at": ingested_at,
            "occurred_at": None, "occurrence_date": None, "access": record_access(), "evidence_refs": []}, "data": cov})

    # ---- idempotent re-ingest: reuse sealed actual clocks when all other content is identical
    def reuse(kind: str, oid: str, body: dict, volatile: tuple[str, ...]) -> None:
        old = rt.db.get_object(kind, oid)
        if old is None:
            return
        if {k: v for k, v in old.items() if k not in volatile} != {k: v for k, v in body.items() if k not in volatile}:
            raise RuntimeError(f"{kind}/{oid} re-derived with different content (nondeterministic parser or changed policy)")
        for k in volatile:
            body[k] = old[k]

    for b in builds.values():
        reuse("asset", b.asset["asset_version_id"], b.asset, ("ingested_at",))
        reuse("extraction", b.extraction["extraction_id"], b.extraction, ("processed_at",))
    for pv in preview_assets:
        reuse("asset", pv["asset_version_id"], pv, ("ingested_at",))
    for r in coverage_records:
        old = rt.db.get_object("coverage", r["data"]["coverage_id"])
        if old is not None:
            r["envelope"]["recorded_at"] = old["envelope"]["recorded_at"]
            r["envelope"]["available_at"] = old["envelope"]["available_at"]

    # ---- validate everything against the frozen contracts before sealing anything
    c = rt.contracts
    all_assets = [b.asset for b in builds.values()] + preview_assets
    for a in all_assets:
        c.check("asset", a)
    for b in builds.values():
        c.check("extraction", b.extraction)
        if b.source_record:
            c.check("input_record", b.source_record, document="runtime")
            assert source_content_sha256(b.source_record["data"]) == b.source_record["data"]["content_sha256"]
    for a in attachments:
        c.check("attachment", a)
    for loc in locators:
        c.check("locator", loc)
    for r in coverage_records:
        c.check("input_record", r, document="runtime")

    # ---- seal objects + release entries
    entries: list[dict] = []
    all_reviewed = None    # decided after natives are sealed
    ext_mech = {}

    def seal(kind: str, entry_kind: str, oid: str, body: dict, acceptance_id: str, links: dict | None = None) -> None:
        rt.db.put_object(kind, oid, body, links=links)
        blob = rt.blobs.put(canonical_bytes(body))
        entries.append({"kind": entry_kind, "immutable_id": oid, "blob_reference": blob_reference(blob),
                        "bytes_sha256": blob, "acceptance_id": acceptance_id})

    for b in sorted(builds.values(), key=lambda x: x.asset["asset_version_id"]):
        a = b.asset
        rt.db.put_object("asset", a["asset_version_id"], a, links={"case": a["case_ids"], "source": a["source_ids"]})
        entry_acc = review_acc.get(a["asset_version_id"]) or (review_acc.get(b.parent.asset["asset_version_id"])
                                                              if b.parent else None) or a["acceptance_id"]
        entries.append({"kind": "asset", "immutable_id": a["asset_version_id"], "blob_reference": a["blob_reference"],
                        "bytes_sha256": a["bytes_sha256"], "acceptance_id": entry_acc})
        ext = b.extraction
        ext_mech[ext["extraction_id"]] = _acceptance(
            rt, subject_kind="extraction", subject_id=ext["extraction_id"], subject_sha256=canonical_sha256(ext),
            gate="extraction", result="pending", checker=INGEST_VERSION, independent=False,
            limitations=["parser ran and outputs schema-validated; extraction fidelity not independently reviewed"]
            + list(ext["omissions"]))
        seal("extraction", "extraction", ext["extraction_id"], ext, ext_mech[ext["extraction_id"]],
             links={"asset": [a["asset_version_id"]]})
        layout = b.output.layout()
        rt.db.put_object("layout", ext["extraction_id"], layout)
        if b.source_record:
            src = b.source_record["data"]
            seal("source", "input", src["source_id"], b.source_record, entry_acc,
                 links={"case": src["case_ids"], "asset": [a["asset_version_id"]], "extraction": [ext["extraction_id"]]})
    for pv in preview_assets:
        rt.db.put_object("asset", pv["asset_version_id"], pv, links={"case": pv["case_ids"],
                                                                    "preview_of": pv["derived_from_asset_version_ids"]})
        entries.append({"kind": "asset", "immutable_id": pv["asset_version_id"], "blob_reference": pv["blob_reference"],
                        "bytes_sha256": pv["bytes_sha256"], "acceptance_id": pv["byte_validation_id"]})
    for h in held_originals:          # exact JSON originals: held, hash-bound, internal (not a Shared asset)
        av = next((x.asset["asset_version_id"] for x in builds.values()
                   if x.entry and x.entry["original_id"] == h["original_id"]), None)
        rt.db.put_object("held_original", h["original_id"], {**h, "derived_asset_version_id": av},
                         links={"asset": [av] if av else []})
    for att in attachments:
        parent_b = next(b for b in builds.values() if b.source and b.source["source_id"] == att["parent_source_id"])
        parent_acc = review_acc.get(parent_b.asset["asset_version_id"]) or parent_b.asset["acceptance_id"]
        seal("attachment", "attachment", att["attachment_occurrence_id"], att, parent_acc,
             links={"parent_source": [att["parent_source_id"]],
                    "child_asset": [att["child_asset_version_id"]] if att["child_asset_version_id"] else []})
    for loc in locators:
        seal("locator", "mapping", loc["mapping_id"], loc, ext_mech[loc["extraction_id"]],
             links={"source": [loc["citation"]["source_id"]], "extraction": [loc["extraction_id"]]})
    for r in coverage_records:
        seal("coverage", "input", r["data"]["coverage_id"], r, _acceptance(
            rt, subject_kind="coverage", subject_id=r["data"]["coverage_id"], subject_sha256=canonical_sha256(r),
            gate="source", result="pending", checker=INGEST_VERSION, independent=False,
            limitations=["coverage declared by the delivery manifest; not independently reviewed"]),
             links={"case": [cid for cid in case_ids_all]})

    native_reviewed = True
    for n in getattr(delivery, "natives", []):
        rid = n.record["envelope"]["record_id"]
        pending = _acceptance(rt, subject_kind="native_record", subject_id=rid, subject_sha256=n.record_sha256,
                              gate="native_input", result="pending", checker=RECEIVER_VERSION, independent=False,
                              limitations=["native row schema-validated; pending independent native-input review"])
        decision = _effective_decision(rt, "native_input", "native_record", rid, n.record_sha256)
        reviewed = is_pass(decision)
        n.acceptance_id = decision["acceptance_id"] if reviewed else None
        native_reviewed &= reviewed
        data = n.record["data"]
        cases = sorted(set(data.get("case_ids") or []) | ({data["case_id"]} if data.get("case_id") else set()))
        seal("native_record", "input", rid, n.record, n.acceptance_id if reviewed else pending,
             links={"case": cases, "record_kind": [n.record["kind"]], "logical": [n.record["envelope"]["logical_id"]]})

    entries.sort(key=lambda x: (x["kind"], x["immutable_id"]))
    source_bindings = sorted((b.source["source_id"], b.source["content_sha256"]) for b in builds.values() if b.source)
    source_set_version = derived_id("SSV", source_bindings)
    all_reviewed = (bool(delivery.originals) and native_reviewed
                    and all(o.entry["original_id"] in reviewed_ids for o in delivery.originals))
    release_acc = _acceptance(
        rt, subject_kind="release_entries", subject_id=delivery.manifest_sha256, subject_sha256=canonical_sha256(entries),
        gate="release_assembly", result="pass" if all_reviewed else "pending", checker=INGEST_VERSION,
        independent=False,
        limitations=(["every original and native row has an effective independent pass bound to its digest; "
                      "assembly is mechanical"] if all_reviewed else
                     ["one or more supplied originals or native rows await independent review"]))
    status = "accepted" if all_reviewed else "unreviewed_candidate"
    if demo is not None and not all_reviewed:
        # explicit synthetic-demo publication policy: a named corpus whose source quality was decided by Data
        # (bound by file digest), NOT an independent per-item pass and never served by the legacy pointers
        status = "synthetic_demo"
        release_acc = _acceptance(
            rt, subject_kind="release_entries", subject_id=delivery.manifest_sha256,
            subject_sha256=canonical_sha256(entries), gate="release_assembly", result="pass",
            checker=INGEST_VERSION, independent=False,
            limitations=[f"synthetic-demo-v1 corpus {demo['corpus_id']}: source-quality basis "
                         f"{demo['source_quality_basis']['path']} sha256 {demo['source_quality_basis']['sha256'][:16]}…; "
                         "per-item independent review not performed; mechanical assembly only"])
    release_id = derived_id("REL", manifest["batch"]["batch_id"], manifest["batch"]["package_version"],
                            canonical_sha256(entries), status, length=20)
    release = {
        "release_id": release_id, "contract_version": CONTRACT_VERSION, "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
        "registry_ids": [BASELINE_REGISTRY_ID, COMPANION_REGISTRY_ID], "published_at": utc_now_iso(),
        "observation_start": obs_start, "observation_end_exclusive": obs_end, "timezone": "Australia/Melbourne",
        "synthetic_demo": True, "time_provenance": "scenario_evidence_times_with_real_ingestion_and_analysis_audit",
        "entries": entries, "source_acceptance_id": release_acc, "extraction_acceptance_id": None}
    existing = rt.db.get_object("release", release_id)
    if existing is not None:
        release = existing                      # idempotent re-ingest: keep the sealed original
    c.check("release", release)
    failed_ext = sorted(f"{b.asset['filename']}: {'; '.join(b.extraction['omissions'])}"
                        for b in builds.values() if b.extraction["state"] == "failed")
    limitations += [f"extraction failed (asset kept in file inventory, no source emitted): {x}" for x in failed_ext]
    control = {"release_id": release_id, "status": status, "source_set_version": source_set_version,
               "case_ids": case_ids_all, "delivery_manifest_sha256": delivery.manifest_sha256,
               "review_acceptance_ids": sorted(set(review_acc.values()) - set(src_acc.values())),
               "native_record_ids": sorted(n.record["envelope"]["record_id"] for n in getattr(delivery, "natives", [])),
               "ingest_version": INGEST_VERSION, "association_profile": associations,
               "limitations": sorted(set(limitations))}
    if factual:
        control["original_assets"] = {}
        for b in builds.values():
            if b.entry and b.parent is None:
                control["original_assets"].setdefault(b.entry["original_id"], b.asset["asset_version_id"])
        for o in delivery.originals:                 # collapsed identical-context duplicates share one asset
            if o.entry["original_id"] not in control["original_assets"]:
                twin = next((b for b in builds.values() if b.entry and b.parent is None
                             and b.asset["bytes_sha256"] == sha256_bytes(o.data)), None)
                if twin is not None:
                    control["original_assets"][o.entry["original_id"]] = twin.asset["asset_version_id"]
    if demo is not None and status == "synthetic_demo":
        control["demo"] = {"corpus_id": demo["corpus_id"], "publication_policy": demo["publication_policy"],
                           "delivery_manifest_sha256": demo["delivery_manifest_sha256"],
                           "source_quality_basis": demo["source_quality_basis"],
                           "human_review": "not_performed_per_item"}
    rt.db.put_object("release", release_id, release)
    rt.db.put_object("release_control", release_id, control)
    rt.db.put_object("receipt", delivery.receipt["receipt_id"], delivery.receipt, links={"release": [release_id]})
    pointer = CURRENT_POINTER if status == "accepted" else "release:demo" if status == "synthetic_demo" \
        else CANDIDATE_POINTER
    current = rt.db.get_pointer(pointer)
    if current is None or current["target_id"] != release_id:
        rt.db.swap_pointer(pointer, expected_generation=None if current is None else current["generation"],
                           target_kind="release", target_id=release_id, acceptance_id=release_acc,
                           authority_revision=policy_binding()["permission_revision"])
    package_dir = rt.settings.package_dir / "releases"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / f"{release_id}.json").write_text(json.dumps({"release": release, "control": control}, indent=1,
                                                                ensure_ascii=False), encoding="utf-8")
    return {"release_id": release_id, "status": status, "pointer": pointer, "source_set_version": source_set_version,
            "assets": len(all_assets), "sources": len(source_bindings), "attachments": len(attachments),
            "natives": len(getattr(delivery, "natives", [])), "locators": len(locators), "extractions": {s: sum(1 for b in builds.values() if b.extraction["state"] == s)
                                                       for s in ("complete", "partial", "failed")},
            "limitations": control["limitations"]}


def _lineage(builds: dict, limitations: list[str]) -> dict:
    """Authored lineage → {original_id: {logical, copy_of, supersedes, derived_from}} over held builds, with
    missing-target, self-reference, conflicting-relation and cycle checks. A refused relation is a limitation and
    is not applied; prose attachment claims never create lineage."""
    by_oid = {b.entry["original_id"]: b for b in builds.values() if b.entry}
    out: dict[str, dict] = {}
    for oid, b in by_oid.items():
        lin = b.entry.get("lineage") or {}
        rel: dict = {}
        if b.entry.get("logical_document_id") and b.entry["logical_document_id"] != oid:
            rel["logical"] = b.entry["logical_document_id"]
        for key, field_ in (("copy_of", "copy_of"), ("supersedes", "supersedes_original_id"),
                            ("derived_from", "derived_from")):
            tgt = lin.get(field_)
            if not tgt:
                continue
            if tgt == oid or tgt not in by_oid:
                limitations.append(f"original {oid}: lineage {key} target {tgt} is not a held original; not applied")
                continue
            rel[key] = by_oid[tgt]
        if rel.get("copy_of") and rel.get("supersedes"):
            limitations.append(f"original {oid}: declared both exact copy and successor; neither applied")
            rel.pop("copy_of"), rel.pop("supersedes")
        if rel.get("copy_of") and rel["copy_of"].entry["sha256"] != b.entry["sha256"]:
            limitations.append(f"original {oid}: declared exact copy of {rel['copy_of'].entry['original_id']} but "
                               "bytes differ; treated as derived (forward/re-rendering), not an exact copy")
            rel["derived_from"] = rel.pop("copy_of")
        if rel:
            out[oid] = rel
    for oid in list(out):
        seen, cur = {oid}, out.get(oid)
        while cur:
            nxt = cur.get("copy_of") or cur.get("supersedes") or cur.get("derived_from")
            if nxt is None:
                break
            noid = nxt.entry["original_id"]
            if noid in seen:
                limitations.append(f"original {oid}: lineage cycle through {noid}; lineage not applied")
                out.pop(oid, None)
                break
            seen.add(noid)
            cur = out.get(noid)
    return out


def _lineage_depth(b, lineage: dict) -> int:
    depth, cur = 0, lineage.get((b.entry or {}).get("original_id"))
    while cur and depth < 50:
        nxt = cur.get("copy_of") or cur.get("supersedes")
        if nxt is None:
            break
        depth += 1
        cur = lineage.get(nxt.entry["original_id"])
    return depth
