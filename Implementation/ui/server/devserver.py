#!/usr/bin/env python3
"""Static server and same-origin gateway for the complaints-leadership UI.

Standard library only. It serves the frontend, exposes the frozen Shared 1.0.0
and pinned Data v0.2 runtime schemas read-only (after verifying their digests
against the adopted manifest), and optionally forwards the authenticated
`POST /v1/operations` transport and opaque asset redemption to the AI/backend
service. It performs no calculation, extraction, authorisation or data access
of its own. Without an upstream it answers the operation path with a transport
"not configured" status so the UI renders an honest unavailable state.

Environment (no secret values belong here):
  SD_HOST / SD_PORT             bind address (default 127.0.0.1:5173)
  SD_API_UPSTREAM               backend origin, e.g. http://127.0.0.1:8080 (unset = not connected)
  SD_RELEASE_ID                 accepted release identifier supplied by the backend handoff
  SD_PREFERRED_VIEW_ID          optional preferred view identifier
  SD_ASSET_REDEEM_PATH          backend-documented path template containing {token}
                                (unset = binary delivery undocumented; viewers stay unavailable)
  SD_SERVE_TESTS=1              also serve /tests/ (test harness and isolated doubles)
  SD_EDGE_SESSION_COOKIE        hosted only: cookie name the edge forwards (Firebase Hosting forwards only
                                `__session`). Inbound it is renamed to SD_UPSTREAM_SESSION_COOKIE, and the
                                backend's Set-Cookie for that name is renamed back. Unset = no renaming.
  SD_UPSTREAM_SESSION_COOKIE    backend session cookie name (default sdb_session, backend api/auth.py)
  SD_SECURE_COOKIES=1           hosted (https) only: add `Secure` to forwarded Set-Cookie values lacking it
  PORT                          used when SD_PORT is unset (Cloud Run)
"""
from __future__ import annotations

import errno
import hashlib
import http.client
import json
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parent.parent
ARTHA = UI_ROOT.parent.parent
SHARED_V1 = ARTHA / "Shared" / "contracts" / "v1"
DATA_CONTRACTS = ARTHA / "Data" / "contracts"
EXPECTED_MANIFEST_DIGEST = "b0b22c6742de24c4bc8643cb6c988624fa4b093db407896071a7e9a64acc1585"
# Backend-owned additive intelligence companion 1.1.0 (two read operations). Served
# read-only, only when its bytes match the digest recorded at UI integration time.
INTEL_SCHEMA = ARTHA / "Implementation" / "ai-backend" / "sdbackend" / "intel" / "schemas" / "intelligence-v1.schema.json"
INTEL_SCHEMA_SHA256 = "c5c2ed07ca5891620b73ceb2d2c1034890f23858a44f2270a4b964acc63a6df8"
MANIFEST_FILE_SHA256 = "da1a71564d2aa2f75727034f92ac8e47b98553cb518f364a1e63faef50535bed"

OPERATION_PATH = "/v1/operations"
MAX_REQUEST_BYTES = 256 * 1024
UPSTREAM_TIMEOUT_S = 60

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",  # inline style attributes for chart geometry only
    "img-src 'self' blob: data:",
    "frame-src 'self' blob:",
    # The browser's built-in PDF viewer is governed by object-src, and a blob: document
    # inherits this policy. Allow only blob: URLs the app itself creates from bytes it
    # has already SHA-256-verified; no remote or same-origin plugin content.
    "object-src blob:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    # 'self' (not 'none'): a blob: PDF frame inherits this policy, and WebKit/Safari then
    # refuses to show it inside our own page. Other origins still cannot frame the app.
    "frame-ancestors 'self'",
])


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_verified_contracts() -> dict[str, bytes]:
    """Return schema bytes only if they match the frozen manifest exactly.

    In the repository the files are read from their frozen locations. A built
    artifact sets SD_CONTRACT_ROOT to a flat directory produced by build.py.
    """
    flat = os.environ.get("SD_CONTRACT_ROOT")
    shared_dir = Path(flat) if flat else SHARED_V1
    data_dir = Path(flat) if flat else DATA_CONTRACTS
    manifest = json.loads((shared_dir / "MANIFEST.json").read_text())
    if manifest.get("manifest_content_sha256") != EXPECTED_MANIFEST_DIGEST or sha256_file(shared_dir / "MANIFEST.json") != MANIFEST_FILE_SHA256:
        raise SystemExit("Shared manifest digest differs from adopted 1.0.0; refusing to serve contracts.")
    checks = {
        "shared.schema.json": (shared_dir / "shared.schema.json", manifest["files"]["shared.schema.json"]),
        "runtime.schema.json": (data_dir / "runtime.schema.json", manifest["pinned_data_baseline"]["runtime.schema.json"]),
        "measure-additions.json": (shared_dir / "measure-additions.json", manifest["files"]["measure-additions.json"]),
        "metric-registry.json": (data_dir / "metric-registry.json", manifest["pinned_data_baseline"]["metric-registry.json"]),
    }
    if not flat:
        checks["CONTRACT-EXAMPLES.json"] = (SHARED_V1 / "CONTRACT-EXAMPLES.json", manifest["files"]["CONTRACT-EXAMPLES.json"])
    out: dict[str, bytes] = {}
    for name, (path, expected) in checks.items():
        actual = sha256_file(path)
        if actual != expected:
            raise SystemExit(f"{name} digest {actual} does not match frozen manifest {expected}.")
        out[name] = path.read_bytes()
    intel = Path(flat) / "intelligence-v1.schema.json" if flat else INTEL_SCHEMA
    if intel.is_file():
        actual = sha256_file(intel)
        if actual != INTEL_SCHEMA_SHA256:
            raise SystemExit(f"intelligence-v1.schema.json digest {actual} differs from the recorded companion 1.1.0 {INTEL_SCHEMA_SHA256}.")
        out["intelligence-v1.schema.json"] = intel.read_bytes()
    return out


