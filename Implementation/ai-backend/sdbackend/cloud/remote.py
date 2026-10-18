"""Private Cloud Storage upload/download of exports, and the Firestore active-publication pointer.

Storage layout (the manifest is authoritative for everything inside the payload):

    gs://<bucket>/publications/<export_id>/payload.tar.gz
    gs://<bucket>/publications/<export_id>/ARTHA-EXPORT-MANIFEST.json      (uploaded last)

Uploads are create-only (`if_generation_match=0`): an existing object is never overwritten. Each object is then
downloaded back by its exact generation and SHA-256 verified. The resulting *upload record* pins
{uri, generation, sha256} for both objects.

Pointer: Firestore document `<prefix>_control/serving_publication`. It holds `active` and `previous` upload records,
an integer `generation` and a short `history`. `activate` re-verifies the complete immutable set by exact
generation before it moves the pointer. The move is a compare-and-swap on the document's update time, so concurrent
activations cannot interleave. `rollback` re-verifies `previous` and swaps it back. A serving instance restores
whatever pointer it reads at start; an incomplete or malformed set can never become `active`.

The clients are injected (`ObjectStore`, Firestore client) so the logic is tested with in-process doubles; the real
Google adapters are thin and are exercised only by the operator's deployment.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..canonical import utc_now_iso
from .publication import EXPORT_KIND, EXPORT_VERSION, MANIFEST_NAME, PAYLOAD_NAME, RestoreRefused, sha256_file

PREFIX = "publications"
POINTER_DOC = "serving_publication"
HISTORY_KEEP = 10


class ActivationRefused(RuntimeError):
    pass


class GcsObjectStore:
    """Thin adapter over google-cloud-storage (only imported when used)."""

    def __init__(self, bucket: str, client=None):
        from google.cloud import storage
        self.bucket_name = bucket
        self.bucket = (client or storage.Client()).bucket(bucket)

    def put_new(self, name: str, path: Path, content_type: str) -> int:
        blob = self.bucket.blob(name)
        blob.upload_from_filename(str(path), content_type=content_type, if_generation_match=0)
        blob.reload()
        return int(blob.generation)

    def get(self, name: str, generation: int, dest: Path) -> None:
        self.bucket.blob(name, generation=int(generation)).download_to_filename(str(dest))

    def uri(self, name: str) -> str:
        return f"gs://{self.bucket_name}/{name}"


def upload_export(store, export_dir: Path) -> dict:
    export_dir = Path(export_dir)
    manifest = json.loads((export_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    eid = manifest["export_id"]
    record = {"export_id": eid, "publication_id": manifest["bindings"]["publication_id"],
              "release_id": manifest["bindings"]["release_id"], "uploaded_at": utc_now_iso()}
    for key, name, ctype in (("payload", PAYLOAD_NAME, "application/gzip"),
                             ("manifest", MANIFEST_NAME, "application/json")):     # manifest last
        obj = f"{PREFIX}/{eid}/{name}"
        gen = store.put_new(obj, export_dir / name, ctype)
        record[key] = {"object": obj, "uri": store.uri(obj), "generation": gen, "sha256": sha256_file(export_dir / name)}
    if record["payload"]["sha256"] != manifest["payload"]["sha256"]:
        raise ActivationRefused("local payload differs from its manifest")
    verify_remote(store, record)
    return record


def fetch(store, record: dict, dest_dir: Path) -> Path:
    """Download both objects by exact generation into dest_dir and verify their digests against the record."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for key, name in (("manifest", MANIFEST_NAME), ("payload", PAYLOAD_NAME)):
        store.get(record[key]["object"], record[key]["generation"], dest_dir / name)
        if sha256_file(dest_dir / name) != record[key]["sha256"]:
            raise RestoreRefused(f"{key} object generation {record[key]['generation']} digest differs from the pointer")
    return dest_dir


