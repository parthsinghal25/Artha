"""Receiving export: a `pre-extraction-delivery-manifest` 0.2.0 bundle of ACCEPTED originals for the AI/backend receiver.

It is a new transport around exact accepted bytes, not a new authoring package:
  * only originals listed in an external source sign-off are exported, and each is re-hashed against both the sign-off and the
    signed authoring manifest it came from (an original accepted in two packages must be byte-identical in both);
  * metadata comes from the signed 0.1.0 manifest where one exists, otherwise from the frozen plan and the actual bytes
    (EML headers and MIME parts are read from the message itself, never re-rendered);
  * native case/party rows are candidates under the existing case/party/procedure JSONL boundary, staged for their own review;
  * review-only material (source-to-export mapping, native field provenance, unknowns, format requests) goes to
    `restricted-review/`, outside the intake bundle; private dialogue, style controls, world labels and gold never enter it.

Copying an accepted original does not accept the transport metadata (dates, relationships, permissions) or the native rows:
the manifest says so, and those need their own review. Publication uses the same staged, verified, no-replace path as releases."""
import email
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from email import policy
from pathlib import Path

from .atomic import rename_noreplace
from .build import ReleaseExistsError
from .paths import CONTRACT_MANIFEST, RELEASE_ROOT, ROOT

EXPORT_GENERATOR = "sdgen receiving-export 1.0.0"
MANIFEST_KIND, MANIFEST_VERSION = "pre-extraction-delivery-manifest", "0.2.0"
FORBIDDEN_PARTS = {"restricted", "private", "private-controls", "gold", "world", "conversations", "archive", "review", "evaluation", "holdout"}
RUNTIME_SCHEMA = ROOT / "Data/contracts/runtime.schema.json"
LEADERSHIP_VIEW = "complaints-leadership"   # the receiver's existing view id (receiver-owned policy; listed as an assumption)

CASE11_SOURCES = (
    {"package": RELEASE_ROOT / "case-11-rehearsal-v3", "manifest_sha256": "d45d9923aa57034b1880a1f75e37a4f650275eb31b4de85056b919c5c3fa8dcd",
     "signoff": ROOT / "Data/reviews/case-11-rehearsal-v3-2026-10-15/REHEARSAL-SOURCE-SIGNOFF.json", "stage_acceptance": None},
    {"package": RELEASE_ROOT / "case-11-style-microbatch-v2", "manifest_sha256": "a8cfa876f794255a564d186e0a3309bca14ed6cf86bd55439a2020a4a23fc2b8",
     "signoff": ROOT / "Data/reviews/case-11-style-microbatch-v2-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json",
     "stage_acceptance": ROOT / "Data/reviews/case-11-style-microbatch-v2-2026-10-15/STAGE-ACCEPTANCE.json"},
)

KIND_FORMAT = {  # export kind -> (declared format, declared media type). Formats stay truthful: JSON is JSON.
    "email": ("EML", "message/rfc822"), "letter": ("PDF", "application/pdf"), "call_summary": ("TXT", "text/plain"),
    "portal_form": ("JSON", "application/json"), "native_register_export": ("CSV", "text/csv"), "note": ("TXT", "text/plain"),
}


