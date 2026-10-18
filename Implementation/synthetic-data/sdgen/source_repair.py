"""Deterministic source repairs for case 11 `remaining_cards` (rulings CL11-EVIDENCE-INDEX-01, CL11-D16-REALISM-01,
CL11-D23-SCOPE-01, CL11-INVOICE-DISPLAY-01), published as one immutable successor of `case-11-remaining-cards-v1`.

Nothing here writes prose or chooses a value. The Principal Data Engineer's rulings file (hash-bound by the exact-repair
authorisation) supplies every replacement; this module only:
  * derives the effective index asset (authoring column dropped, three ruled titles, rows/dates/fields/order unchanged) and the
    invoice's ruled amount presentation (stored 250000 AUD cents shown as A$2,500.00), refusing any disagreement with the frozen plan;
  * writes them, with the literal-repair spans and every binding, into a versioned repair pack (REPAIR-SPEC.json) that it re-derives
    and compares on every load, so the check, the renderer and the packager read one definition;
  * applies the two `replace_once` edits to in-memory copies of the submitted returns (the owned folder is never touched);
  * re-renders with the existing renderers, copies every other original byte for byte from v1, checks each changed span, attachment
    and identity, and publishes with the no-replace rename.
Every original keeps its event/recorded/availability times; only packaging time is new. Nothing is marked accepted."""
import copy
import email
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from email import policy
from pathlib import Path

from .atomic import rename_noreplace
from .build import ReleaseExistsError
from .paths import RELEASE_ROOT, ROOT

S = ROOT / "Data/synthesis"
REVIEW_DIR = ROOT / "Data/reviews/case-11-remaining-cards-v1-2026-10-15"
RULINGS = REVIEW_DIR / "SOURCE-REPAIR-RULINGS.json"
AUTHORISATION = REVIEW_DIR / "DETERMINISTIC-REPAIR-AUTHORISATION.json"
DECISIONS = REVIEW_DIR / "ORIGINAL-DECISIONS.json"
SIGNOFF = REVIEW_DIR / "ORIGINAL-SOURCE-SIGNOFF.json"
PACK_ROOT = S / "worker-packs-v5"
PACK_NAME = "11-C-e1da28538652e263887a"
PACK = PACK_ROOT / PACK_NAME
OWNED = S / "worker-outputs-v5" / PACK_NAME / "remaining_cards"
BASE = RELEASE_ROOT / "case-11-remaining-cards-v1"
EXPORT = S / "receiving-exports/case-11-accepted-slice-v1"
EXPORT_MANIFEST_SHA256 = "cd2be75478bb00d6fbd304d77136a8d501995a69ae222f93a8b0bacf176a3f91"
REPAIR_ROOT = S / "source-repair-packs"
REPAIR_NAME = "case-11-remaining-cards-r1"
STAGE = "remaining_cards"
INDEX_BRANCH_LABELS = ("Administration or specialist reply", "Follow-up call or disputed explanation",
                       "Interim or final IDR position before external referral")


class RepairRefused(RuntimeError):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rel_root(p):
    return str(Path("Artha") / Path(p).resolve().relative_to(ROOT.resolve()))


# --------------------------------------------------------------------------- money
def format_aud(minor):
    """Exact presentation of integer AUD cents: 250000 -> 'A$2,500.00'."""
    if not isinstance(minor, int) or isinstance(minor, bool):
        raise RepairRefused(f"amount {minor!r} is not integer minor units")
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(minor), 100)
    return f"{sign}A${major:,}.{cents:02d}"


AUD_RE = re.compile(r"^(-?)A\$(\d{1,3}(?:,\d{3})*)\.(\d{2})$")


def parse_aud(text):
    """Inverse of format_aud; None unless `text` is exactly one well-formed A$ amount."""
    m = AUD_RE.match(text)
    if not m:
        return None
    v = int(m.group(2).replace(",", "")) * 100 + int(m.group(3))
    return -v if m.group(1) else v


# --------------------------------------------------------------------------- rulings and effective assets
def load_rulings(rulings_path=RULINGS, authorisation_path=AUTHORISATION):
    """(authorisation, rulings, bindings) after checking the authorisation binds these exact rulings and grants no worker/prose."""
    a_raw, r_raw = Path(authorisation_path).read_bytes(), Path(rulings_path).read_bytes()
    auth, rul = json.loads(a_raw), json.loads(r_raw)
    if auth.get("decision") != "approved_for_exact_mechanical_repairs_only":
        raise RepairRefused("the authorisation does not approve exact mechanical repairs")
    if auth.get("rulings_sha256") != sha(r_raw):
        raise RepairRefused("the rulings file is not the one the authorisation binds")
    if auth.get("maximum_new_authoring_workers") != 0 or auth.get("free_prose_generation_authorised") is not False:
        raise RepairRefused("the authorisation must exclude authoring workers and free prose")
    if rul.get("new_authoring_workers_authorised") != 0 or rul.get("runtime_input") is not False:
        raise RepairRefused("the rulings must exclude authoring workers and runtime use")
    if rul.get("bound_package_manifest_sha256") != auth.get("base_manifest_sha256"):
        raise RepairRefused("rulings and authorisation bind different base packages")
    bindings = {"authorisation": {"path": rel_root(authorisation_path), "sha256": sha(a_raw), "reviewer": auth.get("reviewer"),
                                  "decided_at": auth.get("decided_at")},
                "rulings": {"path": rel_root(rulings_path), "sha256": sha(r_raw)},
                "base_package_manifest_sha256": auth["base_manifest_sha256"], "base_pack_digest": auth["base_pack_digest"],
                "base_submitted_output_digest": auth["base_submitted_output_digest"], "bound_plan_sha256": rul["bound_plan_sha256"],
                "changed_originals": list(auth["changed_originals"])}
    return auth, rul, bindings