def verify_remote(store, record: dict) -> dict:
    """Download the exact generations to a temporary directory and verify the manifest/payload set end to end."""
    with tempfile.TemporaryDirectory() as tmp:
        d = fetch(store, record, Path(tmp))
        m = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
        problems = []
        if m.get("kind") != EXPORT_KIND or m.get("version") != EXPORT_VERSION:
            problems.append("not an artha-serving-export 1.0.0 manifest")
        if m.get("export_id") != record["export_id"]:
            problems.append("manifest export_id differs from the record")
        if sha256_file(d / PAYLOAD_NAME) != m["payload"]["sha256"]:
            problems.append("payload digest differs from the manifest")
        for k in ("publication_id", "release_id"):
            if m["bindings"].get(k) != record.get(k):
                problems.append(f"manifest {k} differs from the record")
        for k in ("bindings", "coverage", "cutoff", "model_provenance"):
            if not m.get(k):
                problems.append(f"manifest lacks {k}")
        if problems:
            raise ActivationRefused("; ".join(problems))
        return {"verified": True, "coverage": m["coverage"], "bindings": m["bindings"]}


# ------------------------------------------------------------------ Firestore pointer
def _pointer_ref(client, prefix: str = "artha"):
    return client.collection(f"{prefix}_control").document(POINTER_DOC)


def pointer_status(client, prefix: str = "artha") -> dict | None:
    snap = _pointer_ref(client, prefix).get()
    return snap.to_dict() if snap.exists else None


def _swap(client, prefix: str, expected_generation: int | None, build) -> dict:
    ref = _pointer_ref(client, prefix)
    snap = ref.get()
    cur = snap.to_dict() if snap.exists else None
    gen = 0 if cur is None else cur["generation"]           # 0 = no pointer yet
    if expected_generation is not None and gen != expected_generation:
        raise ActivationRefused(f"pointer generation is {gen}, expected {expected_generation}; nothing was moved")
    new = build(cur)
    new["generation"] = gen + 1
    new["updated_at"] = utc_now_iso()
    try:
        if cur is None:
            ref.create(new)                                   # fails if another activation created it first
        else:
            ref.update(new, option=client.write_option(last_update_time=snap.update_time))
    except Exception as exc:  # noqa: BLE001 - google.api_core Conflict / FailedPrecondition, or a double's error
        raise ActivationRefused(f"pointer changed concurrently; nothing was moved ({type(exc).__name__})") from exc
    return new


def activate(client, store, record: dict, *, expected_generation: int | None, prefix: str = "artha",
             note: str = "") -> dict:
    checked = verify_remote(store, record)            # the complete immutable set, by exact generation

    def build(cur):
        hist = list((cur or {}).get("history") or [])
        hist.append({"action": "activate", "export_id": record["export_id"], "at": utc_now_iso(), "note": note})
        if cur and cur.get("active", {}).get("export_id") == record["export_id"]:
            raise ActivationRefused(f"{record['export_id']} is already active")
        return {"active": record, "previous": (cur or {}).get("active"), "history": hist[-HISTORY_KEEP:]}
    new = _swap(client, prefix, expected_generation, build)
    return {"activated": record["export_id"], "generation": new["generation"], "coverage": checked["coverage"],
            "previous": (new.get("previous") or {}).get("export_id")}


def rollback(client, store, *, expected_generation: int | None, prefix: str = "artha", note: str = "") -> dict:
    cur = pointer_status(client, prefix)
    if not cur or not cur.get("previous"):
        raise ActivationRefused("no previous publication to roll back to")
    verify_remote(store, cur["previous"])

    def build(c):
        if c is None or not c.get("previous"):
            raise ActivationRefused("pointer changed: no previous publication")
        hist = list(c.get("history") or [])
        hist.append({"action": "rollback", "export_id": c["previous"]["export_id"], "at": utc_now_iso(), "note": note})
        return {"active": c["previous"], "previous": c["active"], "history": hist[-HISTORY_KEEP:]}
    new = _swap(client, prefix, expected_generation, build)
    return {"active": new["active"]["export_id"], "generation": new["generation"],
            "previous": new["previous"]["export_id"]}
