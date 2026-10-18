"""Structural receiver check for an authored candidate, using the UNMODIFIED AI/backend code in place:

  1. `intake.delivery.receive(bundle, rt=None)` — manifest kind/version, contract pin, INTAKE-FILES, byte hashes/sizes,
     declared-MIME vs signature, native rows against `#/$defs/input_record`. With no store, every item is
     `staged_pending_review`: nothing is accepted, imported, ingested or published.
  2. `extract.registry.extract` on every original (EML/PDF/CSV/TXT/XLSX; images use local OCR if it is available).
  3. The registered `native_ledger` / `native_work` profiles on each declared financial/work table's extracted text,
     with the source links ingest gives TODAY (one case = `references.case`; AFCA links empty) and, separately
     labelled, with the complete links from the manifest.

Success (exit 0, ok=true) requires ALL of:
  * every original byte-checked and staged, every native row staged;
  * no failed extraction; partial extraction only where disclosed by design (image OCR), listed separately;
  * every declared financial/work table recognised by its profile and projected without an exception;
  * under the full links, the projected ids equal the ids read independently from the table bytes (recorded work
    rows only; planned/estimate rows are excluded by the profile and listed);
  * under today's links, every missing row is explained by the known singleton case / empty AFCA mapping (F13).
Anything else is an unexpected failure, reported with original id, table and reason, and the exit code is 1.

Run with the backend interpreter:
  ../ai-backend/.venv/bin/python tools/authored_receiver_check.py <candidate>/runtime/intake-bundle <out.json>
Optional `--ingest-scratch`: also run the normal ingest into a DISPOSABLE store. SDB_DATA_DIR must name an existing,
empty directory under the system temporary area, distinct from the backend's default/served store. It is validated
before any runtime exists and bound explicitly into the runtime settings."""
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2] / "ai-backend"
ARTHA = HERE.parents[3]
sys.path.insert(0, str(BACKEND))

from sdbackend.engine import native_ledger, native_work  # noqa: E402
from sdbackend.extract.registry import extract  # noqa: E402
from sdbackend.intake.delivery import DeliveryRejected, receive  # noqa: E402

CODE = ["sdbackend/intake/delivery.py", "sdbackend/ingest/build.py", "sdbackend/extract/registry.py",
        "sdbackend/engine/native_ledger.py", "sdbackend/engine/native_work.py", "sdbackend/engine/native_tables.py",
        "sdbackend/engine/resolvers.py", "sdbackend/contracts.py", "sdbackend/config.py", "sdbackend/runtime.py"]
DISCLOSED_PARTIAL = ("image/png", "image/jpeg")   # local OCR is partial by design (unreviewed transcription)
ID_COLUMN = {"financial": "event_id", "work": "work_id"}
ESTIMATE = {"estimate", "estimated", "planned", "forecast", "budget"}


class ScratchRefused(SystemExit):
    pass


def validated_scratch_dir(env=None) -> Path:
    """The disposable store directory, or ScratchRefused before anything touches a runtime."""
    env = os.environ if env is None else env
    raw = (env.get("SDB_DATA_DIR") or "").strip()
    if not raw:
        raise ScratchRefused("--ingest-scratch needs SDB_DATA_DIR set to an explicit scratch directory (missing/blank)")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ScratchRefused(f"SDB_DATA_DIR {path} is not an existing directory")
    if any(path.iterdir()):
        raise ScratchRefused(f"SDB_DATA_DIR {path} is not empty; use a fresh scratch directory")
    default = (BACKEND / "var" / "data").resolve()
    for protected in (default, BACKEND.resolve(), ARTHA.resolve()):
        if path == protected or protected in path.parents or path in protected.parents:
            raise ScratchRefused(f"SDB_DATA_DIR {path} overlaps the protected/default store location {protected}")
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), Path("/private/tmp").resolve(),
                  Path("/private/var/folders").resolve()}
    if not any(r == path or r in path.parents for r in temp_roots):
        raise ScratchRefused(f"SDB_DATA_DIR {path} is not under a system temporary directory")
    return path


def scratch_runtime(path: Path):
    """A runtime bound EXPLICITLY to the validated path (never the settings default)."""
    import dataclasses

    from sdbackend.config import load_settings
    from sdbackend.runtime import make_runtime
    settings = dataclasses.replace(load_settings(), data_dir=path)
    if settings.data_dir != path:
        raise ScratchRefused("runtime settings did not bind the validated scratch path")
    return make_runtime(settings)


def _make(kind, rid, available_at, refs, data):
    return {"kind": kind, "envelope": {"record_id": rid, "evidence_refs": refs}, "data": data}


