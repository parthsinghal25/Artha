"""E10: the D02 portal JSON (actual accepted bytes) — exact original retained internally; its labelled hash-bound text
projection reaches an accepted release only with BOTH the source decision on the exact JSON bytes and a separate
extraction/derived-projection decision on the projection bytes. Decisions here are isolated test specimens."""
from __future__ import annotations

import hashlib

import pytest

from conftest import data_dir, rt  # noqa: F401
from specimens import decide, mini_bundle
from test_m0_receiving import CASE, PORTAL

pytestmark = pytest.mark.skipif(not PORTAL.exists(), reason="accepted microbatch-v2 delivery not present")


def _bundle(tmp_path):
    return mini_bundle(tmp_path, [{"original_id": "ORIG-D02", "rel": "originals/portal/d02.json",
                                   "data": PORTAL.read_bytes(), "media": "application/json", "case": CASE,
                                   "available": "2025-12-31T16:00:00+11:00", "kind": "portal_submission"}])


def test_projection_needs_source_and_derived_projection_decisions(rt, tmp_path):  # noqa: F811
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    from sdbackend.extract.jsondoc import projection_bytes
    data = PORTAL.read_bytes()
    orig_sha = hashlib.sha256(data).hexdigest()
    proj, header = projection_bytes("ORIG-D02", orig_sha, data)
    proj_sha = hashlib.sha256(proj).hexdigest()
    bundle = _bundle(tmp_path)

    # 1) projection decision alone: never enough (the exact original bytes are unreviewed)
    decide(rt, "extraction", [{"subject_kind": "derived_projection", "subject_id": "ORIG-D02",
                               "subject_sha256": proj_sha}])
    assert ingest(rt, receive(bundle, rt=rt))["status"] == "unreviewed_candidate"
    # 2) a source pass bound to the PROJECTION digest is not a decision on the original
    decide(rt, "source", [{"subject_kind": "supplied_original", "subject_id": "ORIG-D02", "subject_sha256": proj_sha}])
    assert ingest(rt, receive(bundle, rt=rt))["status"] == "unreviewed_candidate"
    # 3) source pass on the exact JSON bytes + the separate projection decision: accepted
    decide(rt, "source", [{"subject_kind": "supplied_original", "subject_id": "ORIG-D02", "subject_sha256": orig_sha}])
    summary = ingest(rt, receive(bundle, rt=rt))
    assert summary["status"] == "accepted"
    held = rt.db.get_object("held_original", "ORIG-D02")
    assert (held["bytes_sha256"], held["media_type"], held["projection_sha256"]) == (orig_sha, "application/json",
                                                                                      proj_sha)
    assert held["projection_header_codepoints"] == header


def test_source_decision_without_projection_decision_stays_unserved(rt, tmp_path, monkeypatch):  # noqa: F811
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    data = PORTAL.read_bytes()
    decide(rt, "source", [{"subject_kind": "supplied_original", "subject_id": "ORIG-D02",
                           "subject_sha256": hashlib.sha256(data).hexdigest()}])
    summary = ingest(rt, receive(_bundle(tmp_path), rt=rt))
    assert summary["status"] == "unreviewed_candidate" and summary["pointer"] == "release:candidate"
    monkeypatch.setenv("SDB_ALLOW_UNREVIEWED_SOURCES", "0")       # the served configuration: no bypass
    from sdbackend.api.repo import served_release
    from sdbackend.runtime import make_runtime
    assert served_release(make_runtime()) is None
