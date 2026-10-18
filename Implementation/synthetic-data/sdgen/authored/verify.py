"""Mechanical checks over FINAL bytes, shared by the build (before publication) and `authored-verify` (after).

Each check reports checked / failed / unavailable scope. Passing means only what the check states; realism, causal
reasoning and AI behaviour are outside every check here. `verify_candidate` is read-only: it hashes every file before
and after and fails if anything changed; it never rewrites originals, manifests or ZIPs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from ..leakguard import representations
from ..paths import CONTRACT_MANIFEST, ROOT
from ..tz import OffsetMismatch, melbourne_abbr
from .events import TABLE_PROFILE, check_tables, table_from_bytes
from .expect import compute, end_of_day
from .model import FORMATS, Clock, Report, held_by, load_input
from .render import eml_parts, pdf_text

RUNTIME_SCHEMA = ROOT / "Data/contracts/runtime.schema.json"
FORBIDDEN_KEYS = {"episode", "episode_ref", "episode_id", "mechanism", "split", "holdout", "gold", "expected",
                  "driver", "candidate_driver", "world", "world_intent_ref", "judgement", "diagnosis", "design_group"}
REQUIRED_ENTRY = ("original_id", "kind", "file", "filename", "semantic_type", "declared_format", "declared_media_type",
                  "size_bytes", "sha256", "version", "source_family", "author", "scenario_time", "references")
STATUS_NOT_DONE = {"independent_source_review": "not_performed", "ai_processing": "not_performed"}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def contract_pin():
    pin = json.loads(CONTRACT_MANIFEST.read_text())
    return {"contract_version": pin["contract_version"], "candidate": pin["candidate"],
            "manifest_content_sha256": pin["manifest_content_sha256"]}


def native_validator():
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    schema = json.loads(RUNTIME_SCHEMA.read_text())
    reg = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return Draft202012Validator({"$ref": f"{schema['$id']}#/$defs/input_record"}, registry=reg,
                                format_checker=FormatChecker())


def signature_ok(fmt, data):
    if fmt == "PDF":
        return data.startswith(b"%PDF-")
    if fmt == "PNG":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if fmt == "JPG":
        return data.startswith(b"\xff\xd8\xff")
    if fmt == "XLSX":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return {"[Content_Types].xml", "xl/workbook.xml"} <= set(z.namelist())
        except zipfile.BadZipFile:
            return False
    if fmt in ("CSV", "TXT", "EML"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return fmt == "EML"
        return b"\x00" not in data
    return False


def parse_original(fmt, data):
    """Open with an appropriate parser; returns a short description or raises."""
    if fmt == "EML":
        msg, body, parts = eml_parts(data)
        if not msg.keys():
            raise ValueError("no headers")
        return f"{len(parts)} attachment part(s)"
    if fmt == "PDF":
        t = pdf_text(data)
        if not t.strip():
            raise ValueError("PDF has no extractable text layer")
        return f"{len(t)} text characters"
    if fmt == "XLSX":
        cols, rows = table_from_bytes("XLSX", data)
        if not cols:
            raise ValueError("empty workbook")
        return f"{len(rows)} data row(s)"
    if fmt == "CSV":
        recs = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
        if not recs:
            raise ValueError("empty CSV")
        return f"{len(recs) - 1} data row(s)"
    if fmt == "TXT":
        t = data.decode("utf-8")
        if not t.strip():
            raise ValueError("empty text")
        return f"{len(t)} characters"
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    return "image verified"


def clock_from_manifest(v) -> Clock | None:
    if not v:
        return None
    if v.get("precision") == "unknown":
        return Clock("unknown", None, v.get("reason"))
    return Clock(v["precision"], v["value"])


class Results:
    def __init__(self):
        self.items = []

    def add(self, check, checked, failed, unavailable=(), detail=None, statement=""):
        self.items.append({"check": check, "checked": checked, "failed": len(failed), "failures": failed[:50],
                           "unavailable": list(unavailable), "status": "fail" if failed else "pass",
                           "statement": statement, **({"detail": detail} if detail is not None else {})})

    @property
    def failed(self):
        return [i for i in self.items if i["status"] == "fail"]


def runtime_model(files, manifest):
    """(cases, tables, avail, source_afca, meta) from the bundle's own bytes and manifest clocks."""
    parties = {}
    for line in files.get("native/party.jsonl", b"").decode().splitlines():
        if line.strip():
            r = json.loads(line)
            parties[r["data"]["party_id"]] = r["data"]
    # day-precision native clocks were written as the next Melbourne midnight (envelope needs a date-time); the
    # manifest lists them so they are read back as the day interval, not as an exact next-day instant
    day_clocks = {(c["record_id"], c["clock"]): c["day"] for spec in manifest.get("native_administrative_inputs", [])
                  for c in spec.get("day_precision_clocks", [])}
    cases = []
    for line in files.get("native/case.jsonl", b"").decode().splitlines():
        if line.strip():
            r = json.loads(line)
            d = r["data"]
            cases.append({"case_id": d["case_id"],
                          "receipt": datetime.fromisoformat(d["complaints_receipt_date"]).date() if d["complaints_receipt_date"] else None,
                          "available": (Clock("day", day_clocks[(r["envelope"]["record_id"], "available_at")])
                                        if (r["envelope"]["record_id"], "available_at") in day_clocks
                                        else Clock("second", r["envelope"]["available_at"])),
                          "members": sorted({parties[p]["member_reference"] for p in d["party_ids"]
                                             if p in parties and parties[p]["member_reference"]})})
    tables, avail, source_afca, meta = {}, {}, {}, {}
    for e in manifest["originals"]:
        st = e["scenario_time"]
        av = clock_from_manifest(st.get("available_at"))
        avail[e["original_id"]] = av if av is not None and av.known else None
        source_afca[e["original_id"]] = e["references"].get("afca_matter_ids") or []
        as_of = next((c for c in (clock_from_manifest(st.get(k)) for k in ("created_at", "event_at", "recorded_at", "available_at"))
                      if c is not None and c.known), None)
        meta[e["original_id"]] = {"as_of": as_of, "case_ids": e["references"].get("case_ids") or [],
                                  "afca_matter_ids": e["references"].get("afca_matter_ids") or []}
        prof = TABLE_PROFILE.get(e["kind"])
        if prof and e["declared_format"] in ("CSV", "XLSX") and e["file"] in files:
            cols, rows = table_from_bytes(e["declared_format"], files[e["file"]])
            tables[e["original_id"]] = (prof, cols, rows)
    return cases, tables, avail, source_afca, meta


