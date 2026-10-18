"""Local browser session authentication for the POC.

* Demo principals come from the trusted policy file. Each has a local one-time-generated login code stored in
  `<data_dir>/login-codes.json` (mode 0600, never committed, never logged). `python -m sdbackend logins` prints them.
* `POST /v1/session` exchanges principal + code for an HttpOnly, SameSite=Strict session cookie and returns a CSRF
  token; every state-changing or data request must echo it in `X-CSRF-Token`.
* The browser never holds service credentials and never supplies authority; the server resolves it per request.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..canonical import utc_now
from ..policy import load_policy
from ..runtime import Runtime

COOKIE = "sdb_session"
CSRF_HEADER = "x-csrf-token"
CSRF_COOKIE = "sdb_csrf"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def login_codes(rt: Runtime) -> dict[str, str]:
    path: Path = rt.settings.data_dir / "login-codes.json"
    policy = load_policy()
    codes = json.loads(path.read_text()) if path.is_file() else {}
    changed = False
    for principal in policy["principals"]:
        if principal not in codes:
            codes[principal] = secrets.token_urlsafe(12)
            changed = True
    if changed:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(codes, fh, indent=1)
    return codes


def create_session(rt: Runtime, principal_id: str, code: str) -> dict | None:
    expected = login_codes(rt).get(principal_id)
    if expected is None or not hmac.compare_digest(expected, code or ""):
        return None
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = utc_now()
    expires = now + timedelta(seconds=rt.settings.session_ttl_seconds)
    rt.state.put_session(sid, {"principal_id": principal_id, "csrf_token": csrf, "created_at": _iso(now),
                               "expires_at": _iso(expires)})
    return {"session_id": sid, "principal_id": principal_id, "csrf_token": csrf, "expires_at": _iso(expires)}


def get_session(rt: Runtime, sid: str | None) -> dict | None:
    if not sid:
        return None
    row = rt.state.get_session(sid)
    if row is None or row["revoked"]:
        return None
    if datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        return None
    return dict(row)


def revoke_session(rt: Runtime, sid: str) -> None:
    rt.state.revoke_session(sid)


def csrf_ok(session: dict, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(session["csrf_token"], header_value)