class ExportRefused(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def rel_root(p):
    try:
        return str(Path("Artha") / Path(p).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(p)


def ts(value):
    return {"value": value, "precision": "second", "tz": value[-6:]}


# --------------------------------------------------------------------------- sources
def load_source(src):
    """Verify one accepted package against its expected manifest hash and its external sign-off. Returns a dict with
    the manifest, the sign-off and {original_id: (signed file, sha256)}; raises ExportRefused on any mismatch."""
    pkg = Path(src["package"]) / "intake-bundle"
    raw = (pkg / "delivery-manifest.json").read_bytes()
    if sha(raw) != src["manifest_sha256"]:
        raise ExportRefused(f"{pkg}: manifest is not the signed manifest {src['manifest_sha256'][:12]}")
    so_raw = Path(src["signoff"]).read_bytes()
    so = json.loads(so_raw)
    if so.get("decision") != "accepted" or so.get("bound_manifest_sha256") != src["manifest_sha256"]:
        raise ExportRefused(f"{src['signoff']}: not an acceptance bound to this manifest")
    man = json.loads(raw)
    by_id = {o["original_id"]: o for o in man["originals"]}
    signed = {}
    for o in so["originals"]:
        data = (pkg / o["file"]).read_bytes()
        if sha(data) != o["sha256"]:
            raise ExportRefused(f"{o['file']}: bytes differ from the sign-off")
        if o["original_id"] not in by_id or by_id[o["original_id"]]["sha256"] != o["sha256"]:
            raise ExportRefused(f"{o['original_id']}: sign-off and signed manifest disagree")
        signed[o["original_id"]] = (o["file"], o["sha256"])
    acc = None
    if src.get("stage_acceptance"):
        a_raw = Path(src["stage_acceptance"]).read_bytes()
        a = json.loads(a_raw)
        if a.get("decision") != "accepted" or a.get("bound_manifest_sha256") != src["manifest_sha256"]:
            raise ExportRefused(f"{src['stage_acceptance']}: stage acceptance not bound to this manifest")
        acc = {"path": rel_root(src["stage_acceptance"]), "sha256": sha(a_raw), "output_digest": a.get("output_digest")}
    return {"dir": pkg, "package": Path(src["package"]).name, "manifest": man, "manifest_sha256": src["manifest_sha256"],
            "signoff": {"path": rel_root(src["signoff"]), "sha256": sha(so_raw), "reviewer": so.get("reviewer"), "decision": so["decision"],
                        "review_date": so.get("review_date")},
            "stage_acceptance": acc, "signed": signed, "restricted": Path(src["package"]) / "restricted"}


def eml_facts(data):
    """Headers and attachment parts read from the actual message bytes (MIME path = 1-based index of the root part)."""
    msg = email.message_from_bytes(data, policy=policy.default)
    parts = []
    for i, part in enumerate(msg.iter_parts(), start=1):
        fname = part.get_filename()
        if fname:
            body = part.get_payload(decode=True)
            parts.append({"mime_part_path": str(i), "filename": fname, "content_type": part.get_content_type(),
                          "size_bytes": len(body), "sha256": sha(body)})
    return {"subject": str(msg["Subject"]), "message_id": msg["Message-ID"], "in_reply_to": msg["In-Reply-To"], "mime_parts": parts}


# --------------------------------------------------------------------------- entries
def _provenance(src, oid):
    return {"source_package": src["package"], "source_manifest_sha256": src["manifest_sha256"], "source_file": src["signed"][oid][0],
            "signoff": src["signoff"]["path"], "signoff_sha256": src["signoff"]["sha256"], "decision": "accepted",
            **({"stage_acceptance": src["stage_acceptance"]} if src["stage_acceptance"] else {})}


def entry_from_signed_manifest(src, oid):
    e = json.loads(json.dumps(next(o for o in src["manifest"]["originals"] if o["original_id"] == oid)))
    e["authoring_audit_at_packaging"] = e.pop("audit", None)  # packaging-time status, superseded by the external review below
    if e["kind"] in KIND_FORMAT:
        e.setdefault("declared_format", KIND_FORMAT[e["kind"]][0])
        e.setdefault("declared_media_type", KIND_FORMAT[e["kind"]][1])
    return e


def _stage_identity(stage_entry, copy_of=None):
    """Retain the stage's document version while keeping byte-copy lineage separate."""
    lineage = {"relation": "original", "copy_of": copy_of}
    if stage_entry.get("supersedes"):
        lineage.update(relation="new_document_version", supersedes=stage_entry["supersedes"])
    return {"version": stage_entry.get("version", 1), "lineage": lineage}


def entry_from_plan(src, oid, plan, data, stage_entry, letter_titles):
    """Metadata for an original whose signed manifest is a stage manifest: frozen plan facts + the original's own bytes."""
    rel = src["signed"][oid][0]
    name = Path(rel).name
    if stage_entry["kind"] == "attachment_original" or rel.startswith("originals/attachments/"):
        a = plan.assets_by_name[name]
        spec = a["document_spec"]
        native = {k: v for k, v in (spec.get("fields") or {}).items()
                  if k in ("document_reference", "upload_reference", "member_account_number", "request_reference", "request", "version")}
        return {"original_id": oid, "kind": "attachment_original", "staging_source_ref": a["source_id"], "file": rel, "filename": name,
                "semantic_type": a["semantic_type"], "declared_format": a["format"], "declared_media_type": a["media_type"],
                "detected_media_type": a["media_type"], "size_bytes": len(data), "sha256": sha(data),
                **_stage_identity(stage_entry, a["copy_of_asset_id"]), "owner": a["owner"],
                "held_by_complaints": a["held_by_complaints"], "source_family": a["source_family"], "native_reference_codes": native,
                "references": {"case": plan.case_id, "request": plan.request_reference},
                "scenario_time": {"created_at": ts(a["created_at"]), "available_at": ts(a["available_at"]),
                                  "note": "synthetic scenario dates; not real wall-clock times"}}
    card = stage_entry["staging_card_ref"]
    art = plan.artifact(card)
    if rel.startswith("originals/ledgers/"):
        e = ledger_entry(oid, rel, name, data, plan, art)
        e.update(_stage_identity(stage_entry, e["lineage"]["copy_of"]))
        return e
    if rel.startswith("originals/letters/"):
        kind = "letter"
    elif rel.startswith("originals/emails/"):
        kind = "email"
    elif rel.startswith("originals/call-summaries/"):
        kind = "call_summary"
    elif rel.startswith("originals/portal/"):
        kind = "portal_form"
    elif rel.startswith("originals/registers/"):
        kind = "native_register_export"
    elif rel.startswith("originals/notes/"):
        kind = "note"
    else:
        raise ExportRefused(f"{rel}: no export kind for this original")
    fmt, media = KIND_FORMAT[kind]
    e = {"original_id": oid, "kind": kind, "staging_card_ref": card, "staging_source_ref": art["planned_source_id"], "file": rel, "filename": name,
         "declared_format": fmt, "declared_media_type": media, "detected_media_type": media, "size_bytes": len(data), "sha256": sha(data),
         **_stage_identity(stage_entry), "source_family": art["source_family"], "subject": art["subject"],
         "author": art["author"], "recipients": art["recipients"], "references": {"case": plan.case_id, "request": plan.request_reference},
         "scenario_time": {"event_at": ts(art["event_at"]), "recorded_at": ts(art["recorded_at"]), "available_at": ts(art["available_at"]),
                           "note": "synthetic scenario dates; not real wall-clock times"}}
    if kind == "email":
        f = eml_facts(data)
        e.update({"subject": f["subject"], "message_id": f["message_id"], "in_reply_to": f["in_reply_to"], "staging_thread_ref": art["thread_id"],
                  "preview_file": "previews/" + name[:-4] + ".preview.html", "mime_parts": f["mime_parts"]})
    if kind == "letter":
        e["subject"] = letter_titles.get(card, art["subject"])
        e["parent_message_file"] = next(o["file"] for o in src["manifest"]["originals"]
                                        if o.get("staging_card_ref") == card and o["file"].startswith("originals/emails/"))
    if kind == "call_summary":
        e.update({"producer": "vendor-style summary service", "input_scope": "summary_only"})
    if kind == "portal_form":
        form = json.loads(data)
        e["form_attachment_references"] = [{"filename": x["filename"], "sha256": x["sha256"], "size_bytes": x["size_bytes"]}
                                           for x in form.get("attachments", [])]
        e["format_note"] = ("delivered truthfully as application/json (the accepted portal submission), never relabelled as text; "
                            "receiver extraction support and its accuracy review belong to AI/backend")
    if kind == "native_register_export":
        e["native_record"] = "native administrative export (source evidence); the canonical case input is the separate native case row"
    if kind == "note":
        e["note_form"] = "internal file note (plain text); not a message, no MIME parts"
    return e


def ledger_entry(oid, rel, name, data, plan, art):
    """A native ledger/work export delivered as a SOURCE original (never a sixth ingestion kind). Times: the export asset's own creation and
    availability (the reviewed facts), the card's case-file receipt and recording; rows keep their own business dates inside the file."""
    a = plan.assets_by_name[name]
    if a["asset_id"] != oid:
        raise ExportRefused(f"{rel}: ledger original id {oid} is not its asset id {a['asset_id']}")
    return {"original_id": oid, "kind": "ledger_export", "staging_card_ref": art["artifact_ref"], "staging_source_ref": a["source_id"],
            "staging_card_source_ref": art["planned_source_id"], "file": rel, "filename": name, "semantic_type": a["semantic_type"],
            "declared_format": a["format"], "declared_media_type": a["media_type"], "detected_media_type": a["media_type"],
            "size_bytes": len(data), "sha256": sha(data), "version": 1, "lineage": {"relation": "original", "copy_of": a["copy_of_asset_id"]},
            "source_family": a["source_family"], "subject": art["subject"], "author": art["author"], "owner": a["owner"],
            "recipients": art["recipients"], "held_by_complaints": a["held_by_complaints"],
            "references": {"case": plan.case_id, "request": plan.request_reference},
            "scenario_time": {"created_at": ts(a["created_at"]), "event_at": ts(art["event_at"]), "recorded_at": ts(art["recorded_at"]),
                              "available_at": ts(a["available_at"]),
                              "note": ("synthetic scenario dates. created_at/available_at are the export file's own; event_at/recorded_at are its receipt "
                                       "into the case file. Row dates inside the file are business dates of the recorded events or work and do not "
                                       "make the export available earlier.")},
            "native_record": ("native business export delivered as source evidence; its event, obligation and work identifiers are the system's "
                              "own and are preserved verbatim; projection belongs to AI/backend")}


# --------------------------------------------------------------------------- native candidates
def native_rows(plan, register_entry, register_bytes):
    """Case and party candidates under the existing 0.2.0 input boundary, from the accepted register and directory facts.
    evidence_refs stay empty: the receiver binds its own canonical source ids/hashes; pre-extraction provenance is listed
    separately for review."""
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(register_bytes.decode("utf-8"))))
    if len(rows) != 1:
        raise ExportRefused("the register export must hold exactly one case row")
    r = rows[0]
    if r["case_id"] != plan.case_id:
        raise ExportRefused("register case id differs from the plan")
    t = register_entry["scenario_time"]
    access = {"view_ids": [LEADERSHIP_VIEW], "classification": "standard", "redaction_state": "none"}

    def env(record_id, logical_id, occurred_at):
        return {"record_id": record_id, "logical_id": logical_id, "supersedes_record_id": None,
                "recorded_at": t["recorded_at"]["value"], "available_at": t["available_at"]["value"],
                "occurred_at": occurred_at, "occurrence_date": None, "access": access, "evidence_refs": []}
    member = next(a for a in plan.pack["actors"] if a["id"] in plan.pack["party_ids"] and a["role"].lower().startswith("member"))
    contact = next(a for a in plan.pack["actors"] if a["id"] in plan.pack["party_ids"] and a["id"] != member["id"])
    case = {"schema_version": "0.2.0", "kind": "case", "envelope": env(f"NAT-{plan.case_id}-case-r1", f"CASE-{plan.case_id}", t["event_at"]["value"]),
            "data": {"case_id": r["case_id"], "party_ids": [member["id"], contact["id"]], "broad_tags": [r["existing_tag"]],
                     "original_receipt_date": r["original_receipt"], "complaints_receipt_date": r["complaints_receipt"],
                     "manager_reference": r["manager"], "product_or_journey": r["product"], "channel": r["intake_channel"]}}
    parties = [
        {"schema_version": "0.2.0", "kind": "party", "envelope": env(f"NAT-{member['id']}-party-r1", f"PARTY-{member['id']}", None),
         "data": {"party_id": member["id"], "member_reference": r["member_reference"], "party_role": "member", "support_preferences": []}},
        {"schema_version": "0.2.0", "kind": "party", "envelope": env(f"NAT-{contact['id']}-party-r1", f"PARTY-{contact['id']}", None),
         "data": {"party_id": contact["id"], "member_reference": None, "party_role": "other", "support_preferences": []}},
    ]
    reg = {"original_id": register_entry["original_id"], "file": register_entry["file"], "sha256": register_entry["sha256"]}

    def cell(col):
        return {**reg, "locator": {"kind": "csv_record_column", "record": 1, "column": col}}
    provenance = {
        case["envelope"]["record_id"]: {
            "case_id": cell("case_id"), "original_receipt_date": cell("original_receipt"), "complaints_receipt_date": cell("complaints_receipt"),
            "manager_reference": cell("manager"), "product_or_journey": cell("product"), "channel": cell("intake_channel"), "broad_tags": cell("existing_tag"),
            "party_ids": {"basis": "case directory (frozen plan party_ids) linking the register's member_reference and the claimant contact; "
                                   "the register itself carries no party id column", "member_reference_cell": cell("member_reference")},
            "envelope.recorded_at/available_at/occurred_at": {"basis": "the register original's own recorded/available/event times", **reg}},
        parties[0]["envelope"]["record_id"]: {"member_reference": cell("member_reference"),
                                              "party_role": {"basis": "case directory: member (deceased)"}},
        parties[1]["envelope"]["record_id"]: {"party_role": {"basis": "case directory: claimant and verified correspondence contact; no assumed "
                                                                      "benefit entitlement. Emitted as 'other'; 'representative' is NOT asserted"}},
    }
    unknowns = [
        {"field": "party.support_preferences (both parties)", "emitted": [], "state": "unknown",
         "note": "no source records support preferences; the empty list is the schema's only form and must be read as 'none recorded', not 'none'"},
        {"field": "party.party_role for the claimant contact", "emitted": "other", "state": "needs_ruling",
         "note": "the accepted 11-04 record limits her to correspondence only; whether she is a representative or beneficiary is not established"},
        {"field": "envelope.access.view_ids", "emitted": [LEADERSHIP_VIEW], "state": "receiver_assumption",
         "note": "view ids are receiver policy (AI/backend owned); confirm or replace at intake"},
        {"field": "envelope.evidence_refs", "emitted": [], "state": "pending_binding",
         "note": "the receiver's canonical source_id/content_sha256/anchor exist only after its extraction; pre-extraction cell provenance is given here"},
        {"field": "envelope.occurrence_date", "emitted": None, "state": "not_applicable", "note": "occurred_at is given where the register supplies it"},
        {"field": "case.original_receipt_date", "emitted": r["original_receipt"], "state": "as_recorded",
         "note": "the register records 2025-12-31 for both receipts; the 21 December request email is a request, not a complaint receipt"},
    ]
    not_emitted = [
        {"candidate": "procedure OPS-DELEGATION-02", "reason": "the accepted 11-08 excerpt names the procedure and an effective-from date, but "
         "procedure.version and procedure.source_id (the receiver's source id) are not established before extraction; the excerpt's document "
         "version is not a procedure version. Not emitted; no procedure facts invented.", "owner": "Data (ruling) + AI/backend (source binding)"},
        {"candidate": "contacts, commitments, episodes, membership intervals, findings, MX records", "reason": "not native inputs; produced by "
         "AI/backend from evidence. Data does not manufacture them.", "owner": "AI/backend"},
        {"candidate": "work entries and financial events", "reason": "no accepted native work/financial export exists yet (D19/D28/D29 are among "
         "the seventeen unwritten cards); they arrive as source originals, never as a sixth ingestion kind", "owner": "Data (render) + AI/backend (projection)"},
    ]
    return case, parties, {"field_provenance": provenance, "unknowns": unknowns, "not_emitted": not_emitted}


