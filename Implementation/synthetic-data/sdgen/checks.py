"""Layered package verification. Layers are reported separately so a byte-integrity review never depends on
reconstruction tooling:

  integrity       manifest + bytes only (hashes, formats, MIME structure, attachment reuse, chronology headers, privacy scan)
  provenance      recorded authored revisions/hashes, plan digest, renderer provenance, base-package lineage
  semantic        plan-bound facts (locked values, later-knowledge, summary fidelity); 'unavailable' if the plan changed
  reconstruction  rebuild from the exact recorded inputs and compare bytes; 'unavailable' if an input is gone or changed

These are generator-side authoring and packaging checks, never independent source acceptance."""
import email
import io
import json
import re
import tempfile
from datetime import date, datetime
from email import policy
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from .build import BATCH, REPAIRS, Batch, InputUnavailable, load_pinned, sha256
from .paths import RELEASE_ROOT, TOOL_ROOT
from .plan import CasePlan, parse_dt
from .textfmt import MONTHS, date_variants

# Scoped leakage vocabulary: control-field names, planning identifiers and generator artefacts. Ordinary words
# (family, planned, incident, episode, evaluation, replay, private, gold, diagnosis ...) are NOT private labels by
# themselves and are deliberately absent; leakage is caught through identifiers/control fields and semantic review.
FORBIDDEN_TEXT = [r"source_assertion", r"assertion_type", r"attributed_to", r"writer_constraints?", r"replay_constraints?",
                  r"must_not_claim", r"forbidden_inference", r"\bSRC-R2", r"\bAST-[0-9a-f]{6}", r"THR-R2", r"\b11-D\d\d\b",
                  r"\{\"", r"\{\{", r"\b(TODO|TBD)\b", r"lorem ipsum", r"\bplaceholder (text|value)\b",
                  r"\b(incident|episode|cause)[_ -]id\b", r"\bfamily [A-F]\b", r"\bgold (label|answer|key)\b",
                  r"\bdiagnostic[_ ](label|gold|finding)\b", r"\b(synthetic|fictional) (data|demonstration|demo|record|example)\b",
                  r"writing constraint", r"\bsplit[_ ](id|assignment)\b", r"\bplanned[_ ]label\b"]
FORBIDDEN_MANIFEST_KEYS = re.compile(r"^(writer_constraints?|replay_constraints?|source_assertions|conversation|conversations|transcript|"
                                     r"incident(_\w+)?|episode(_\w+)?|family_label|split(_\w+)?|tier(_\w+)?|world(_\w+)?|"
                                     r"diagnostic_\w+|gold\w*|expected_\w+|answer\w*|planned_label)$", re.I)
ID_FAMILIES = [r"APR-\d+-\d+", r"ACT-\d+-\d+", r"OBL-\d+-[A-Z]+", r"INS-\d+-\d+", r"REQDOC-\d+-\d+",
               r"AF-[0-9a-f]{10,}", r"FIN-R2-[0-9a-f]+", r"INV-\d+-[A-Z]+", r"DOC-\d+-[A-Z]{3,4}",
               r"UP-\d+-\d+", r"AUTH-\d+-\d+"]
DATE_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b")
SPOKEN_ID_RE = re.compile(r"\b(?:C-[0-9a-f]{10,}|REQ-[\d-]+|DC-\d+|ID-\d+|RF-\d+)\b")
LAYERS = ("integrity", "provenance", "semantic", "reconstruction")


class ReviewRecordProtected(RuntimeError):
    """Refusing to overwrite an existing review/decision/check record."""


class Results:
    def __init__(self):
        self.items = []

    def add(self, cid, name, ok, detail="", severity="error", layer="integrity"):
        st = "pass" if ok else ("fail" if severity == "error" else "warn")
        self.items.append({"id": cid, "layer": layer, "name": name, "status": st, "detail": detail})

    def unavailable(self, cid, name, detail, layer):
        self.items.append({"id": cid, "layer": layer, "name": name, "status": "unavailable", "detail": detail})

    def summary(self):
        def cnt(items):
            return {s: sum(i["status"] == s for i in items) for s in ("pass", "fail", "warn", "unavailable")}
        by = {l: cnt([i for i in self.items if i["layer"] == l]) for l in LAYERS}
        c = cnt(self.items)
        return {"total": len(self.items), "passed": c["pass"], "failed": c["fail"], "warnings": c["warn"],
                "unavailable": c["unavailable"], "by_layer": by,
                "byte_integrity_ok": by["integrity"]["fail"] == 0}