def _cookie_names() -> tuple[str | None, str]:
    return os.environ.get("SD_EDGE_SESSION_COOKIE") or None, os.environ.get("SD_UPSTREAM_SESSION_COOKIE") or "sdb_session"


def edge_to_upstream_cookie(header: str) -> str:
    """Rename the edge-forwarded session cookie (e.g. Firebase `__session`) to the backend's own name."""
    edge, upstream = _cookie_names()
    if not edge:
        return header
    parts = []
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if name == upstream:
            continue            # a stray backend-named cookie never overrides the edge session
        parts.append(f"{upstream}={value}" if name == edge and sep else part.strip())
    return "; ".join(p for p in parts if p)


def upstream_to_edge_set_cookie(value: str) -> str:
    """Rename the backend session Set-Cookie to the edge name; optionally require Secure (https hosting)."""
    edge, upstream = _cookie_names()
    name, sep, rest = value.partition("=")
    if edge and sep and name.strip() == upstream:
        value = f"{edge}={rest}"
    if os.environ.get("SD_SECURE_COOKIES") == "1" and "secure" not in [a.strip().lower() for a in value.split(";")[1:]]:
        value += "; Secure"
    return value


def runtime_config() -> dict:
    redeem = os.environ.get("SD_ASSET_REDEEM_PATH") or None
    signin = os.environ.get("SD_SIGNIN_URL") or None
    if signin and not signin.startswith(("http://", "https://")):
        raise SystemExit("SD_SIGNIN_URL must be an http(s) URL.")
    if redeem and "{token}" not in redeem:
        raise SystemExit("SD_ASSET_REDEEM_PATH must contain {token}.")
    return {
        "contract_version": "1.0.0",
        "intelligence_schema": "1.1.0" if "intelligence-v1.schema.json" in Handler.contracts else None,
        "manifest_digest": EXPECTED_MANIFEST_DIGEST,
        "operation_path": OPERATION_PATH,
        "transport_configured": bool(os.environ.get("SD_API_UPSTREAM")),
        "release_id": os.environ.get("SD_RELEASE_ID") or None,
        "preferred_view_id": os.environ.get("SD_PREFERRED_VIEW_ID") or None,
        "asset_redeem_path": redeem,
        # Backend-owned sign-in page the UI links to on 401/403 (no credentials pass through the UI).
        "signin_url": signin,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ArthaUI/1.0"
    contracts: dict[str, bytes] = {}
    config: dict = {}
    serve_tests = False

    # ---- helpers -------------------------------------------------------
    def _headers(self, status: int, ctype: str, length: int, extra=None, csp: str | None = CSP) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra.items() if isinstance(extra, dict) else (extra or [])):
            self.send_header(k, v)
        self.end_headers()

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self._headers(status, ctype, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def log_message(self, fmt, *args):  # keep request bodies/tokens out of logs
        path = urllib.parse.urlsplit(self.path).path
        if self.config.get("asset_redeem_path") and path.startswith(self._redeem_prefix()):
            path = self._redeem_prefix() + "<redacted>"
        sys.stderr.write(f"{self.command} {path} {args[1] if len(args) > 1 else ''}\n")

    def _redeem_prefix(self) -> str:
        tmpl = self.config.get("asset_redeem_path") or ""
        return tmpl.split("{token}")[0] if tmpl else "\0"

    # ---- routing -------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/config.json":
            return self._json(200, self.config)
        if path == "/healthz":
            # Gateway liveness only (no upstream call); backend readiness stays the backend's own /readyz.
            return self._json(200, {"status": "ok", "transport_configured": self.config.get("transport_configured")})
        if path.startswith("/contracts/"):
            name = path[len("/contracts/"):]
            if name in self.contracts and name != "CONTRACT-EXAMPLES.json":
                return self._send(200, self.contracts[name], "application/json")
            return self._send(404, b"not found", "text/plain")
        if self.config.get("asset_redeem_path") and path.startswith(self._redeem_prefix()):
            return self._proxy()
        if path.startswith("/src/"):
            return self._static(UI_ROOT / "src", path[len("/src/"):])
        if path == "/tests/contract-examples.json" and self.serve_tests and "CONTRACT-EXAMPLES.json" in self.contracts:
            # Isolated contract specimens for the test suite only; never served to the app.
            return self._send(200, self.contracts["CONTRACT-EXAMPLES.json"], "application/json")
        if path.startswith("/tests/") and self.serve_tests:
            return self._static(UI_ROOT / "tests", path[len("/tests/"):])
        if path.startswith("/assets/"):
            return self._static(UI_ROOT / "public" / "assets", path[len("/assets/"):])
        if path.startswith("/fonts/"):
            return self._static(UI_ROOT / "public" / "fonts", path[len("/fonts/"):])
        if path in ("/styles.css", "/favicon.svg"):
            return self._static(UI_ROOT / "public", path[1:])
        # Dev/test only: once the visual harness is opened it sets a session cookie, so a
        # browser reload of an app route stays in the harness instead of dropping into
        # the real (release-less) app. Never active without SD_SERVE_TESTS=1.
        if self.serve_tests and "sd_harness=1" in (self.headers.get("Cookie") or ""):
            return self._static(UI_ROOT / "tests", "visual.html")
        # application routes resolve to the single page
        return self._static(UI_ROOT / "public", "index.html")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/tests/report" and self.serve_tests and os.environ.get("SD_TEST_REPORT"):
            length = min(int(self.headers.get("Content-Length") or 0), MAX_REQUEST_BYTES)
            Path(os.environ["SD_TEST_REPORT"]).write_bytes(self.rfile.read(length))
            return self._json(200, {"stored": True})
        if path != OPERATION_PATH and not (self.config.get("asset_redeem_path") and path.startswith(self._redeem_prefix())):
            return self._send(404, b"not found", "text/plain")
        if not self.config.get("transport_configured"):
            # Not a contract response: transport-level signal that no backend is connected.
            return self._json(503, {"transport": "not_configured"})
        return self._proxy()

    def _static(self, root: Path, rel: str):
        target = (root / urllib.parse.unquote(rel)).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            return self._send(404, b"not found", "text/plain")
        if not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".js", ".mjs"):
            ctype = "text/javascript"
        return self._send(200, target.read_bytes(), ctype)

    def _proxy(self):
        upstream = urllib.parse.urlsplit(os.environ["SD_API_UPSTREAM"])
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BYTES:
            return self._json(413, {"transport": "request_too_large"})
        body = self.rfile.read(length) if length else None
        conn_cls = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(upstream.netloc, timeout=UPSTREAM_TIMEOUT_S)
        fwd = {}
        for h in ("Content-Type", "Cookie", "Authorization", "Range", "If-Range", "X-CSRF-Token", "Accept"):
            if self.headers.get(h):
                fwd[h] = self.headers[h]
        if "Cookie" in fwd:
            fwd["Cookie"] = edge_to_upstream_cookie(fwd["Cookie"])
        try:
            conn.request(self.command, self.path, body=body, headers=fwd)
            resp = conn.getresponse()
            data = resp.read()
        except OSError:
            return self._json(502, {"transport": "upstream_unreachable"})
        finally:
            conn.close()
        passthrough = []
        is_asset = self.command in ("GET", "HEAD")
        names = ["Content-Range", "Accept-Ranges", "Content-Disposition", "ETag"]
        if is_asset:
            # Asset redemption (backend handoff §2a U2): keep the backend's integrity headers.
            names += ["X-Content-SHA256", "X-Asset-Version-Id"]
        for h in names:
            v = resp.headers.get(h)
            if v is not None:
                passthrough.append((h, v))
        # Every Set-Cookie is kept (session and the readable double-submit CSRF cookie are separate headers).
        for v in resp.headers.get_all("Set-Cookie") or []:
            passthrough.append(("Set-Cookie", upstream_to_edge_set_cookie(v)))
        # Asset bytes keep the backend's own isolation policy (e.g. `sandbox`), or none
        # when the backend sends none (PDFs, so the built-in viewer works). Operations
        # responses keep the application policy.
        csp = resp.headers.get("Content-Security-Policy") if is_asset else CSP
        self._headers(resp.status, resp.headers.get("Content-Type", "application/octet-stream"), len(data), passthrough, csp=csp)
        if self.command != "HEAD":
            self.wfile.write(data)


def main() -> None:
    Handler.contracts = load_verified_contracts()
    Handler.config = runtime_config()
    Handler.serve_tests = os.environ.get("SD_SERVE_TESTS") == "1"
    host = os.environ.get("SD_HOST", "127.0.0.1")
    port = int(os.environ.get("SD_PORT") or os.environ.get("PORT") or "5173")
    ThreadingHTTPServer.request_queue_size = 128  # browsers fetch many ES modules in parallel
    ThreadingHTTPServer.daemon_threads = True
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            sys.exit(f"Port {port} on {host} is already in use — the UI server is probably already running at "
                     f"http://{host}:{port}/. Open that, stop the other process, or choose another port with SD_PORT=5174.")
        raise
    print(f"UI on http://{host}:{port}  contracts verified ({EXPECTED_MANIFEST_DIGEST[:12]}…)  "
          f"upstream={'configured' if Handler.config['transport_configured'] else 'not configured'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.", flush=True)
        httpd.server_close()


if __name__ == "__main__":
    main()