# --------------------------------------------------------------------------- build
def build_export(plan, out_root, name, sources=CASE11_SOURCES, letter_titles=None, now=None):
    """Stage, verify and publish (no-replace) one receiving export. Returns the published directory."""
    loaded = [load_source(s) for s in sources]
    letter_titles = letter_titles or {}
    files, originals, prov, previews, occurrences = {}, {}, {}, [], []
    for src in loaded:
        stage_entries = {o["original_id"]: o for o in src["manifest"]["originals"]}
        for oid, (rel, digest) in src["signed"].items():
            data = (src["dir"] / rel).read_bytes()
            if oid in originals:
                if originals[oid]["sha256"] != digest or originals[oid]["file"] != rel:
                    raise ExportRefused(f"{oid} is accepted in two packages with different bytes or paths")
                prov[oid].append(_provenance(src, oid))
                continue
            if src["manifest"].get("manifest_kind") == MANIFEST_KIND:
                e = entry_from_signed_manifest(src, oid)
            else:
                e = entry_from_plan(src, oid, plan, data, stage_entries[oid], letter_titles)
            originals[oid] = e
            prov[oid] = [_provenance(src, oid)]
            files[rel] = data
        for p in src["manifest"].get("previews", []):
            previews.append(p)
            files[p["preview_file"]] = (src["dir"] / p["preview_file"]).read_bytes()
        for e in originals.values():  # previews of plan-built emails (the stage manifest lists none)
            pf = e.get("preview_file")
            if pf and pf not in files and (src["dir"] / pf).is_file() and src["signed"].get(e["original_id"]):
                files[pf] = (src["dir"] / pf).read_bytes()
                previews.append({"preview_file": pf, "preview_of": e["original_id"], "role": "display_aid_not_independent_evidence"})
    for e in originals.values():
        e["review_provenance"] = prov[e["original_id"]]
    by_sha = {}
    for e in originals.values():
        by_sha.setdefault(e["sha256"], []).append(e["original_id"])
    # attachment occurrences from the actual messages; portal forms list references, not MIME parts
    for e in sorted(originals.values(), key=lambda x: x["file"]):
        if e["kind"] != "email":
            continue
        for part in eml_facts(files[e["file"]])["mime_parts"]:
            refs = by_sha.get(part["sha256"], [])
            if len(refs) != 1:
                raise ExportRefused(f"{e['file']} part {part['mime_part_path']} resolves to {refs or 'no'} exported original")
            target = originals[refs[0]]
            own = target["kind"] == "letter" and target.get("parent_message_file") == e["file"]
            occurrences.append({"parent_message_file": e["file"], "staging_card_ref": e.get("staging_card_ref"), **part,
                                "references_original": refs[0], "relation": "message_own_document" if own else "reused_original_bytes",
                                "business_occurrence_note": "embedding of identical bytes in a message is one attachment occurrence, "
                                                            "not a new document submission or independent finding"})
    for e in originals.values():
        for ref in e.get("form_attachment_references", []):
            ids = by_sha.get(ref["sha256"], [])
            if len(ids) != 1:
                raise ExportRefused(f"portal attachment {ref['filename']} resolves to {ids or 'no'} exported original")
            ref["references_original"] = ids[0]
            ref["relation"] = "form_reference_to_reused_original_bytes"
    # native candidates from the accepted register
    reg = next(e for e in originals.values() if e["kind"] == "native_register_export")
    case_row, party_rows, native_review = native_rows(plan, reg, files[reg["file"]])
    native_files = {"native/case.jsonl": [case_row], "native/party.jsonl": party_rows}
    native_specs = []
    for rel, recs in native_files.items():
        data = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in recs).encode()
        files[rel] = data
        native_specs.append({"file": rel, "sha256": sha(data), "size_bytes": len(data), "record_kind": recs[0]["kind"], "schema_version": "0.2.0",
                             "review": "pending native_input review bound to each row's canonical digest; not accepted by this export"})
    unheld = []
    for src in loaded:
        for u in src["manifest"].get("advertised_unheld", []):
            if not any(x["staging_asset_ref"] == u["staging_asset_ref"] for x in unheld):
                unheld.append(u)
    exported_cards = sorted({e["staging_card_ref"] for e in originals.values() if e.get("staging_card_ref")},
                            key=lambda c: plan.artifact(c)["event_at"])
    refs = {r for e in originals.values() for r in [e.get("author"), e.get("owner"), *e.get("recipients", [])] if r}
    participants = [{"actor_ref": r, "display_name": plan.actor(r)["name"], "role": plan.actor(r)["role"], "email": plan.actor(r)["email"]}
                    for r in sorted(refs)]
    pin = json.loads(CONTRACT_MANIFEST.read_text())
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "manifest_kind": MANIFEST_KIND, "manifest_version": MANIFEST_VERSION,
        "note": ("Transport manifest for AI/backend intake around exact accepted bytes. Not an ingestion kind, not an amendment to Shared 1.0.0, "
                 "and not an extraction, canonical source hash, locator mapping or diagnostic result. Binary sha256 values are byte digests, "
                 "distinct from the receiver's later canonical source hashes. Staging refs are release-mapping keys, not canonical source IDs."),
        "batch": {"batch_id": f"{name}", "package_version": 1, "created_at_utc": stamp, "generator": EXPORT_GENERATOR,
                  "contract_pin": {"contract_version": pin["contract_version"], "candidate": pin["candidate"],
                                   "manifest_content_sha256": pin["manifest_content_sha256"]}},
        "status": {"source_acceptance": "originals_accepted_by_the_referenced_external_reviews",
                   "transport_metadata_review": "pending_independent_review",
                   "native_input_review": "pending", "production_extraction": "not_performed", "diagnostic_gold": "not_prepared",
                   "ingestion_receipt": "not_issued",
                   "statement": ("Every original here is byte-identical to an original an external review accepted (review_provenance). That acceptance "
                                 "covers the bytes. The transport metadata in this manifest (dates, relationships, permissions) and the native rows "
                                 "are new and pending their own review; this manifest accepts nothing.")},
        "scope": {"case_reference": plan.case_id, "member_reference": plan.pack["member_reference"], "request_reference": plan.request_reference,
                  "observation_window": {"start": "2025-10-01", "end_exclusive": plan.pack["observation_end_exclusive"]},
                  "corpus": "selected complaints-team corpus; not institution-wide completeness"},
        "participants": participants,
        "originals": sorted(originals.values(), key=lambda e: (e["kind"] != "attachment_original", e["file"])),
        "attachment_occurrences": occurrences, "advertised_unheld": unheld,
        "previews": sorted(previews, key=lambda p: p["preview_file"]),
        "native_administrative_inputs": native_specs,
        "coverage": {"cards_in_export": exported_cards,
                     "cards_of_case_not_in_export": sorted((c for c in plan.artifacts if c not in exported_cards), key=lambda c: plan.artifact(c)["event_at"]),
                     "note": "Unwritten cards are absent; their absence is not evidence of absence.",
                     "native_inputs_note": "One case row and two party rows from the accepted register and case directory; no procedure, work or financial row."},
        "provenance": {"sources": [{"package": s["package"], "manifest_sha256": s["manifest_sha256"], "signoff": s["signoff"],
                                    **({"stage_acceptance": s["stage_acceptance"]} if s["stage_acceptance"] else {})} for s in loaded],
                       "plan_pack_sha256": plan.sha256, "excluded": "restricted/ and private-controls/ of every source package; "
                       "authoring returns; style controls; world/gold material"},
    }
    review = {"mapping": [{"original_id": oid, "export_file": e["file"], "sha256": e["sha256"], "size_bytes": e["size_bytes"], "kind": e["kind"],
                           "staging_card_ref": e.get("staging_card_ref"), "review_provenance": e["review_provenance"]}
                          for oid, e in sorted(originals.items(), key=lambda kv: kv[1]["file"])],
              "previews": manifest["previews"], "native": native_review}
    return _publish(out_root, name, files, manifest, review, loaded)