def private_terms(inp):
    terms = set(inp.leak_terms if inp else [])
    if inp:
        for j in inp.judgements:
            for k in ("candidate_driver", "episode_ref", "world_intent_ref", "judgement_id"):
                if isinstance(j.get(k), str):
                    terms.add(j[k])
        for e in inp.events.values():
            if isinstance(e.get("episode_ref"), str):
                terms.add(e["episode_ref"])
    return sorted(t for t in terms if len(t) >= 5)


def _keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def mechanical_checks(files: dict, manifest: dict, index_rows: list, inp=None, rendered_text=None):
    """files: {bundle-relative path: bytes}. Returns (Results, Report, model, expectations)."""
    res, rep = Results(), Report()
    entries = manifest.get("originals", [])
    # C01 manifest shape and pin
    fails = []
    if (manifest.get("manifest_kind"), manifest.get("manifest_version")) != ("pre-extraction-delivery-manifest", "0.2.0"):
        fails.append("manifest kind/version is not pre-extraction-delivery-manifest 0.2.0")
    if manifest.get("batch", {}).get("contract_pin") != contract_pin():
        fails.append("batch.contract_pin differs from the current frozen Shared pin")
    for e in entries:
        miss = [k for k in REQUIRED_ENTRY if e.get(k) in (None, "", [])]
        if miss:
            fails.append(f"{e.get('original_id')}: missing {miss}")
        st = e.get("scenario_time") or {}
        if not (st.get("available_at") or {}).get("value"):
            fails.append(f"{e.get('original_id')}: scenario_time.available_at has no value")
        if e.get("declared_format") in FORMATS and e.get("declared_media_type") != FORMATS[e["declared_format"]][0]:
            fails.append(f"{e.get('original_id')}: declared_media_type {e.get('declared_media_type')} does not match {e['declared_format']}")
        if not e.get("references", {}).get("case") and not e.get("association_note"):
            fails.append(f"{e.get('original_id')}: no case reference and no association note")
    res.add("manifest_shape_and_pin", len(entries) + 1, fails, statement="fields the active receiver/ingest read are present")
    # C02 bytes and checksum list
    fails, listed = [], {}
    for line in files.get("INTAKE-FILES.sha256", b"").decode().splitlines():
        if line.strip():
            d, rel = line.split(None, 1)
            listed[rel.strip()] = d
    for rel, data in files.items():
        if rel == "INTAKE-FILES.sha256":
            continue
        if listed.get(rel) != sha(data):
            fails.append(f"{rel}: INTAKE-FILES.sha256 does not list its actual digest")
    for e in entries:
        data = files.get(e["file"])
        if data is None:
            fails.append(f"{e['original_id']}: file {e['file']} not present")
        elif sha(data) != e["sha256"] or len(data) != e["size_bytes"]:
            fails.append(f"{e['original_id']}: manifest hash/size differ from the bytes")
    for e in entries:
        cp = (e.get("lineage") or {}).get("copy_of")
        if cp and (cp not in {x["original_id"] for x in entries} or
                   next(x["sha256"] for x in entries if x["original_id"] == cp) != e["sha256"]):
            fails.append(f"{e['original_id']}: lineage claims an exact copy of {cp} but the bytes differ")
    res.add("bytes_and_checksums", len(files), fails, statement="listed digests, manifest hash/size, exact-copy identity")
    # C03 parsers and signatures
    fails, unavailable = [], []
    for e in entries:
        data = files.get(e["file"], b"")
        if not signature_ok(e["declared_format"], data):
            fails.append(f"{e['original_id']}: bytes do not carry a {e['declared_format']} signature")
            continue
        try:
            parse_original(e["declared_format"], data)
        except Exception as exc:  # noqa: BLE001
            fails.append(f"{e['original_id']}: {e['declared_format']} parser: {type(exc).__name__}: {exc}")
        if e["declared_format"] in ("PNG", "JPG"):
            unavailable.append(f"{e['original_id']}: image text content not read (no OCR in this check)")
    res.add("formats_open", len(entries), fails, unavailable)
    # C04 native rows
    fails, n = [], 0
    v = native_validator()
    for spec in manifest.get("native_administrative_inputs", []):
        data = files.get(spec["file"])
        if data is None or sha(data) != spec["sha256"]:
            fails.append(f"{spec['file']}: missing or hash differs")
            continue
        for ln, line in enumerate(data.decode().splitlines(), 1):
            if not line.strip():
                continue
            n += 1
            rec = json.loads(line)
            errs = [e.message for e in v.iter_errors(rec)]
            if rec.get("kind") != spec.get("record_kind"):
                errs.append(f"kind {rec.get('kind')} differs from declared {spec.get('record_kind')}")
            for k in ("recorded_at", "available_at"):
                try:
                    datetime.fromisoformat(rec["envelope"][k])
                except (KeyError, ValueError, TypeError):
                    errs.append(f"envelope.{k} is not a date-time")
            if errs:
                fails.append(f"{spec['file']}:{ln}: {errs[:2]}")
    res.add("native_input_records", n, fails, statement="validated against runtime.schema.json#/$defs/input_record (not the root)")
    # C05 attachments
    fails = []
    by_id = {e["original_id"]: e for e in entries}
    for occ in manifest.get("attachment_occurrences", []):
        parent = files.get(occ["parent_message_file"])
        target = by_id.get(occ["references_original"])
        if parent is None or target is None or target["sha256"] != occ["sha256"]:
            fails.append(f"{occ['parent_message_file']} → {occ.get('references_original')}: unresolved or hash differs")
            continue
        _, _, parts = eml_parts(parent)
        if occ["sha256"] not in {p["sha256"] for p in parts}:
            fails.append(f"{occ['parent_message_file']}: no MIME part carries {occ['references_original']}'s bytes")
    for e in entries:
        if e["declared_format"] == "EML":
            _, _, parts = eml_parts(files.get(e["file"], b""))
            declared = {o["sha256"] for o in manifest.get("attachment_occurrences", []) if o["parent_message_file"] == e["file"]}
            for p in parts:
                if p["sha256"] not in declared:
                    fails.append(f"{e['original_id']}: MIME part {p['mime_part_path']} is not an inventoried held original")
    for adv in manifest.get("advertised_unheld", []):
        if adv.get("file") or adv.get("sha256") or adv.get("size_bytes"):
            fails.append(f"{adv.get('staging_asset_ref')}: unheld item carries bytes")
    res.add("attachments_and_unheld", len(manifest.get("attachment_occurrences", [])) + len(manifest.get("advertised_unheld", [])), fails)
    # C06 clocks
    fails = []
    for e in entries:
        st = e["scenario_time"]
        cl = {k: clock_from_manifest(st.get(k)) for k in ("event_at", "created_at", "recorded_at", "available_at")}
        for k, c in cl.items():
            if c is not None and c.known and c.precision != "day":
                try:
                    melbourne_abbr(c.value)
                except (OffsetMismatch, ValueError) as exc:
                    fails.append(f"{e['original_id']}: {k}: {exc}")
        av = cl["available_at"]
        for k in ("event_at", "created_at", "recorded_at"):
            c = cl[k]
            if c is not None and c.known and av is not None and av.known and c.start() > av.end():
                fails.append(f"{e['original_id']}: {k} after available_at")
    res.add("scenario_clocks", len(entries), fails)
    # C07 tables (financial/work/lifecycle) from final bytes
    cases, tables, avail, source_afca, meta = runtime_model(files, manifest)
    trep = Report()
    seen = check_tables(tables, meta, inp, trep)
    res.add("ledger_work_lifecycle_tables", sum(len(t[2]) for t in tables.values()),
            [f"{f.code} {f.subject}: {f.message}" for f in trep.errors],
            statement="profiles, dated-snapshot rule, obligation/payment/reversal identities, duplicates, lifecycle order")
    rep.findings += trep.findings
    # C08 leakage
    fails = []
    terms = private_terms(inp)
    for rel, data in files.items():
        if any(part.lower() in ("private", "review", "gold", "world") for part in rel.split("/")):
            fails.append(f"{rel}: runtime path in a private location")
        texts = [rel] + [t for _, t in (representations(rel, data) if not rel.endswith(".sha256") else [])]
        for t in texts:
            low = t.lower()
            for term in terms:
                if term.lower() in low:
                    fails.append(f"{rel}: contains private term {term!r}")
    keys = _keys(manifest, set()) | _keys(index_rows, set())
    for rel in ("native/case.jsonl", "native/party.jsonl"):
        for line in files.get(rel, b"").decode().splitlines():
            if line.strip():
                _keys(json.loads(line), keys)
    bad = sorted(keys & FORBIDDEN_KEYS)
    if bad:
        fails.append(f"runtime metadata uses private-label keys {bad}")
    res.add("runtime_leakage", len(files), sorted(set(fails)),
            ["image pixels are not OCR'd"] if any(e["declared_format"] in ("PNG", "JPG") for e in entries) else [],
            statement=f"{len(terms)} private terms and the private-key list searched in every receiver-readable representation")
    # C09 authored text preserved
    if rendered_text is not None:
        fails = []
        for oid, (fmt, want) in rendered_text.items():
            e = by_id[oid]
            data = files[e["file"]]
            if fmt == "TXT" and data.decode("utf-8") != want:
                fails.append(f"{oid}: TXT bytes differ from the authored text")
            elif fmt == "EML":
                _, body, _ = eml_parts(data)
                if body is None or body.replace("\r\n", "\n") != want.replace("\r\n", "\n"):
                    fails.append(f"{oid}: EML text/plain body differs from the authored body")
            elif fmt == "PDF":
                got = re.sub(r"\s+", " ", pdf_text(data))
                for para in want:
                    if re.sub(r"\s+", " ", para).strip() not in got:
                        fails.append(f"{oid}: authored passage not found in the PDF text layer: {para[:60]!r}")
            elif fmt == "FILE" and sha(data) != want:
                fails.append(f"{oid}: supplied original not byte-identical to the author's file")
        res.add("authored_content_preserved", len(rendered_text), fails,
                statement="TXT/EML bodies equal the authored text; PDF passages present; supplied files byte-identical")
    # expectations
    checkpoints = (inp.batch["replay_checkpoints"] if inp else manifest.get("replay_checkpoints", []))
    window = (datetime.fromisoformat(manifest["scope"]["observation_window"]["start"]).date(),
              datetime.fromisoformat(manifest["scope"]["observation_window"]["end_exclusive"]).date())
    exp = compute(cases, seen, avail, source_afca, checkpoints, window)
    res.add("expectation_reconciliation", len(exp["reconciliation"]),
            [f"{r['grain']} {r['metric']}" for r in exp["reconciliation"] if not r["ok"]],
            statement="every grain's buckets sum to the total and partition the exact contributor set")
    return res, rep, (cases, seen, avail), exp


