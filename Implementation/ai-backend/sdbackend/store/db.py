"""SQLite control/metadata store.

Design rules (AI/03):
* `objects` holds immutable sealed JSON records keyed by (kind, object_id). Rows are insert-only; triggers reject
  UPDATE/DELETE so an accepted identity can never be rewritten. Re-inserting identical content is idempotent;
  different content under an existing identity is an integrity error.
* Mutable control state (tasks, leases, pointers, sessions) lives in separate tables, updated by compare-and-swap.
* No model call, parse or file write ever happens inside a transaction.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..canonical import canonical_bytes, canonical_sha256, utc_now_iso

SCHEMA_VERSION = 4

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS objects (
  kind TEXT NOT NULL,
  object_id TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (kind, object_id)
);
CREATE TRIGGER IF NOT EXISTS objects_no_update BEFORE UPDATE ON objects
BEGIN SELECT RAISE(ABORT, 'immutable object'); END;
CREATE TRIGGER IF NOT EXISTS objects_no_delete BEFORE DELETE ON objects
BEGIN SELECT RAISE(ABORT, 'immutable object'); END;

-- relational indexes over immutable objects (derived; rebuildable from objects)
CREATE TABLE IF NOT EXISTS object_links (
  kind TEXT NOT NULL, object_id TEXT NOT NULL, rel TEXT NOT NULL, target TEXT NOT NULL,
  PRIMARY KEY (kind, object_id, rel, target)
);
CREATE INDEX IF NOT EXISTS object_links_target ON object_links (rel, target);

CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL, rel_path TEXT NOT NULL, stored_at TEXT NOT NULL
);

-- release lifecycle (pointer is mutable; release object itself immutable in `objects`)
CREATE TABLE IF NOT EXISTS publication_pointers (
  pointer_key TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL, target_sha256 TEXT NOT NULL,
  generation INTEGER NOT NULL, acceptance_id TEXT NOT NULL, authority_revision TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- runs / tasks / checkpoints (AI-PA-01)
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL, runspec_sha256 TEXT NOT NULL,
  task_kind TEXT NOT NULL, subject TEXT NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, fencing_token INTEGER NOT NULL DEFAULT 0, lease_expires_at TEXT,
  dependency_digest TEXT, output_object_id TEXT, output_sha256 TEXT, safe_error TEXT, coverage TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, ended_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_run ON tasks (run_id, state);

-- mutable run lifecycle (the sealed RunSpec stays immutable in `objects`)
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, state TEXT NOT NULL, compat_key TEXT NOT NULL, parent_run_id TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL, reason TEXT
);
CREATE INDEX IF NOT EXISTS runs_compat ON runs (compat_key, state);

-- process-independent provider pacing reservations (RPM/TPM/RPD), written before dispatch
CREATE TABLE IF NOT EXISTS rate_reservations (
  reservation_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, at_epoch REAL NOT NULL,
  tokens INTEGER NOT NULL, state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_reservations_provider ON rate_reservations (provider, at_epoch);

-- dependency edges for targeted invalidation (incl. negative-search scopes)
CREATE TABLE IF NOT EXISTS dependency_edges (
  dependent_kind TEXT NOT NULL, dependent_id TEXT NOT NULL, dep_kind TEXT NOT NULL, dep_id TEXT NOT NULL,
  dep_sha256 TEXT, detail TEXT,
  PRIMARY KEY (dependent_kind, dependent_id, dep_kind, dep_id)
);
CREATE INDEX IF NOT EXISTS dependency_edges_dep ON dependency_edges (dep_kind, dep_id);

CREATE TABLE IF NOT EXISTS usage_ledger (
  ledger_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
  input_tokens INTEGER, cached_tokens INTEGER, thinking_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
  usage_source TEXT NOT NULL, latency_ms INTEGER, retries INTEGER NOT NULL DEFAULT 0, error_class TEXT,
  config_sha256 TEXT, prompt_version TEXT, started_at TEXT NOT NULL, ended_at TEXT
);

CREATE TABLE IF NOT EXISTS provider_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
  detail TEXT NOT NULL, at TEXT NOT NULL
);

-- serving state
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS contexts (
  context_id TEXT PRIMARY KEY, session_principal TEXT NOT NULL, body TEXT NOT NULL, internal TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_tokens (
  token TEXT PRIMARY KEY, principal_id TEXT NOT NULL, context_id TEXT NOT NULL, asset_version_id TEXT NOT NULL,
  representation TEXT NOT NULL, disposition TEXT NOT NULL, permission_revision TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
  cursor_id TEXT PRIMARY KEY, context_id TEXT NOT NULL, stream TEXT NOT NULL, binding TEXT NOT NULL,
  offset_value INTEGER NOT NULL, expires_at TEXT NOT NULL
);

-- lexical passage index (rebuildable, versioned by index_version)
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
  passage_id UNINDEXED, index_version UNINDEXED, source_id UNINDEXED, text, tokenize='unicode61'
);
"""


# Read-through cache for sealed objects, active only in the hosted read-only serving container (SDB_HOSTED_DEMO=1):
# never in workers, intake, publication or offline tools, which may build or replace objects.
_READ_CACHE_KINDS = frozenset({"intel_publication", "intel_crosswalk", "release", "release_control"})
_READ_CACHE: dict[tuple[str, str, str], tuple[str, dict]] = {}
_READ_CACHE_LOCK = threading.Lock()


