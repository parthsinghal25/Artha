#!/usr/bin/env python3
"""LOCAL EMULATION ONLY — not Firebase. Serves dist/hosting with the generated dist/firebase.json.

Purpose: check the built Hosting tree and its rewrites against the real gateway/backend before a deploy,
including the one Firebase behaviour that matters for transport: requests rewritten to Cloud Run carry
only the `__session` cookie (all other cookies are stripped). Static headers come from firebase.json.

    EMU_UPSTREAM=http://127.0.0.1:18080 EMU_HOST=::1 EMU_PORT=15176 python3 tests/hosting_emulator.py

EMU_UPSTREAM is the gateway standing in for the Cloud Run service named in firebase.json.
Standard library only; never part of a build.
"""
from __future__ import annotations

import fnmatch
import http.client
import json
import mimetypes
import os
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIST = Path(os.environ.get("EMU_DIST") or Path(__file__).resolve().parent.parent / "dist")
CFG = json.loads((DIST / "firebase.json").read_text())["hosting"]
PUBLIC = (DIST / CFG["public"]).resolve()
FORWARD = ("Content-Type", "Accept", "Range", "If-Range", "X-CSRF-Token", "Content-Length")


def glob(src: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path, src.replace("**", "*")) or path == src


class Emu(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {urllib.parse.urlsplit(self.path).path.split('/v1/assets/')[0]} {args[1] if len(args) > 1 else ''}\n")

    def _static_headers(self, path: str) -> list[tuple[str, str]]:
        out = {}
        for rule in CFG.get("headers", []):
            if glob(rule["source"], path):
                for h in rule["headers"]:
                    out[h["key"]] = h["value"]
        return list(out.items())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        path = urllib.parse.urlsplit(self.path).path
        target = (PUBLIC / path.lstrip("/")).resolve()
        if self.command in ("GET", "HEAD") and PUBLIC in target.parents and target.is_file():
            return self._file(target, path)          # Hosting serves existing static files first
        for rw in CFG.get("rewrites", []):
            if glob(rw["source"], path):
                if "run" in rw:
                    return self._run()
                return self._file(PUBLIC / rw["destination"].lstrip("/"), path)
        self.send_error(404)

    def _file(self, f: Path, path: str):
        data = f.read_bytes()
        ctype = "text/javascript" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_response(200)
        hs = dict([("Content-Type", ctype)] + self._static_headers(path))
        for k, v in hs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _run(self):
        up = urllib.parse.urlsplit(os.environ["EMU_UPSTREAM"])
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else None
        fwd = {h: self.headers[h] for h in FORWARD if self.headers.get(h)}
        sess = [p.strip() for p in (self.headers.get("Cookie") or "").split(";") if p.strip().startswith("__session=")]
        if sess:
            fwd["Cookie"] = sess[0]                      # Firebase: only `__session` reaches Cloud Run
        c = http.client.HTTPConnection(up.netloc, timeout=60)
        try:
            c.request(self.command, self.path, body=body, headers=fwd)
            r = c.getresponse()
            data = r.read()
        except OSError:
            self.send_error(502)
            return
        finally:
            c.close()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("content-length", "connection", "transfer-encoding", "date", "server"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def main() -> None:
    host = os.environ.get("EMU_HOST", "127.0.0.1")
    port = int(os.environ.get("EMU_PORT", "15176"))
    if ":" in host:
        Server.address_family = socket.AF_INET6
    httpd = Server((host, port), Emu)
    print(f"LOCAL HOSTING EMULATION of {CFG['site']} on {host}:{port} -> {os.environ['EMU_UPSTREAM']}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
