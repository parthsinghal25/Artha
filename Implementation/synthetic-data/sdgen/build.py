"""Build one reviewable batch: render originals, package the intake bundle, write the manifest.

Release safety (R03): a delivered package directory is never deleted, replaced or rebuilt in place. Work happens in a
private staging directory beside the release root and is published with one atomic rename that fails if the final
directory already exists. Historical binding (R04): rendered cards record the exact authored files (path, revision,
sha256), the plan digest and the renderer version; repairs carry accepted originals forward byte for byte from the base
package instead of re-rendering them."""
import hashlib
import uuid
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import RENDERER_VERSION, TOOL_VERSION
from .paths import AUTHORED, CONTRACT_MANIFEST, RELEASE_ROOT, TOOL_ROOT
from .plan import CasePlan
from .render_call import render_call_summary
from .atomic import rename_noreplace, PublishUnsupported  # noqa: F401
from .render_csv import render_rows_csv
from .render_image import render_upload_receipt
from .render_mail import build_eml, message_id
from .render_pdf import render_document, render_letter

BATCH = {
    "case11-rehearsal": {
        "case_number": 11,
        "cards": ["11-D05", "11-D06", "11-D10", "11-D15", "11-D17"],
        # reusable originals prepared once for the five cards (not a fabricated outcome file)
        "assets": ["DC-11.pdf", "ID-11.pdf", "RF-11.pdf", "11-05-portal-upload-receipt.jpg",
                   "11-07-receiving-document-checklist.csv", "11-10-new-dc-11-assessment.pdf"],
        "unheld_assets": ["11-09-historic-dc-11-validity-assessment.pdf"],
        "dir_name": "case-11-rehearsal",
    }
}
# Repair releases: which cards are re-rendered from authored overlays; everything else is copied byte for byte
# from the base package. Keyed by (batch, package_version).
REPAIRS = {
    ("case11-rehearsal", 2): {
        "base_package_version": 1,
        "rerender_cards": ["11-D05", "11-D06", "11-D10"],
        "carry_cards": ["11-D15", "11-D17"],
        "carry_assets": "all",
    },
    # v3 is based on v2: only D05 (sentence-level ruling SR-2) and its D10 quotation are re-rendered. The ten originals
    # accepted by the independent v2 review (six attachments, D06 v2, D15, D17 email, D17 letter) are copied unchanged.
    ("case11-rehearsal", 3): {
        "base_package_version": 2,
        "rerender_cards": ["11-D05", "11-D10"],
        "carry_cards": ["11-D06", "11-D15", "11-D17"],
        "carry_assets": "all",
    },
}
CHECKLIST_COLUMNS = ["request", "as_of", "member_account_number", "item", "status", "historic_validity_decision"]


class ReleaseExistsError(RuntimeError):
    """The final release directory already exists; delivered releases are immutable."""