def pdf_text(data: bytes) -> str:
    r = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)


def eml_parts(data: bytes):
    msg = email.message_from_bytes(data, policy=policy.default)
    body = msg.get_body(preferencelist=("plain",)).get_content()
    atts = [(a.get_filename(), a.get_content_type(), a.get_payload(decode=True)) for a in msg.iter_attachments()]
    return msg, body, atts


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip().lower()


def value_present(text: str, value) -> bool:
    t = norm(text)
    if isinstance(value, bool):
        return True
    v = str(value)
    cands = {v}
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        cands |= date_variants(v)
    if re.match(r"^\d{4}-\d{2}-\d{2}T", v):
        cands |= date_variants(v[:10])
    return any(norm(c) in t for c in cands)


def resolve_date(m, event_dt: datetime):
    d, mon, y = int(m.group(1)), MONTHS.index(m.group(2)) + 1, m.group(3)
    if y:
        return date(int(y), mon, d)
    best = None
    for yr in (event_dt.year - 1, event_dt.year, event_dt.year + 1):
        try:
            c = date(yr, mon, d)
        except ValueError:
            continue
        if best is None or abs((c - event_dt.date()).days) < abs((best - event_dt.date()).days):
            best = c
    return best


def summary_parts(txt):
    head, rest = txt.split("\nSUMMARY\n", 1)
    return head, rest.split("\n\nFOLLOW-UP", 1)[0]


