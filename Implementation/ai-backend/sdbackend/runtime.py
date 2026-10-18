"""Process-wide wiring of settings, store, blobs and contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from functools import lru_cache

from .config import Settings, load_settings
from .contracts import Contracts, load_contracts
from .store.blobs import BlobStore
from .store.db import Database


@dataclass
class Runtime:
    settings: Settings
    db: Database
    blobs: BlobStore
    contracts: Contracts
    state: Any = None          # serving request state (api/state.py): local SQLite tables or Firestore


def make_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    from .api.state import make_state
    return Runtime(settings=settings, db=db, blobs=BlobStore(settings.blob_dir, db), contracts=load_contracts(),
                   state=make_state(db))


@lru_cache(maxsize=1)
def default_runtime() -> Runtime:
    return make_runtime()
