#!/usr/bin/env python3
"""Assemble the backend image build context `Implementation/cloud/dist/backend/` from an explicit allow-list.

    python3 Implementation/cloud/build_backend_context.py [--replace]

Copied:
* `ai-backend/sdbackend/**` (Python sources and intel schemas; no __pycache__);
* `ai-backend/requirements/cloud.lock`;
* the frozen Shared contract files pinned by `Shared/contracts/v1/MANIFEST.json`, with the Data baseline files it
  pins;
* `backend-image/Dockerfile` and `.dockerignore`.

Nothing else: no stores, exports, .env, login codes, model weights, reports or authoring material.
`BUILD-INFO.json` records every file's SHA-256 and the context digest; the image tag is the first 12 hex digits of
that digest. Standard library only, deterministic for identical inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

CLOUD = Path(__file__).resolve().parent
IMPL = CLOUD.parent
BACKEND = IMPL / "ai-backend"
SHARED = IMPL.parent / "Shared" / "contracts" / "v1"
DATA = IMPL.parent / "Data" / "contracts"
OUT = CLOUD / "dist" / "backend"
REGISTRY = "us-central1-docker.pkg.dev/project-c2a6c144-4e0d-440b-918/superdashboard"
BASE_IMAGE = "python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
FORBIDDEN = (".env", "login-codes", ".sqlite3", "/var/", "/reports/", "safetensors", ".gguf")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replace", action="store_true", help="replace an existing dist/backend")
    a = ap.parse_args()
    if OUT.exists():
        if not a.replace:
            sys.exit(f"{OUT} exists; pass --replace to rebuild it")
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for p in sorted((BACKEND / "sdbackend").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            dest = OUT / p.relative_to(BACKEND)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
    (OUT / "requirements").mkdir()
    shutil.copyfile(BACKEND / "requirements" / "cloud.lock", OUT / "requirements" / "cloud.lock")
    manifest = json.loads((SHARED / "MANIFEST.json").read_text(encoding="utf-8"))
    (OUT / "contracts" / "shared").mkdir(parents=True)
    (OUT / "contracts" / "data").mkdir(parents=True)
    shutil.copyfile(SHARED / "MANIFEST.json", OUT / "contracts" / "shared" / "MANIFEST.json")
    for name in manifest["files"]:
        shutil.copyfile(SHARED / name, OUT / "contracts" / "shared" / name)
    for name in manifest["pinned_data_baseline"]:
        shutil.copyfile(DATA / name, OUT / "contracts" / "data" / name)
    for name in ("Dockerfile", ".dockerignore"):
        shutil.copyfile(CLOUD / "backend-image" / name, OUT / name)
    files = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob("*")) if p.is_file()}
    bad = [f for f in files if any(x in f"/{f}" for x in FORBIDDEN)]
    if bad:
        sys.exit(f"forbidden files in context: {bad}")
    digest = hashlib.sha256("".join(f"{k}\n{v}\n" for k, v in sorted(files.items())).encode()).hexdigest()
    tag = digest[:12]
    info = {"component": "artha-backend (read-serving sidecar)", "context_sha256": digest, "tag": tag,
            "image": f"{REGISTRY}/artha-backend:{tag}", "base_image": BASE_IMAGE,
            "contract_manifest_sha256": manifest["manifest_content_sha256"],
            "cloud_lock_sha256": files["requirements/cloud.lock"], "files": files,
            "excluded": ["stores/exports (restored at start from private Storage)", ".env / keys", "login codes",
                         "MLX / Apple Vision / pyobjc / google-genai / model weights", "reports, tests, authoring data"]}
    (OUT / "BUILD-INFO.json").write_text(json.dumps(info, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"context": str(OUT), "files": len(files) + 1, "tag": tag, "image": info["image"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