# ============================ integrity ============================
def _integrity(R, pkg, man):
    L = "integrity"
    bundle, restricted = pkg / "intake-bundle", pkg / "restricted"
    originals = man["originals"]
    texts = {}
    for o in originals:
        f = bundle / o["file"]
        ok = f.exists()
        R.add("A1", f"file exists: {o['file']}", ok, layer=L)
        if ok:
            data = f.read_bytes()
            R.add("A1", f"sha256/size match: {o['file']}", sha256(data) == o["sha256"] and len(data) == o["size_bytes"], layer=L)
    sums = {}
    for line in (bundle / "INTAKE-FILES.sha256").read_text().splitlines():
        h, rel = line.split("  ", 1)
        sums[rel] = h
    actual = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()} - {"INTAKE-FILES.sha256"}
    R.add("A2", "INTAKE-FILES.sha256 lists exactly the bundle files", set(sums) == actual,
          f"extra={sorted(actual - set(sums))} missing={sorted(set(sums) - actual)}", layer=L)
    R.add("A2", "hash list matches file bytes", all(sha256((bundle / r).read_bytes()) == h for r, h in sums.items() if (bundle / r).exists()), layer=L)
    referenced = {o["file"] for o in originals} | {p["preview_file"] for p in man["previews"]} | {"delivery-manifest.json"}
    R.add("A3", "no unreferenced file in bundle (allowlist)", actual == referenced, f"unreferenced={sorted(actual - referenced)}", layer=L)

    for o in originals:
        f = bundle / o["file"]
        if not f.exists():
            continue
        d, ext = f.read_bytes(), f.suffix.lower()
        if ext == ".pdf":
            ok = d.startswith(b"%PDF-")
            try:
                r = PdfReader(io.BytesIO(d))
                ok = ok and len(r.pages) >= 1 and not r.is_encrypted
                texts[o["file"]] = pdf_text(d)
                cd = (r.metadata or {}).get("/CreationDate", "")
                st = o["scenario_time"]
                created = (st.get("created_at") or st["event_at"])["value"]
                want = "D:" + re.sub(r"[-:T]", "", created[:19])
                R.add("A6", f"PDF creation date is the scenario date: {o['filename']}", cd.startswith(want), f"{cd} vs {want}", layer=L)
            except Exception:
                ok = False
            R.add("A4", f"valid PDF: {o['filename']}", ok, layer=L)
        elif ext == ".jpg":
            try:
                im = Image.open(io.BytesIO(d))
                ok = d[:3] == b"\xff\xd8\xff" and im.format == "JPEG" and im.size[0] > 400
            except Exception:
                ok = False
            R.add("A4", f"valid JPEG: {o['filename']}", ok, layer=L)
        elif ext == ".csv":
            try:
                t = d.decode("utf-8")
                rows = list(__import__("csv").reader(io.StringIO(t)))
                ok = len(rows) >= 2 and len({len(r) for r in rows}) == 1
                texts[o["file"]] = t
            except Exception:
                ok = False
            R.add("A4", f"valid rectangular CSV: {o['filename']}", ok, layer=L)
        elif ext == ".txt":
            try:
                texts[o["file"]] = d.decode("utf-8")
                ok = True
            except Exception:
                ok = False
            R.add("A4", f"valid UTF-8 TXT: {o['filename']}", ok, layer=L)
        elif ext == ".eml":
            try:
                msg, body, atts = eml_parts(d)
                texts[o["file"]] = body
                ok = all(msg[h] for h in ("From", "To", "Date", "Subject", "Message-ID"))
            except Exception:
                ok = False
            R.add("A4", f"parseable EML with required headers: {o['filename']}", ok, layer=L)

    # attachments / dedupe, manifest-driven (no plan)
    held_hash, occ_total = {}, 0
    emls = [o for o in originals if o.get("kind") == "email"]
    by_file = {}
    for oc in man["attachment_occurrences"]:
        by_file.setdefault(oc["parent_message_file"], []).append(oc)
    for o in emls:
        card = o["staging_card_ref"]
        msg, body, atts = eml_parts((bundle / o["file"]).read_bytes())
        occ = by_file.get(o["file"], [])
        R.add("B1", f"{card} embedded attachments equal manifest occurrences",
              sorted((a[0], sha256(a[2])) for a in atts) == sorted((x["filename"], x["sha256"]) for x in occ), layer=L)
        occ_total += len(atts)
        for fname, ctype, payload in atts:
            standalone = [x for x in originals if x["filename"] == fname and x["file"] != o["file"]]
            R.add("B1", f"{card} embedded bytes equal standalone original: {fname}",
                  bool(standalone) and all(sha256(payload) == s["sha256"] for s in standalone), layer=L)
            held_hash.setdefault(fname, set()).add(sha256(payload))
    R.add("B2", "same filename reused with identical bytes in every message", all(len(v) == 1 for v in held_hash.values()),
          str({k: len(v) for k, v in held_hash.items() if len(v) != 1}), layer=L)
    R.add("B3", "occurrence records equal embedded attachment count", len(man["attachment_occurrences"]) == occ_total,
          f"{len(man['attachment_occurrences'])} vs {occ_total}", layer=L)
    names_in_bundle = {Path(a).name for a in actual}
    for u in man["advertised_unheld"]:
        R.add("B5", f"unheld file has no bytes/hash/file: {u['advertised_filename']}",
              u["file"] is None and u["sha256"] is None and u["advertised_filename"] not in names_in_bundle, layer=L)
        for o in emls:
            R.add("B5", f"unheld file not embedded in {o['staging_card_ref']}",
                  u["advertised_filename"] not in [a[0] for a in eml_parts((bundle / o["file"]).read_bytes())[2]], layer=L)

    # chronology headers from the manifest
    parts_by_id = {p["actor_ref"]: p for p in man["participants"]}
    mids = {}
    for o in emls:
        c = o["staging_card_ref"]
        msg, body, atts = eml_parts((bundle / o["file"]).read_bytes())
        R.add("D1", f"{c}: Date header equals scenario event time", parse_dt(o["scenario_time"]["event_at"]["value"]) == email.utils.parsedate_to_datetime(msg["Date"]), layer=L)
        R.add("D1", f"{c}: Subject equals manifest subject", " ".join(str(msg["Subject"]).split()) == o["subject"], layer=L)
        R.add("D1", f"{c}: recipients equal manifest", {parts_by_id[r]["email"] for r in o["recipients"]} == {a.addr_spec for a in msg["To"].addresses}, layer=L)
        R.add("D1", f"{c}: sender equals manifest author", msg["From"].addresses[0].addr_spec == parts_by_id[o["author"]]["email"], layer=L)
        mids[msg["Message-ID"]] = mids.get(msg["Message-ID"], 0) + 1
    R.add("D2", "Message-IDs unique", all(v == 1 for v in mids.values()), layer=L)
    for o in originals:
        st = o.get("scenario_time", {})
        if "event_at" in st:
            ev, rec, av = (parse_dt(st[k]["value"]) for k in ("event_at", "recorded_at", "available_at"))
            R.add("D5", f"{o['filename']}: event <= recorded <= available", ev <= rec <= av, layer=L)
        elif "created_at" in st and st["available_at"]["value"]:
            R.add("D5", f"{o['filename']}: created <= available", parse_dt(st["created_at"]["value"]) <= parse_dt(st["available_at"]["value"]), layer=L)

    # exact forward quotation
    for o in emls:
        msg, body, _ = eml_parts((bundle / o["file"]).read_bytes())
        if "---------- Forwarded message ---------" not in body:
            continue
        hdr, quoted = body.split("---------- Forwarded message ---------", 1)[1].split("\n\n", 1)
        subj = re.search(r"^Subject: (.+?)\nTo:", hdr, re.S | re.M)
        subj = " ".join(subj.group(1).split()) if subj else None
        srcs = [x for x in emls if x["subject"] == subj and x is not o and parse_dt(x["scenario_time"]["event_at"]["value"]) < parse_dt(o["scenario_time"]["event_at"]["value"])]
        ok = bool(srcs) and norm(eml_parts((bundle / srcs[0]["file"]).read_bytes())[1]) == norm(quoted)
        R.add("D6", f"{o['staging_card_ref']}: forwarded quotation equals the quoted original body exactly", ok,
              "" if srcs else "quoted original not in package", layer=L)

    # privacy / leakage (scoped)
    scan = dict(texts)
    for p in man["previews"]:
        scan[p["preview_file"]] = re.sub(r"<style>.*?</style>", "", (bundle / p["preview_file"]).read_text(), flags=re.S)
    for rel, t in scan.items():
        hits = [pat for pat in FORBIDDEN_TEXT if re.search(pat, t, re.I)]
        R.add("E1", f"no control fields, planning IDs or generator artefacts: {rel}", not hits, str(hits), layer=L)
    for rel in actual:
        R.add("E2", f"filename carries no planning IDs: {rel}", not re.search(r"AST-|SRC-|THR-|ORIG-|11-D\d\d|gold|incident", Path(rel).name), layer=L)
    bad = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if path == "status":
                    continue
                if FORBIDDEN_MANIFEST_KEYS.match(k):
                    bad.append(path + "/" + k)
                walk(v, path + "/" + k if path else k)
        elif isinstance(o, list):
            for x in o:
                walk(x, path)
    walk(man)
    R.add("E3", "manifest carries no control-field keys (outside the status block)", not bad, str(bad), layer=L)
    R.add("E5", "no restricted material inside intake-bundle", not [r for r in actual if re.search(r"conversation|fidelity|private", r)], layer=L)
    conv_dir = restricted / "private-controls/conversations"
    if conv_dir.exists():
        lines = [t["text"] for f in conv_dir.glob("*.json") for t in json.loads(f.read_text())["turns"] if len(t["text"]) >= 50]
        blob = " ".join(norm(t) for t in texts.values())
        R.add("E4", "no private-conversation line appears verbatim in the intake bundle", not any(norm(l) in blob for l in lines), layer=L)
    else:
        R.unavailable("E4", "private-conversation leakage scan", "restricted conversations not present", L)
    for o in originals:
        if o.get("kind") == "call_summary":
            t = texts[o["file"]]
            head, summ = summary_parts(t)
            R.add("F1", f"{o['staging_card_ref']}: summary length 60-180 words", 60 <= len(summ.split()) <= 180, f"{len(summ.split())} words", layer=L)
            R.add("F2", f"{o['staging_card_ref']}: no transcript-style speaker lines", not re.search(r"^[A-Z][a-z]+( [A-Z][a-z]+)?:\s", summ, re.M), layer=L)
            R.add("F3", f"{o['staging_card_ref']}: header metadata present", all(k in head for k in ("Call date", "Call start", "Participants")), layer=L)
    return texts, actual, sums


