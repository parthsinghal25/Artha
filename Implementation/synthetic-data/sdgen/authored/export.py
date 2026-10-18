"""authored-build: check → render → assemble the existing 0.2.0 receiving format → check final bytes → publish a NEW
candidate (atomic no-replace), or publish nothing and return the findings.

Layout of `<out_root>/<batch_id>-p<package_version>/`:
  runtime/intake-bundle/{delivery-manifest.json, INTAKE-FILES.sha256, originals/…, native/case.jsonl, native/party.jsonl}
  runtime/SOURCE-INDEX.jsonl   runtime/INPUT-COMPATIBILITY.md
  private/AUTHORED-INPUT/…  private/EXPECTED-METRICS.json  private/JUDGEMENT-ANCHOR-CHECKS.json  private/WORLD-EVENTS-WITHOUT-RUNTIME-EVIDENCE.json
  review/CHECK-RESULTS.json  review/REVIEW-STATUS.json  review/INDEPENDENT-REVIEW-FORM.json  review/BUILD-LOG.json
  README.md  <name>-runtime.zip (runtime/ only)  <name>-private-review.zip (private/, review/, README.md)

The manifest and every original are a pure function of the authored input (no wall-clock), so an unchanged member's
originals are byte-identical across rebuilds; the build time is recorded only in review/BUILD-LOG.json."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..atomic import rename_noreplace
from ..paths import ROOT
from .events import TABLE_PROFILE, check_events, check_reports
from .inputcheck import as_of, check_input, scan_case_mentions, scan_date_mentions
from .model import FORMATS, Report, load_input
from .render import render_order, render_source, sha256
from .verify import contract_pin, expected_text, judgement_anchor_checks, mechanical_checks

GENERATOR = "sdgen authored-build 1.0.0"
DEFAULT_OUT_ROOT = ROOT / "Data/synthesis/authored-candidates"
PRESERVED = [ROOT / p for p in (
    "Data/replacement-pilot-100-members-v1", "Data/replacement-pilot-100-members-v2", "Data/archive", "Data/reviews",
    "Data/contracts", "Data/Handoffs", "Shared", "Implementation/ai-backend", "Implementation/ui",
    "Data/synthesis/pilot-v2-releases", "Data/synthesis/receiving-exports", "Data/synthesis/source-repair-packs",
    "Data/synthesis/authoring-return-archive", "Data/synthesis/private")]
PRESERVED_GLOBS = ("Data/synthesis/worker-packs*", "Data/synthesis/worker-outputs*")
KIND_FOR_MANIFEST = {"case_note": "note"}


class BuildRefused(RuntimeError):
    pass


def preserved_roots():
    return PRESERVED + [p for g in PRESERVED_GLOBS for p in ROOT.glob(g)]


def check_destination(out_root: Path, input_root: Path):
    out_root = Path(out_root).resolve()
    for p in preserved_roots():
        p = p.resolve()
        if out_root == p or p in out_root.parents:
            raise BuildRefused(f"output root {out_root} is inside preserved {p}; choose a new candidates folder")
    inr = Path(input_root).resolve()
    if out_root == inr or inr in out_root.parents:
        raise BuildRefused("output root is inside the authored input")
    return out_root


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def input_digest(root: Path):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b"\0" + sha256(p.read_bytes()).encode() + b"\n")
    return h.hexdigest()


def native_rows(inp):
    """case/party v0.2.0 input records. A day-only clock becomes the next Melbourne midnight in the envelope (the
    receiver's not-before rule); the conversion is listed, never hidden."""
    conversions = []

    def env(key, rid, logical, clocks, revision):
        def inst(k):
            c = clocks.get(k)
            if c is None or not c.known:
                return None
            if c.precision == "day":
                v = c.available_from().isoformat()
                conversions.append({"record_id": rid, "clock": k, "day": c.value, "envelope_value": v,
                                    "rule": "day-only clock → next Melbourne midnight (not before that day ends)"})
                return v
            return c.value
        occ = clocks.get("occurred_at")
        return {"access": {"classification": "standard", "redaction_state": "none", "view_ids": ["complaints-leadership"]},
                "available_at": inst("available_at"), "evidence_refs": [], "logical_id": logical,
                "occurred_at": occ.value if occ is not None and occ.known and occ.precision != "day" else None,
                "occurrence_date": occ.value if occ is not None and occ.known and occ.precision == "day" else None,
                "record_id": rid, "recorded_at": inst("recorded_at"), "supersedes_record_id":
                    None if revision == 1 else rid.rsplit("-r", 1)[0] + f"-r{revision - 1}"}
    parties, cases = [], []
    for pid, p in inp.parties.items():
        rev = p.get("revision", 1)
        rid = f"NAT-{pid}-party-r{rev}"
        parties.append({"schema_version": "0.2.0", "kind": "party",
                        "envelope": env("party", rid, f"PARTY-{pid}", inp.native_clocks[("party", pid)], rev),
                        "data": {"party_id": pid, "member_reference": p["member_reference"], "party_role": p["party_role"],
                                 "support_preferences": p["support_preferences"]}})
    for cid, c in inp.cases.items():
        rev = c.get("revision", 1)
        rid = f"NAT-{cid}-case-r{rev}"
        cases.append({"schema_version": "0.2.0", "kind": "case",
                      "envelope": env("case", rid, f"CASE-{cid}", inp.native_clocks[("case", cid)], rev),
                      "data": {k: c[k] for k in ("case_id", "party_ids", "broad_tags", "original_receipt_date",
                                                 "complaints_receipt_date", "manager_reference", "product_or_journey", "channel")}})
    enc = lambda rows: "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows).encode()  # noqa: E731
    return enc(cases), enc(parties), conversions


def assemble(inp, rendered):
    """→ (files {bundle-relative: bytes}, manifest, index_rows, native_conversions)."""
    files, entries, occurrences, unheld, index = {}, [], [], [], []
    path_of = {sid: f"originals/{FORMATS[s['format']][2]}/{s['filename']}" for sid, s in inp.sources.items()}
    attached_to = {}
    for sid, s in inp.sources.items():
        for a in s.get("attachments") or []:
            if a.get("source_id"):
                attached_to.setdefault(a["source_id"], []).append(sid)
    used_actors = set()
    for sid, s in inp.sources.items():
        r = rendered[sid]
        rel = path_of[sid]
        files[rel] = r.data
        cl = inp.clocks[sid]
        used_actors.update([s["author"]] + (s.get("recipients") or []) + (s.get("cc") or []) + ([s["owner"]] if s.get("owner") else []))
        st = {k: cl[k].manifest() for k in ("created_at", "event_at", "recorded_at", "available_at") if k in cl and cl[k] is not None}
        st["note"] = ("synthetic scenario clocks (Australia/Melbourne); event_at = when the recorded thing happened/was sent, "
                      "created_at = when this original was produced, recorded_at = when it entered the case file, "
                      "available_at = when the complaints team held it; rows inside a table are business dates")
        cases = s.get("case_ids") or []
        lineage = {"relation": "new_version" if s.get("supersedes_source_id") else "exact_copy" if s.get("copy_of_source_id")
                   else "derived" if s.get("derived_from") else "original",
                   "supersedes_original_id": s.get("supersedes_source_id"), "copy_of": s.get("copy_of_source_id"),
                   "derived_from": s.get("derived_from"),
                   "note": "authored lineage; the current backend derives its own document/version ids and does not consume these links"}
        e = {"original_id": sid, "kind": KIND_FOR_MANIFEST.get(s["kind"], s["kind"]), "file": rel, "filename": s["filename"],
             "semantic_type": s["semantic_type"], "declared_format": s["format"],
             "declared_media_type": FORMATS[s["format"]][0], "size_bytes": len(r.data), "sha256": sha256(r.data),
             "version": int(s.get("version", 1)), "logical_document_id": s.get("logical_document_id") or sid,
             "lineage": lineage, "source_family": s["source_family"], "author": s["author"],
             "author_role": inp.participants[s["author"]]["role"],
             "recipients": s.get("recipients") or [], "cc": s.get("cc") or [],
             "held_by_complaints": s.get("held_by_complaints", True),
             "references": {"case": cases[0] if cases else None, "case_ids": cases,
                            "member_references": s.get("member_references") or [],
                            "afca_matter_ids": s.get("afca_matter_ids") or [],
                            "business_reference": s.get("business_reference")},
             "scenario_time": st}
        for k in ("owner", "subject", "association_note"):
            if s.get(k):
                e[k] = s[k]
        if len(cases) > 1:
            e["association_note"] = (e.get("association_note", "") + " " if e.get("association_note") else "") + \
                f"one original linked to {len(cases)} cases; references.case is the first listed, references.case_ids is complete"
        if s["format"] == "EML":
            e["message_id"] = r.message_id
            irt = (s["content"] or {}).get("in_reply_to_source_id")
            e["in_reply_to"] = rendered[irt].message_id if irt else None
            e["mime_parts"] = r.mime_parts
        if s["kind"] in TABLE_PROFILE:
            e["table_profile"] = TABLE_PROFILE[s["kind"]]
            e["native_record"] = ("business export delivered as source evidence; its own event/obligation/work ids are "
                                  "preserved verbatim; projection belongs to AI/backend")
        n_unheld = 0
        for a in s.get("attachments") or []:
            if a.get("source_id"):
                target = rendered[a["source_id"]]
                part = next((p for p in r.mime_parts if p["sha256"] == sha256(target.data)), None)
                occurrences.append({"parent_message_file": rel, "parent_original_id": sid,
                                    "mime_part_path": part and part["mime_part_path"], "filename": part and part["filename"],
                                    "content_type": FORMATS[inp.sources[a["source_id"]]["format"]][0],
                                    "size_bytes": len(target.data), "sha256": sha256(target.data),
                                    "references_original": a["source_id"], "relation": "embedded_copy_of_held_original",
                                    "disposition": a["disposition"],
                                    "business_occurrence_note": "embedding identical bytes is one attachment occurrence, not a new document or a new charge"})
            else:
                n_unheld += 1
                unheld.append({"staging_asset_ref": f"{sid}#unheld-{n_unheld}", "advertised_filename": a["filename"],
                               "state": "advertised_or_requested_but_not_held" if a["disposition"] == "not_held" else "mentioned_only",
                               "file": None, "sha256": None, "size_bytes": None, "advertised_in": [sid]})
                e["staging_card_ref"] = sid   # the receiver binds advertised_unheld edges through this key
        entries.append(e)
        index.append({
            "source_id": sid, "logical_document_id": e["logical_document_id"], "version": e["version"],
            "supersedes_source_id": s.get("supersedes_source_id"), "copy_of_source_id": s.get("copy_of_source_id"),
            "derived_from": s.get("derived_from"),
            "case_ids": cases, "member_references": s.get("member_references") or [],
            "afca_matter_ids": s.get("afca_matter_ids") or [], "association_note": e.get("association_note"),
            "relative_path": f"intake-bundle/{rel}", "filename": s["filename"], "kind": s["kind"],
            "semantic_type": s["semantic_type"], "source_family": s["source_family"], "author": s["author"],
            "author_role": e["author_role"], "recipients": e["recipients"], "cc": e["cc"],
            "business_reference": s.get("business_reference"), "subject": s.get("subject"),
            "clocks": {k: v for k, v in st.items() if k != "note"},
            "text_content_sha256": sha256(r.body_text.encode("utf-8")) if r.body_text is not None else None,
            "text_content_note": None if r.body_text is not None else "no plain text representation computed here (PDF/XLSX/image); the receiver extracts it",
            "original_sha256": e["sha256"], "size_bytes": e["size_bytes"], "media_type": e["declared_media_type"],
            "message_id": e.get("message_id"), "in_reply_to_source_id": (s["content"] or {}).get("in_reply_to_source_id"),
            "attachments": [{"source_id": a.get("source_id"), "filename": a.get("filename"), "disposition": a["disposition"]}
                            for a in s.get("attachments") or []],
            "attached_to_source_ids": attached_to.get(sid, []),
            "representation": "original", "provenance": {"content_mode": s["content"]["mode"],
                                                         "rendering": "copied byte-for-byte" if s["content"]["mode"] == "supplied_file"
                                                         else "neutral formatting of authored content"}})
    case_rows, party_rows, conversions = native_rows(inp)
    files["native/case.jsonl"] = case_rows
    files["native/party.jsonl"] = party_rows
    participants = [{"actor_ref": a, "display_name": inp.participants[a]["display_name"], "role": inp.participants[a]["role"],
                     **({"email": inp.participants[a]["email"]} if inp.participants[a].get("email") else {})}
                    for a in sorted(used_actors | {c["manager_reference"] for c in inp.cases.values() if c.get("manager_reference")})]
    manifest = {
        "manifest_kind": "pre-extraction-delivery-manifest", "manifest_version": "0.2.0",
        "note": ("Transport manifest for AI/backend intake around exact original bytes. Byte sha256 values are not canonical "
                 "source hashes. Clocks are synthetic scenario clocks. Nothing here is accepted."),
        "batch": {"batch_id": inp.batch["batch_id"], "package_version": inp.batch["package_version"],
                  "generator": GENERATOR, "input_digest": input_digest(inp.root), "contract_pin": contract_pin(),
                  "purpose": inp.batch["purpose"],
                  **({"excluded_from_corpus": True} if inp.batch.get("excluded_from_corpus") else {})},
        "status": {"authored": "supplied_by_author", "rendered": GENERATOR, "source_acceptance": "pending_independent_review",
                   "independent_source_review": "not_performed", "ai_processing": "not_performed",
                   "native_input_review": "pending", "transport_metadata_review": "pending",
                   "statement": "Mechanical checks only (see the review package). Independent source review and AI processing have not occurred."},
        "scope": {"observation_window": inp.batch["observation_window"], "timezone": "Australia/Melbourne",
                  "cases": len(inp.cases), "corpus": "authored synthetic complaints-team holdings; not institution-wide completeness"},
        "participants": participants, "originals": entries, "attachment_occurrences": occurrences,
        "advertised_unheld": unheld, "previews": [],
        "native_administrative_inputs": [
            {"file": f"native/{k}.jsonl", "sha256": sha256(files[f"native/{k}.jsonl"]), "size_bytes": len(files[f"native/{k}.jsonl"]),
             "record_kind": k, "schema_version": "0.2.0", "review": "pending native_input review bound to each row's canonical digest",
             "day_precision_clocks": [{"record_id": c["record_id"], "clock": c["clock"], "day": c["day"]}
                                      for c in conversions if f"-{k}-r" in c["record_id"]],
             "day_precision_note": "these envelope date-times are the next Melbourne midnight standing for a day-only clock (not before that day ends); read them as the listed day"}
            for k in ("case", "party")],
        "coverage": {"deliberate_gaps": inp.batch.get("deliberate_gaps") or [],
                     "note": "Absent records are absent from these holdings; absence is not evidence of absence.",
                     "relationship_note": ("multi-case and AFCA links are complete in references.case_ids/afca_matter_ids and in table "
                                           "linked_case_ids/afca_matter_id columns; the current receiver maps references.case only")}}
    files["delivery-manifest.json"] = json.dumps(manifest, indent=1, ensure_ascii=False).encode() + b"\n"
    lines = sorted(f"{sha256(b)}  {rel}" for rel, b in files.items())
    files["INTAKE-FILES.sha256"] = ("\n".join(lines) + "\n").encode()
    return files, manifest, index, conversions


def build(input_root, out_root=None, name=None, dry_run=False):
    """→ dict(ok, findings, checks, candidate_dir|None). Publishes nothing when any error exists or on dry_run."""
    input_root = Path(input_root).resolve()
    inp, rep = load_input(input_root)
    out = {"ok": False, "candidate_dir": None, "findings": None, "checks": None}
    if inp is None:
        out["findings"] = rep.as_list()
        return out
    out_root = None if dry_run else check_destination(out_root or DEFAULT_OUT_ROOT, input_root)
    check_input(inp, rep)
    check_events(inp, rep)
    check_reports(inp, lambda sid: as_of(inp, sid), rep)
    rendered = {}
    if not rep.errors:
        for sid in render_order(inp, rep):
            r = render_source(inp, sid, rendered, rep)
            if r is not None:
                rendered[sid] = r
                scan_date_mentions(inp, sid, r.body_text if inp.sources[sid]["format"] in ("TXT", "EML") else None, rep)
                scan_case_mentions(inp, sid, _plain(inp.sources[sid], r), rep)
    for sid, src in inp.sources.items():
        cp = src.get("copy_of_source_id")
        if cp and sid in rendered and cp in rendered and rendered[sid].data != rendered[cp].data:
            rep.add("false_copy", f"source:{sid}", f"declares exact copy of {cp} but its final bytes differ",
                    "use derived_from {source_id, relation} for a forward/revision/representation, or supersedes_source_id for a new version")
    if rep.errors or len(rendered) != len(inp.sources):
        out["findings"] = rep.as_list()
        return out
    files, manifest, index, conversions = assemble(inp, rendered)
    res, trep, model, exp = mechanical_checks(files, manifest, index, inp, expected_text(inp))
    rep.findings += trep.findings
    anchors, arep = judgement_anchor_checks(inp, files, manifest, model[2])
    rep.findings += arep.findings
    res.add("judgement_anchors", sum(len(a["anchors"]) for a in anchors), [f"{f.code} {f.subject}" for f in arep.errors],
            statement="anchors resolve in final bytes and were available by the cutoff; reasoning not assessed")
    out["checks"] = res.items
    out["findings"] = rep.as_list()
    if res.failed or rep.errors:
        return out
    if dry_run:
        out.update(ok=True, candidate_dir=None, note="dry run: all checks passed in memory; nothing published")
        return out
    name = name or f"{inp.batch['batch_id']}-p{inp.batch['package_version']}"
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root / name
    if final.exists():
        raise BuildRefused(f"{final} exists; candidates are immutable — bump package_version")
    stage = out_root / f".staging-{name}-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        _write(stage, name, inp, files, index, conversions, res, rep, exp, anchors, rendered, model)
        rename_noreplace(stage, final)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    out.update(ok=True, candidate_dir=str(final))
    return out


def _plain(src, r):
    if r.body_text is not None:
        return r.body_text
    c = src["content"]
    if c.get("mode") == "pdf_document":
        return json.dumps([c.get("title")] + (c.get("blocks") or []), ensure_ascii=False)
    return None


def _write(stage, name, inp, files, index, conversions, res, rep, exp, anchors, rendered, model):
    rt = stage / "runtime"
    for rel, data in files.items():
        p = rt / "intake-bundle" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (rt / "SOURCE-INDEX.jsonl").write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in index))
    (rt / "INPUT-COMPATIBILITY.md").write_text(COMPATIBILITY)
    pv = stage / "private"
    shutil.copytree(inp.root, pv / "AUTHORED-INPUT")
    dump = lambda p, o: p.write_text(json.dumps(o, indent=1, ensure_ascii=False, default=str) + "\n")  # noqa: E731
    dump(pv / "EXPECTED-METRICS.json", json.loads(json.dumps(exp, default=str)))
    dump(pv / "JUDGEMENT-ANCHOR-CHECKS.json", {"statement": "anchor resolution only; no judgement is supported by this file",
                                               "judgements": anchors})
    seen = model[1]
    evidenced = {k for prof in seen.values() for k in prof}
    dump(pv / "WORLD-EVENTS-WITHOUT-RUNTIME-EVIDENCE.json",
         {"statement": "authored world events with no row in any delivered table (process events are expected here; they are evidenced in prose if at all)",
          "event_ids": sorted(e for e, ev in inp.events.items() if e not in evidenced and ev["kind"] in ("financial", "work"))})
    rv = stage / "review"
    rv.mkdir()
    dump(rv / "CHECK-RESULTS.json", {"kind": "measured-mechanical-checks", "generator": GENERATOR, "checks": res.items,
                                     "findings": rep.as_list(), "not_checked": NOT_CHECKED})
    dump(rv / "REVIEW-STATUS.json", {"authored": "supplied_by_author", "rendered": "yes", "mechanical_checks": "measured; see CHECK-RESULTS.json",
                                     "independent_source_review": "not_performed", "ai_processing": "not_performed",
                                     "acceptance": "none", "statement": "No review or AI processing has occurred. A separate reviewer fills INDEPENDENT-REVIEW-FORM.json after reading the sources."})
    dump(rv / "INDEPENDENT-REVIEW-FORM.json", REVIEW_FORM)
    dump(rv / "BUILD-LOG.json", {"built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "note": "wall-clock build time; never a scenario clock", "generator": GENERATOR,
                                 "native_clock_conversions": conversions,
                                 "sources": {sid: {"mode": r.mode, "sha256": sha256(r.data)} for sid, r in rendered.items()}})
    counts = {"members": len({p["member_reference"] for p in inp.parties.values() if p.get("member_reference")}),
              "cases": len(inp.cases), "parties": len(inp.parties), "originals": len(inp.sources)}
    (stage / "README.md").write_text(README.format(name=name, purpose=inp.batch["purpose"], counts=json.dumps(counts)))
    for zname, roots in ((f"{name}-runtime.zip", ("runtime",)), (f"{name}-private-review.zip", ("private", "review", "README.md"))):
        with zipfile.ZipFile(stage / zname, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                rel = str(p.relative_to(stage))
                if p.is_file() and rel.split("/")[0] in roots and not rel.endswith(".zip"):
                    zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o644 << 16
                    z.writestr(zi, p.read_bytes())


NOT_CHECKED = ["realism or quality of authored prose", "whether private judgements are causally right",
               "independent source-first review (not performed)", "AI extraction/analysis (not performed)",
               "image pixel text (no OCR here)", "backend shared-matter / multi-case projection (current ingest maps one case)"]
REVIEW_FORM = {"form": "independent-source-review", "instructions": (
    "Filled ONLY by a reviewer who did not author the sources, after reading runtime originals before any private file. "
    "Record your own chronology, supported/uncertain problems, money/work totals and exact source references, then compare."),
    "reviewer": {"name": None, "independence_statement": None}, "read_before_private": None, "members_reviewed": [],
    "conclusions": [], "disagreements_with_private_expectations": [], "date": None}
COMPATIBILITY = """# Input compatibility (measured, not assumed)

The intake bundle uses the existing `pre-extraction-delivery-manifest` 0.2.0 layout with the current frozen Shared
contract pin. Each original carries original_id, file, filename, declared_media_type/format, sha256/size, kind,
semantic_type, source_family, author (bound to `participants`), case reference and nested `scenario_time` clocks with
precision. Native case/party rows are v0.2.0 input records validated against `#/$defs/input_record`.

Run the receiver check (unmodified backend receiver, extractors and ledger/work profiles; no store, no publication):

    ../ai-backend/.venv/bin/python tools/authored_receiver_check.py <candidate>/runtime/intake-bundle <out.json>

Known integration limits of the CURRENT backend (not defects in this export; nothing is duplicated to work around them):
* Ingest maps one case per original (`references.case`) and gives every source empty AFCA links. Rows of a
  financial/work table whose case is not that first case, or that carry an `afca_matter_id`, are left unresolved by the
  ledger/work projection. The full links are preserved in `references.case_ids`/`afca_matter_ids`, in SOURCE-INDEX and
  in the tables' `linked_case_ids`/`afca_matter_id` columns. Shared-matter projection is later AI integration work.
* Lifecycle (received/closed/reopened) tables have no registered native profile; they are source evidence.
* The backend's ledger/work profiles read `date` only; the optional `event_time` column (known intraday order) is
  kept as evidence and is not yet consumed. Authored lineage (`logical_document_id`, supersession, exact copy,
  `derived_from`) is preserved in the manifest and SOURCE-INDEX; ingest derives its own document/version ids and does
  not consume it yet.
* Backend cutoffs compare `available_at <= end of day` (inclusive); these expectations treat an exact next-midnight
  instant as the next day (half-open). Authors are warned about exact-midnight availability.
* Image text is read only by the receiver's local OCR.
"""
README = """# {name}

Purpose: {purpose}. Counts: {counts}.

* `runtime/` — what AI/backend may receive (intake bundle, SOURCE-INDEX, compatibility note). Never put `private/` or
  `review/` into an AI pipeline.
* `private/` — the authored input as supplied, mechanical expectations from final bytes, judgement anchor checks.
* `review/` — measured mechanical checks, status (independent review and AI processing NOT performed), empty
  independent review form.

Status: ready for independent review. Not accepted, not AI-processed.
"""
