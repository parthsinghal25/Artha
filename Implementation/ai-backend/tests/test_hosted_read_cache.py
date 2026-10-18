"""Hosted read-only serving parses large sealed objects once per process; a changed row is never served stale,
and outside hosted mode every read parses afresh."""
from sdbackend.store import db as dbmod
from sdbackend.store.db import Database


def test_cache_only_in_hosted_mode_and_revalidated_by_sha(tmp_path, monkeypatch):
    d = Database(tmp_path / "c.sqlite3")
    d.put_object("intel_publication", "IPUB-x", {"v": 1})
    monkeypatch.delenv("SDB_HOSTED_DEMO", raising=False)
    assert d.get_object("intel_publication", "IPUB-x") is not d.get_object("intel_publication", "IPUB-x")
    monkeypatch.setenv("SDB_HOSTED_DEMO", "1")
    dbmod._READ_CACHE.clear()
    a = d.get_object("intel_publication", "IPUB-x")
    assert a == {"v": 1} and d.get_object("intel_publication", "IPUB-x") is a        # parsed once
    # objects are immutable in the store (a trigger refuses updates); a cache entry whose digest no longer matches
    # the row is still never served
    key = (str(d.path), "intel_publication", "IPUB-x")
    dbmod._READ_CACHE[key] = ("0" * 64, {"v": "stale"})
    assert d.get_object("intel_publication", "IPUB-x") == {"v": 1}
    assert d.get_object("intel_publication", "missing") is None
    d.put_object("source", "SRC-1", {"s": 1})                                         # other kinds: never cached
    assert d.get_object("source", "SRC-1") is not d.get_object("source", "SRC-1")