# ============================ provenance ============================
def _pins_from_record(rec):
    pins = {}
    for k in ("authored_used",):
        for c, i in rec.get(k, {}).items():
            pins[c] = {"path": i["path"], "sha256": i["sha256"]}
    for c, i in rec.get("authored_inherited", {}).items():
        if i.get("role") == "forward_quote_dependency":
            pins.setdefault(c, {"path": i["path"], "sha256": i["sha256"]})
    return pins


def _provenance(R, pkg, man, rec, plan, base_root):
    L = "provenance"
    if rec is None:
        R.unavailable("P1", "build record present", "restricted/private-controls/build-record.json not available", L)
        return {"plan_ok": False, "pins": None, "inputs_ok": False}
    R.add("P1", "build record present", True, layer=L)
    inputs_ok = True
    for section in ("authored_used", "authored_inherited"):
        for card, i in rec.get(section, {}).items():
            p = TOOL_ROOT / i["path"]
            if not p.exists():
                R.unavailable("P2", f"recorded authored input {card} ({i['path']})", "file missing", L)
                inputs_ok = False
            elif sha256(p.read_bytes()) != i["sha256"]:
                R.unavailable("P2", f"recorded authored input {card} ({i['path']})", "file differs from recorded sha256; cannot be used to reconstruct", L)
                inputs_ok = False
            else:
                R.add("P2", f"recorded authored input {card} ({i['path']}) revision {i['revision']} unchanged", True, layer=L)
    rec_plan = rec.get("plan_pack_sha256") or man["provenance"]["plan_pack_sha256"]
    plan_ok = plan is not None and plan.sha256 == rec_plan
    if plan is None:
        R.unavailable("P3", "plan digest", "plan pack not available", L)
    elif plan_ok:
        R.add("P3", "current plan pack digest equals the recorded digest", True, layer=L)
    else:
        R.unavailable("P3", "plan digest", f"plan pack changed (recorded {rec_plan[:12]}..., now {plan.sha256[:12]}...); plan-bound checks skipped", L)
    R.add("P5", "manifest and build record agree on plan digest", man["provenance"]["plan_pack_sha256"] == rec_plan, layer=L)
    from . import RENDERER_VERSION, TOOL_VERSION
    R.add("P4", "renderer/tool provenance equals recorded", rec.get("renderer_version") == RENDERER_VERSION and rec.get("tool_version") == TOOL_VERSION,
          f"recorded {rec.get('tool_version')}/{rec.get('renderer_version')}, current {TOOL_VERSION}/{RENDERER_VERSION}", severity="warn", layer=L)
    sup = man["batch"].get("supersedes_package")
    if sup:
        base = base_root / sup["package"]
        if not (base / "intake-bundle/delivery-manifest.json").exists():
            R.unavailable("P6", "base package lineage", f"{sup['package']} not available beside this package", L)
        else:
            bm = base / "intake-bundle/delivery-manifest.json"
            R.add("P6", "base manifest digest equals the recorded digest", sha256(bm.read_bytes()) == sup["manifest_sha256"], layer=L)
            bman = json.loads(bm.read_text())
            status = json.loads((base / "restricted/review/REVIEW-STATUS.json").read_text())
            by_id = {o["original_id"]: o for o in man["originals"]}
            for c in rec.get("carried_forward", []):
                bo = next(x for x in bman["originals"] if x["original_id"] == c["original_id"])
                same = (base / "intake-bundle" / bo["file"]).read_bytes() == (pkg / "intake-bundle" / by_id[c["original_id"]]["file"]).read_bytes()
                R.add("P6", f"carried original byte-identical to base: {bo['filename']}", same and sha256((pkg / "intake-bundle" / bo["file"]).read_bytes()) == bo["sha256"], layer=L)
                R.add("P6", f"carried original recorded as externally accepted in base: {bo['filename']}", c["original_id"] in status.get("accepted_records", []), layer=L)
            changed = [o for o in man["originals"] if o["lineage"]["relation"] == "revision" and "carried_forward" not in o["lineage"]]
            want = {c for c in rec.get("rerendered_cards", [])}
            R.add("P6", "changed originals are exactly the re-rendered cards", {o["staging_card_ref"] for o in changed} == want, str(sorted(o["staging_card_ref"] for o in changed)), layer=L)
            for o in changed:
                bo = next(x for x in bman["originals"] if x["original_id"] == o["lineage"]["supersedes_original_id"])
                R.add("P6", f"changed original has a distinct identity and bytes: {o['filename']}",
                      o["original_id"] != bo["original_id"] and o["sha256"] != bo["sha256"] and o["version"] > bo["version"] and o["staging_card_ref"] == bo["staging_card_ref"], layer=L)
    for rl in rec.get("supplemental_rulings", []):
        p = pkg / "restricted/review/supplemental-rulings" / rl["path"]
        R.add("P7", f"supplemental ruling recorded and present: {rl['path']}", p.exists() and sha256(p.read_bytes()) == rl["sha256"], layer=L)
    return {"plan_ok": plan_ok, "pins": _pins_from_record(rec), "inputs_ok": inputs_ok}


