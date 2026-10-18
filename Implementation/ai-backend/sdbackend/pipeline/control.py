"""AI-PA-01 run/task controls: sealed RunSpec, mutable run lifecycle, task leases + fencing tokens, checkpoints.

Rules (R06):
* A task completes only if the caller still holds the lease: same fencing token, state `running`, lease not
  expired, and the run still `active` and unexpired. Cancellation is terminal; a cancelled or lease-expired worker
  can never complete or write outputs, even without a takeover.
* Outputs are written in the SAME transaction as the fenced completion, so a stale worker cannot leave a staged
  object behind that a later run would reuse.
* Long model work renews its lease with `heartbeat`, which also observes cancellation/expiry (`TaskRevoked`).
* `resume` continues the latest compatible unfinished run; a fresh run is sealed only when none exists or on
  explicit request. Spent attempts survive crashes in `tasks.attempt` and the usage ledger.
Transactions only claim/record state; model calls and parsing happen outside them.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone

from ..canonical import canonical_sha256, derived_id, random_id, utc_now_iso
from ..contracts import EXPECTED_MANIFEST_SHA256
from ..runtime import Runtime

LEASE_SECONDS = 900                       # local-model profile: > one measured S03 case (≈445 s) plus heartbeat margin
RUN_TTL = timedelta(days=2)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
TERMINAL_TASK_STATES = ("accepted", "staged", "rejected", "cancelled", "failed", "limited")
OPEN_RUN_STATES = ("active",)


class TaskRevoked(RuntimeError):
    """The worker no longer holds the task: cancelled, lease lost, or run expired."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value.replace("Z", "+00:00"))


def compat_key(*, purpose: str, release_id: str, boundary: dict, worklist: list[str], provider: dict,
               versions: dict, budget: dict) -> str:
    return canonical_sha256({"purpose": purpose, "release_id": release_id, "boundary": boundary,
                             "worklist": sorted(worklist), "provider": provider, "versions": versions,
                             "budget": budget, "contract": EXPECTED_MANIFEST_SHA256})


def seal_runspec(rt: Runtime, *, purpose: str, release_id: str, source_set_version: str, boundary: dict,
                 worklist: list[str], provider: dict, versions: dict, budget: dict, parent_run_id: str | None,
                 selection_reason: str) -> dict:
    spec = {"purpose": purpose, "accepted_contract_digest": EXPECTED_MANIFEST_SHA256, "release_id": release_id,
            "source_set_version": source_set_version, "context_boundary": boundary,
            "input_worklist_sha256": canonical_sha256(sorted(worklist)), "worklist_size": len(worklist),
            "provider": provider, "versions": versions, "budget_profile": budget, "parent_run_id": parent_run_id,
            "provider_selection_reason": selection_reason, "monetary_ceiling": {"currency": "AUD", "amount_minor": 0,
                                                                                  "basis": "free tier / local only"}}
    if provider.get("provider") == "vertex-ai":
        spec["monetary_ceiling"] = {"currency": "USD", "amount_minor": 200,
                                    "basis": "trial-credit-backed qualification; token/attempt bounds, not a billing cap"}
    run_id = random_id("RUN", 8)
    created = _now()
    body = {"run_id": run_id, **spec, "created_at": _iso(created), "expires_at": _iso(created + RUN_TTL)}
    rt.db.put_object("runspec", run_id, body, links={"release": [release_id]})
    key = compat_key(purpose=purpose, release_id=release_id, boundary=boundary, worklist=worklist,
                     provider=provider, versions=versions, budget=budget)
    with rt.db.tx() as c:
        c.execute("INSERT INTO runs(run_id, state, compat_key, parent_run_id, created_at, expires_at, updated_at, reason)"
                  " VALUES (?,?,?,?,?,?,?,?)", (run_id, "active", key, parent_run_id, body["created_at"],
                                               body["expires_at"], body["created_at"], selection_reason[:500]))
    body["runspec_sha256"] = rt.db.object_sha("runspec", run_id)
    body["compat_key"] = key
    return body


def load_run(rt: Runtime, run_id: str) -> dict | None:
    spec = rt.db.get_object("runspec", run_id)
    if spec is None:
        return None
    spec["runspec_sha256"] = rt.db.object_sha("runspec", run_id)
    row = rt.db.connect().execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    spec["compat_key"] = row["compat_key"] if row else None
    spec["run_state"] = row["state"] if row else "unknown"
    return spec


def find_resumable(rt: Runtime, key: str) -> dict | None:
    """Latest compatible run that is still active and unexpired (crash/interrupt leaves it active)."""
    rows = rt.db.connect().execute("SELECT run_id, expires_at FROM runs WHERE compat_key=? AND state='active' "
                                   "ORDER BY created_at DESC", (key,)).fetchall()
    for r in rows:
        if _parse(r["expires_at"]) > _now():
            return load_run(rt, r["run_id"])
    return None


