"""No-overwrite packaging of a stage's rendered cards (email, call summary, letter, portal, register, note) into a reviewable
package: an ingestible `intake-bundle/` (allowlisted paths and types only) and a `restricted/` side for private controls.

Flow: author-check every returned card -> render with the real renderers -> reuse accepted attachment bytes exactly from their
bound originals -> build everything in a private staging directory -> verify -> publish with the kernel no-replace rename.
Nothing here accepts anything: the manifest and review form carry `pending_independent_review` and blank decisions.

This is the packaging path for NEW stage output. It deliberately does not re-run the rehearsal's 377 package checks (those are tied
to the five-card batch); `verify_package` below is its own, smaller set, and an independent review of the published files is still required."""
import email
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from email import policy
from pathlib import Path

from .atomic import rename_noreplace
from .authoring_check import author_check, load_pack
from .build import ReleaseExistsError
from .card_adapter import ContractGap, accepted_attachment_index, render_card

INTAKE_PREFIXES = ("originals/emails/", "originals/call-summaries/", "originals/letters/", "originals/attachments/", "originals/portal/",
                   "originals/registers/", "originals/ledgers/", "originals/notes/", "previews/")
INTAKE_EXTENSIONS = {".eml", ".txt", ".pdf", ".csv", ".xlsx", ".png", ".jpg", ".json", ".html"}
PRIVATE_PREFIX = "private-controls/"
TEXT_EXTENSIONS = {".eml", ".txt", ".csv", ".json", ".html"}
PRIVATE_MARKERS = re.compile(r"private-controls|style_completion|simulated_conversation|fidelity|never_ingested", re.I)


