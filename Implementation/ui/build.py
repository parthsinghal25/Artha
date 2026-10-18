#!/usr/bin/env python3
"""Build a self-contained UI artifact in ./dist.

Copies the static frontend, the gateway server and the frozen contract files
(after verifying every digest against the adopted Shared 1.0.0 manifest).
Never copies tests, test doubles, specimens, contract examples or any data.

    python3 build.py                     gateway artifact (dist/, Docker build context)
    python3 build.py --hosting SERVICE   also dist/hosting/ + dist/firebase.json for Firebase Hosting,
                                         rewriting the gateway routes to Cloud Run service SERVICE
                                         (--region, default us-central1; --project, default below)
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

UI = Path(__file__).resolve().parent
SD = UI.parent.parent
DIST = UI / "dist"
EXPECTED = "b0b22c6742de24c4bc8643cb6c988624fa4b093db407896071a7e9a64acc1585"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


PROJECT = "project-c2a6c144-4e0d-440b-918"
# Same policy the gateway sends (server/devserver.py CSP); static files on Hosting must carry it too.
sys.path.insert(0, str(UI / "server"))
from devserver import CSP  # noqa: E402
# Routes the gateway owns dynamically. Everything else is static or the single page.
GATEWAY_ROUTES = ["/v1/**", "/config.json"]
SERVED_CONTRACTS = ["shared.schema.json", "measure-additions.json", "runtime.schema.json", "metric-registry.json",
                    "intelligence-v1.schema.json"]


def hosting(files: dict, service: str, region: str, project: str) -> None:
    """Static Hosting tree + firebase.json. API, config and asset routes go to the Cloud Run gateway."""
    out = DIST / "hosting"
    out.mkdir()
    for name in ("index.html", "styles.css", "favicon.svg"):
        shutil.copy2(UI / "public" / name, out / name)
    shutil.copytree(UI / "src", out / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(UI / "public" / "fonts", out / "fonts")      # self-hosted fonts (OFL), no font CDN
    (out / "contracts").mkdir()
    for name in SERVED_CONTRACTS:
        shutil.copy2(files[name][0], out / "contracts" / name)
    common = [{"key": "Content-Security-Policy", "value": CSP}, {"key": "X-Content-Type-Options", "value": "nosniff"},
              {"key": "Referrer-Policy", "value": "no-referrer"}, {"key": "Cache-Control", "value": "no-cache"}]
    run = {"serviceId": service, "region": region}
    cfg = {"hosting": {
        "site": project, "public": "hosting", "cleanUrls": False, "trailingSlash": False,
        "ignore": ["firebase.json", "**/.*"],
        "headers": [{"source": "**", "headers": common},
                    {"source": "/contracts/**", "headers": [{"key": "Content-Type", "value": "application/json"}]}],
        # API rewrites precede the SPA catch-all. Gateway responses are `no-store` and never cached at the edge.
        "rewrites": [{"source": r, "run": run} for r in GATEWAY_ROUTES] + [{"source": "**", "destination": "/index.html"}],
    }}
    (DIST / "firebase.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (DIST / ".firebaserc").write_text(json.dumps({"projects": {"default": project}}, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosting", metavar="SERVICE", help="also build Firebase Hosting output for this Cloud Run service")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--project", default=PROJECT)
    args = ap.parse_args()
    manifest_path = SD / "Shared" / "contracts" / "v1" / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["manifest_content_sha256"] != EXPECTED:
        print("Manifest is not the adopted Shared 1.0.0 package", file=sys.stderr)
        return 1
    files = {
        "shared.schema.json": (SD / "Shared/contracts/v1/shared.schema.json", manifest["files"]["shared.schema.json"]),
        "measure-additions.json": (SD / "Shared/contracts/v1/measure-additions.json", manifest["files"]["measure-additions.json"]),
        "runtime.schema.json": (SD / "Data/contracts/runtime.schema.json", manifest["pinned_data_baseline"]["runtime.schema.json"]),
        "metric-registry.json": (SD / "Data/contracts/metric-registry.json", manifest["pinned_data_baseline"]["metric-registry.json"]),
        # Backend-owned additive companion; digest recorded in server/devserver.py.
        "intelligence-v1.schema.json": (SD / "Implementation/ai-backend/sdbackend/intel/schemas/intelligence-v1.schema.json",
                                        "c5c2ed07ca5891620b73ceb2d2c1034890f23858a44f2270a4b964acc63a6df8"),
    }
    for name, (path, expected) in files.items():
        if sha(path) != expected:
            print(f"{name} digest mismatch", file=sys.stderr)
            return 1
    if DIST.exists():
        shutil.rmtree(DIST)
    for sub in ("public", "src", "server"):
        shutil.copytree(UI / sub, DIST / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (DIST / "contracts").mkdir()
    shutil.copy2(manifest_path, DIST / "contracts" / "MANIFEST.json")
    for name, (path, _) in files.items():
        shutil.copy2(path, DIST / "contracts" / name)
    # The gateway image (Docker context dist/) never carries the Hosting tree or its configuration.
    shutil.copy2(UI / "Dockerfile", DIST / "Dockerfile")      # dist/ is the image build context (gcloud builds submit dist)
    (DIST / ".dockerignore").write_text("hosting/\nfirebase.json\n.firebaserc\nBUILD-INFO.json\nDockerfile\n.dockerignore\n")
    if args.hosting:
        hosting(files, args.hosting, args.region, args.project)
    tree = sorted(str(f.relative_to(DIST)) for f in DIST.rglob("*") if f.is_file() and f.name != "BUILD-INFO.json")
    (DIST / "BUILD-INFO.json").write_text(json.dumps({
        "contract_version": "1.0.0", "manifest_digest": EXPECTED,
        "files": {name: exp for name, (_, exp) in files.items()},
        "hosting": {"service": args.hosting, "region": args.region, "project": args.project} if args.hosting else None,
        # Content digest of every built file: the exact UI build that is deployed.
        "build_sha256": hashlib.sha256("".join(f"{t}:{sha(DIST / t)}\n" for t in tree).encode()).hexdigest(),
    }, indent=2))
    print(f"Built {DIST}" + (f" (+ Hosting for Cloud Run service {args.hosting}, {args.region})" if args.hosting else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