# ============================ semantic ============================
def _semantic(R, pkg, man, rec, plan, texts, actual):
    L = "semantic"
    bundle, restricted = pkg / "intake-bundle", pkg / "restricted"
    originals = man["originals"]
    spec = BATCH[man["batch"]["batch_id"]]
    held_assets = [o for o in originals if o["kind"] == "attachment_original"]

    def authored_for(card):
        i = rec.get("authored_used", {}).get(card) or rec.get("authored_inherited", {}).get(card)
        return load_pinned({"path": i["path"], "sha256": i["sha256"]})[0]

    for o in held_assets:
        a = plan.assets_by_name[o["filename"]]
        d = (bundle / o["file"]).read_bytes()
        s = a["document_spec"]
        name = o["filename"]
        if a["media_type"] == "application/pdf":
            txt = pdf_text(d)
            for k, v in (s.get("fields") or {}).items():
                R.add("C1", f"{name}: locked field {k}", value_present(txt, v), str(v), layer=L)
            for row in s.get("rows") or []:
                for k, v in row.items():
                    R.add("C1", f"{name}: locked row value {k}", value_present(txt, v), str(v), layer=L)
        elif a["media_type"] == "text/csv":
            import csv
            rows = list(csv.DictReader(io.StringIO(d.decode())))
            R.add("C1", f"{name}: row count", len(rows) == len(s["rows"]), layer=L)
            for got, w in zip(rows, s["rows"]):
                for k, v in w.items():
                    R.add("C1", f"{name}: row {w.get('item')} {k} exact", got.get(k, "") == (v or ""), layer=L)
            for k, v in s["fields"].items():
                R.add("C1", f"{name}: field {k} exact in every row", all(r.get(k) == str(v) for r in rows), layer=L)
            R.add("C1", f"{name}: no absent value filled", all(r["historic_validity_decision"] == "" for r in rows if r["item"] != "DC-11"), layer=L)
        elif a["media_type"] == "image/jpeg":
            log = (rec.get("render_log") or {}).get(name)
            if log:
                drawn = " | ".join(log["drawn_text"])
                for r in s["rows"]:
                    R.add("C1", f"{name}: file row {r['file']}", r["file"] in drawn and r["receipt_state"].capitalize() in drawn, layer=L)
                R.add("C1", f"{name}: upload reference drawn", s["fields"]["upload_reference"] in drawn, layer=L)
                R.add("C1", f"{name}: account number drawn", s["fields"]["member_account_number"] in drawn, layer=L)
                R.add("C1", f"{name}: no validity/acceptance wording drawn", not re.search(r"accept|valid|approv", drawn, re.I), layer=L)
            else:
                R.unavailable("C1", f"{name}: drawn-text check", "render log not recorded in this package (carried original); verified by the package that rendered it", L)
            ex = Image.open(io.BytesIO(d)).getexif()
            R.add("C1", f"{name}: capture date metadata equals captured date", ex.get(0x0132, "").replace(":", "-", 2).startswith(s["fields"]["captured_date"]), layer=L)

    card_text = {}
    for o in originals:
        if o.get("kind") in ("email", "call_summary", "letter"):
            card_text.setdefault(o["staging_card_ref"], "")
            card_text[o["staging_card_ref"]] += "\n" + texts.get(o["file"], "")
    for c in spec["cards"]:
        art = plan.artifact(c)
        txt = card_text[c]
        authored = authored_for(c)
        for tok in authored.get("required_tokens", []):
            R.add("C2", f"{c}: required token present: {tok}", tok.lower() in norm(txt) or tok in txt, layer=L)
        for pat in authored.get("must_not_match", []):
            R.add("G1", f"{c}: forbidden claim absent: {pat}", not re.search(pat, txt, re.I), layer=L)
        ref = plan.assertion(c, "request_reference")
        if ref:
            R.add("C3", f"{c}: request reference present", ref in txt, layer=L)
        em = next((o for o in originals if o.get("kind") == "email" and o["staging_card_ref"] == c), None)
        if em:
            expected = [plan.assets[a]["filename"] for a in art["attachments"] if plan.assets[a]["delivery_state"] == "held"] + \
                       [x["filename"] for x in originals if x.get("kind") == "letter" and x.get("parent_message_file") == em["file"]]
            got = [a[0] for a in eml_parts((bundle / em["file"]).read_bytes())[2]]
            R.add("B1", f"{c} attachment names match plan ({len(expected)} expected)", sorted(got) == sorted(expected), f"got={got}", layer=L)
            ids = [i for i in (plan.assertion(c, "attached_asset_ids") or []) if plan.assets[i]["delivery_state"] == "held"]
            letter_names = [x["filename"] for x in originals if x.get("kind") == "letter" and x.get("parent_message_file") == em["file"]]
            R.add("B4", f"{c} attached_asset_ids (held) match embedded attachments",
                  sorted(plan.assets[i]["filename"] for i in ids) == sorted(n for n in got if n not in letter_names), layer=L)
            want_to = {plan.actor(r)["email"] for r in art["recipients"]}
            msg = eml_parts((bundle / em["file"]).read_bytes())[0]
            R.add("D1", f"{c}: recipients/sender/subject equal plan", want_to == {a.addr_spec for a in msg["To"].addresses}
                  and msg["From"].addresses[0].addr_spec == plan.actor(art["author"])["email"]
                  and " ".join(str(msg["Subject"]).split()) == art["subject"], layer=L)

    def tokens(s):
        out = set()
        for pat in ID_FAMILIES:
            out |= set(re.findall(pat, s))
        return out

    def plan_tokens(arts, assets):
        return tokens(json.dumps([arts, assets], default=str))

    for c in spec["cards"]:
        art = plan.artifact(c)
        ev = parse_dt(art["event_at"])
        earlier = [a for a in plan.artifacts.values() if parse_dt(a["event_at"]) <= ev]
        later = [a for a in plan.artifacts.values() if parse_dt(a["event_at"]) > ev]
        own_assets = [plan.assets[i] for i in art["attachments"]]
        prior_assets = [a for a in plan.assets.values() if a.get("available_at") and parse_dt(a["available_at"]) <= ev]
        allowed = plan_tokens(earlier, prior_assets + own_assets)
        later_tok = plan_tokens(later, [a for a in plan.assets.values() if a not in prior_assets + own_assets]) - allowed
        leaked = {t for t in later_tok if t in card_text[c]}
        R.add("D3", f"{c}: no identifiers from later records", not leaked, str(sorted(leaked)), layer=L)
        lf = [a["filename"] for a in plan.assets.values() if a not in prior_assets + own_assets and a["filename"] in card_text[c]]
        R.add("D3", f"{c}: no later-asset filenames", not lf, str(lf), layer=L)
        due = {date.fromisoformat(sa["value"]["due_expression"]) for a in earlier for sa in a["source_assertions"]
               if sa["key"] == "promise" and sa["value"].get("due_precision") == "date"}
        bad = [m.group(0) for m in DATE_RE.finditer(card_text[c]) if (resolve_date(m, ev) or ev.date()) > ev.date() and resolve_date(m, ev) not in due]
        R.add("D4", f"{c}: no mention of a later date unless a known prospective due date", not bad, str(bad), layer=L)

    # call-summary fidelity (restricted material)
    for o in originals:
        if o.get("kind") != "call_summary":
            continue
        c = o["staging_card_ref"]
        t = texts[o["file"]]
        head, summ = summary_parts(t)
        conv_f = restricted / f"private-controls/conversations/{c}-conversation.json"
        fid_f = restricted / f"private-controls/fidelity/{c}-summary-fidelity.json"
        if not (conv_f.exists() and fid_f.exists()):
            R.unavailable("F4", f"{c}: summary fidelity", "restricted conversation/fidelity material not present", L)
            continue
        turns = {x["n"]: x["text"] for x in json.loads(conv_f.read_text())["turns"]}
        fid = json.loads(fid_f.read_text())
        conv_all = norm(" ".join(turns.values()))
        for cl in fid["claims"]:
            src = cl.get("source", "spoken")
            if src == "spoken":
                cited = " ".join(turns.get(n, "") for n in cl["turns"])
                ok = bool(cl["turns"]) and all(n in turns for n in cl["turns"]) and all(norm(s) in norm(cited) for s in cl["check_strings"])
                R.add("F4", f"{c}: spoken claim traced to cited turns: {cl['claim'][:60]}", ok, layer=L)
            else:
                R.add("F4", f"{c}: metadata claim sits in the summary header and is not cited as spoken: {cl['claim'][:50]}",
                      not cl.get("turns") and all(norm(s) in norm(head) for s in cl["check_strings"]), layer=L)
        for rq in fid.get("required_content", []):
            R.add("F6", f"{c}: consequential content preserved: {rq['id']}",
                  all(re.search(p, summ, re.I) for p in rq["must_match"]) and not any(re.search(p, summ, re.I) for p in rq.get("must_not_match", [])),
                  rq["detail"], layer=L)
            R.add("F6", f"{c}: required content cites real turns: {rq['id']}", all(n in turns for n in rq["turns"]), layer=L)
            if rq.get("turn_unfinished"):
                t = turns.get(rq["turn_unfinished"], "")
                R.add("F6", f"{c}: turn {rq['turn_unfinished']} is genuinely cut off (supports '{rq['id']}')", bool(t) and t.rstrip()[-1] not in ".?!", layer=L)
        for m in set(SPOKEN_ID_RE.findall(summ)):
            R.add("F8", f"{c}: identifier in summary prose was actually spoken: {m}", norm(m) in conv_all,
                  "case reference and other header metadata belong in the header, not narrated as spoken", layer=L)
        for tok in set(re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", summ)):
            if tok in ("Case Summary", "Death Benefits", "Beth Martin", "Alicia Romero"):
                continue
            R.add("F5", f"{c}: summary term traceable: {tok}", norm(tok) in conv_all or tok in head, severity="warn", layer=L)


# ============================ reconstruction ============================
def _reconstruct(R, pkg, man, rec, plan_ok, pins, sums, base_root):
    L = "reconstruction"
    if rec is None or not plan_ok or not pins:
        R.unavailable("I1", "rebuild from recorded inputs", "recorded provenance or plan not available", L)
        return
    try:
        for p in pins.values():
            load_pinned(p)
        batch_key = man["batch"]["batch_id"]
        pv = man["batch"]["package_version"]
        sup = man["batch"].get("supersedes_package")
        base_dir = base_root / sup["package"] if sup else None
        with tempfile.TemporaryDirectory() as td:
            b = Batch(batch_key, rec.get("authored_version", 1), pv, td, pins=pins, base_dir=base_dir)
            b.render_assets()
            b.render_cards()
            diff = [rel for rel, d in b.files.items() if sums.get(rel) != sha256(d)]
            missing = [rel for rel in sums if rel not in b.files and rel != "delivery-manifest.json"]
    except InputUnavailable as e:
        R.unavailable("I1", "rebuild from recorded inputs", str(e), L)
        return
    from . import RENDERER_VERSION
    note = "" if rec.get("renderer_version") == RENDERER_VERSION else f" (recorded renderer {rec.get('renderer_version')}, current {RENDERER_VERSION})"
    R.add("I1", "rebuild from the exact recorded inputs reproduces identical bytes for every bundle file" + note, not diff and not missing,
          f"differs={diff} missing={missing}", layer=L)


# ============================ entry point ============================
def run_checks(package_dir, write=False, deep=True, output=None, base_root=None, label=None):
    pkg = Path(package_dir)
    base_root = Path(base_root) if base_root else pkg.parent
    R = Results()
    man = json.loads((pkg / "intake-bundle/delivery-manifest.json").read_text())
    rec_path = pkg / "restricted/private-controls/build-record.json"
    rec = json.loads(rec_path.read_text()) if rec_path.exists() else None
    try:
        plan = CasePlan(BATCH[man["batch"]["batch_id"]]["case_number"])
    except FileNotFoundError:
        plan = None
    texts, actual, sums = _integrity(R, pkg, man)
    prov = _provenance(R, pkg, man, rec, plan, base_root)
    if prov["plan_ok"] and prov["inputs_ok"]:
        _semantic(R, pkg, man, rec, plan, texts, actual)
    else:
        R.unavailable("C0", "plan-bound semantic checks", "plan digest or recorded authored inputs unavailable/changed; byte-integrity layer is unaffected", "semantic")
    if deep:
        _reconstruct(R, pkg, man, rec, prov["plan_ok"] and prov["inputs_ok"], prov["pins"], sums, base_root)
    res = {"package": label or pkg.name, "checked_path": None if label else str(pkg), "run_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "scope_note": "authoring-consistency and packaging-integrity checks by the generator; not independent review",
           "summary": R.summary(), "results": R.items}
    if write:
        out = pkg / "restricted/review/CHECK-RESULTS.json"
        if out.exists():
            raise ReviewRecordProtected(f"{out} already exists; refusing to overwrite. Use --output for a copy elsewhere.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2) + "\n")
    if output:
        op = Path(output).resolve()
        if pkg.resolve() in op.parents:
            raise ReviewRecordProtected("refusing to write check output inside a release package; choose a path outside it")
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(res, indent=2) + "\n")
    return res
