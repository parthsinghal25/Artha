"""Immutable content-addressed blob store for original/derived bytes and sealed packages.

Blobs are written create-only (temp file + fsync + atomic link) under their SHA-256 and re-verified on read.
`blob_reference` IDs are internal and never become public paths or URLs.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ..canonical import utc_now_iso
from .db import Database, IntegrityConflict


def blob_reference(sha256: str) -> str:
    return f"blob-sha256-{sha256}"


class BlobStore:
    def __init__(self, root: Path, db: Database):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise IntegrityConflict(f"stored blob {digest} is corrupt")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                try:
                    os.link(tmp, path)       # create-only: fails if a concurrent writer won
                except FileExistsError:
                    pass
            finally:
                os.unlink(tmp)
            os.chmod(path, 0o444)
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO blobs(sha256, byte_size, rel_path, stored_at) VALUES (?,?,?,?)",
                      (digest, len(data), str(path.relative_to(self.root)), utc_now_iso()))
        return digest

    def get(self, sha256: str) -> bytes:
        data = self._path(sha256).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise IntegrityConflict(f"blob {sha256} failed verification")
        return data

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def size(self, sha256: str) -> int:
        return self._path(sha256).stat().st_size

    def open_path(self, sha256: str) -> Path:
        return self._path(sha256)
