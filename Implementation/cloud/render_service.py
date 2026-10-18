#!/usr/bin/env python3
"""Render `Implementation/cloud/service.yaml`: the Cloud Run service `superdashboard-serving`.

    python3 Implementation/cloud/render_service.py --export-manifest <export_dir>/ARTHA-EXPORT-MANIFEST.json

The service has two containers in one instance. The UI-owned gateway is the ingress on `$PORT` (8080). The
backend sidecar is internal on 127.0.0.1:8091 and restores the activated publication before it listens. The image
tags come from `cloud/dist/backend/BUILD-INFO.json` and `ui/dist/BUILD-INFO.json`. The release ID and the expected
export ID come from the export manifest. Re-render and `gcloud run services replace` it to adopt a successor or roll
back. Standard library only; the output is plain YAML written as JSON-compatible text.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CLOUD = Path(__file__).resolve().parent
UI_DIST = CLOUD.parent / "ui" / "dist"
PROJECT = "project-c2a6c144-4e0d-440b-918"
REGION = "us-central1"
SERVICE = "superdashboard-serving"
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/superdashboard"
BUCKET = "superdashboard-c2a6c144-artifacts"
READER_SA = f"superdashboard-reader@{PROJECT}.iam.gserviceaccount.com"
ORIGINS = [f"https://{PROJECT}.web.app", f"https://{PROJECT}.firebaseapp.com"]


def env(d: dict) -> list:
    return [{"name": k, "value": str(v)} for k, v in d.items()]


def render(manifest: dict, backend_tag: str, gateway_tag: str, backend_memory: str = "512Mi",
           startup_failures: int = 60) -> dict:
    gateway_env = {"SD_HOST": "0.0.0.0", "SD_CONTRACT_ROOT": "/app/contracts",
                   "SD_API_UPSTREAM": "http://127.0.0.1:8091", "SD_RELEASE_ID": manifest["bindings"]["release_id"],
                   "SD_ASSET_REDEEM_PATH": "/v1/assets/{token}", "SD_EDGE_SESSION_COOKIE": "__session",
                   "SD_SECURE_COOKIES": "1"}
    backend_env = {"ARTHA_PUBLICATION_SOURCE": "firestore", "ARTHA_BUCKET": BUCKET,
                   "ARTHA_EXPECTED_EXPORT_ID": manifest["export_id"], "ARTHA_RESTORE_ROOT": "/tmp/artha",
                   "SDB_SERVING_STATE": "firestore", "SDB_FIRESTORE_PROJECT": PROJECT,
                   "SDB_FIRESTORE_DATABASE": "(default)", "SDB_FIRESTORE_PREFIX": "artha", "SDB_HOSTED_DEMO": "1",
                   "SDB_NO_DOTENV": "1", "SDB_HOST": "0.0.0.0", "SDB_PORT": "8091", "SDB_COOKIE_SECURE": "1",
                   "SDB_SESSION_TTL_SECONDS": "28800", "SDB_UI_ORIGIN": ORIGINS[0],
                   "SDB_ALLOWED_ORIGINS": ",".join(ORIGINS), "SDB_LOG_LEVEL": "warning"}
    return {
        "apiVersion": "serving.knative.dev/v1", "kind": "Service",
        "metadata": {"name": SERVICE, "labels": {"cloud.googleapis.com/location": REGION, "app": "artha"},
                     "annotations": {"run.googleapis.com/ingress": "all"}},
        "spec": {"template": {
            "metadata": {"labels": {"artha-export": manifest["export_id"].lower()[:63]},
                         "annotations": {"autoscaling.knative.dev/minScale": "0",
                                         "autoscaling.knative.dev/maxScale": "2",
                                         "run.googleapis.com/cpu-throttling": "true",       # request-based billing
                                         "run.googleapis.com/startup-cpu-boost": "true",
                                         "run.googleapis.com/container-dependencies": json.dumps({"ui-gateway": ["backend"]})}},
            "spec": {
                "serviceAccountName": READER_SA, "containerConcurrency": 40, "timeoutSeconds": 60,
                "containers": [
                    {"name": "ui-gateway", "image": f"{REGISTRY}/ui-gateway:{gateway_tag}",
                     "ports": [{"name": "http1", "containerPort": 8080}], "env": env(gateway_env),
                     "resources": {"limits": {"cpu": "1", "memory": "256Mi"}},
                     "startupProbe": {"httpGet": {"path": "/healthz", "port": 8080}, "periodSeconds": 2,
                                      "timeoutSeconds": 2, "failureThreshold": 30}},
                    {"name": "backend", "image": f"{REGISTRY}/artha-backend:{backend_tag}", "env": env(backend_env),
                     # the restored store lives in the in-memory filesystem, so it counts against this limit
                     "resources": {"limits": {"cpu": "1", "memory": backend_memory}},
                     "startupProbe": {"httpGet": {"path": "/healthz", "port": 8091}, "periodSeconds": 2,
                                      "timeoutSeconds": 2, "failureThreshold": startup_failures},
                     "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8091}, "periodSeconds": 30,
                                       "timeoutSeconds": 3, "failureThreshold": 3}}]}}}}


def to_yaml(x, indent=0) -> str:
    """Minimal YAML emitter (block style; scalars JSON-quoted, which YAML accepts)."""
    pad = "  " * indent
    if isinstance(x, dict):
        out = []
        for k, v in x.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n{to_yaml(v, indent + 1)}")
            else:
                out.append(f"{pad}{k}: {json.dumps(v)}")
        return "\n".join(out)
    if isinstance(x, list):
        out = []
        for v in x:
            body = to_yaml(v, indent + 1)
            out.append(f"{pad}- {body.lstrip()}" if isinstance(v, (dict, list)) else f"{pad}- {json.dumps(v)}")
        return "\n".join(out)
    return f"{pad}{json.dumps(x)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-manifest", required=True)
    ap.add_argument("--out", default=str(CLOUD / "service.yaml"))
    ap.add_argument("--backend-memory", default=None, help="default: 512Mi, or 2Gi when the export restores >200 MB")
    ap.add_argument("--startup-failures", type=int, default=None, help="startup probe attempts x 2 s (default 60, 110 for large exports)")
    a = ap.parse_args()
    manifest = json.loads(Path(a.export_manifest).read_text(encoding="utf-8"))
    backend = json.loads((CLOUD / "dist" / "backend" / "BUILD-INFO.json").read_text(encoding="utf-8"))
    ui = json.loads((UI_DIST / "BUILD-INFO.json").read_text(encoding="utf-8"))
    large = (manifest.get("payload") or {}).get("uncompressed_bytes", 0) > 200_000_000
    svc = render(manifest, backend["tag"], ui["build_sha256"][:8], a.backend_memory or ("2Gi" if large else "512Mi"),
                 a.startup_failures or (110 if large else 60))
    header = (f"# GENERATED by render_service.py — do not hand-edit. export {manifest['export_id']} "
              f"({manifest['bindings']['publication_id']}, {manifest['bindings']['release_id']}); "
              f"backend context {backend['context_sha256'][:12]}; UI build {ui['build_sha256'][:12]}\n")
    Path(a.out).write_text(header + to_yaml(svc) + "\n", encoding="utf-8")
    print(json.dumps({"written": a.out, "backend_image": svc["spec"]["template"]["spec"]["containers"][1]["image"],
                      "gateway_image": svc["spec"]["template"]["spec"]["containers"][0]["image"],
                      "expected_export_id": manifest["export_id"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
