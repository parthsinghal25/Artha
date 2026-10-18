#!/usr/bin/env python3
"""Gateway checks against a stub upstream (standard library only).

Covers the backend handoff's browser-seam items owned by the UI gateway
(INTEGRATION-ENVIRONMENT §2a): the session cookie and X-CSRF-Token reach the
backend on operations POSTs (U1), and asset redemption keeps the backend's own
Content-Security-Policy (or none, for PDFs) plus X-Content-SHA256 and
X-Asset-Version-Id (U2). The stub is not the backend; it only records headers.
"""
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UI = Path(__file__).resolve().parent.parent
SEEN: list[dict] = []
HTML_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; frame-ancestors 'self'"


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.append({"path": self.path, "headers": dict(self.headers), "body": body})
        out = json.dumps({"stub": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        # the backend issues its session and readable CSRF cookies as two separate headers
        self.send_header("Set-Cookie", "sdb_session=s-new; HttpOnly; Path=/; SameSite=strict")
        self.send_header("Set-Cookie", "sdb_csrf=t-new; Path=/; SameSite=strict")
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        SEEN.append({"path": self.path, "headers": dict(self.headers)})
        pdf = self.path.endswith("/pdf-token")
        data = b"%PDF-1.4 stub" if pdf else b"<p>preview</p>"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf" if pdf else "text/html")
        self.send_header("Content-Length", str(len(data)))
        if not pdf:
            self.send_header("Content-Security-Policy", HTML_CSP)
        self.send_header("X-Content-SHA256", "a" * 64)
        self.send_header("X-Asset-Version-Id", "asset-1")
        self.send_header("ETag", '"' + "a" * 64 + '"')
        self.end_headers()
        self.wfile.write(data)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    up_port, ui_port = free_port(), free_port()
    upstream = ThreadingHTTPServer(("127.0.0.1", up_port), Stub)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    env = {**os.environ, "SD_PORT": str(ui_port), "SD_API_UPSTREAM": f"http://127.0.0.1:{up_port}",
           "SD_RELEASE_ID": "stub-release", "SD_ASSET_REDEEM_PATH": "/v1/assets/{token}",
           "SD_SIGNIN_URL": f"http://127.0.0.1:{up_port}/v1/session/signin"}
    env.pop("SD_SERVE_TESTS", None)
    server = subprocess.Popen([sys.executable, str(UI / "server" / "devserver.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", ui_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        def req(method, path, headers=None, body=None):
            c = http.client.HTTPConnection("127.0.0.1", ui_port, timeout=5)
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            data = r.read()
            c.close()
            return r, data

        r, data = req("GET", "/config.json")
        cfg = json.loads(data)
        check("config exposes the backend sign-in page and no credentials", cfg.get("signin_url", "").endswith("/v1/session/signin") and "csrf" not in json.dumps(cfg).lower())

        r, data = req("GET", "/contracts/intelligence-v1.schema.json")
        check("companion 1.1.0 schema served read-only with its recorded digest", r.status == 200 and cfg.get("intelligence_schema") == "1.1.0"
              and hashlib.sha256(data).hexdigest() == "c5c2ed07ca5891620b73ceb2d2c1034890f23858a44f2270a4b964acc63a6df8", f"{r.status} {cfg.get('intelligence_schema')}")

        r, _ = req("POST", "/v1/operations", {"Content-Type": "application/json", "Cookie": "sdb_session=s1; sdb_csrf=tok-1", "X-CSRF-Token": "tok-1",
                                              "X-Forwarded-Host": "evil.example", "Origin": "http://evil.example"}, b"{}")
        h = SEEN[-1]["headers"]
        check("U1: session cookie and X-CSRF-Token forwarded on operations POST", h.get("X-CSRF-Token") == "tok-1" and "sdb_session=s1" in h.get("Cookie", ""), str(h))
        check("U1: X-Forwarded-Host and Origin are not forwarded as authority", "X-Forwarded-Host" not in h and "Origin" not in h, str(h))
        check("operations responses keep the application CSP", "script-src 'self'" in (r.getheader("Content-Security-Policy") or ""))
        cookies = r.msg.get_all("Set-Cookie") or []
        check("every backend Set-Cookie is passed through (session and CSRF), names unchanged locally",
              [c.split("=")[0] for c in cookies] == ["sdb_session", "sdb_csrf"] and not any("Secure" in c for c in cookies), str(cookies))

        r, _ = req("GET", "/v1/assets/html-token", {"Cookie": "sdb_session=s1"})
        check("U2: HTML asset keeps the backend's sandbox CSP unchanged", r.getheader("Content-Security-Policy") == HTML_CSP, r.getheader("Content-Security-Policy"))
        check("U2: X-Content-SHA256 and X-Asset-Version-Id passed through", r.getheader("X-Content-SHA256") == "a" * 64 and r.getheader("X-Asset-Version-Id") == "asset-1")
        r, _ = req("GET", "/v1/assets/pdf-token", {"Cookie": "sdb_session=s1"})
        check("U2: PDF asset carries no CSP when the backend sends none", r.getheader("Content-Security-Policy") is None, r.getheader("Content-Security-Policy"))
        # Hosted mode: Firebase Hosting forwards only `__session`; Cloud Run supplies PORT.
        edge_port = free_port()
        env2 = {k: v for k, v in env.items() if k != "SD_PORT"}
        env2.update({"PORT": str(edge_port), "SD_EDGE_SESSION_COOKIE": "__session", "SD_SECURE_COOKIES": "1"})
        edge = subprocess.Popen([sys.executable, str(UI / "server" / "devserver.py")], env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", edge_port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            c = http.client.HTTPConnection("127.0.0.1", edge_port, timeout=5)
            c.request("GET", "/healthz")
            hr = c.getresponse(); hb = json.loads(hr.read())
            check("hosted: PORT binding and gateway /healthz (no upstream call)", hr.status == 200 and hb.get("status") == "ok", str(hb))
            c.request("POST", "/v1/operations", body=b"{}", headers={"Content-Type": "application/json", "Cookie": "__session=s1; sdb_session=stale", "X-CSRF-Token": "tok-1"})
            er = c.getresponse(); er.read(); c.close()
            h = SEEN[-1]["headers"]
            check("hosted: edge `__session` reaches the backend as its own session cookie (stray name dropped)",
                  h.get("Cookie") == "sdb_session=s1" and h.get("X-CSRF-Token") == "tok-1", str(h.get("Cookie")))
            cookies = er.msg.get_all("Set-Cookie") or []
            check("hosted: backend session Set-Cookie renamed to `__session`, CSRF kept, both Secure",
                  [x.split("=")[0] for x in cookies] == ["__session", "sdb_csrf"] and all(x.endswith("; Secure") for x in cookies), str(cookies))
        finally:
            edge.terminate()
    finally:
        server.terminate()
        upstream.shutdown()
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  gateway: {name}" + ("" if passed else f"\n      {detail}"))
    failed = sum(1 for _, p, _ in results if not p)
    print(f"\ngateway {len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