def project(entry, text, profile, case_ids, afca_ids):
    """→ {profile_recognised, projected, obligations, unresolved} or {exception}."""
    src = {"source_id": entry["original_id"], "text": text, "case_ids": case_ids, "afca_matter_ids": afca_ids,
           "content_sha256": "0" * 64}
    mod = native_ledger if profile == "financial" else native_work
    try:
        recognised = (mod.is_ledger if profile == "financial" else mod.is_work_log)(src)
        recs, unresolved = mod.project(src, _make)
    except Exception as exc:  # noqa: BLE001
        return {"exception": f"{type(exc).__name__}: {exc}"}
    return {"profile_recognised": recognised,
            "projected": sorted(r["envelope"]["record_id"] for r in recs if r["kind"] in ("financial_event", "work_entry")),
            "obligations": sorted(r["envelope"]["record_id"] for r in recs if r["kind"] == "obligation"),
            "unresolved": [{"subject": u["subject"], "reason": u["reason"]} for u in unresolved]}


def table_rows(fmt, data):
    """Rows read independently of the backend: CSV via csv, XLSX first sheet via openpyxl (stored values)."""
    if fmt == "CSV":
        recs = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        recs = [["" if v is None else str(v) for v in r] for r in wb.worksheets[0].iter_rows(values_only=True)]
        wb.close()
    if not recs:
        return []
    head = recs[0]
    return [dict(zip(head, r)) for r in recs[1:] if any(str(x).strip() for x in r)]


def check_table(e, data, text, failures, known_f13):
    prof, refs = e["table_profile"], e["references"]
    rows = table_rows(e["declared_format"], data)
    idc = ID_COLUMN[prof]
    excluded = sorted(r.get(idc, "") for r in rows if prof == "work" and str(r.get("basis", "")).strip().lower() in ESTIMATE)
    expected = sorted(r.get(idc, "") for r in rows if r.get(idc, "") not in excluded)
    today = project(e, text, prof, [refs["case"]] if refs.get("case") else [], [])
    full = project(e, text, prof, refs.get("case_ids") or [], refs.get("afca_matter_ids") or [])
    out = {"original_id": e["original_id"], "table_profile": prof, "expected_ids": expected,
           "excluded_estimates": excluded, "current_ingest_links": today, "full_links_hypothetical": full,
           "known_f13_losses": [], "unexpected": []}

    def fail(reason):
        out["unexpected"].append(reason)
        failures.append({"original_id": e["original_id"], "table": prof, "reason": reason})
    for label, res in (("current", today), ("full", full)):
        if "exception" in res:
            fail(f"{label}-links projection raised {res['exception']}")
        elif not res["profile_recognised"]:
            fail(f"{label}-links: declared {prof} table not recognised by the registered profile")
    if out["unexpected"]:
        return out
    if full["projected"] != expected:
        fail(f"full links projected {full['projected']} but the table holds {expected} "
             f"(unresolved: {[u['reason'] for u in full['unresolved']][:3]})")
    by_id = {r.get(idc, ""): r for r in rows}
    for rid in sorted(set(expected) - set(today["projected"])):
        r = by_id.get(rid, {})
        if r.get("case_id") != refs.get("case") or (r.get("afca_matter_id") or "").strip():
            out["known_f13_losses"].append(rid)
            known_f13.append({"original_id": e["original_id"], "row": rid,
                              "reason": "current ingest maps one case per original and no AFCA links (F13)"})
        else:
            fail(f"row {rid} not projected under current links for an unexplained reason")
    return out


def ingest_scratch(d, path):
    from sdbackend.ingest.build import ingest
    rt = scratch_runtime(path)
    res = ingest(rt, d)
    rel = rt.db.get_object("release", res["release_id"])
    rows = []
    for ent in rel["entries"]:
        if ent["kind"] != "asset":
            continue
        a = rt.db.get_object("asset", ent["immutable_id"])
        if a is None or a.get("derived_from_asset_version_ids") or not a["source_ids"]:
            continue
        s = rt.db.get_object("source", a["source_ids"][0])
        env, data = s["envelope"], s["data"]
        rows.append({"filename": a["filename"], "author_reference": data["author_reference"], "author_role": data["author_role"],
                     "source_family": data["source_family"], "subtype": data["subtype"], "case_ids": data["case_ids"],
                     "afca_matter_ids": data["afca_matter_ids"], "available_at": env["available_at"],
                     "occurred_at": env["occurred_at"], "occurrence_date": env["occurrence_date"]})
    return {"release_state": {k: res[k] for k in res if k not in ("limitations",)}, "limitations": res.get("limitations", []),
            "sources": sorted(rows, key=lambda r: r["filename"]), "store": "disposable scratch store; not served",
            "data_dir_bound": str(rt.settings.data_dir)}


