"""Local backup/export and verified restore.

Backup: consistent SQLite snapshot via the online backup API + immutable blobs + sealed packages, with a SHA-256
manifest of every member. Restore: extract into an empty target data directory, verify every digest, run SQLite
integrity_check, re-verify each blob against its content address and that every release entry's blob exists.
Login codes and .env secrets are deliberately excluded.

Serving/authentication state is REVOKED by design (R12): the snapshot copy has every row of `sessions` (session IDs and
CSRF tokens), `contexts`, `asset_tokens` (opaque original actions), `cursors` and `rate_reservations` deleted and is
VACUUMed so no freed page retains them. After a restore every user signs in again and re-resolves contexts; published
analysis sets, releases and acceptance decisions are preserved, so re-resolved contexts give identical results.

Restore also checks compatibility before declaring success: store schema version, the adopted contract baseline, the
current access-policy revision against every publication pointer's authority revision, and whether the reviewer
allowlist in use is the empty production one. A mismatch is reported (`compatibility`), never silently accepted.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from ..canonical import utc_now_iso
from ..config import load_settings

EXCLUDED = {"login-codes.json"}
REVOKED_TABLES = ("sessions", "contexts", "asset_tokens", "cursors", "rate_reservations")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(out_dir: Path) -> dict:
    s = load_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now_iso().replace(":", "").replace("-", "").replace(".", "")
    archive = out_dir / f"sdb-backup-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir()
        src = sqlite3.connect(s.db_path)
        dst = sqlite3.connect(stage / "control.sqlite3")
        with dst:
            src.backup(dst)
        src.close()
        revoked = {}
        with dst:
            for t in REVOKED_TABLES:
                revoked[t] = dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                dst.execute(f"DELETE FROM {t}")
        dst.execute("VACUUM")
        dst.close()
        for sub in ("blobs", "packages"):
            if (s.data_dir / sub).is_dir():
                shutil.copytree(s.data_dir / sub, stage / sub, ignore=shutil.ignore_patterns(".tmp-*"))
        files = sorted(p for p in stage.rglob("*") if p.is_file() and p.name not in EXCLUDED)
        manifest = {"kind": "sdb-backup", "version": "1.1.0", "created_at": utc_now_iso(),
                    "session_policy": "revoked_at_backup", "revoked_rows": revoked,
                    "files": {str(p.relative_to(stage)): {"sha256": _sha(p), "bytes": p.stat().st_size} for p in files}}
        (stage / "BACKUP-MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(stage, arcname="data")
    return {"archive": str(archive), "files": len(manifest["files"]), "archive_sha256": _sha(archive),
            "excluded": sorted(EXCLUDED), "revoked_rows": revoked}


def restore(archive: Path, target: Path, *, force: bool = False) -> dict:
    target = Path(target)
    if target.exists() and any(target.iterdir()) and not force:
        raise SystemExit(f"restore target {target} is not empty (use --force to restore into it)")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        stage = Path(tmp) / "data"
        manifest = json.loads((stage / "BACKUP-MANIFEST.json").read_text(encoding="utf-8"))
        bad = [name for name, meta in manifest["files"].items() if _sha(stage / name) != meta["sha256"]]
        if bad:
            raise SystemExit(f"backup corrupt: {len(bad)} file(s) fail digest, e.g. {bad[:3]}")
        for item in stage.iterdir():
            dest = target / item.name
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(item), dest)
    conn = sqlite3.connect(target / "control.sqlite3")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    blob_rows = conn.execute("SELECT sha256, rel_path FROM blobs").fetchall()
    blob_bad = [sha for sha, rel in blob_rows if not (target / "blobs" / rel).is_file() or _sha(target / "blobs" / rel) != sha]
    releases = [json.loads(r[0]) for r in conn.execute("SELECT body FROM objects WHERE kind='release'")]
    missing = [e["immutable_id"] for r in releases for e in r["entries"]
               if not (target / "blobs" / e["bytes_sha256"][:2] / e["bytes_sha256"]).is_file()]
    pointers = [dict(zip(("pointer_key", "target_id", "generation"), row)) for row in
                conn.execute("SELECT pointer_key, target_id, generation FROM publication_pointers")]
    leftover = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in REVOKED_TABLES}
    schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    authority = {row[0] for row in conn.execute("SELECT authority_revision FROM publication_pointers")}
    conn.close()
    compatibility = _compatibility(schema[0] if schema else None, authority)
    ok = integrity == "ok" and not blob_bad and not missing and not any(leftover.values()) and compatibility["ok"]
    return {"restored_to": str(target), "files_verified": len(manifest["files"]), "sqlite_integrity": integrity,
            "blobs_verified": len(blob_rows) - len(blob_bad), "blob_failures": blob_bad[:5],
            "release_entries_missing_blobs": missing[:5], "pointers": pointers,
            "session_state": {"policy": manifest.get("session_policy", "not_recorded"), "remaining_rows": leftover},
            "compatibility": compatibility, "result": "ok" if ok else "failed"}


def _compatibility(schema_version: str | None, pointer_authority: set[str]) -> dict:
    from ..acceptance import load_reviewers
    from ..contracts import EXPECTED_MANIFEST_SHA256, load_contracts
    from ..policy import permission_revision
    from ..store.db import SCHEMA_VERSION
    problems, notes = [], []
    if schema_version != str(SCHEMA_VERSION):
        problems.append(f"store schema {schema_version} != running code {SCHEMA_VERSION}")
    try:
        load_contracts()
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        problems.append(f"frozen contracts do not verify: {type(exc).__name__}")
    current = permission_revision()
    stale = sorted(a for a in pointer_authority if a and a != current)
    if stale:
        notes.append(f"{len(stale)} pointer(s) were published under an older access-policy revision; contexts "
                     "re-resolve under the current policy and old capabilities are refused")
    reviewers = load_reviewers().get("reviewers", {})
    if any(r.get("specimen") for r in reviewers.values()):
        notes.append("reviewer allowlist in use contains specimen reviewers (test registry, not production)")
    return {"ok": not problems, "problems": problems, "notes": notes, "contract_baseline": EXPECTED_MANIFEST_SHA256,
            "policy_revision": current}