def judgement_anchor_checks(inp, files, manifest, avail):
    """Private review shape: anchors resolve to final bytes and were available by the judgement cutoff. Says nothing
    about whether the reasoning is right; never promotes an author label."""
    out, rep = [], Report()
    by_id = {e["original_id"]: e for e in manifest["originals"]}
    texts = {}

    def text_of(sid):
        if sid not in texts:
            e = by_id[sid]
            texts[sid] = re.sub(r"\s+", " ", " ".join(t for _, t in representations(e["file"], files[e["file"]])))
        return texts[sid]
    for j in inp.judgements:
        s = f"judgement:{j.get('judgement_id')}"
        row = {"judgement_id": j.get("judgement_id"), "judgement": j.get("judgement"), "anchors": []}
        if j.get("review_status") != "author_proposed":
            rep.add("self_promoted_gold", s, f"review_status {j.get('review_status')!r}",
                    "authors propose; only a separate source-first reviewer can support a label (review_status: author_proposed)")
        if j.get("judgement") not in ("supported", "uncertain", "contradicted", "not_assessed"):
            rep.add("judgement_value", s, f"judgement {j.get('judgement')!r}", "supported, uncertain, contradicted or not_assessed")
        if j.get("judgement") == "supported" and not j.get("supporting"):
            rep.add("unsupported_judgement", s, "supported without any supporting anchor", "cite the passage(s)")
        try:
            cutoff_d = datetime.fromisoformat(j["cutoff"]).date()
        except (KeyError, ValueError, TypeError):
            rep.add("judgement_cutoff", s, "cutoff must be YYYY-MM-DD", "")
            continue
        for side in ("supporting", "contrary"):
            for a in j.get(side) or []:
                sid, quote = a.get("source_id"), a.get("quote", "")
                st = {"side": side, "source_id": sid, "quote": quote}
                if sid not in by_id:
                    st["result"] = "unknown_source"
                    rep.add("anchor_unknown_source", s, f"{sid} is not a delivered original", "")
                elif not held_by(avail.get(sid), cutoff_d):
                    st["result"] = "not_available_at_cutoff"
                    rep.add("anchor_after_cutoff", s, f"{sid} was not available by {j['cutoff']}", "cite only evidence held by the cutoff")
                elif not quote.strip() or re.sub(r"\s+", " ", quote).strip() not in text_of(sid):
                    st["result"] = "passage_not_found"
                    rep.add("anchor_not_found", s, f"passage not found in {sid}: {quote[:60]!r}", "quote the final text exactly")
                else:
                    st["result"] = "resolved"
                row["anchors"].append(st)
        row["missing_evidence"] = j.get("missing_evidence") or []
        row["review_status"] = "author_proposed_pending_independent_review"
        out.append(row)
    return out, rep