def main(bundle, out_path, scratch=False):
    path = validated_scratch_dir() if scratch else None          # before ANY runtime exists
    bundle = Path(bundle).resolve()
    report = {"kind": "authored-candidate-receiver-check",
              "statement": "unmodified backend receiver/extractors/profiles; no store, no import, no publication; "
                           "a structural receipt with review pending, not acceptance",
              "backend_code_sha256": {p: hashlib.sha256((BACKEND / p).read_bytes()).hexdigest() for p in CODE},
              "bundle": str(bundle)}
    failures, known_f13, disclosed_partial = [], [], []
    try:
        d = receive(bundle, rt=None)
    except DeliveryRejected as exc:
        report.update(receiver={"rejected": str(exc)}, ok=False,
                      failures=[{"original_id": None, "reason": f"receiver rejected the delivery: {exc}"}])
        Path(out_path).write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps({"ok": False, "receiver_rejected": str(exc)}))
        return 1
    r = d.receipt
    for e in r["originals"]:
        if e["disposition"] != "staged_pending_review":
            failures.append({"original_id": e["original_id"], "reason": f"receiver {e['disposition']}: {e['reasons']}"})
    for n in r["native_administrative_inputs"]:
        if n["state"] != "received":
            failures.append({"original_id": n["file"], "reason": f"native file {n['state']}: {n['reasons']}"})
        for x in n["rows"]:
            if x["state"] != "staged_pending_review":
                failures.append({"original_id": x.get("record_id"), "reason": f"native row {x['state']}: {x.get('reasons')}"})
    report["receiver"] = {"summary": r["summary"], "problems": r["problems"], "unlisted_files_ignored": r["unlisted_files_ignored"],
                          "originals": [{k: e[k] for k in ("original_id", "byte_checks", "disposition", "reasons")} for e in r["originals"]],
                          "attachment_occurrences": [o["check"] for o in r["attachment_occurrences"]],
                          "advertised_unheld": r["advertised_unheld"], "native": r["native_administrative_inputs"]}
    for o in r["attachment_occurrences"]:
        if o["check"] != "bytes_reference_consistent":
            failures.append({"original_id": o["references_original"], "reason": f"attachment occurrence {o['check']}"})
    entries = {e["original_id"]: e for e in d.manifest["originals"]}
    ext, tables = [], []
    for o in d.originals:
        oid = o.entry["original_id"]
        try:
            prof, outp = extract(o.data, o.media_type)
            state, omissions, text = outp.state, list(outp.omissions), outp.text or ""
        except Exception as exc:  # noqa: BLE001
            state, omissions, text = "failed", [f"extractor raised {type(exc).__name__}: {exc}"], ""
        ext.append({"original_id": oid, "media_type": o.media_type, "state": state, "omissions": omissions[:3],
                    "text_chars": len(text)})
        if state == "failed":
            failures.append({"original_id": oid, "reason": f"extraction failed: {omissions[:2]}"})
            continue
        if state == "partial":
            if o.media_type in DISCLOSED_PARTIAL:
                disclosed_partial.append({"original_id": oid, "omissions": omissions[:3]})
            else:
                failures.append({"original_id": oid, "reason": f"unexpected partial extraction of {o.media_type}: {omissions[:2]}"})
                continue
        e = entries[oid]
        if e.get("table_profile") in ("financial", "work"):
            tables.append(check_table(e, o.data, text, failures, known_f13))
    report.update(extraction=ext, tables=tables)
    if path is not None and not failures:
        report["scratch_ingest"] = ingest_scratch(d, path)
        unknown = [x["filename"] for x in report["scratch_ingest"]["sources"] if x["author_role"] == "unknown"]
        failures += [{"original_id": f, "reason": "ingested source has author_role unknown"} for f in unknown]
    report["failures"] = failures
    report["known_f13_losses"] = known_f13
    report["disclosed_partial_extractions"] = disclosed_partial
    report["ok"] = not failures
    s = r["summary"]
    report["summary"] = {"ok": report["ok"], "originals": s, "unexpected_failures": len(failures),
                         "extraction_states": {st: sum(1 for x in ext if x["state"] == st) for st in sorted({x["state"] for x in ext})},
                         "disclosed_partial": [x["original_id"] for x in disclosed_partial],
                         "table_rows_expected": sum(len(t["expected_ids"]) for t in tables),
                         "table_rows_projected_current": sum(len(t["current_ingest_links"].get("projected", [])) for t in tables),
                         "table_rows_projected_full_links": sum(len(t["full_links_hypothetical"].get("projected", [])) for t in tables),
                         "rows_lost_to_current_mapping": sorted(x["row"] for x in known_f13)}
    Path(out_path).write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps(report["summary"], indent=1))
    for f in failures:
        print(f"UNEXPECTED: {f}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        sys.exit(main(args[0], args[1], "--ingest-scratch" in sys.argv))
    except ScratchRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