def index_effective(asset, ruling):
    """Effective evidence-index asset under CL11-EVIDENCE-INDEX-01. Raises RepairRefused on any disagreement."""
    r = ruling
    for k in ("asset_id", "filename", "format", "media_type", "created_at", "available_at"):
        if r.get(k) != asset.get(k):
            raise RepairRefused(f"{r['id']}: {k} {r.get(k)!r} differs from the planned {asset.get(k)!r}")
    spec = asset["document_spec"]
    if r["fields"] != spec.get("fields"):
        raise RepairRefused(f"{r['id']}: header fields differ from the planned asset")
    drop = list(r["drop_runtime_columns"])
    rows = spec["rows"]
    cross = r["private_original_row_to_card_crosswalk"]
    if {str(i): row.get("case_file_item") for i, row in enumerate(rows)} != cross:
        raise RepairRefused(f"{r['id']}: the private row-to-card crosswalk differs from the planned case_file_item column")
    titles = r["private_title_replacements"]
    if not set(titles) <= set(cross.values()):
        raise RepairRefused(f"{r['id']}: a title replacement names a card that is not an index row")
    eff_rows, changes = [], []
    for i, row in enumerate(rows):
        new = {k: v for k, v in row.items() if k not in drop}
        card = row.get("case_file_item")
        if card in titles:
            changes.append({"row": i, "date": row["date"], "from": row["title"], "to": titles[card]})
            new["title"] = titles[card]
        eff_rows.append(new)
    if eff_rows != r["expected_rows"] or len(eff_rows) != r["expected_row_count"]:
        raise RepairRefused(f"{r['id']}: the projected rows differ from the ruling's expected rows (values, columns or order)")
    if sorted(c["from"] for c in changes) != sorted(INDEX_BRANCH_LABELS):
        raise RepairRefused(f"{r['id']}: the ruled title changes are not exactly the three planning branch labels")
    if list(r["fields"]) + sorted({k for row in eff_rows for k in row}, key=["date", "title"].index) != r["native_columns"]:
        raise RepairRefused(f"{r['id']}: runtime columns differ from the ruling's native columns")
    roles = {p: v for p, v in (spec.get("field_temporal_roles") or {}).items() if not any(p.endswith("/" + c) for c in drop)}
    eff = copy.deepcopy(asset)
    eff["document_spec"]["rows"] = eff_rows
    eff["document_spec"]["field_temporal_roles"] = roles
    eff["effective_spec"] = {"ruling": r["id"], "planned_asset_sha256": sha(_canon(asset).encode()), "dropped_runtime_columns": drop,
                             "title_changes": changes, "row_count": len(eff_rows), "work_log": False,
                             "note": "non-work index: no work_id, seconds or work-log arithmetic"}
    return eff


def invoice_effective(asset, ruling, d28_csv=None):
    """Effective invoice asset under CL11-INVOICE-DISPLAY-01: stored fields unchanged, the amount line presented in currency units."""
    r = ruling
    if r.get("asset_id") != asset["asset_id"] or r.get("filename") != asset["filename"]:
        raise RepairRefused(f"{r['id']}: names a different asset")
    f = asset["document_spec"]["fields"]
    if f.get("amount_minor") != r["authoritative_amount_minor"] or f.get("currency") != r["currency"] != "AUD":
        raise RepairRefused(f"{r['id']}: the planned stored amount/currency differ from the ruling")
    if format_aud(f["amount_minor"]) != r["display_value"] or parse_aud(r["display_value"]) != f["amount_minor"]:
        raise RepairRefused(f"{r['id']}: {r['display_value']!r} is not an exact presentation of {f['amount_minor']} cents")
    if d28_csv is not None:
        import csv
        import io
        inv = [x for x in csv.DictReader(io.StringIO(d28_csv.decode())) if x["obligation_id"] == f["invoice_number"] and x["state"] == "invoiced"]
        if len(inv) != 1 or int(inv[0]["amount_minor"]) != f["amount_minor"] or inv[0]["date"] != f["invoice_date"]:
            raise RepairRefused(f"{r['id']}: the invoice does not reconcile with exactly one invoiced row of the native fee ledger")
    eff = copy.deepcopy(asset)
    eff["document_spec"]["display_overrides"] = {"amount_minor": {"label": r["display_label"], "value": r["display_value"]}}
    eff["effective_spec"] = {"ruling": r["id"], "planned_asset_sha256": sha(_canon(asset).encode()),
                             "presentation": {"field": "amount_minor", "stored_minor": f["amount_minor"], "currency": f["currency"],
                                              "label": r["display_label"], "value": r["display_value"]},
                             "note": "presentation only; every stored field, identity and date unchanged; no tax, due date or payment field added"}
    return eff


