"""Serving request state: sessions, contexts, cursors and opaque asset tokens.

Two interchangeable stores with one interface:

* `SqliteServingState` (default): the store's own `sessions`/`contexts`/`cursors`/`asset_tokens` tables, exactly as
  before. Correct for one local process.
* `FirestoreServingState` (`SDB_SERVING_STATE=firestore`): small documents in Firestore so a session, context, cursor
  or asset token issued by one Cloud Run instance is honoured by any other instance and survives restarts and cold
  starts. Only this request state is shared; the analytical store stays a local, read-only serving copy.

Bearer secrets (session IDs, asset tokens, cursor IDs) are never stored as document IDs or fields: a document is keyed
by `sha256(kind|secret)`, so read access to Firestore does not yield a usable session or download link. Each
document carries `expire_at` (a timestamp) for a Firestore TTL policy; expiry is also enforced on every read.

Rows are insert-once (create) except the single session revocation flag. No analytical data is written here.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

from ..canonical import utc_now_iso

SESSION_FIELDS = ("principal_id", "csrf_token", "created_at", "expires_at", "revoked")
CONTEXT_FIELDS = ("context_id", "session_principal", "body", "internal", "expires_at")
CURSOR_FIELDS = ("context_id", "stream", "binding", "offset_value", "expires_at")
TOKEN_FIELDS = ("principal_id", "context_id", "asset_version_id", "representation", "disposition",
                "permission_revision", "expires_at")


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


class SqliteServingState:
    kind = "sqlite"

    def __init__(self, db):
        self.db = db

    # sessions
    def put_session(self, sid: str, row: dict) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO sessions(session_id, principal_id, csrf_token, created_at, expires_at) "
                      "VALUES (?,?,?,?,?)", (sid, row["principal_id"], row["csrf_token"], row["created_at"],
                                             row["expires_at"]))

    def get_session(self, sid: str) -> dict | None:
        r = self.db.connect().execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()
        return None if r is None else dict(r)

    def revoke_session(self, sid: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE sessions SET revoked=1 WHERE session_id=?", (sid,))

    # contexts
    def put_context(self, row: dict) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO contexts(context_id, session_principal, body, internal, expires_at) "
                      "VALUES (?,?,?,?,?)", tuple(row[k] for k in CONTEXT_FIELDS))

    def get_context(self, context_id: str) -> dict | None:
        r = self.db.connect().execute("SELECT * FROM contexts WHERE context_id=?", (context_id,)).fetchone()
        return None if r is None else dict(r)

    # cursors
    def put_cursor(self, cursor_id: str, row: dict) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO cursors(cursor_id, context_id, stream, binding, offset_value, expires_at) "
                      "VALUES (?,?,?,?,?,?)", (cursor_id, *(row[k] for k in CURSOR_FIELDS)))

    def get_cursor(self, cursor_id: str) -> dict | None:
        r = self.db.connect().execute("SELECT * FROM cursors WHERE cursor_id=?", (cursor_id,)).fetchone()
        return None if r is None else dict(r)

    # asset tokens
    def put_token(self, token: str, row: dict) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO asset_tokens VALUES (?,?,?,?,?,?,?,?)", (token, *(row[k] for k in TOKEN_FIELDS)))

    def get_token(self, token: str) -> dict | None:
        r = self.db.connect().execute("SELECT * FROM asset_tokens WHERE token=?", (token,)).fetchone()
        return None if r is None else dict(r)


class FirestoreServingState:
    """`client` is a `google.cloud.firestore.Client` (or a test double with the same document API)."""
    kind = "firestore"

    def __init__(self, client, prefix: str = "artha"):
        self.client = client
        self.prefix = prefix

    def _doc(self, collection: str, key: str):
        digest = hashlib.sha256(f"{collection}|{key}".encode()).hexdigest()
        return self.client.collection(f"{self.prefix}_{collection}").document(digest)

    def _create(self, collection: str, key: str, row: dict, fields: tuple[str, ...]) -> None:
        data = {k: row[k] for k in fields if k in row}
        data["expire_at"] = _ts(row["expires_at"])          # TTL policy field (deletion is best-effort)
        data["written_at"] = utc_now_iso()
        self._doc(collection, key).create(data)             # insert-once: an existing key is an error

    def _get(self, collection: str, key: str) -> dict | None:
        snap = self._doc(collection, key).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        data.pop("expire_at", None)
        data.pop("written_at", None)
        return data

    def put_session(self, sid: str, row: dict) -> None:
        self._create("sessions", sid, {**row, "revoked": 0}, SESSION_FIELDS)

    def get_session(self, sid: str) -> dict | None:
        d = self._get("sessions", sid)
        return None if d is None else {"session_id": sid, **d}

    def revoke_session(self, sid: str) -> None:
        ref = self._doc("sessions", sid)
        if ref.get().exists:
            ref.update({"revoked": 1})

    def put_context(self, row: dict) -> None:
        self._create("contexts", row["context_id"], row, CONTEXT_FIELDS)

    def get_context(self, context_id: str) -> dict | None:
        return self._get("contexts", context_id)

    def put_cursor(self, cursor_id: str, row: dict) -> None:
        self._create("cursors", cursor_id, row, CURSOR_FIELDS)

    def get_cursor(self, cursor_id: str) -> dict | None:
        d = self._get("cursors", cursor_id)
        return None if d is None else {"cursor_id": cursor_id, **d}

    def put_token(self, token: str, row: dict) -> None:
        self._create("asset_tokens", token, row, TOKEN_FIELDS)

    def get_token(self, token: str) -> dict | None:
        d = self._get("asset_tokens", token)
        return None if d is None else {"token": token, **d}


def firestore_client():
    from google.cloud import firestore     # only imported when the Firestore store is selected
    project = os.environ.get("SDB_FIRESTORE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or None
    return firestore.Client(project=project, database=os.environ.get("SDB_FIRESTORE_DATABASE", "(default)"))


def make_state(db, kind: str | None = None, client: Any = None):
    kind = kind or os.environ.get("SDB_SERVING_STATE", "sqlite")
    if kind == "sqlite":
        return SqliteServingState(db)
    if kind == "firestore":
        return FirestoreServingState(client or firestore_client(), os.environ.get("SDB_FIRESTORE_PREFIX", "artha"))
    raise ValueError(f"unknown SDB_SERVING_STATE {kind!r} (sqlite | firestore)")


def describe(state) -> dict:
    return {"serving_state": state.kind, **({"collection_prefix": state.prefix} if state.kind == "firestore" else {})}