def read_tree(base: Path):
    return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def verify_candidate(cand) -> dict:
    """Read-only re-verification of a published candidate."""
    cand = Path(cand).resolve()
    before = {str(p.relative_to(cand)): sha(p.read_bytes()) for p in sorted(cand.rglob("*")) if p.is_file()}
    bundle = cand / "runtime/intake-bundle"
    files = read_tree(bundle)
    manifest = json.loads(files["delivery-manifest.json"])
    index_rows = [json.loads(x) for x in (cand / "runtime/SOURCE-INDEX.jsonl").read_text().splitlines() if x.strip()]
    inp_dir = cand / "private/AUTHORED-INPUT"
    inp, irep = load_input(inp_dir) if inp_dir.is_dir() else (None, Report())
    rendered_text = None
    if inp is not None:
        rendered_text = expected_text(inp)
    res, rep, model, exp = mechanical_checks(files, manifest, index_rows, inp, rendered_text)
    stored = json.loads((cand / "private/EXPECTED-METRICS.json").read_text())
    res.add("expectations_match_recorded", 1, [] if stored == json.loads(json.dumps(exp, default=str)) else
            ["private/EXPECTED-METRICS.json differs from expectations recomputed from the final bytes"])
    # status honesty
    status = json.loads((cand / "review/REVIEW-STATUS.json").read_text())
    form = json.loads((cand / "review/INDEPENDENT-REVIEW-FORM.json").read_text())
    fails = []
    filled = bool(form.get("reviewer", {}).get("name")) and bool(form.get("conclusions"))
    for k, v in STATUS_NOT_DONE.items():
        if status.get(k) != v and not (k == "independent_source_review" and filled):
            fails.append(f"REVIEW-STATUS {k}={status.get(k)!r} but no recorded review/processing evidence exists")
    if manifest.get("status", {}).get("independent_source_review") != "not_performed" or \
            manifest.get("status", {}).get("ai_processing") != "not_performed":
        fails.append("delivery manifest status claims review or AI processing")
    recorded = json.loads((cand / "review/CHECK-RESULTS.json").read_text())
    claimed = {i["check"]: i["status"] for i in recorded.get("checks", [])}
    actual = {i["check"]: i["status"] for i in res.items}
    for k, v in claimed.items():
        if k in actual and actual[k] != v:
            fails.append(f"CHECK-RESULTS claims {k}={v} but recomputation gives {actual[k]}")
    res.add("status_honesty", len(claimed) + 3, fails, statement="no report claims review/AI processing or a check result that recomputation contradicts")
    # zips
    fails = []
    for zname, roots in ((f"{cand.name}-runtime.zip", ("runtime/",)),
                         (f"{cand.name}-private-review.zip", ("private/", "review/", "README.md"))):
        zp = cand / zname
        if not zp.is_file():
            fails.append(f"{zname} missing")
            continue
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
            tree = {k for k in before if k.startswith(roots)}
            if names != tree:
                fails.append(f"{zname}: entries differ from the tree ({len(names ^ tree)} differences)")
            for n in names & tree:
                if sha(z.read(n)) != before[n]:
                    fails.append(f"{zname}: {n} differs from the tree bytes")
    res.add("zip_parity", 2, fails)
    after = {str(p.relative_to(cand)): sha(p.read_bytes()) for p in sorted(cand.rglob("*")) if p.is_file()}
    res.add("verification_did_not_mutate", len(before), [] if before == after else ["files changed during verification"])
    return {"candidate": cand.name, "checks": res.items, "failed": [i["check"] for i in res.failed],
            "findings": rep.as_list() + irep.as_list(),
            "outside_scope": ["realism of prose", "causal correctness of private judgements", "independent source review",
                              "AI extraction/analysis", "backend shared-matter projection"]}


