"""Read-only serving entrypoint for Cloud Run (also runnable locally): restore, verify, then serve.

    python -m sdbackend.cloud.boot

1. Resolve the publication from `ARTHA_PUBLICATION_SOURCE`:
   * `firestore`: read the active pointer (`<prefix>_control/serving_publication`), then download both objects by
     exact generation from `ARTHA_BUCKET` and verify their SHA-256 against the pointer. If the revision pins
     `ARTHA_EXPECTED_EXPORT_ID`, the pointer must name exactly that export or the instance refuses to start.
   * `local:<export_dir>`: a local export directory (container smoke tests).
2. Restore into a fresh directory under `ARTHA_RESTORE_ROOT` (default `/tmp/artha`; on Cloud Run this is the
   instance's in-memory filesystem: a disposable serving copy, never authoritative storage). Every file, blob,
   binding and the backend read path is verified (publication.restore_export).
3. Only then start the API on `SDB_HOST:SDB_PORT` (default 127.0.0.1:8091, internal to the Cloud Run instance).
   It runs in hosted zero-login mode, with shared request state in Firestore (`SDB_SERVING_STATE`).

Any failure exits non-zero before the port opens, so Cloud Run never routes to a half-restored instance and keeps
the previous revision serving. No model provider is configured and no worker is started; reads never call a model.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import sys
import time
import uuid
from pathlib import Path

REVIEW_BYPASS_FLAGS = ("SDB_ALLOW_UNREVIEWED_SOURCES", "SDB_ALLOW_SPECIMEN_ACCEPTANCE", "SDB_REVIEWER_REGISTRY")
MODEL_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def log(event: str, **fields) -> None:
    print(json.dumps({"severity": "INFO", "component": "artha-backend-boot", "event": event, **fields}), flush=True)


def _du(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _rss_bytes() -> int:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024


def resolve_and_restore() -> dict:
    from .publication import restore_export
    t0 = time.monotonic()
    source = os.environ.get("ARTHA_PUBLICATION_SOURCE", "firestore")
    root = Path(os.environ.get("ARTHA_RESTORE_ROOT", "/tmp/artha"))
    work = root / f"instance-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    if work.exists():
        raise SystemExit(f"restore directory {work} already exists")
    if source.startswith("local:"):
        export_dir, manifest_sha, pointer = Path(source[6:]), None, None
    elif source == "firestore":
        from ..api.state import firestore_client
        from .remote import GcsObjectStore, fetch, pointer_status
        bucket = os.environ.get("ARTHA_BUCKET")
        if not bucket:
            raise SystemExit("ARTHA_BUCKET is required with ARTHA_PUBLICATION_SOURCE=firestore")
        pointer = pointer_status(firestore_client(), os.environ.get("SDB_FIRESTORE_PREFIX", "artha"))
        if not pointer or not pointer.get("active"):
            raise SystemExit("no active publication pointer: upload and activate an export first (DEPLOYMENT-RUNBOOK)")
        record = pointer["active"]
        expected = os.environ.get("ARTHA_EXPECTED_EXPORT_ID")
        if expected and record["export_id"] != expected:
            raise SystemExit(f"active pointer names {record['export_id']} but this revision expects {expected}; "
                             "activate that export or deploy a revision for the active one (DEPLOYMENT-RUNBOOK)")
        export_dir = fetch(GcsObjectStore(bucket), record, work / "download")
        manifest_sha = record["manifest"]["sha256"]
    else:
        raise SystemExit(f"unknown ARTHA_PUBLICATION_SOURCE {source!r}")
    receipt = restore_export(export_dir, work / "serving", manifest_sha256=manifest_sha)
    if source == "firestore":
        shutil.rmtree(work / "download")                     # keep only the restored serving copy in memory
    receipt["boot"] = {"source": "firestore-pointer" if pointer else source,
                       "pointer_generation": (pointer or {}).get("generation"),
                       "restore_seconds": round(time.monotonic() - t0, 2),
                       "restored_bytes": _du(work / "serving")}
    return receipt


def main() -> int:
    for k in REVIEW_BYPASS_FLAGS + MODEL_KEYS:
        os.environ.pop(k, None)                              # serving never bypasses review or holds model keys
    os.environ["SDB_NO_DOTENV"] = "1"                        # never load a local .env (keys, flags) into serving
    os.environ.setdefault("SDB_HOSTED_DEMO", "1")
    os.environ.setdefault("SDB_SERVING_STATE", "firestore")
    receipt = resolve_and_restore()
    from .publication import serving_env
    os.environ.update({k: v for k, v in serving_env(receipt).items() if v})
    host, port = os.environ.get("SDB_HOST", "127.0.0.1"), int(os.environ.get("SDB_PORT", "8091"))
    from ..api.app import create_app
    from ..runtime import make_runtime
    rt = make_runtime()
    app = create_app(rt)
    log("restored_and_verified", export_id=receipt["export_id"], publication_id=receipt["bindings"]["publication_id"],
        release_id=receipt["bindings"]["release_id"], coverage=receipt["coverage"], cutoff=receipt["cutoff"],
        serving_state=rt.state.kind, listen=f"{host}:{port}", peak_rss_bytes=_rss_bytes(), **receipt["boot"])
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("SDB_LOG_LEVEL", "warning"),
                proxy_headers=False, server_header=False, timeout_graceful_shutdown=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