def _private_turns(loaded):
    turns = []
    for src in loaded:
        for f in sorted((src["restricted"] / "private-controls/conversations").glob("*.json")) if (src["restricted"] / "private-controls/conversations").is_dir() else []:
            turns += [t["text"] for t in json.loads(f.read_text()).get("turns", []) if len(t["text"]) >= 25]
    return turns


def verify_export(pkg, loaded=None, pending_ids=None):
    """[{name, ok, detail}] for a staged or published export. `pending_ids` is used only for a candidate export: those exact originals may
    carry pending (never accepted) provenance; every other original must still be accepted by an external sign-off."""
    from .microbatch import PRIVATE_MARKERS
    pkg = Path(pkg)
    intake = pkg / "intake-bundle"
    out = []

    def ck(n, ok, d=""):
        out.append({"name": n, "ok": bool(ok), "detail": d})
    raw = (intake / "delivery-manifest.json").read_bytes()
    man = json.loads(raw)
    rows = dict((p, h) for h, p in (ln.split("  ", 1) for ln in (intake / "INTAKE-FILES.sha256").read_text().splitlines()))
    on_disk = {str(f.relative_to(intake)) for f in intake.rglob("*") if f.is_file() and f.name != "INTAKE-FILES.sha256"}
    ck("manifest kind/version is the receiver's 0.2.0 envelope", man["manifest_kind"] == MANIFEST_KIND and man["manifest_version"] == MANIFEST_VERSION)
    ck("contract pin is the frozen Shared manifest", man["batch"]["contract_pin"]["manifest_content_sha256"] == json.loads(CONTRACT_MANIFEST.read_text())["manifest_content_sha256"])
    ck("checksum inventory lists exactly the intake files and the manifest digest", set(rows) == on_disk and rows.get("delivery-manifest.json") == sha(raw))
    ck("every intake file matches its listed hash", all(sha((intake / r).read_bytes()) == h for r, h in rows.items()))
    listed = ({o["file"] for o in man["originals"]} | {p["preview_file"] for p in man["previews"]}
              | {n["file"] for n in man["native_administrative_inputs"]} | {"delivery-manifest.json"})
    ck("no unlisted file in the intake bundle", on_disk == listed, str(sorted(on_disk - listed)))
    ck("no manifest path touches a restricted/private/review location", not any(FORBIDDEN_PARTS & {x.lower() for x in Path(r).parts} for r in listed))
    ck("every original's bytes match its manifest hash and size", all(sha((intake / o["file"]).read_bytes()) == o["sha256"]
                                                                       and (intake / o["file"]).stat().st_size == o["size_bytes"] for o in man["originals"]))
    if pending_ids is None:
        ck("every original carries review provenance to an accepted sign-off", all(o.get("review_provenance") and all(p["decision"] == "accepted" for p in o["review_provenance"])
                                                                                   for o in man["originals"]))
    else:
        pend = {o["original_id"] for o in man["originals"] if any(p["decision"] != "accepted" for p in o.get("review_provenance") or [{"decision": None}])}
        ck("candidate: every original is accepted by an external sign-off except exactly the declared pending originals",
           pend == set(pending_ids) and all(o.get("review_provenance") for o in man["originals"])
           and all(p["decision"] in ("accepted", "pending_independent_review") for o in man["originals"] for p in o["review_provenance"]), str(sorted(pend)))
        ck("candidate: the manifest says it is a candidate and not fully accepted", man["status"].get("candidate") is True
           and man["status"]["source_acceptance"] != "originals_accepted_by_the_referenced_external_reviews")
    if loaded and pending_ids is None:
        signed = {oid: d for s in loaded for oid, (_, d) in s["signed"].items()}
        ck("exported originals are exactly the signed originals, byte for byte", {o["original_id"]: o["sha256"] for o in man["originals"]} == signed)
    ids = {o["original_id"]: o for o in man["originals"]}
    ck("every attachment occurrence resolves to one original with identical bytes", all(o["references_original"] in ids and ids[o["references_original"]]["sha256"] == o["sha256"]
                                                                                       for o in man["attachment_occurrences"]))
    ck("advertised-unheld items carry no bytes, hash or file", all(not (u.get("file") or u.get("sha256") or u.get("size_bytes")) for u in man["advertised_unheld"]))
    turns = _private_turns(loaded or [])
    texty = [r for r in on_disk if Path(r).suffix.lower() in (".eml", ".txt", ".csv", ".json", ".html", ".jsonl") and r != "delivery-manifest.json"]
    leaks = []
    for r in texty:
        t = (intake / r).read_text(encoding="utf-8", errors="replace")
        if r.startswith("originals/emails/"):
            m = email.message_from_bytes((intake / r).read_bytes(), policy=policy.default)
            b = m.get_body(preferencelist=("plain",))
            t = (b.get_content() if b else "") + str(m["Subject"])
        if PRIVATE_MARKERS.search(t) or any(x in t for x in turns):
            leaks.append(r)
    ck("no private dialogue, style control or authoring marker in any intake text", not leaks, str(leaks))
    from .leakguard import scan_files
    card_ids = scan_files({r: (intake / r).read_bytes() for r in sorted(on_disk) if r != "delivery-manifest.json"})
    ck("no private authoring-card ID in any held text, stored XLSX cell, PDF text layer or decoded attachment", not card_ids,
       str({r: [h["where"] for h in v][:3] for r, v in card_ids.items()}))
    mtxt = raw.decode()
    ck("manifest carries no style/authoring control fields", not any(k in mtxt for k in ("style_completion", "applied_profile", "simulated_conversation", "fidelity")))
    ck("native rows validate against the frozen runtime input_record schema", not _native_errors(intake, man), str(_native_errors(intake, man)[:3]))
    ck("restricted-review sits outside the intake bundle", (pkg / "restricted-review").is_dir() and not (intake / "restricted-review").exists())
    return out