def _hosted_read_only() -> bool:
    import os
    return os.environ.get("SDB_HOSTED_DEMO") == "1"


class IntegrityConflict(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(DDL)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_ledger)")}
            if "peak_memory_bytes" not in cols:          # additive migration (v3 → v4)
                conn.execute("ALTER TABLE usage_ledger ADD COLUMN peak_memory_bytes INTEGER")
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ---- immutable objects -------------------------------------------------
    def put_object(self, kind: str, object_id: str, body: Any, *, links: dict[str, list[str]] | None = None,
                   conn: sqlite3.Connection | None = None) -> str:
        digest = canonical_sha256(body)
        text = canonical_bytes(body).decode("utf-8")
        c = conn or self.connect()
        row = c.execute("SELECT sha256 FROM objects WHERE kind=? AND object_id=?", (kind, object_id)).fetchone()
        if row is not None:
            if row["sha256"] != digest:
                raise IntegrityConflict(f"{kind}/{object_id} already sealed with different content")
            return digest
        c.execute("INSERT INTO objects(kind, object_id, sha256, body, created_at) VALUES (?,?,?,?,?)",
                  (kind, object_id, digest, text, utc_now_iso()))
        for rel, targets in (links or {}).items():
            for t in targets:
                c.execute("INSERT OR IGNORE INTO object_links(kind, object_id, rel, target) VALUES (?,?,?,?)",
                          (kind, object_id, rel, t))
        return digest

    def get_object(self, kind: str, object_id: str) -> dict | None:
        if kind in _READ_CACHE_KINDS and _hosted_read_only():
            return self._cached_object(kind, object_id)
        row = self.connect().execute("SELECT body FROM objects WHERE kind=? AND object_id=?",
                                     (kind, object_id)).fetchone()
        return None if row is None else json.loads(row["body"])

    def _cached_object(self, kind: str, object_id: str) -> dict | None:
        """Hosted read-only serving only: large sealed objects (a publication is ~16 MB of JSON) are parsed once per
        process instead of on every read. Each hit is re-validated against the row's stored SHA-256, so a changed row
        is never served from the cache. Callers on the read path treat the returned object as read-only."""
        row = self.connect().execute("SELECT sha256 FROM objects WHERE kind=? AND object_id=?",
                                     (kind, object_id)).fetchone()
        if row is None:
            return None
        key = (str(self.path), kind, object_id)
        hit = _READ_CACHE.get(key)
        if hit is not None and hit[0] == row["sha256"]:
            return hit[1]
        body = self.connect().execute("SELECT body, sha256 FROM objects WHERE kind=? AND object_id=?",
                                      (kind, object_id)).fetchone()
        if body is None:
            return None
        obj = json.loads(body["body"])
        with _READ_CACHE_LOCK:
            _READ_CACHE[key] = (body["sha256"], obj)
        return obj

    def get_objects_batched(self, kind: str, object_ids: list[str], *, chunk_size: int = 500) -> dict[str, dict]:
        """Fetch multiple objects of the same kind in chunks using SQL IN (...).

        Returns a dict mapping object_id -> parsed body dict. Missing objects are omitted.
        """
        if not object_ids:
            return {}
        conn = self.connect()
        results: dict[str, dict] = {}
        unique_ids = list(dict.fromkeys(object_ids))
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT object_id, body FROM objects WHERE kind=? AND object_id IN ({placeholders})",
                [kind, *chunk],
            ).fetchall()
            for r in rows:
                results[r["object_id"]] = json.loads(r["body"])
        return results

    def object_sha(self, kind: str, object_id: str) -> str | None:
        row = self.connect().execute("SELECT sha256 FROM objects WHERE kind=? AND object_id=?",
                                     (kind, object_id)).fetchone()
        return None if row is None else row["sha256"]

    def list_objects(self, kind: str) -> list[dict]:
        rows = self.connect().execute("SELECT body FROM objects WHERE kind=? ORDER BY object_id", (kind,)).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def linked(self, kind: str, rel: str, target: str) -> list[str]:
        rows = self.connect().execute(
            "SELECT object_id FROM object_links WHERE kind=? AND rel=? AND target=? ORDER BY object_id",
            (kind, rel, target)).fetchall()
        return [r["object_id"] for r in rows]

    # ---- publication pointers (compare-and-swap) ---------------------------
    def get_pointer(self, key: str) -> sqlite3.Row | None:
        return self.connect().execute("SELECT * FROM publication_pointers WHERE pointer_key=?", (key,)).fetchone()

    def swap_pointer(self, key: str, *, expected_generation: int | None, target_kind: str, target_id: str,
                     acceptance_id: str, authority_revision: str) -> int:
        target_sha = self.object_sha(target_kind, target_id)
        if target_sha is None:
            raise IntegrityConflict(f"pointer target {target_kind}/{target_id} not sealed")
        with self.tx() as c:
            row = c.execute("SELECT generation FROM publication_pointers WHERE pointer_key=?", (key,)).fetchone()
            current = None if row is None else row["generation"]
            if current != expected_generation:
                raise IntegrityConflict(f"pointer {key} generation {current} != expected {expected_generation}")
            gen = 1 if current is None else current + 1
            c.execute("INSERT OR REPLACE INTO publication_pointers VALUES (?,?,?,?,?,?,?,?)",
                      (key, target_kind, target_id, target_sha, gen, acceptance_id, authority_revision, utc_now_iso()))
        return gen