def run_state(rt: Runtime, run_id: str) -> str:
    row = rt.db.connect().execute("SELECT state, expires_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return "unknown"
    if row["state"] == "active" and _parse(row["expires_at"]) <= _now():
        return "expired"
    return row["state"]


def close_run(rt: Runtime, run_id: str, state: str, reason: str) -> None:
    """Mark a run finished: complete | partial | limited | failed. Never revives a cancelled run."""
    with rt.db.tx() as c:
        c.execute("UPDATE runs SET state=?, reason=?, updated_at=? WHERE run_id=? AND state='active'",
                  (state, reason[:500], utc_now_iso(), run_id))


def task_key(*parts) -> str:
    return canonical_sha256(list(parts))


def ensure_task(rt: Runtime, run: dict, kind: str, subject: str, idem_parts: list) -> dict:
    key = task_key(run["run_id"], kind, subject, *idem_parts)
    now = utc_now_iso()
    with rt.db.tx() as c:
        row = c.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            tid = derived_id("TASK", key)
            c.execute("INSERT INTO tasks(task_id, idempotency_key, run_id, runspec_sha256, task_kind, subject, state, "
                      "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (tid, key, run["run_id"], run["runspec_sha256"], kind, subject, "queued", now, now))
            row = c.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
    return dict(row)


def _run_open(c, run_id: str) -> bool:
    """Open, unexpired, and still under the access-policy revision it was sealed with (R04: authority rechecked
    during long work — a policy change mid-run revokes heartbeat and completion)."""
    row = c.execute("SELECT state, expires_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row["state"] not in OPEN_RUN_STATES or _parse(row["expires_at"]) <= _now():
        return False
    spec = c.execute("SELECT body FROM objects WHERE kind='runspec' AND object_id=?", (run_id,)).fetchone()
    sealed = (json.loads(spec["body"]).get("context_boundary") or {}).get("permission_revision") if spec else None
    from ..policy import permission_revision
    return sealed is None or sealed == permission_revision()


def claim(rt: Runtime, task_id: str, lease_seconds: int = LEASE_SECONDS) -> int | None:
    """Claim a queued task or take over an expired lease; returns the new fencing token or None."""
    now = _now()
    with rt.db.tx() as c:
        row = c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["state"] in TERMINAL_TASK_STATES or not _run_open(c, row["run_id"]):
            return None
        if row["state"] == "running" and row["lease_expires_at"] and _parse(row["lease_expires_at"]) > now:
            return None
        token = row["fencing_token"] + 1
        c.execute("UPDATE tasks SET state='running', attempt=attempt+1, lease_owner=?, fencing_token=?, "
                  "lease_expires_at=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE task_id=?",
                  (WORKER_ID, token, _iso(now + timedelta(seconds=lease_seconds)), utc_now_iso(), utc_now_iso(),
                   task_id))
    return token


def _holds(c, task_id: str, token: int) -> bool:
    row = c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return (row is not None and row["fencing_token"] == token and row["state"] == "running"
            and row["lease_expires_at"] is not None and _parse(row["lease_expires_at"]) > _now()
            and _run_open(c, row["run_id"]))


def heartbeat(rt: Runtime, task_id: str, token: int, lease_seconds: int = LEASE_SECONDS) -> None:
    """Renew the lease of a task still held by this worker; raise TaskRevoked otherwise (cancel/expiry/takeover)."""
    with rt.db.tx() as c:
        if not _holds(c, task_id, token):
            raise TaskRevoked(f"task {task_id} is no longer held by this worker")
        c.execute("UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE task_id=?",
                  (_iso(_now() + timedelta(seconds=lease_seconds)), utc_now_iso(), task_id))


def finish(rt: Runtime, task_id: str, token: int, state: str, *, output_object_id: str | None = None,
           output_sha256: str | None = None, error: str | None = None, coverage: dict | None = None,
           dependency_digest: str | None = None, outputs: list[tuple[str, str, dict, dict | None]] = (),
           dependencies: list[tuple[str, str, list]] = ()) -> bool:
    """Fenced completion. `outputs` = [(kind, object_id, body, links)] and `dependencies` =
    [(dependent_kind, dependent_id, [(dep_kind, dep_id, dep_sha256, detail)])] are sealed in the SAME transaction.

    Returns False — and writes nothing — when the caller no longer holds a live lease on a running task of an open
    run (cancelled, expired without takeover, taken over, or run expired)."""
    if state not in ("staged", "failed", "limited", "rejected", "accepted"):
        raise ValueError(f"invalid terminal state {state}")
    with rt.db.tx() as c:
        if not _holds(c, task_id, token):
            return False
        for kind, oid, body, links in outputs:
            rt.db.put_object(kind, oid, body, links=links, conn=c)
        for dkind, did, deps in dependencies:
            _insert_dependencies(c, dkind, did, deps)
        if outputs and output_object_id and output_sha256 is None:
            output_sha256 = c.execute("SELECT sha256 FROM objects WHERE kind=? AND object_id=?",
                                      (outputs[0][0], output_object_id)).fetchone()["sha256"]
        c.execute("UPDATE tasks SET state=?, output_object_id=?, output_sha256=?, safe_error=?, coverage=?, "
                  "dependency_digest=?, lease_owner=NULL, lease_expires_at=NULL, ended_at=?, updated_at=? WHERE task_id=?",
                  (state, output_object_id, output_sha256, error, json.dumps(coverage) if coverage else None,
                   dependency_digest, utc_now_iso(), utc_now_iso(), task_id))
    return True


def recover_dead_lease(rt: Runtime, task_id: str) -> bool:
    """Re-queue a running task whose lease owner is a process on THIS host that no longer exists (e.g. a worker
    stopped by its exact PID). Owners on other hosts, or live/reused PIDs, keep their lease until it expires."""
    with rt.db.tx() as c:
        row = c.execute("SELECT state, lease_owner FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["state"] != "running" or not row["lease_owner"]:
            return False
        host, _, pid = row["lease_owner"].rpartition(":")
        if host != socket.gethostname() or not pid.isdigit() or int(pid) == os.getpid():
            return False
        try:
            os.kill(int(pid), 0)
            return False                                   # alive (or reused): never steal a live lease
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        c.execute("UPDATE tasks SET state='queued', safe_error=?, lease_owner=NULL, lease_expires_at=NULL, "
                  "updated_at=? WHERE task_id=? AND state='running' AND lease_owner=?",
                  (f"lease owner {row['lease_owner']} no longer running; re-queued", utc_now_iso(), task_id,
                   row["lease_owner"]))
    return True


def release_lease(rt: Runtime, task_id: str, token: int, state: str = "queued", error: str | None = None) -> bool:
    with rt.db.tx() as c:
        if not _holds(c, task_id, token):
            return False
        c.execute("UPDATE tasks SET state=?, safe_error=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
                  "WHERE task_id=? AND fencing_token=?", (state, error, utc_now_iso(), task_id, token))
    return True


def cancel_run(rt: Runtime, run_id: str, reason: str) -> int:
    """Terminal: the run and every non-terminal task (including running ones) become cancelled."""
    with rt.db.tx() as c:
        c.execute("UPDATE runs SET state='cancelled', reason=?, updated_at=? WHERE run_id=? AND state='active'",
                  (reason[:500], utc_now_iso(), run_id))
        cur = c.execute("UPDATE tasks SET state='cancelled', safe_error=?, lease_owner=NULL, lease_expires_at=NULL, "
                        "updated_at=? WHERE run_id=? AND state IN ('queued','claimed','running','validating')",
                        (reason, utc_now_iso(), run_id))
    return cur.rowcount


def task_counts(rt: Runtime, run_id: str) -> dict:
    rows = rt.db.connect().execute("SELECT state, COUNT(*) AS n FROM tasks WHERE run_id=? GROUP BY state",
                                   (run_id,)).fetchall()
    return {r["state"]: r["n"] for r in rows}


def reusable_output(rt: Runtime, kind: str, reuse_key: str) -> dict | None:
    """Compatible earlier output keyed by exact dependencies (inputs + provider/model fingerprint + versions).
    Only outputs sealed by a fenced completion exist, so a stale worker's work can never be reused."""
    oid = derived_id("STAGE", kind, reuse_key)
    return rt.db.get_object(f"stage_{kind}", oid)


def record_dependencies(rt: Runtime, dependent_kind: str, dependent_id: str, deps: list[tuple[str, str, str | None, dict | None]]) -> None:
    """deps = [(dep_kind, dep_id, dep_sha256, detail)]; negative-search scopes use dep_kind='search_scope'."""
    with rt.db.tx() as c:
        _insert_dependencies(c, dependent_kind, dependent_id, deps)


def _insert_dependencies(c, dependent_kind: str, dependent_id: str, deps) -> None:
    for kind, did, sha, detail in deps:
        c.execute("INSERT OR IGNORE INTO dependency_edges VALUES (?,?,?,?,?,?)",
                  (dependent_kind, dependent_id, kind, did, sha, json.dumps(detail) if detail else None))