class PackagingRefused(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def _safe_rel(rel):
    p = Path(rel)
    return not p.is_absolute() and ".." not in p.parts and rel == p.as_posix()


def allowlisted(rel):
    return _safe_rel(rel) and rel.startswith(INTAKE_PREFIXES) and Path(rel).suffix.lower() in INTAKE_EXTENSIONS


def load_returns(outputs_dir, stage_cards):
    """The returned card files of an owned folder: <card>.json for each card in the stage (the checklist copy is not a card)."""
    out = {}
    for f in sorted(Path(outputs_dir).glob("*.json")):
        if f.name == "RETURN-CHECKLIST.json":
            continue
        out[f.stem] = f
    extra = sorted(set(out) - set(stage_cards))
    if extra:
        raise PackagingRefused(f"returned files for cards outside the stage: {extra}")
    return out


def build_package(pack_dir, stage, outputs_dir, out_root, name, accepted_package=None, expected_manifest_sha256=None, now=None):
    """Package every card file in `outputs_dir`. Returns the published directory. A pre-existing target of any kind, empty or not,
    is refused atomically and the verified staging directory is kept."""
    plan, sty, prof, stage_doc, clars, prior = load_pack(pack_dir)
    st = next((s for s in stage_doc["stages"] if s["stage"] == stage and s.get("launchable")), None)
    if st is None:
        raise PackagingRefused(f"stage {stage!r} is not a launchable stage of this pack")
    returns = load_returns(outputs_dir, st["cards"])
    if not returns:
        raise PackagingRefused("no returned card files to package")
    bound = (prior.get("bound_to_manifest") or {})
    accepted = {}
    if accepted_package:
        accepted = accepted_attachment_index(accepted_package, expected_manifest_sha256 or bound.get("delivery_manifest_sha256"))
    files, private, originals, occurrences, records = {}, {}, [], [], []
    label = "microbatch" if stage == "style_microbatch" else stage.replace("_", "-")
    for card, path in sorted(returns.items(), key=lambda kv: plan.artifact(kv[0])["event_at"]):
        rows = author_check(path, pack_dir, stage)
        bad = [r for r in rows if not r["ok"]]
        if bad:
            raise PackagingRefused(f"{card} fails the author check: {[r['detail'] for r in bad][:3]}")
        d = json.loads(path.read_text())
        art = plan.artifact(card)
        rc = render_card(d, plan, art, {n: v[1] for n, v in accepted.items()})
        for rel, data in rc.files.items():
            if rel in files and files[rel] != data:
                raise PackagingRefused(f"two cards render different bytes to {rel}")
            files[rel] = data
        for rel, data in rc.private.items():
            private[rel] = data
        main = [r for r in rc.files if r.startswith(("originals/emails/", "originals/call-summaries/", "originals/portal/", "originals/registers/",
                                                    "originals/ledgers/", "originals/notes/"))]
        for rel in main:
            ledger_asset = plan.assets_by_name.get(rel.rsplit("/", 1)[-1]) if rel.startswith("originals/ledgers/") else None
            originals.append({"original_id": ledger_asset["asset_id"] if ledger_asset else f"ORIG-{plan.case_id[:6]}-{label}-{card}", "file": rel, "kind": "email" if rc.kind in ("email", "letter_with_cover_email") else rc.kind,
                              "authored_kind": rc.kind, "staging_card_ref": card, "event_at": art["event_at"], "sha256": sha(rc.files[rel]),
                              "size_bytes": len(rc.files[rel]), "review_status": "pending_independent_review"})
        for rel in (r for r in rc.files if r.startswith("originals/letters/")):
            originals.append({"original_id": f"ORIG-{plan.case_id[:6]}-{label}-{card}-letter", "file": rel, "kind": "letter", "authored_kind": rc.kind,
                              "staging_card_ref": card, "event_at": art["event_at"], "sha256": sha(rc.files[rel]), "size_bytes": len(rc.files[rel]),
                              "review_status": "pending_independent_review"})
        letter_names = {r.rsplit("/", 1)[-1] for r in rc.files if r.startswith("originals/letters/")}
        parent = next((r for r in rc.files if r.endswith(".eml") or r.startswith("originals/portal/")), None)
        for fname, media, data in rc.attachments:
            if parent:
                occurrences.append({"parent_message_file": parent, "filename": fname, "sha256": sha(data)})
            if fname in letter_names:
                continue  # the letter is an original of this card, already recorded
            rel = f"originals/attachments/{fname}"
            if rel in files and files[rel] != data:
                raise PackagingRefused(f"attachment {fname} differs between cards")
            if fname in accepted and accepted[fname][1] != data:
                raise PackagingRefused(f"attachment {fname} differs from its accepted original")
            files[rel] = data
            if not any(o["file"] == rel for o in originals):
                asset = plan.assets_by_name.get(fname)
                originals.append({"original_id": accepted[fname][0] if fname in accepted else f"ORIG-{plan.case_id[:6]}-{asset['asset_id'] if asset else fname}",
                                  "file": rel, "kind": "attachment_original", "media_type": media, "filename": fname,
                                  "reuse": "accepted_exact" if fname in accepted else "new_render", "sha256": sha(data), "size_bytes": len(data),
                                  "review_status": "accepted_inherited" if fname in accepted else "pending_independent_review"})
        records.append({"card": card, "authored_file": path.name, "authored_sha256": sha(path.read_bytes()), "author_check_rows": len(rows)})
    for rel in files:
        if not allowlisted(rel):
            raise PackagingRefused(f"{rel} is not on the intake allowlist")
    for rel in private:
        if not (_safe_rel(rel) and rel.startswith(PRIVATE_PREFIX)):
            raise PackagingRefused(f"private path {rel} is outside {PRIVATE_PREFIX}")
    leaks = private_leaks(files, private)
    if leaks:
        raise PackagingRefused(f"private material found in the intake side: {leaks[:3]}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {"kind": "stage_delivery_manifest", "package": name, "case_id": plan.case_id, "stage": stage, "built_at_utc": stamp,
                "pack_digest": json.loads((Path(pack_dir) / "PACK-MANIFEST.json").read_text())["pack_digest"],
                "pack_set_version": json.loads((Path(pack_dir) / "STAGE.json").read_text())["pack_set_version"],
                "accepted_attachment_source": bound or None, "originals": originals, "attachment_occurrences": occurrences,
                "review_status": "pending_independent_review",
                "note": "Authored by a worker, rendered and packaged by the supervisor; not accepted. Private controls are outside this folder's restricted side."}
    out_root = Path(out_root)
    target = out_root / name
    stage_dir = out_root / ".staging" / f"{name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    (stage_dir / "intake-bundle").mkdir(parents=True)
    for rel, data in files.items():
        f = stage_dir / "intake-bundle" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (stage_dir / "intake-bundle/delivery-manifest.json").write_bytes((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
    listed = {rel: sha(f.read_bytes()) for rel in [*files, "delivery-manifest.json"] for f in [stage_dir / "intake-bundle" / rel]}
    (stage_dir / "intake-bundle/INTAKE-FILES.sha256").write_text("".join(f"{h}  {rel}\n" for rel, h in sorted(listed.items())))
    for rel, data in private.items():
        f = stage_dir / "restricted" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    rev = stage_dir / "restricted/review"
    rev.mkdir(parents=True)
    (rev / "packaging-record.json").write_text(json.dumps({"returns": records, "stage": stage, "pack": Path(pack_dir).name}, indent=2) + "\n")
    (rev / "REVIEW-STATUS.json").write_text(json.dumps({"status": "pending_independent_review", "accepted_records": []}, indent=2) + "\n")
    (rev / "review-form.json").write_text(json.dumps({"decisions": [{"original_id": o["original_id"], "decision": None, "notes": ""} for o in originals if o["review_status"] != "accepted_inherited"]}, indent=2) + "\n")
    result = verify_package(stage_dir, accepted_package=accepted_package)
    failed = [c for c in result if not c["ok"]]
    if failed:
        raise PackagingRefused(f"staged package failed verification (kept at {stage_dir}): {failed[:3]}")
    try:
        rename_noreplace(stage_dir, target)
    except FileExistsError as e:
        raise ReleaseExistsError(f"{target} already exists; nothing was replaced. Verified stage kept at {stage_dir}") from e
    return target


def private_leaks(files, private):
    """Intake paths whose text contains private dialogue turns or private-control markers."""
    turns = []
    for rel, data in private.items():
        if "conversations/" in rel:
            turns += [t["text"] for t in json.loads(data).get("turns", []) if len(t["text"]) >= 25]
    bad = []
    for rel, data in files.items():
        if Path(rel).suffix.lower() not in TEXT_EXTENSIONS:
            continue
        text = data.decode("utf-8", "replace")
        if rel.startswith("originals/emails/"):
            m = email.message_from_bytes(data, policy=policy.default)
            body = m.get_body(preferencelist=("plain",))
            text = (body.get_content() if body else "") + str(m["Subject"])
        if PRIVATE_MARKERS.search(text) or any(t in text for t in turns):
            bad.append(rel)
    return bad


def verify_package(pkg, accepted_package=None):
    """[{name, ok, detail}] for a staged or published package folder."""
    pkg = Path(pkg)
    out = []

    def ck(n, ok, d=""):
        out.append({"name": n, "ok": bool(ok), "detail": d})

    intake = pkg / "intake-bundle"
    man = json.loads((intake / "delivery-manifest.json").read_text())
    on_disk = {str(f.relative_to(intake)) for f in intake.rglob("*") if f.is_file() and f.name != "INTAKE-FILES.sha256"}
    rows = dict((p, h) for h, p in (ln.split("  ", 1) for ln in (intake / "INTAKE-FILES.sha256").read_text().splitlines()))
    ck("checksum inventory lists exactly the intake files", set(rows) == on_disk)
    ck("every intake file matches its recorded hash", all(sha((intake / r).read_bytes()) == h for r, h in rows.items() if (intake / r).exists()))
    ck("every intake path is allowlisted (or is the manifest)", all(allowlisted(r) or r == "delivery-manifest.json" for r in on_disk), str([r for r in on_disk if not allowlisted(r) and r != "delivery-manifest.json"]))
    ck("manifest originals exist with recorded hash and size", all((intake / o["file"]).exists() and sha((intake / o["file"]).read_bytes()) == o["sha256"]
                                                                   and (intake / o["file"]).stat().st_size == o["size_bytes"] for o in man["originals"]))
    priv = {str(f.relative_to(pkg / "restricted")): f.read_bytes() for f in (pkg / "restricted").rglob("*") if f.is_file() and f.relative_to(pkg / "restricted").parts[0] == "private-controls"}
    ck("no private dialogue or control marker in intake text", not private_leaks({r: (intake / r).read_bytes() for r in on_disk}, priv))
    from .leakguard import scan_files
    card_ids = scan_files({r: (intake / r).read_bytes() for r in sorted(on_disk) if r != "delivery-manifest.json"})
    ck("no private authoring-card ID in any held text, stored XLSX cell, PDF text layer or decoded attachment", not card_ids,
       str({r: [h["where"] for h in v][:3] for r, v in card_ids.items()}))
    ck("restricted side holds no intake allowlisted originals", not any(str(f.relative_to(pkg / "restricted")).startswith("originals/") for f in (pkg / "restricted").rglob("*") if f.is_file()))
    ok_msgs = True
    for o in (x for x in man["originals"] if x["file"].endswith(".eml")):
        msg = email.message_from_bytes((intake / o["file"]).read_bytes(), policy=policy.default)
        ok_msgs &= not any(p.defects for p in msg.walk())
        for part in msg.iter_attachments():
            fname = part.get_filename()
            ref = intake / "originals/attachments" / fname
            lref = intake / "originals/letters" / fname
            target = ref if ref.exists() else lref
            ok_msgs &= target.exists() and part.get_payload(decode=True) == target.read_bytes()
    ck("every message parses and each attachment is byte-identical to its stored original", ok_msgs)
    if accepted_package:
        acc = accepted_attachment_index(accepted_package, None)
        ck("reused accepted attachments are byte-identical to the accepted originals",
           all((intake / o["file"]).read_bytes() == acc[o["filename"]][1] for o in man["originals"] if o.get("reuse") == "accepted_exact"))
    rs = json.loads((pkg / "restricted/review/REVIEW-STATUS.json").read_text())
    ck("nothing is marked accepted by the packager", rs["accepted_records"] == [] and man["review_status"] == "pending_independent_review"
       and all(d["decision"] is None for d in json.loads((pkg / "restricted/review/review-form.json").read_text())["decisions"]))
    return out