def literal_repairs(rul):
    reps = rul["literal_prose_repairs"]
    for x in reps:
        if x["operation"] != "replace_once" or not x["old"]:
            raise RepairRefused(f"{x['id']}: only replace_once of a non-empty span is supported")
    return reps


def apply_literal(authored, rep):
    """In-memory copy of a return with exactly one occurrence of rep['old'] replaced. Returns (copy, JSON pointer)."""
    hits = []

    def walk(o, p):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{p}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{p}/{i}")
        elif isinstance(o, str) and rep["old"] in o:
            hits.append((p, o.count(rep["old"])))
    walk(authored, "")
    if len(hits) != 1 or hits[0][1] != 1:
        raise RepairRefused(f"{rep['id']}: the span occurs {sum(n for _, n in hits)} times in the return, not exactly once")
    ptr = hits[0][0]
    out = copy.deepcopy(authored)
    node = out
    parts = ptr.strip("/").split("/")
    for k in parts[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    last = int(parts[-1]) if isinstance(node, list) else parts[-1]
    node[last] = node[last].replace(rep["old"], rep["new"], 1)
    return out, ptr


def repair_spec(plan_pack, plan_sha, rul, bindings, returns, d28_csv):
    """The REPAIR-SPEC.json body. Re-derived on every load; the file must equal it exactly."""
    assets = {a["asset_id"]: a for a in plan_pack["assets"]}
    idx = index_effective(assets[rul["index_projection"]["asset_id"]], rul["index_projection"])
    inv = invoice_effective(assets[rul["invoice_display"]["asset_id"]], rul["invoice_display"], d28_csv)
    lits = []
    for rep in literal_repairs(rul):
        raw = returns[rep["card"]]
        _, ptr = apply_literal(json.loads(raw), rep)
        lits.append({"ruling": rep["id"], "card": rep["card"], "return_file_sha256": sha(raw), "json_pointer": ptr,
                     "old": rep["old"], "new": rep["new"]})
    return {"kind": "source_repair_specification", "version": 1, "repair_pack": REPAIR_NAME, "case": 11, "stage": STAGE,
            "bound_plan_sha256": plan_sha, "bindings": bindings,
            "note": ("Versioned successor repair definition. Derived from the frozen plan and the Principal Data Engineer's rulings; the frozen plan, "
                     "pack set 5 and the submitted returns are unchanged. The renderer and packager re-derive this and refuse if it differs. "
                     "Review aid; never a runtime input or evaluation gold."),
            "effective_assets": [{"asset_id": a["asset_id"], "filename": a["filename"], "format": a["format"], "media_type": a["media_type"],
                                  "created_at": a["created_at"], "available_at": a["available_at"], "fields": a["document_spec"]["fields"],
                                  "rows": a["document_spec"]["rows"], "field_temporal_roles": a["document_spec"]["field_temporal_roles"],
                                  "display_overrides": a["document_spec"].get("display_overrides"), "provenance": a["effective_spec"]}
                                 for a in (idx, inv)],
            "literal_repairs": lits,
            "re_embedded": {"11-D23": [idx["filename"]], "11-D26": [inv["filename"]]},
            "unchanged_by_ruling": rul["other_question_rulings"]}


def _returns():
    return {f.stem: f.read_bytes() for f in sorted(OWNED.glob("11-D*.json"))}


def _check_bases(bindings):
    from . import progress, workpack2
    if workpack2.pack_digest(PACK)[0] != bindings["base_pack_digest"]:
        raise RepairRefused("pack set 5 differs from the digest the authorisation binds")
    if progress.tree_digest(OWNED) != bindings["base_submitted_output_digest"]:
        raise RepairRefused("the submitted returns differ from the output digest the authorisation binds")
    if sha((BASE / "intake-bundle/delivery-manifest.json").read_bytes()) != bindings["base_package_manifest_sha256"]:
        raise RepairRefused("the v1 package manifest differs from the one the authorisation binds")


def write_repair_pack(root=REPAIR_ROOT, name=REPAIR_NAME):
    """Create the versioned repair pack (no-replace). Returns (dir, digest)."""
    from .authoring_check import load_pack
    auth, rul, bindings = load_rulings()
    _check_bases(bindings)
    plan = load_pack(PACK)[0]
    if plan.sha256 != rul["bound_plan_sha256"]:
        raise RepairRefused("the pack's frozen plan is not the plan the rulings bind")
    d28 = (BASE / "intake-bundle/originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv").read_bytes()
    body = repair_spec(json.loads(plan.path.read_bytes()), plan.sha256, rul, bindings, _returns(), d28)
    target = Path(root) / name
    stage = Path(root) / ".staging" / f"{name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    stage.mkdir(parents=True)
    raw = (json.dumps(body, indent=1, ensure_ascii=False) + "\n").encode()
    (stage / "REPAIR-SPEC.json").write_bytes(raw)
    digest = sha(json.dumps({"REPAIR-SPEC.json": sha(raw)}, sort_keys=True).encode())
    (stage / "REPAIR-PACK-MANIFEST.json").write_text(json.dumps({"repair_pack": name, "files": {"REPAIR-SPEC.json": sha(raw)},
                                                                 "repair_pack_digest": digest, "authoring_workers": 0,
                                                                 "stage_permission_needed": False,
                                                                 "note": "Mechanical repairs under the exact-repair authorisation; no worker and no new prose."},
                                                                indent=1) + "\n")
    try:
        rename_noreplace(stage, target)
    except FileExistsError as e:
        raise ReleaseExistsError(f"{target} already exists; repair packs are immutable") from e
    return target, digest


def load_repair(root=REPAIR_ROOT, name=REPAIR_NAME):
    """(plan with effective assets applied, repair spec, rulings, bindings, repair pack digest). Refuses on any drift."""
    from .authoring_check import load_pack
    auth, rul, bindings = load_rulings()
    _check_bases(bindings)
    pdir = Path(root) / name
    man = json.loads((pdir / "REPAIR-PACK-MANIFEST.json").read_text())
    raw = (pdir / "REPAIR-SPEC.json").read_bytes()
    if man["files"] != {"REPAIR-SPEC.json": sha(raw)} or man["repair_pack_digest"] != sha(json.dumps(man["files"], sort_keys=True).encode()):
        raise RepairRefused("the repair pack manifest does not match its files")
    plan = load_pack(PACK)[0]
    d28 = (BASE / "intake-bundle/originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv").read_bytes()
    spec = repair_spec(json.loads(plan.path.read_bytes()), plan.sha256, rul, bindings, _returns(), d28)
    if json.loads(raw) != spec:
        raise RepairRefused("REPAIR-SPEC.json differs from the projection of the frozen plan and the rulings")
    eff = {a["asset_id"]: a for a in (index_effective(plan.assets[rul["index_projection"]["asset_id"]], rul["index_projection"]),
                                      invoice_effective(plan.assets[rul["invoice_display"]["asset_id"]], rul["invoice_display"], d28))}
    plan.pack = copy.deepcopy(plan.pack)
    plan.pack["assets"] = [eff.get(a["asset_id"], a) for a in plan.pack["assets"]]
    plan.assets = {a["asset_id"]: a for a in plan.pack["assets"]}
    plan.assets_by_name = {a["filename"]: a for a in plan.pack["assets"]}
    return plan, spec, rul, bindings, man["repair_pack_digest"]


# --------------------------------------------------------------------------- checks of the changed files
def _msg(data):
    return email.message_from_bytes(data, policy=policy.default)


def _parts(msg):
    """(plain, html, [(filename, content_type, bytes)]) of a rendered message."""
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    atts = [(p.get_filename(), p.get_content_type(), p.get_payload(decode=True)) for p in msg.iter_attachments()]
    return plain.get_content() if plain else None, html.get_content() if html else None, atts


def _headers(msg):
    return [(k, str(v)) for k, v in msg.items()]


def message_diff(old, new, span=None, attachment_changes=None):
    """Check that `new` differs from `old` only by the literal span (in both the text and HTML bodies) and the named attachments.
    Returns a list of failure strings (empty when the change is exactly as authorised)."""
    fails = []
    mo, mn = _msg(old), _msg(new)
    if _headers(mo) != _headers(mn):
        fails.append("message headers differ")
    po, ho, ao = _parts(mo)
    pn, hn, an = _parts(mn)
    for label, a, b in (("text body", po, pn), ("HTML body", ho, hn)):
        if span:
            from html import escape
            o, n = (span["old"], span["new"]) if label == "text body" else (escape(span["old"], quote=False), escape(span["new"], quote=False))
            if a is None or a.count(o) != 1 or a.replace(o, n, 1) != b:
                fails.append(f"{label}: change is not exactly the authorised span")
        elif a != b:
            fails.append(f"{label} changed")
    attachment_changes = attachment_changes or {}
    if [x[:2] for x in ao] != [x[:2] for x in an]:
        fails.append("attachment names, types or order changed")
    for (fo, _, bo), (fn, _, bn) in zip(ao, an):
        if fo in attachment_changes:
            if bn != attachment_changes[fo]:
                fails.append(f"{fo}: embedded bytes are not the repaired original")
            if bn == bo:
                fails.append(f"{fo}: embedded bytes did not change")
        elif bo != bn:
            fails.append(f"{fo}: embedded bytes changed without authorisation")
    return fails


def index_cell_checks(data, ruling):
    """Fails for the saved index workbook: exact stored header/rows, no card IDs, no planning branch labels."""
    from .leakguard import card_id_hits, xlsx_cells
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    vals = [list(r) for ws in wb.worksheets for r in ws.iter_rows(values_only=True)]
    wb.close()
    fails = []
    cols = ruling["native_columns"]
    if vals[0] != cols:
        fails.append(f"header {vals[0]} differs from {cols}")
    want = [[*ruling["fields"].values(), r["date"], r["title"]] for r in ruling["expected_rows"]]
    if vals[1:] != want:
        fails.append("stored rows differ from the ruled rows")
    if card_id_hits("index.xlsx", data):
        fails.append("stored cells carry private authoring-card IDs")
    labels = [v for _, v in xlsx_cells(data) if v in INDEX_BRANCH_LABELS]
    if labels:
        fails.append(f"planning branch labels remain: {labels}")
    return fails


def invoice_text_checks(data, ruling, asset):
    """Fails for the rendered invoice: the A$ amount is shown and is exactly the stored cents; no storage-unit field; facts preserved."""
    from .leakguard import pdf_text
    text = "\n".join(t for w, t in pdf_text(data) if w.startswith("page"))
    lines = [ln.strip() for ln in text.splitlines()]
    fails = []
    shown = [lines[i + 1] for i, ln in enumerate(lines[:-1]) if ln == ruling["display_label"]]
    if shown != [ruling["display_value"]]:
        fails.append(f"amount line {shown} is not exactly one {ruling['display_label']!r} = {ruling['display_value']!r}")
    if [parse_aud(v) for v in shown] != [asset["document_spec"]["fields"]["amount_minor"]]:
        fails.append("displayed amount is not currency-equivalent to the stored integer cents")
    if "Amount minor" in text or str(ruling["authoritative_amount_minor"]) in text:
        fails.append("the storage-unit field is still displayed")
    f = asset["document_spec"]["fields"]
    for k in ("invoice_number", "matter_id", "billed_to", "member_account_number"):
        if f[k] not in text:
            fails.append(f"{k} {f[k]!r} missing")
    if "28 January 2026" not in text or "Case management" not in text or "AUD" not in text:
        fails.append("invoice date, charge or currency missing")
    for banned in ("GST", "Due date", "Pay by", "BSB", "ABN"):
        if banned.lower() in text.lower():
            fails.append(f"unruled field {banned!r} appears")
    return fails


# --------------------------------------------------------------------------- successor package
def build_successor(out_root=RELEASE_ROOT, name="case-11-remaining-cards-v2", now=None, repair_root=REPAIR_ROOT):
    """Render, check and publish (no-replace) the successor package. Returns (published dir, repair-check rows)."""
    from .authoring_check import author_check
    from .card_adapter import accepted_attachment_index, render_card
    from .microbatch import PackagingRefused, verify_package
    plan, spec, rul, bindings, rp_digest = load_repair(repair_root)
    base_intake = BASE / "intake-bundle"
    base_raw = (base_intake / "delivery-manifest.json").read_bytes()
    base = json.loads(base_raw)
    decisions = {d["original_id"]: d for d in json.loads(DECISIONS.read_text())["decisions"]}
    if json.loads(DECISIONS.read_text())["bound_manifest_sha256"] != sha(base_raw):
        raise RepairRefused("the per-original decisions are not bound to the v1 manifest")
    signoff_raw = SIGNOFF.read_bytes()
    so = json.loads(signoff_raw)
    signed = {o["original_id"]: o["sha256"] for o in so["originals"]}
    changed = set(bindings["changed_originals"])
    if {o for o, d in decisions.items() if d["decision"] == "repair_required"} != changed:
        raise RepairRefused("the repair-required originals differ from the authorisation's changed originals")
    accepted = accepted_attachment_index(EXPORT, EXPORT_MANIFEST_SHA256)
    acc_bytes = {n: v[1] for n, v in accepted.items()}
    returns = _returns()
    lit = {x["card"]: x for x in spec["literal_repairs"]}
    checks = []

    def ck(n, ok, d=""):
        checks.append({"name": n, "ok": bool(ok), "detail": d})

    old_files = {str(f.relative_to(base_intake)): f.read_bytes() for f in base_intake.rglob("*")
                 if f.is_file() and f.name not in ("delivery-manifest.json", "INTAKE-FILES.sha256")}
    files = dict(old_files)
    staging_private = {}
    rerender_same = []
    rows = {}
    for card in sorted(returns, key=lambda c: plan.artifact(c)["event_at"]):
        d = json.loads(returns[card])
        if card in lit:
            d, ptr = apply_literal(d, next(r for r in rul["literal_prose_repairs"] if r["card"] == card))
            if ptr != lit[card]["json_pointer"]:
                raise RepairRefused(f"{card}: span moved")
            staging_private[f"review/repaired-authored-overlays/{card}.json"] = (json.dumps(d, indent=2, ensure_ascii=False) + "\n").encode()
        rc = render_card(d, plan, plan.artifact(card), acc_bytes)
        for rel, data in rc.files.items():
            if rel not in old_files:
                raise RepairRefused(f"{card}: renders a new path {rel}")
            if data == old_files[rel]:
                rerender_same.append(rel)
            else:
                files[rel] = data
        for fname, _, data in rc.attachments:
            rel = f"originals/attachments/{fname}"
            if rel in old_files and data != old_files[rel]:
                files[rel] = data
        rows[card] = rc
    changed_paths = sorted(r for r in files if files[r] != old_files[r])
    by_id = {o["original_id"]: o for o in base["originals"]}
    want_changed = sorted(by_id[o]["file"] for o in changed)
    ck("exactly the five authorised originals change (re-rendering every other card reproduces its v1 bytes)",
       sorted(r for r in changed_paths if r.startswith("originals/")) == want_changed, str(changed_paths))
    ck("only the changed emails' previews change", all(r.startswith("originals/") or r.startswith("previews/") for r in changed_paths)
       and {r for r in changed_paths if r.startswith("previews/")} <= {"previews/" + Path(p).name[:-4] + ".preview.html" for p in want_changed if p.endswith(".eml")},
       str([r for r in changed_paths if r.startswith("previews/")]))
    # the five changed originals, one by one
    idx_rel = by_id["ORIG-C-e1da-AST-6c79aeba2042bfb60a"]["file"]
    inv_rel = by_id["ORIG-C-e1da-AST-1b54d6f51bbc9da7de"]["file"]
    f_idx = index_cell_checks(files[idx_rel], rul["index_projection"])
    ck("evidence index: saved cells are exactly the 19 ruled rows, no card IDs, no planning branch labels", not f_idx, str(f_idx))
    ck("evidence index: the delivered v1 workbook fails the same cell checks", bool(index_cell_checks(old_files[idx_rel], rul["index_projection"])))
    f_inv = invoice_text_checks(files[inv_rel], rul["invoice_display"], plan.assets[rul["invoice_display"]["asset_id"]])
    ck("invoice: shows Amount A$2,500.00, currency-equivalent to the stored 250000 cents, other facts preserved, nothing invented", not f_inv, str(f_inv))
    d16 = by_id["ORIG-C-e1da-remaining-cards-11-D16"]["file"]
    d23 = by_id["ORIG-C-e1da-remaining-cards-11-D23"]["file"]
    d26 = by_id["ORIG-C-e1da-remaining-cards-11-D26"]["file"]
    f16 = message_diff(old_files[d16], files[d16], span=lit["11-D16"])
    ck("D16: only the ruled sentence is deleted; headers and the accepted assessment attachment are unchanged", not f16, str(f16))
    f23 = message_diff(old_files[d23], files[d23], span=lit["11-D23"], attachment_changes={Path(idx_rel).name: files[idx_rel]})
    ck("D23: only the ruled description changes and only the index is re-embedded; the comparison PDF is byte-identical", not f23, str(f23))
    f26 = message_diff(old_files[d26], files[d26], attachment_changes={Path(inv_rel).name: files[inv_rel]})
    ck("D26: own text and headers unchanged; only the invoice attachment is replaced", not f26, str(f26))
    ck("native fee ledger D28 (integer cents) is byte-identical", files["originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv"]
       == old_files["originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv"])
    ck("D29 work export is byte-identical (22 rows, 19,920 s, reviewed)", sha(files["originals/ledgers/11-16-complaints-effort-export.xlsx"])
       == "a7e8c5586c3effdf31efec881824ec17a616ef7709e638e320d037315409be3f")
    ok_acc = all(sha(files[by_id[o]["file"]]) == h for o, h in signed.items())
    ck("all 16 source-accepted originals are byte-identical to their signed hashes", ok_acc and len(signed) == 16)
    inh = [o for o, d in decisions.items() if d["decision"] == "accepted_inherited"]
    ck("both inherited attachments are byte-identical to the accepted receiving export", len(inh) == 2 and all(
        files[by_id[o]["file"]] == acc_bytes[Path(by_id[o]["file"]).name] for o in inh))
    for card in ("11-D16", "11-D23", "11-D26"):
        tmp = Path(out_root) / ".staging" / f"author-check-{card}-{uuid.uuid4().hex[:8]}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(staging_private.get(f"review/repaired-authored-overlays/{card}.json") or returns[card])
        try:
            res = author_check(tmp, PACK, STAGE)
        finally:
            tmp.unlink()
        rows[card] = res
        ck(f"{card}: the (repaired) return passes the existing author check", all(r["ok"] for r in res), str([r["detail"] for r in res if not r["ok"]][:3]))
    failed = [c for c in checks if not c["ok"]]
    if failed:
        raise RepairRefused(f"repair checks failed: {failed[:3]}")
    # manifest
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    originals = []
    for o in base["originals"]:
        e = dict(o)
        dec = decisions[o["original_id"]]
        if o["original_id"] in changed:
            e.update({"sha256": sha(files[o["file"]]), "size_bytes": len(files[o["file"]]), "version": 2,
                      "supersedes": {"package": BASE.name, "manifest_sha256": sha(base_raw), "file": o["file"], "sha256": o["sha256"], "version": 1},
                      "revision": {"kind": "deterministic_source_repair", "rulings": [x for x in _rulings_for(o, spec, rul)],
                                   "authorisation_sha256": bindings["authorisation"]["sha256"]},
                      "review_status": "pending_independent_review"})
            if e.get("reuse"):
                e["reuse"] = "repaired_render"
        else:
            e.update({"version": 1, "review_status": dec["decision"],
                      "prior_review": {"decision": dec["decision"], "decisions_file": rel_root(DECISIONS),
                                       **({"signoff": rel_root(SIGNOFF), "signoff_sha256": sha(signoff_raw)} if o["original_id"] in signed else
                                          {"inherited_from": f"receiving-exports/{EXPORT.name}", "export_manifest_sha256": EXPORT_MANIFEST_SHA256})}})
        originals.append(e)
    occurrences = []
    for e in originals:
        if e["file"].endswith(".eml"):
            for p in _msg(files[e["file"]]).iter_attachments():
                occurrences.append({"parent_message_file": e["file"], "filename": p.get_filename(), "sha256": sha(p.get_payload(decode=True))})
    manifest = {"kind": "stage_delivery_manifest", "package": name, "package_version": 2, "case_id": plan.case_id, "stage": STAGE,
                "built_at_utc": stamp, "pack_digest": base["pack_digest"], "pack_set_version": base["pack_set_version"],
                "repair_pack": {"name": REPAIR_NAME, "repair_pack_digest": rp_digest},
                "supersedes_package": {"package": BASE.name, "manifest_sha256": sha(base_raw)},
                "repair_authorisation": bindings["authorisation"], "repair_rulings": bindings["rulings"],
                "accepted_attachment_source": base["accepted_attachment_source"], "originals": originals,
                "attachment_occurrences": occurrences, "review_status": "pending_independent_review",
                "note": ("Successor of case-11-remaining-cards-v1 under exact mechanical repairs; no worker and no new prose. Five originals are new "
                         "versions pending independent review; every other original is byte-identical to v1 and keeps its recorded decision. "
                         "Not a complete-case acceptance.")}
    return _publish_successor(out_root, name, files, manifest, staging_private, checks, spec, rows, old_files, base, base_raw, rerender_same)


def _rulings_for(o, spec, rul):
    f = o["file"]
    out = []
    if f.endswith("11-13-external-submission-evidence-index.xlsx") or "_to_case-correspondence-team" in f:
        out.append(rul["index_projection"]["id"])
    if f.endswith("11-14-external-case-management-invoice.pdf") or "_to_zoe-turner" in f:
        out.append(rul["invoice_display"]["id"])
    out += [x["ruling"] for x in spec["literal_repairs"] if o.get("staging_card_ref") == x["card"]]
    return out


def complete_case_inventory(manifest, manifest_sha256):
    """All 29 case-11 cards in event order with their originals, held/unheld attachments and actual review state. Review aid, never runtime."""
    from .authoring_check import load_pack
    plan = load_pack(PACK)[0]
    em = json.loads((EXPORT / "intake-bundle/delivery-manifest.json").read_text())
    exp_att = {o["filename"]: o for o in em["originals"] if o["kind"] == "attachment_original"}
    new_att = {o["filename"]: o for o in manifest["originals"] if o["kind"] == "attachment_original"}
    cards = []
    for a in sorted(plan.pack["artifacts"], key=lambda a: a["event_at"]):
        ref = a["artifact_ref"]
        old = [o for o in em["originals"] if o.get("staging_card_ref") == ref and o["kind"] != "attachment_original"]
        new = [o for o in manifest["originals"] if o.get("staging_card_ref") == ref]
        atts = []
        for aid in a.get("attachments", []):
            asset = plan.assets[aid]
            if asset.get("delivery_state") != "held":
                atts.append({"asset_id": aid, "filename": asset["filename"], "status": "advertised_not_held_no_bytes"})
                continue
            o = new_att.get(asset["filename"]) or exp_att.get(asset["filename"])
            if o is None and asset["filename"] in {Path(x["file"]).name for x in new}:
                o = next(x for x in new if Path(x["file"]).name == asset["filename"])  # a ledger card's own export
            atts.append({"asset_id": aid, "filename": asset["filename"], "original_id": o and o["original_id"], "sha256": o and o["sha256"],
                         "where": "successor package" if asset["filename"] in new_att or o in new else "receiving export" if o else "MISSING",
                         "status": (o.get("review_status") if o in new or asset["filename"] in new_att else "accepted (receiving export)") if o else "MISSING"})
        if old:
            status = "accepted_unchanged_in_receiving_export"
        elif new:
            status = "pending_independent_review" if any(o["review_status"] == "pending_independent_review" for o in new) else "source_accepted"
        else:
            status = "MISSING"
        cards.append({"card": ref, "event_at": a["event_at"], "plan_kind": a["kind"], "status": status,
                      "originals": [{"original_id": o["original_id"], "file": o["file"], "sha256": o["sha256"], "version": o.get("version", 1),
                                     "review_status": o.get("review_status", "accepted"),
                                     "where": "receiving export case-11-accepted-slice-v1" if o in old else manifest["package"]} for o in old + new],
                      "attachments": atts})
    counts = {"cards": len(cards), "accepted_in_receiving_export": sum(c["status"].startswith("accepted") for c in cards),
              "source_accepted_new": sum(c["status"] == "source_accepted" for c in cards),
              "pending_repaired": sum(c["status"] == "pending_independent_review" for c in cards), "missing": sum(c["status"] == "MISSING" for c in cards),
              "successor_originals": len(manifest["originals"]),
              "successor_originals_by_status": {s: sum(o["review_status"] == s for o in manifest["originals"])
                                                for s in sorted({o["review_status"] for o in manifest["originals"]})}}
    return {"kind": "complete_case_inventory", "case": 11, "case_id": plan.case_id, "package": manifest["package"],
            "package_manifest_sha256": manifest_sha256, "receiving_export_manifest_sha256": EXPORT_MANIFEST_SHA256,
            "note": ("Restricted review aid; never runtime. Cards, originals and attachment occurrences are different counts. The 12 earlier cards stay "
                     "accepted in the receiving export; the remaining 17 cards' originals are in this package with their actual review state."),
            "counts": counts, "cards": cards}


def _publish_successor(out_root, name, files, manifest, private, checks, spec, rows, old_files, base, base_raw, rerender_same):
    from .microbatch import verify_package
    out_root = Path(out_root)
    target = out_root / name
    if target.exists() or target.is_symlink():
        raise ReleaseExistsError(f"{target} already exists; packages are immutable")
    stage = out_root / ".staging" / f"{name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    intake = stage / "intake-bundle"
    for rel, data in files.items():
        f = intake / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    mraw = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    (intake / "delivery-manifest.json").write_bytes(mraw)
    listed = {rel: sha(d) for rel, d in files.items()}
    listed["delivery-manifest.json"] = sha(mraw)
    (intake / "INTAKE-FILES.sha256").write_text("".join(f"{h}  {rel}\n" for rel, h in sorted(listed.items())))
    for rel, data in private.items():
        f = stage / "restricted" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    rev = stage / "restricted/review"
    rev.mkdir(parents=True, exist_ok=True)
    (rev / "REVIEW-STATUS.json").write_text(json.dumps({"status": "pending_independent_review", "accepted_records": []}, indent=2) + "\n")
    (rev / "review-form.json").write_text(json.dumps({"decisions": [{"original_id": o["original_id"], "sha256": o["sha256"], "decision": None, "notes": ""}
                                                                    for o in manifest["originals"] if o["review_status"] == "pending_independent_review"]}, indent=2) + "\n")
    comparison = []
    old_by_file = {o["file"]: o for o in base["originals"]}
    for o in manifest["originals"]:
        old = old_by_file[o["file"]]
        comparison.append({"original_id": o["original_id"], "file": o["file"], "v1_sha256": old["sha256"], "v1_size": old["size_bytes"],
                           "successor_sha256": o["sha256"], "successor_size": o["size_bytes"],
                           "change": "repaired_new_version" if o["sha256"] != old["sha256"] else "byte_identical",
                           "successor_review_status": o["review_status"]})
    for rel in sorted(r for r in files if r.startswith("previews/")):
        comparison.append({"preview": rel, "v1_sha256": sha(old_files[rel]), "successor_sha256": sha(files[rel]),
                           "change": "regenerated_display_aid" if files[rel] != old_files[rel] else "byte_identical"})
    (rev / "V1-TO-SUCCESSOR-COMPARISON.json").write_text(json.dumps({
        "kind": "source_package_byte_comparison", "from": {"package": base["package"], "manifest_sha256": sha(base_raw)},
        "to": {"package": name, "manifest_sha256": sha(mraw)}, "entries": comparison,
        "counts": {"originals": len(manifest["originals"]), "byte_identical": sum(c.get("change") == "byte_identical" for c in comparison if "original_id" in c),
                   "repaired": sum(c.get("change") == "repaired_new_version" for c in comparison)}}, indent=1) + "\n")
    (rev / "REPAIR-CHECKS.json").write_text(json.dumps({"checks": checks, "author_check_rows": {c: len(r) for c, r in rows.items() if isinstance(r, list)},
                                                        "rerendered_identical_paths": sorted(rerender_same)}, indent=1) + "\n")
    (rev / "REPAIR-SPEC.copy.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
    (rev / "COMPLETE-CASE-INVENTORY.json").write_text(json.dumps(complete_case_inventory(manifest, sha(mraw)), indent=1) + "\n")
    result = verify_package(stage, accepted_package=EXPORT)
    (rev / "PACKAGE-CHECKS.json").write_text(json.dumps({"checks": result}, indent=1) + "\n")
    failed = [c for c in result if not c["ok"]]
    if failed:
        raise RepairRefused(f"successor failed package verification (kept at {stage}): {failed[:3]}")
    try:
        rename_noreplace(stage, target)
    except FileExistsError as e:
        raise ReleaseExistsError(f"{target} already exists; nothing was replaced. Verified stage kept at {stage}") from e
    return target, checks