def _native_errors(intake, man):
    import jsonschema
    schema = json.loads(RUNTIME_SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/input_record", "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker())
    errs = []
    for spec in man["native_administrative_inputs"]:
        for n, line in enumerate((intake / spec["file"]).read_text().splitlines(), 1):
            rec = json.loads(line)
            errs += [f"{spec['file']}:{n}: {e.message}" for e in validator.iter_errors(rec)]
            if rec.get("kind") != spec["record_kind"]:
                errs.append(f"{spec['file']}:{n}: kind {rec.get('kind')} differs from declared {spec['record_kind']}")
    return errs


def _publish(out_root, name, files, manifest, review, loaded, pending_ids=None, extra_review=None):
    out_root = Path(out_root)
    target = out_root / name
    if target.exists() or target.is_symlink():
        raise ReleaseExistsError(f"{target} already exists; exports are immutable. Use a new name.")
    stage = out_root / ".staging" / f"{name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    intake = stage / "intake-bundle"
    intake.mkdir(parents=True)
    for rel, data in files.items():
        f = intake / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    mraw = (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode()
    (intake / "delivery-manifest.json").write_bytes(mraw)
    listed = {rel: sha(data) for rel, data in files.items()}
    listed["delivery-manifest.json"] = sha(mraw)
    (intake / "INTAKE-FILES.sha256").write_text("".join(f"{h}  {rel}\n" for rel, h in sorted(listed.items())))
    rr = stage / "restricted-review"
    rr.mkdir()
    (rr / "SOURCE-TO-EXPORT-MAPPING.json").write_text(json.dumps({"kind": "source_to_export_review_mapping", "export": name,
                                                                  "delivery_manifest_sha256": sha(mraw), "originals": review["mapping"],
                                                                  "previews": review["previews"]}, indent=1, ensure_ascii=False) + "\n")
    (rr / "NATIVE-CANDIDATES-REVIEW.json").write_text(json.dumps({"kind": "native_candidate_review_metadata", "export": name,
                                                                 "native_files": manifest["native_administrative_inputs"], **review["native"]},
                                                                indent=1, ensure_ascii=False) + "\n")
    for fname, body in (extra_review or {}).items():
        (rr / fname).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    checks = verify_export(stage, loaded, pending_ids)
    failed = [c for c in checks if not c["ok"]]
    (rr / "EXPORT-CHECKS.json").write_text(json.dumps({"checks": checks}, indent=1) + "\n")
    if failed:
        raise ExportRefused(f"staged export failed verification (kept at {stage}): {failed[:3]}")
    try:
        rename_noreplace(stage, target)
    except FileExistsError as e:
        raise ReleaseExistsError(f"{target} already exists; nothing was replaced. Verified stage kept at {stage}") from e
    return target


# --------------------------------------------------------------------------- case 11 entry point
CASE11_PACK = ROOT / "Data/synthesis/worker-packs-v3/11-C-e1da28538652e263887a"
CASE11_PACK_DIGEST = "eab3258558e04c3b7378f18a2657db8bfa64852a82cebdef0f6a086d79ba7dbd"
CASE11_ACCEPTED_RETURNS = ROOT / "Data/synthesis/worker-outputs-v3/11-C-e1da28538652e263887a/style_microbatch"


def case11_export(out_root, name, now=None):
    """The accepted case-11 slice (rehearsal v3 + style micro-batch v2). The frozen plan comes from the bound pack (digest checked);
    the D20 letter title comes from the accepted return, read only after the owned folder matches the accepted output digest."""
    from .authoring_check import load_pack
    from .progress import pack_digest_on_disk, tree_digest
    if pack_digest_on_disk(CASE11_PACK.parent, CASE11_PACK.name) != CASE11_PACK_DIGEST:
        raise ExportRefused("the case-11 pack differs from the digest its acceptance is bound to")
    acc = json.loads(CASE11_SOURCES[1]["stage_acceptance"].read_text())
    if tree_digest(CASE11_ACCEPTED_RETURNS) != acc["output_digest"]:
        raise ExportRefused("the accepted micro-batch returns differ from the accepted output digest")
    plan = load_pack(CASE11_PACK)[0]
    titles = {}
    for f in CASE11_ACCEPTED_RETURNS.glob("11-D*.json"):
        d = json.loads(f.read_text())
        if d.get("kind") == "letter_with_cover_email":
            titles[d["card"]] = d["letter"]["title"]
    return build_export(plan, out_root, name, CASE11_SOURCES, titles, now)


# --------------------------------------------------------------------------- case 11 complete-case CANDIDATE (not an accepted export)
CASE11_SLICE_EXPORT = ROOT / "Data/synthesis/receiving-exports/case-11-accepted-slice-v1"
CASE11_SLICE_MANIFEST_SHA256 = "cd2be75478bb00d6fbd304d77136a8d501995a69ae222f93a8b0bacf176a3f91"
CASE11_REMAINING_V1 = {"package": RELEASE_ROOT / "case-11-remaining-cards-v1",
                       "manifest_sha256": "97c60b8c32c3002be78d81f60f883a32152e00390b09b40c386d93a1699c1fd8",
                       "signoff": ROOT / "Data/reviews/case-11-remaining-cards-v1-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json", "stage_acceptance": None}
CASE11_SUCCESSOR = RELEASE_ROOT / "case-11-remaining-cards-v2"
CASE11_SUCCESSOR_MANIFEST_SHA256 = "7c0c1ddbaef0bc1b97850eb59640479ed795d5ebec27d697a7714d2a0f52112a"


def _verified_bundle(pkg_dir, manifest_sha256):
    intake = Path(pkg_dir) / "intake-bundle"
    raw = (intake / "delivery-manifest.json").read_bytes()
    if sha(raw) != manifest_sha256:
        raise ExportRefused(f"{pkg_dir}: manifest is not {manifest_sha256[:12]}")
    rows = dict((p, h) for h, p in (ln.split("  ", 1) for ln in (intake / "INTAKE-FILES.sha256").read_text().splitlines()))
    bad = [r for r, h in rows.items() if sha((intake / r).read_bytes()) != h]
    if bad:
        raise ExportRefused(f"{pkg_dir}: files differ from their checksum inventory: {bad[:3]}")
    return intake, json.loads(raw), rows


def case11_complete_candidate(out_root, name="case-11-complete-candidate-v1", now=None, successor=CASE11_SUCCESSOR,
                              successor_manifest_sha256=CASE11_SUCCESSOR_MANIFEST_SHA256):
    """The complete 29-card case-11 receiving CANDIDATE: the accepted 22-original slice carried over verbatim (same entries, bytes, native rows
    and IDs, so an unchanged reimport keeps its source/asset identity), plus the remaining-cards originals: 16 accepted by the partial
    sign-off (bytes checked against it) and the 5 repaired originals of the successor, whose provenance says pending. Nothing is accepted
    here; the normal accepted export (`build_export`) and its checks are unchanged."""
    from .source_repair import load_repair
    plan = load_repair()[0]  # pack-5 plan with the ruled D29 export and the ruled index/invoice (re-derived and checked)
    s_intake, s_man, _ = _verified_bundle(CASE11_SLICE_EXPORT, CASE11_SLICE_MANIFEST_SHA256)
    checks = json.loads((CASE11_SLICE_EXPORT / "restricted-review/EXPORT-CHECKS.json").read_text())["checks"]
    if not all(c["ok"] for c in checks):
        raise ExportRefused("the accepted slice export did not pass its own checks")
    v1 = load_source(CASE11_REMAINING_V1)
    n_intake, n_man, _ = _verified_bundle(successor, successor_manifest_sha256)
    files = {}
    for rel in [o["file"] for o in s_man["originals"]] + [p["preview_file"] for p in s_man["previews"]] + [n["file"] for n in s_man["native_administrative_inputs"]]:
        files[rel] = (s_intake / rel).read_bytes()
    originals = {o["original_id"]: json.loads(json.dumps(o)) for o in s_man["originals"]}
    pending, carried, added = [], [], []
    succ_src = {"package": successor.name, "manifest_sha256": successor_manifest_sha256, "signed": {}, "manifest": n_man}
    for o in n_man["originals"]:
        oid, data = o["original_id"], (n_intake / o["file"]).read_bytes()
        if sha(data) != o["sha256"]:
            raise ExportRefused(f"{o['file']}: bytes differ from the successor manifest")
        if oid in originals:
            if originals[oid]["sha256"] != o["sha256"] or originals[oid]["file"] != o["file"] or o["review_status"] != "accepted_inherited":
                raise ExportRefused(f"{oid}: an inherited original differs from the accepted slice")
            carried.append(oid)
            continue
        if o["file"] in files:
            raise ExportRefused(f"{o['file']}: path already used by another original")
        if o["review_status"] == "pending_independent_review":
            if o.get("version") != 2 or not o.get("supersedes"):
                raise ExportRefused(f"{oid}: a pending original must be an explicit successor version")
            succ_src["signed"][oid] = (o["file"], o["sha256"])
            e = entry_from_plan(succ_src, oid, plan, data, o, {})
            e["version"] = 2
            e["lineage"] = {"relation": "new_document_version", "copy_of": e["lineage"].get("copy_of"), "supersedes": o["supersedes"]}
            e["review_provenance"] = [{"source_package": successor.name, "source_manifest_sha256": successor_manifest_sha256, "source_file": o["file"],
                                       "decision": "pending_independent_review", "revision": o["revision"],
                                       "note": "repaired under the exact-repair authorisation; awaiting the consolidated independent source decision"}]
            pending.append(oid)
        else:
            if v1["signed"].get(oid) != (o["file"], o["sha256"]) or o["review_status"] != "accepted":
                raise ExportRefused(f"{oid}: not an accepted original of the remaining-cards sign-off with these bytes")
            e = entry_from_plan(v1, oid, plan, data, next(x for x in v1["manifest"]["originals"] if x["original_id"] == oid), {})
            e["review_provenance"] = [{**_provenance(v1, oid), "also_byte_identical_in": {"package": successor.name, "manifest_sha256": successor_manifest_sha256}}]
            added.append(oid)
        originals[oid] = e
        files[o["file"]] = data
    previews = list(s_man["previews"])
    for e in originals.values():
        pf = e.get("preview_file")
        if pf and pf not in files:
            files[pf] = (n_intake / pf).read_bytes()
            previews.append({"preview_file": pf, "preview_of": e["original_id"], "role": "display_aid_not_independent_evidence"})
    by_sha = {}
    for e in originals.values():
        by_sha.setdefault(e["sha256"], []).append(e["original_id"])
    occurrences = list(s_man["attachment_occurrences"])
    done = {o["parent_message_file"] for o in occurrences}
    for e in sorted(originals.values(), key=lambda x: x["file"]):
        if e["kind"] != "email" or e["file"] in done:
            continue
        for part in eml_facts(files[e["file"]])["mime_parts"]:
            refs = by_sha.get(part["sha256"], [])
            if len(refs) != 1:
                raise ExportRefused(f"{e['file']} part {part['mime_part_path']} resolves to {refs or 'no'} exported original")
            occurrences.append({"parent_message_file": e["file"], "staging_card_ref": e.get("staging_card_ref"), **part, "references_original": refs[0],
                                "relation": "reused_original_bytes",
                                "business_occurrence_note": "embedding of identical bytes in a message is one attachment occurrence, "
                                                            "not a new document submission or independent finding"})
    cards = sorted({e["staging_card_ref"] for e in originals.values() if e.get("staging_card_ref")}, key=lambda c: plan.artifact(c)["event_at"])
    participants = list(s_man["participants"])
    have = {p["actor_ref"] for p in participants}
    for r in sorted({r for e in originals.values() for r in [e.get("author"), e.get("owner"), *e.get("recipients", [])] if r} - have):
        participants.append({"actor_ref": r, "display_name": plan.actor(r)["name"], "role": plan.actor(r)["role"], "email": plan.actor(r)["email"]})
    pin = json.loads(CONTRACT_MANIFEST.read_text())
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "manifest_kind": MANIFEST_KIND, "manifest_version": MANIFEST_VERSION,
        "note": s_man["note"],
        "batch": {"batch_id": name, "package_version": 1, "created_at_utc": stamp, "generator": EXPORT_GENERATOR + " (complete-case candidate)",
                  "contract_pin": {"contract_version": pin["contract_version"], "candidate": pin["candidate"], "manifest_content_sha256": pin["manifest_content_sha256"]}},
        "status": {"candidate": True,
                   "source_acceptance": f"partial: {len(originals) - len(pending)} originals accepted by referenced external reviews; {len(pending)} repaired originals pending",
                   "pending_original_ids": sorted(pending),
                   "transport_metadata_review": "pending_independent_review", "native_input_review": "pending",
                   "production_extraction": "not_performed", "diagnostic_gold": "not_prepared", "ingestion_receipt": "not_issued",
                   "statement": ("Complete-case CANDIDATE, not a fully accepted release. Each original's review_provenance states its actual decision: accepted "
                                 "originals are byte-identical to externally signed bytes; the pending originals are repaired successor versions awaiting the "
                                 "consolidated independent decision. Transport metadata and native rows are pending their own review. This manifest accepts nothing.")},
        "scope": s_man["scope"], "participants": participants,
        "originals": sorted(originals.values(), key=lambda e: (e["kind"] != "attachment_original", e["file"])),
        "attachment_occurrences": occurrences, "advertised_unheld": s_man["advertised_unheld"],
        "previews": sorted(previews, key=lambda p: p["preview_file"]),
        "native_administrative_inputs": s_man["native_administrative_inputs"],
        "coverage": {"cards_in_export": cards, "cards_of_case_not_in_export": sorted((c for c in plan.artifacts if c not in cards), key=lambda c: plan.artifact(c)["event_at"]),
                     "note": "All authored cards of the case are supplied; absence of a record inside them is not evidence of absence.",
                     "native_inputs_note": ("One case row and two party rows, byte-identical to the accepted-slice export. Ledger and work exports arrive as "
                                            "source originals, not native rows; no procedure row.")},
        "provenance": {"sources": [{"export": CASE11_SLICE_EXPORT.name, "manifest_sha256": CASE11_SLICE_MANIFEST_SHA256, "carried": "verbatim"},
                                   {"package": v1["package"], "manifest_sha256": v1["manifest_sha256"], "signoff": v1["signoff"]},
                                   {"package": successor.name, "manifest_sha256": successor_manifest_sha256, "pending": sorted(pending)}],
                       "plan_pack_sha256": plan.sha256, "excluded": s_man["provenance"]["excluded"] + "; repair rulings and repaired authored overlays"},
    }
    s_native = json.loads((CASE11_SLICE_EXPORT / "restricted-review/NATIVE-CANDIDATES-REVIEW.json").read_text())
    native = {k: v for k, v in s_native.items() if k not in ("kind", "export", "native_files")}
    native["not_emitted"] = [x for x in native["not_emitted"] if not x["candidate"].startswith("work entries")] + [
        {"candidate": "work entries and financial events", "reason": ("delivered as the native ledger/work export SOURCE originals 11-11 (D19), 11-15 (D28) "
         "and 11-16 (D29) with their own native IDs; no sixth ingestion kind and no Data-made projection"), "owner": "AI/backend (projection)"}]
    native["carried_from"] = {"export": CASE11_SLICE_EXPORT.name, "manifest_sha256": CASE11_SLICE_MANIFEST_SHA256, "native_bytes": "identical"}
    review = {"mapping": [{"original_id": oid, "export_file": e["file"], "sha256": e["sha256"], "size_bytes": e["size_bytes"], "kind": e["kind"],
                           "version": e.get("version", 1), "staging_card_ref": e.get("staging_card_ref"), "review_provenance": e["review_provenance"],
                           "origin": ("accepted slice (verbatim)" if any(x["original_id"] == oid for x in s_man["originals"]) else
                                      "remaining-cards pending successor" if oid in pending else "remaining-cards accepted")}
                          for oid, e in sorted(originals.items(), key=lambda kv: kv[1]["file"])],
              "previews": manifest["previews"], "native": native}
    counts = {"originals": len(originals), "from_slice_verbatim": len(s_man["originals"]), "remaining_accepted": len(added),
              "remaining_inherited_already_in_slice": len(carried), "pending": len(pending), "cards": len(cards),
              "attachment_occurrences": len(occurrences), "previews": len(previews)}
    return _publish(out_root, name, files, manifest, review, None, pending_ids=pending,
                    extra_review={"CANDIDATE-COUNTS.json": {"kind": "candidate_counts", "export": name, **counts}})
