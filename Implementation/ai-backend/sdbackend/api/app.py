"""Local HTTP service: session auth, `POST /v1/operations`, opaque asset redemption, health/readiness.

Read service only. It never starts extraction or model analysis (those run in the separate worker/CLI).
"""
from __future__ import annotations

import html
import logging
import os
import re
from urllib.parse import parse_qsl, quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..canonical import utc_now_iso
from ..policy import load_policy, permission_revision
from ..runtime import Runtime, make_runtime
from . import auth
from .ops import OpError, Operations
from .repo import served_release

log = logging.getLogger("sdbackend.api")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
SIGNIN_HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Sign in - Example Super complaints (local POC)</title>
<style>body{font:15px system-ui;margin:3rem auto;max-width:28rem;padding:0 1rem;color:#14213d}label{display:block;margin:.8rem 0 .2rem}
select,input,button{font:inherit;padding:.45rem;width:100%;box-sizing:border-box}button{margin-top:1rem;background:#14213d;color:#fff;border:0}
p.note{color:#555;font-size:13px}</style>
<h1>Sign in</h1><p class="note">Local proof-of-concept with synthetic data. Demo principals and their local login codes are
printed by <code>python -m sdbackend logins</code> on the server machine.</p>
<form method="post" action="/v1/session"><label for="p">Principal</label><select id="p" name="principal_id">{{options}}</select>
<label for="c">Login code</label><input id="c" name="login_code" type="password" autocomplete="off" required>
<button type="submit">Sign in</button></form><p class="note">Dashboard: <a href="{{ui}}">{{ui}}</a></p></html>"""
SIGNED_IN_HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Signed in</title>
<style>body{font:15px system-ui;margin:3rem auto;max-width:28rem;padding:0 1rem}</style>
<h1>Signed in as {{name}}</h1><p><a href="{{ui}}">Open the dashboard</a></p></html>"""
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                    "Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"}


def create_app(rt: Runtime | None = None) -> FastAPI:
    rt = rt or make_runtime()
    ops = Operations(rt)
    app = FastAPI(title="Example Super complaints-leadership backend (local POC)", docs_url=None, redoc_url=None,
                  openapi_url=None)
    auth.login_codes(rt)   # ensure local demo login codes exist (file mode 0600)
    # Hosted zero-login demo (cloud/SERVING-CONTRACT.md): no sign-in page and no code exchange are offered; every
    # visitor gets the fixed read-only `leader.demo` session server-side (session_of). Login codes never leave the
    # instance and are never needed by a browser.
    hosted = os.environ.get("SDB_HOSTED_DEMO") == "1"

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        host = request.headers.get("host", "")        # forwarding headers are caller-controlled: never trusted
        if urlsplit(origin).netloc == host:
            return True
        return origin in rt.settings.allowed_origins

    def json_resp(body, status=200, headers=None) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers={**SECURITY_HEADERS, **(headers or {})})

    def session_of(request: Request):
        s = auth.get_session(rt, request.cookies.get(auth.COOKIE))
        if s is not None:
            return s
        codes = auth.login_codes(rt)
        code = codes.get("leader.demo", "")
        return auth.create_session(rt, "leader.demo", code)

    @app.get("/healthz")
    def healthz():
        return json_resp({"status": "ok", "at": utc_now_iso()})

    @app.get("/readyz")
    def readyz():
        rv = served_release(rt)
        from ..models.gateway import provider_status
        return json_resp({
            "status": "ready" if rv else "no_release",
            "contract": {"version": "1.0.0", "manifest_sha256": rt.contracts.manifest["manifest_content_sha256"]},
            "release": None if rv is None else {"release_id": rv.release_id, "status": rv.control["status"],
                                                "source_set_version": rv.control["source_set_version"]},
            "unreviewed_sources_enabled": rt.settings.allow_unreviewed_sources,
            "serving_state": rt.state.kind if rt.state is not None else None,
            "permission_revision": permission_revision(),
            "models": provider_status(rt.settings)})

    @app.get("/v1/session/signin")
    def signin_page():
        if hosted:
            return json_resp({"error": "not offered: this hosted demo signs every visitor in automatically"}, 404)
        principals = load_policy()["principals"]
        options = "".join(f'<option value="{html.escape(p)}">{html.escape(v["display_name"])}</option>'
                          for p, v in principals.items())
        page = SIGNIN_HTML.replace("{{options}}", options).replace("{{ui}}", html.escape(rt.settings.ui_origin))
        # B-01: with `no-referrer` Chrome posts this same-origin form with `Origin: null`, which is (correctly)
        # refused; `same-origin` lets the browser send the page's real origin. `Origin: null` stays refused.
        return Response(page, media_type="text/html", headers={**SECURITY_HEADERS, "Referrer-Policy": "same-origin",
                        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                                   "frame-ancestors 'none'"})

    @app.post("/v1/session")
    async def login(request: Request):
        if hosted:
            return json_resp({"error": "not offered: this hosted demo signs every visitor in automatically"}, 404)
        if not origin_ok(request):
            return json_resp({"error": "origin not allowed"}, 403)
        form_post = request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
        try:
            if form_post:
                body = dict(parse_qsl((await request.body()).decode("utf-8")))
            else:
                body = await request.json()
        except Exception:  # noqa: BLE001
            return json_resp({"error": "invalid request body"}, 400)
        session = auth.create_session(rt, str(body.get("principal_id", "")), str(body.get("login_code", "")))
        if session is None:
            if form_post:
                return Response("Sign-in failed. <a href='/v1/session/signin'>Try again</a>", status_code=401,
                                media_type="text/html", headers=SECURITY_HEADERS)
            return json_resp({"error": "sign-in failed"}, 401)
        info = {"principal_id": session["principal_id"], "csrf_token": session["csrf_token"],
                "expires_at": session["expires_at"],
                "display_name": load_policy()["principals"][session["principal_id"]]["display_name"]}
        if form_post:
            resp = Response(SIGNED_IN_HTML.replace("{{ui}}", html.escape(rt.settings.ui_origin))
                            .replace("{{name}}", html.escape(info["display_name"])), media_type="text/html",
                            headers={**SECURITY_HEADERS, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
        else:
            resp = json_resp(info)
        resp.set_cookie(auth.COOKIE, session["session_id"], httponly=True, samesite="strict", path="/",
                        secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
        # double-submit CSRF cookie: readable by the UI's JavaScript on the same host, echoed in X-CSRF-Token
        resp.set_cookie(auth.CSRF_COOKIE, session["csrf_token"], httponly=False, samesite="strict", path="/",
                        secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
        return resp

    @app.get("/v1/session")
    def whoami(request: Request):
        session = session_of(request)
        if session is None:
            return json_resp({"error": "sign-in required"}, 401)
        resp = json_resp({"principal_id": session["principal_id"], "csrf_token": session["csrf_token"],
                          "expires_at": session["expires_at"]})
        if not request.cookies.get(auth.COOKIE):
            resp.set_cookie(auth.COOKIE, session["session_id"], httponly=True, samesite="strict", path="/",
                            secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
            resp.set_cookie(auth.CSRF_COOKIE, session["csrf_token"], httponly=False, samesite="strict", path="/",
                            secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
        return resp

    @app.delete("/v1/session")
    def logout(request: Request):
        session = session_of(request)
        if session is None:
            return json_resp({"error": "sign-in required"}, 401)
        if not origin_ok(request) or not auth.csrf_ok(session, request.headers.get(auth.CSRF_HEADER)):
            return json_resp({"error": "CSRF check failed"}, 403)
        auth.revoke_session(rt, session["session_id"])
        resp = json_resp({"status": "signed_out"})
        resp.delete_cookie(auth.COOKIE, path="/")
        resp.delete_cookie(auth.CSRF_COOKIE, path="/")
        return resp

    @app.post("/v1/operations")
    async def operations(request: Request):
        session = session_of(request)
        if session is None:
            return json_resp({"error": "sign-in required"}, 401)
        if not origin_ok(request):
            return json_resp({"error": "origin not allowed"}, 403)
        csrf = request.headers.get(auth.CSRF_HEADER)
        if csrf and not auth.csrf_ok(session, csrf):
            return json_resp({"error": "CSRF check failed"}, 403)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return json_resp({"error": "invalid JSON"}, 400)
        # Synchronous work runs off the event loop, so /healthz and other requests stay responsive meanwhile.
        result = await run_in_threadpool(ops.handle, session["principal_id"], body)
        if "_http_status" in result:
            return json_resp({"error": result["error"]}, result["_http_status"])
        resp = json_resp(result)
        if not request.cookies.get(auth.COOKIE):
            resp.set_cookie(auth.COOKIE, session["session_id"], httponly=True, samesite="strict", path="/",
                            secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
            resp.set_cookie(auth.CSRF_COOKIE, session["csrf_token"], httponly=False, samesite="strict", path="/",
                            secure=rt.settings.cookie_secure, max_age=rt.settings.session_ttl_seconds)
        return resp

    @app.get("/v1/assets/{token}")
    @app.head("/v1/assets/{token}")
    def redeem(token: str, request: Request):
        session = session_of(request)
        if session is None:
            return Response(status_code=401, headers=SECURITY_HEADERS)
        try:
            found = ops.redeem(session["principal_id"], token)
        except OpError:
            return Response(status_code=410, headers=SECURITY_HEADERS)
        if found is None:
            return Response(status_code=404, headers=SECURITY_HEADERS)
        meta, data = found
        a = meta["asset"]
        headers = {**SECURITY_HEADERS, "Accept-Ranges": "bytes", "ETag": f'"{a["bytes_sha256"]}"',
                   "Content-Type": a["media_type"] + ("; charset=utf-8" if a["media_type"].startswith("text/") else ""),
                   "Content-Disposition": f"{meta['disposition']}; filename*=UTF-8''{quote(a['filename'])}",
                   "X-Asset-Version-Id": a["asset_version_id"], "X-Content-SHA256": a["bytes_sha256"],
                   "Cache-Control": "private, no-store"}
        if a["media_type"] == "text/html":
            headers["Content-Security-Policy"] = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; "
                                                  "img-src data:; form-action 'none'; frame-ancestors 'self'")
        else:
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'self'"
            if a["media_type"] == "application/pdf":
                headers.pop("Content-Security-Policy")   # built-in PDF viewers need to run; bytes are not HTML
        total = len(data)
        rng = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if rng and (if_range is None or if_range.strip() == headers["ETag"]):
            m = RANGE_RE.match(rng.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{total}"})
            if m.group(1) == "":
                length = int(m.group(2))
                start, end = max(0, total - length), total - 1
            else:
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
            if start >= total or start > end:
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{total}"})
            body = data[start:end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"
            return Response(content=b"" if request.method == "HEAD" else body, status_code=206, headers=headers)
        return Response(content=b"" if request.method == "HEAD" else data, status_code=200, headers=headers)

    return app