def expected_text(inp):
    """{source_id: (FMT, authored text | [paragraphs] | sha)} for the preservation check."""
    from .model import safe_input_path
    out = {}
    for sid, s in inp.sources.items():
        c = s["content"]
        mode = c.get("mode")
        if mode == "text":
            out[sid] = ("TXT", c["text"] if "text" in c else safe_input_path(inp.root, c["path"]).read_text(encoding="utf-8"))
        elif mode == "email":
            out[sid] = ("EML", c["body_text"] if "body_text" in c else safe_input_path(inp.root, c["body_path"]).read_text(encoding="utf-8"))
        elif mode == "pdf_document":
            paras = [c["title"]] + [b["p"] for b in c.get("blocks") or [] if "p" in b] + \
                    [i for b in c.get("blocks") or [] if "ul" in b for i in b["ul"]]
            out[sid] = ("PDF", paras)
        elif mode == "supplied_file":
            out[sid] = ("FILE", sha(safe_input_path(inp.root, c["path"]).read_bytes()))
    return out


def diff_candidates(old, new):
    """Original-by-original comparison of two candidates' manifests (for resuming/repairing one member)."""
    load = lambda c: {e["original_id"]: e for e in json.loads((Path(c) / "runtime/intake-bundle/delivery-manifest.json").read_text())["originals"]}  # noqa: E731
    a, b = load(old), load(new)
    meta = lambda e: {k: v for k, v in e.items() if k not in ("sha256", "size_bytes", "mime_parts")}  # noqa: E731
    return {"unchanged": sorted(k for k in a.keys() & b.keys() if a[k]["sha256"] == b[k]["sha256"] and meta(a[k]) == meta(b[k])),
            "bytes_changed": sorted(k for k in a.keys() & b.keys() if a[k]["sha256"] != b[k]["sha256"]),
            "metadata_changed": sorted(k for k in a.keys() & b.keys() if a[k]["sha256"] == b[k]["sha256"] and meta(a[k]) != meta(b[k])),
            "added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys())}