class InputUnavailable(RuntimeError):
    """A recorded input (authored file, plan, base package) is missing or its hash differs."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): sha256(p.read_bytes()) for p in sorted(Path(root).rglob("*")) if p.is_file()}


def load_authored(card: str, authored_version: int):
    """Highest authored revision <= requested that contains the card (repairs overlay only changed cards)."""
    for v in range(authored_version, 0, -1):
        p = AUTHORED / "case-11" / f"v{v}" / f"{card}.json"
        if p.exists():
            return json.loads(p.read_text()), p, v
    raise FileNotFoundError(card)


def load_pinned(pin: dict):
    """Exactly the recorded authored file; never 'latest'."""
    p = TOOL_ROOT / pin["path"]
    if not p.exists():
        raise InputUnavailable(f"authored input missing: {pin['path']}")
    raw = p.read_bytes()
    if sha256(raw) != pin["sha256"]:
        raise InputUnavailable(f"authored input changed since recording: {pin['path']}")
    return json.loads(raw), p


def scenario_ts(value, precision="second"):
    return {"value": value, "precision": precision, "tz": value[19:] or "unspecified"}


def stem_for(art, plan):
    dt = datetime.fromisoformat(art["event_at"])
    a = slug(plan.actor(art["author"])["name"])
    r = slug(plan.actor(art["recipients"][0])["name"]) if art["recipients"] else "file"
    return f"{dt:%Y-%m-%d_%H%M}_{a}_to_{r}"


def orig_id(key, card, version, suffix=""):
    base = f"ORIG-{key}-{card}{suffix}"
    return base if version == 1 else f"{base}.v{version}"


class Batch:
    def __init__(self, batch_key="case11-rehearsal", authored_version=1, package_version=1, out_root=None,
                 pins=None, base_dir=None, rulings=()):
        self.key = batch_key
        self.spec = BATCH[batch_key]
        self.plan = CasePlan(self.spec["case_number"])
        self.authored_version = authored_version
        self.package_version = package_version
        self.root = Path(out_root) if out_root else RELEASE_ROOT
        self.dir = self.root / f"{self.spec['dir_name']}-v{package_version}"
        # staging identity is separate from the final package identity (self.dir.name); reports use the latter
        self.stage_id = f"staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.stage = self.root / ".staging" / f"{self.dir.name}.{self.stage_id}"
        self.pins = pins or {}
        self.repair = REPAIRS.get((batch_key, package_version))
        self.base_dir = Path(base_dir) if base_dir else None
        if self.repair and not self.base_dir:
            self.base_dir = self.root / f"{self.spec['dir_name']}-v{self.repair['base_package_version']}"
        self.rulings = list(rulings)
        self.files = {}
        self.entries = []
        self.occurrences = []
        self.previews = []
        self.private = {}
        self.timings = {}
        self.authored_used = {}
        self.authored_inherited = {}
        self.carried = []
        self.carried_private = {}
        self.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.asset_bytes = {}
        self.render_log = {}
        self._base = None
        self._cache = {}

    # ---------- inputs ----------
    def _authored(self, card):
        if card in self._cache:
            return self._cache[card]
        if card in self.pins:
            authored, path = load_pinned(self.pins[card])
            ver = authored["revision"]
        else:
            authored, path, ver = load_authored(card, self.authored_version)
        rec = {"path": str(path.relative_to(TOOL_ROOT)), "revision": authored["revision"], "sha256": sha256(path.read_bytes())}
        self._cache[card] = (authored, rec)
        return authored, rec

    def _load_base(self):
        """Verify the base package is intact, externally reviewed and that every carried original was accepted."""
        if self._base:
            return self._base
        b = self.base_dir
        mpath = b / "intake-bundle/delivery-manifest.json"
        if not mpath.exists():
            raise InputUnavailable(f"base package missing: {b}")
        man = json.loads(mpath.read_text())
        sums = {}
        for line in (b / "intake-bundle/INTAKE-FILES.sha256").read_text().splitlines():
            h, rel = line.split("  ", 1)
            sums[rel] = h
        for rel, h in sums.items():
            if sha256((b / "intake-bundle" / rel).read_bytes()) != h:
                raise InputUnavailable(f"base package byte integrity failed: {rel}")
        status = json.loads((b / "restricted/review/REVIEW-STATUS.json").read_text())
        rec = json.loads((b / "restricted/private-controls/build-record.json").read_text())
        self._base = {"manifest": man, "manifest_sha256": sha256(mpath.read_bytes()), "status": status, "record": rec,
                      "review_status_sha256": sha256((b / "restricted/review/REVIEW-STATUS.json").read_bytes()),
                      "review_form_sha256": sha256((b / "restricted/review/review-form.json").read_bytes()),
                      "sums": sums}
        return self._base

    def _carry(self, rel):
        """Copy one accepted original byte for byte from the base package."""
        base = self._load_base()
        data = (self.base_dir / "intake-bundle" / rel).read_bytes()
        if sha256(data) != base["sums"][rel]:
            raise InputUnavailable(f"carried file differs from base inventory: {rel}")
        self.files[rel] = data
        return data

    def _require_accepted(self, original_id):
        accepted = self._load_base()["status"].get("accepted_records", [])
        if original_id not in accepted:
            raise InputUnavailable(f"{original_id} is not recorded as externally accepted in the base package; refusing to carry it")

    # ---------- assets ----------
    def _author_name(self, owner):
        return self.plan.actor(owner)["name"]

    def render_assets(self):
        t0 = time.perf_counter()
        for name in self.spec["assets"]:
            a = self.plan.assets_by_name[name]
            rel = f"originals/attachments/{name}"
            if self.repair:
                entry = next(e for e in self._load_base()["manifest"]["originals"] if e["filename"] == name and e["kind"] == "attachment_original")
                self._require_accepted(entry["original_id"])
                data = self._carry(rel)
                self.asset_bytes[a["asset_id"]] = (name, a["media_type"], data)
                continue
            spec = a["document_spec"]
            if a["media_type"] == "application/pdf":
                data = render_document(a, self._author_name(a["owner"]))
            elif a["media_type"] == "image/jpeg":
                fields = {k: spec["fields"][k] for k in ("upload_reference", "uploaded_at", "member_account_number")}
                data, log = render_upload_receipt(a, spec["rows"], fields)
                self.render_log[name] = log
            elif a["media_type"] == "text/csv":
                f = spec["fields"]
                rows = [{"request": f["request"], "as_of": f["as_of"], "member_account_number": f["member_account_number"],
                         "item": r["item"], "status": r["status"], "historic_validity_decision": r.get("historic_validity_decision")}
                        for r in spec["rows"]]
                data = render_rows_csv(CHECKLIST_COLUMNS, rows)
            else:
                raise ValueError(f"no renderer for {name}")
            self.asset_bytes[a["asset_id"]] = (name, a["media_type"], data)
            self.files[rel] = data
        self.timings["render_assets_s"] = round(time.perf_counter() - t0, 3)

    # ---------- cards ----------
    def render_cards(self):
        t0 = time.perf_counter()
        p = self.plan
        for card in self.spec["cards"]:
            if self.repair and card in self.repair["carry_cards"]:
                self._carry_card(card)
                continue
            authored, rec = self._authored(card)
            self.authored_used[card] = rec
            art = p.artifact(card)
            stem = stem_for(art, p)
            sender = p.actor(art["author"])
            kind = authored["kind"]
            if kind == "call_summary":
                data = render_call_summary(card, art, authored, p)
                rel = f"originals/call-summaries/{stem}.txt"
                self.files[rel] = data
                self.private[f"private-controls/conversations/{card}-conversation.json"] = json.dumps(
                    {"card": card, "revision": authored["revision"], "kind": "simulated_conversation_for_summary_fidelity_review",
                     "never_ingested": True, "turns": authored["conversation"]}, indent=2, ensure_ascii=False).encode() + b"\n"
                fid = authored.get("fidelity") or {"claims": authored["fidelity_claims"]}
                self.private[f"private-controls/fidelity/{card}-summary-fidelity.json"] = json.dumps(
                    {"card": card, **fid, "independent_review": "pending"}, indent=2, ensure_ascii=False).encode() + b"\n"
                self._add_original(card, art, rel, data, "call_summary", "text/plain", authored)
                continue
            attachments, plan_assets = [], []
            for aid in art["attachments"]:
                if aid in self.asset_bytes:
                    attachments.append(self.asset_bytes[aid])
                    plan_assets.append(aid)
            letter_bytes = letter_rel = None
            if kind == "letter_with_cover_email":
                letter_bytes = render_letter(authored["letter"], art["event_at"], sender["name"])
                letter_rel = f"originals/letters/{authored['letter_filename']}"
                self.files[letter_rel] = letter_bytes
                attachments = [(authored["letter_filename"], "application/pdf", letter_bytes)] + attachments
            fwd = None
            if authored.get("forward_of"):
                src_auth, src_rec = self._authored(authored["forward_of"])
                self.authored_inherited.setdefault(authored["forward_of"], {**src_rec, "role": "forward_quote_dependency"})
                fwd = (p.artifact(authored["forward_of"]), src_auth)
            irt = None
            if authored.get("in_reply_to_card"):
                prev = p.artifact(authored["in_reply_to_card"])
                irt = message_id(authored["in_reply_to_card"], p.actor(prev["author"])["email"])
            refs = [message_id(c, p.actor(p.artifact(c)["author"])["email"]) for c in authored.get("references_cards", [])]
            eml, preview, parts, mid = build_eml(card, art, authored, p, attachments, fwd, irt, refs)
            rel = f"originals/emails/{stem}.eml"
            self.files[rel] = eml
            prel = f"previews/{stem}.preview.html"
            self.files[prel] = preview.encode("utf-8")
            self._add_original(card, art, rel, eml, "email", "message/rfc822", authored,
                               message_id=mid, in_reply_to=irt, parts=parts, preview=prel)
            if letter_rel:
                self._add_original(card, art, letter_rel, letter_bytes, "letter", "application/pdf", authored, parent_eml=rel)
            for part in parts:
                self._add_occurrence(card, rel, part, letter_rel)
        self.timings["render_cards_s"] = round(time.perf_counter() - t0, 3)

    def _carry_card(self, card):
        """Carry an accepted card's originals, previews, occurrences and authored-input record from the base."""
        base = self._load_base()
        man = base["manifest"]
        for e in [x for x in man["originals"] if x.get("staging_card_ref") == card]:
            self._require_accepted(e["original_id"])
            self._carry(e["file"])
            ne = json.loads(json.dumps(e))
            ne["lineage"]["carried_forward"] = {"from_package": self.base_dir.name, "from_manifest_sha256": base["manifest_sha256"],
                                                "from_file_sha256": e["sha256"], "relation": "byte_identical_copy"}
            if e.get("preview_file"):
                self._carry(e["preview_file"])
                self.previews.append(next(x for x in man["previews"] if x["preview_file"] == e["preview_file"]))
            self.entries.append(ne)
            self.carried.append({"original_id": e["original_id"], "file": e["file"], "sha256": e["sha256"]})
            if e["kind"] == "call_summary":
                self._carry_private_controls(card)
        for o in man["attachment_occurrences"]:
            if o["staging_card_ref"] == card:
                self.occurrences.append(o)
        rec = base["record"]
        src = rec["authored_used"].get(card) or rec["authored_inherited"][card]
        self.authored_inherited[card] = {k: v for k, v in src.items() if k != "role"} | {"role": "inherited_unchanged_input_of_carried_original"}

    def _carry_private_controls(self, card):
        """The accepted private dialogue and summary-fidelity matrix travel with the carried summary, byte for byte."""
        for rel in (f"private-controls/conversations/{card}-conversation.json", f"private-controls/fidelity/{card}-summary-fidelity.json"):
            src = self.base_dir / "restricted" / rel
            if not src.exists():
                raise InputUnavailable(f"base private control missing: {rel}")
            data = src.read_bytes()
            self.private[rel] = data
            self.carried_private[rel] = sha256(data)

    def _add_original(self, card, art, rel, data, kind, media, authored, **extra):
        p = self.plan
        suffix = "-letter" if kind == "letter" else ""
        base_id = orig_id(self.key, card, 1, suffix)
        e = {
            "original_id": orig_id(self.key, card, self.package_version, suffix),
            "staging_card_ref": card, "staging_source_ref": art["planned_source_id"],
            "kind": kind, "file": rel, "filename": Path(rel).name, "detected_media_type": media,
            "size_bytes": len(data), "sha256": sha256(data), "version": self.package_version,
            "lineage": {"relation": "original", "copy_of": None},
            "source_family": art["source_family"], "subject": art["subject"] if kind != "letter" else authored["letter"]["title"],
            "author": art["author"], "recipients": art["recipients"],
            "references": {"case": p.case_id, "request": p.request_reference},
            "scenario_time": {"event_at": scenario_ts(art["event_at"]), "recorded_at": scenario_ts(art["recorded_at"]),
                              "available_at": scenario_ts(art["available_at"]),
                              "note": "synthetic scenario dates; not real wall-clock times"},
            "audit": {"generated_at_utc": self.generated_at, "generator": f"sdgen {TOOL_VERSION}/{RENDERER_VERSION}",
                      "authored_revision": authored["revision"], "review_status": "pending_independent_review"},
        }
        if self.repair:
            b = self._load_base()
            old = next(x for x in b["manifest"]["originals"] if x.get("staging_card_ref") == card and x["kind"] == kind)
            e["lineage"] = {"relation": "revision", "copy_of": None, "supersedes_original_id": old["original_id"],
                            "supersedes_sha256": old["sha256"], "supersedes_package": self.base_dir.name,
                            "supersedes_manifest_sha256": b["manifest_sha256"]}
            e["audit"]["review_status"] = "pending_independent_review_changed_bytes"
        if kind == "email":
            e.update({"message_id": extra["message_id"], "in_reply_to": extra["in_reply_to"],
                      "staging_thread_ref": art["thread_id"], "preview_file": extra["preview"], "mime_parts": extra["parts"]})
            self.previews.append({"preview_file": extra["preview"], "preview_of": e["original_id"],
                                  "role": "display_aid_not_independent_evidence"})
        if kind == "letter":
            e["parent_message_file"] = extra["parent_eml"]
        if kind == "call_summary":
            e.update({"producer": "vendor-style summary service", "input_scope": "summary_only", "format": "TXT"})
        self.entries.append(e)

    def _add_occurrence(self, card, eml_rel, part, letter_rel):
        name = part["filename"]
        asset = self.plan.assets_by_name.get(name)
        is_letter = letter_rel is not None and name == Path(letter_rel).name
        self.occurrences.append({
            "parent_message_file": eml_rel, "staging_card_ref": card, "mime_part_path": part["mime_part_path"],
            "filename": name, "content_type": part["content_type"], "size_bytes": part["size_bytes"], "sha256": part["sha256"],
            "references_original": (orig_id(self.key, card, 1 if self.repair else self.package_version, "-letter") if is_letter else (asset["asset_id"] if asset else None)),
            "relation": "message_own_document" if is_letter else "reused_original_bytes",
            "business_occurrence_note": "embedding of identical bytes in a message is one attachment occurrence, "
                                        "not a new document submission or independent finding",
        })

    # ---------- manifest ----------
    def asset_entries(self):
        if self.repair:
            out = []
            b = self._load_base()
            for name in self.spec["assets"]:
                e = json.loads(json.dumps(next(x for x in b["manifest"]["originals"] if x["filename"] == name and x["kind"] == "attachment_original")))
                e["lineage"]["carried_forward"] = {"from_package": self.base_dir.name, "from_manifest_sha256": b["manifest_sha256"],
                                                   "from_file_sha256": e["sha256"], "relation": "byte_identical_copy"}
                self.carried.append({"original_id": e["original_id"], "file": e["file"], "sha256": e["sha256"]})
                out.append(e)
            return out
        out = []
        for name in self.spec["assets"]:
            a = self.plan.assets_by_name[name]
            data = self.files[f"originals/attachments/{name}"]
            spec = a["document_spec"]
            native = {k: v for k, v in (spec.get("fields") or {}).items()
                      if k in ("document_reference", "upload_reference", "member_account_number", "request_reference", "request", "version")}
            out.append({
                "original_id": a["asset_id"], "kind": "attachment_original", "staging_source_ref": a["source_id"],
                "file": f"originals/attachments/{name}", "filename": name, "semantic_type": a["semantic_type"],
                "declared_format": a["format"], "declared_media_type": a["media_type"], "detected_media_type": a["media_type"],
                "size_bytes": len(data), "sha256": sha256(data), "version": 1,
                "lineage": {"relation": "original", "copy_of": a["copy_of_asset_id"]},
                "owner": a["owner"], "held_by_complaints": a["held_by_complaints"], "source_family": a["source_family"],
                "native_reference_codes": native, "references": {"case": self.plan.case_id, "request": self.plan.request_reference},
                "scenario_time": {"created_at": scenario_ts(a["created_at"]), "available_at": scenario_ts(a["available_at"]),
                                  "note": "synthetic scenario dates; not real wall-clock times"},
                "audit": {"generated_at_utc": self.generated_at, "generator": f"sdgen {TOOL_VERSION}/{RENDERER_VERSION}",
                          "authored_revision": 1, "review_status": "pending_independent_review"},
            })
        return out

    def unheld_entries(self):
        out = []
        for name in self.spec["unheld_assets"]:
            a = self.plan.assets_by_name[name]
            out.append({"staging_asset_ref": a["asset_id"], "advertised_filename": name, "semantic_type": a["semantic_type"],
                        "state": "advertised_or_requested_but_not_held", "file": None, "sha256": None, "size_bytes": None,
                        "advertised_in": [c for c in self.spec["cards"] if a["asset_id"] in self.plan.artifact(c)["attachments"]],
                        "note": "No bytes, hash or placeholder file exist. Absence is part of the case evidence."})
        return out

    def manifest(self):
        pin = json.loads(CONTRACT_MANIFEST.read_text())
        p = self.plan
        refs = {r for e in self.entries for r in [e["author"], *e["recipients"]]}
        participants = [{"actor_ref": r, "display_name": p.actor(r)["name"], "role": p.actor(r)["role"], "email": p.actor(r)["email"]}
                        for r in sorted(refs)]
        assets = self.asset_entries()
        originals = assets + self.entries
        batch = {"batch_id": self.key, "package_version": self.package_version, "created_at_utc": self.generated_at,
                 "generator": f"sdgen {TOOL_VERSION}/{RENDERER_VERSION}",
                 "contract_pin": {"contract_version": pin["contract_version"], "candidate": pin["candidate"],
                                  "manifest_content_sha256": pin["manifest_content_sha256"]}}
        prov = {"authoring": "prose authored in-session by the synthetic-data coding assistant (Claude); structured documents "
                             "rendered deterministically from locked document_spec fields",
                "plan_pack_sha256": p.sha256}
        status_stmt = "Nothing in this bundle has been accepted by any Principal or reviewer."
        if self.repair:
            b = self._load_base()
            batch["supersedes_package"] = {"package": self.base_dir.name, "package_version": self.repair["base_package_version"],
                                           "manifest_sha256": b["manifest_sha256"]}
            prov["package_lineage"] = {
                "base_package": self.base_dir.name, "base_manifest_sha256": b["manifest_sha256"],
                "carried_forward_unchanged": [c["original_id"] for c in self.carried],
                "changed_originals": [e["original_id"] for e in self.entries if e["lineage"]["relation"] == "revision" and "carried_forward" not in e["lineage"]],
                "note": "Carried originals are byte-identical copies of originals the base package's external review recorded as accepted; "
                        "that decision is referenced, not re-asserted. Changed originals are pending independent review."}
            status_stmt = ("Changed originals are pending independent review. Carried-forward originals are byte-identical to originals "
                           "recorded as accepted in the base package's external review; this manifest does not record any acceptance.")
        return {
            "manifest_kind": "pre-extraction-delivery-manifest", "manifest_version": "0.1.0",
            "note": ("Private transport/packaging metadata for AI/backend intake. Not an ingestion kind, not an amendment to "
                     "Shared 1.0.0, and not an extraction, source-content hash, locator mapping or diagnostic result. "
                     "Staging refs are release-mapping keys, not canonical source IDs."),
            "batch": batch,
            "status": {"source_acceptance": "pending_independent_review", "production_extraction": "not_performed",
                       "diagnostic_gold": "not_prepared", "ingestion_receipt": "not_issued", "statement": status_stmt},
            "scope": {"case_reference": p.case_id, "member_reference": p.pack["member_reference"], "request_reference": p.request_reference,
                      "observation_window": {"start": "2025-10-01", "end_exclusive": p.pack["observation_end_exclusive"]},
                      "corpus": "selected complaints-team corpus; not institution-wide completeness"},
            "participants": participants, "originals": originals, "attachment_occurrences": self.occurrences,
            "advertised_unheld": self.unheld_entries(), "previews": self.previews, "native_administrative_inputs": [],
            "coverage": {"cards_in_batch": self.spec["cards"],
                         "cards_of_case_not_in_batch": [r for r in p.artifacts if r not in self.spec["cards"]],
                         "note": "Other case-11 cards are not authored in this batch; their absence is not evidence of absence.",
                         "native_inputs_note": "No genuine native administrative export is part of the five-card rehearsal."},
            "provenance": prov,
        }

    # ---------- write (staging only) ----------
    def _assert_target_free(self):
        if self.dir.exists():
            raise ReleaseExistsError(f"{self.dir} already exists; delivered releases are immutable. Use a new --package-version.")

    def write_stage(self):
        self._assert_target_free()
        if self.stage.exists():
            raise ReleaseExistsError(f"staging path exists: {self.stage}")
        bundle, restricted = self.stage / "intake-bundle", self.stage / "restricted"
        for rel, data in self.files.items():
            path = bundle / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = self.manifest()
        mb = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
        (bundle / "delivery-manifest.json").write_bytes(mb)
        lines = [f"{sha256(d)}  {rel}" for rel, d in sorted(self.files.items())] + [f"{sha256(mb)}  delivery-manifest.json"]
        (bundle / "INTAKE-FILES.sha256").write_text("\n".join(lines) + "\n")
        for rel, data in self.private.items():
            path = restricted / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        meta = {"authored_used": self.authored_used, "authored_inherited": self.authored_inherited, "timings": self.timings,
                "render_log": self.render_log, "tool_version": TOOL_VERSION, "renderer_version": RENDERER_VERSION,
                "generated_at_utc": self.generated_at, "authored_version": self.authored_version,
                "package_version": self.package_version, "plan_pack_sha256": self.plan.sha256}
        if self.repair:
            b = self._load_base()
            meta["base_package"] = {"name": self.base_dir.name, "manifest_sha256": b["manifest_sha256"],
                                    "review_status_sha256": b["review_status_sha256"], "review_form_sha256": b["review_form_sha256"],
                                    "base_record_authored_used": b["record"]["authored_used"]}
            meta["carried_forward"] = [{**c, "verified_byte_equal_to_base": True} for c in self.carried]
            meta["rerendered_cards"] = self.repair["rerender_cards"]
            meta["carried_private_controls_sha256"] = self.carried_private
            meta["render_log"] = {**b["record"].get("render_log", {}), **meta["render_log"]}
        meta["supplemental_rulings"] = [{"path": str(Path(r).name), "sha256": sha256(Path(r).read_bytes())} for r in self.rulings]
        rp = restricted / "private-controls" / "build-record.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(meta, indent=2) + "\n")
        return manifest

    def write_reading_copies(self):
        from .checks import eml_parts
        out = self.stage / "reading-copies"
        out.mkdir(parents=True, exist_ok=True)
        for rel, data in sorted(self.files.items()):
            if rel.endswith(".eml"):
                msg, body, atts = eml_parts(data)
                t = (f"From: {msg['From']}\nTo: {msg['To']}\nDate: {msg['Date']}\nSubject: {' '.join(str(msg['Subject']).split())}\n"
                     f"Attachments: {', '.join(a[0] for a in atts) or 'none'}\n\n{body}")
                (out / (Path(rel).stem + ".txt")).write_text(t)
        (out / "README.txt").write_text("Plain-text reading copies of the .eml originals for people. Derived aids, not part of the "
                                        "intake bundle; do not ingest.\n")

    def publish(self):
        """Move the checked staging directory into place with an atomic no-replace rename. The kernel refuses any
        existing destination (including an empty directory or a symlink created after the last absence check); there
        is no separate check-then-rename. Unsupported host -> PublishUnsupported, nothing moved. Stage is kept on refusal."""
        try:
            rename_noreplace(self.stage, self.dir)
        except FileExistsError:
            raise ReleaseExistsError(f"{self.dir} exists; atomic no-replace publication refused. Staged package kept at {self.stage}")
        try:
            self.stage.parent.rmdir()
        except OSError:
            pass


def build(batch_key="case11-rehearsal", authored_version=1, package_version=1, out_root=None, publish=True, pins=None,
          base_dir=None, rulings=()):
    b = Batch(batch_key, authored_version, package_version, out_root, pins, base_dir, rulings)
    b._assert_target_free()
    b.render_assets()
    b.render_cards()
    b.write_stage()
    b.write_reading_copies()
    if publish:
        b.publish()
    return b
