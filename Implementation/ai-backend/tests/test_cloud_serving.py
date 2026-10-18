"""Hosted read demo (Artha): deployment export → verified restore → shared serving state → atomic activation.

Real: the pilot store (read-only), the SQLite online backup, tar/gzip, the backend read path and FastAPI app.
EMULATED: Firestore and Cloud Storage are in-process doubles (tests/cloud_fakes.py); no Google service is called.
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

from cloud_fakes import FakeFirestore, FakeObjectStore

BACKEND = Path(__file__).resolve().parents[1]
STORE = BACKEND / "var" / "demo" / "p4-pilot-v1"
pytestmark = pytest.mark.skipif(not (STORE / "control.sqlite3").is_file(), reason="pilot store not present")


def _pointers(db: Path) -> list:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [tuple(r) for r in c.execute("SELECT * FROM publication_pointers ORDER BY pointer_key")]
    finally:
        c.close()


@pytest.fixture(scope="module")
def export(tmp_path_factory):
    from sdbackend.cloud.publication import PILOT, export_serving_publication
    before = _pointers(STORE / "control.sqlite3")
    out = tmp_path_factory.mktemp("exports")
    r = export_serving_publication(STORE, BACKEND / PILOT["corpus"], BACKEND / PILOT["policy"], out, expect=dict(PILOT))
    assert _pointers(STORE / "control.sqlite3") == before          # live pointers never change
    return r


@pytest.fixture()
def serving_env(monkeypatch):
    for k in ("SDB_ALLOW_UNREVIEWED_SOURCES", "SDB_ALLOW_SPECIMEN_ACCEPTANCE", "SDB_REVIEWER_REGISTRY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "SDB_SERVING_STATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SDB_NO_DOTENV", "1")
    monkeypatch.setenv("SDB_HOSTED_DEMO", "1")
    return monkeypatch


def restore(export, target):
    from sdbackend.cloud.publication import restore_export
    return restore_export(Path(export["export_dir"]), target, manifest_sha256=export["manifest_sha256"])


def manifest(export) -> dict:
    return json.loads((Path(export["export_dir"]) / "ARTHA-EXPORT-MANIFEST.json").read_text())


# ------------------------------------------------------------------ export
def test_export_pins_the_pilot_bindings_coverage_and_provenance(export):
    m = manifest(export)
    b = m["bindings"]
    assert (b["release_id"], b["publication_id"], b["analysis_set_version"], b["taxonomy_version"]) == (
        "REL-8c0fb856c850adfe3674", "IPUB-f183e075f92f77c9a7a977e9", "ASET-ecc8f4a04020c04cde6c9bab",
        "TAXV-b90d96b59fd4796d5a640873")
    assert b["intelligence_schema_version"] == "1.1.0" and b["release_status"] == "synthetic_demo"
    assert b["permission_revision"] == b["pointer_authority_revision"]
    cov = m["coverage"]
    assert (cov["assessed_case_memories"], cov["received_cases_in_view"], cov["pending_memories"]) == (20, 25, 0)
    assert m["cutoff"]["view_date"] == "2026-09-30" and m["cutoff"]["knowledge_end_exclusive"].startswith("2026-10-01")
    assert m["model_provenance"]["case_memories"] == [{"provider": "gemma-local-mlx", "memories": 20,
                                                       "model": "mlx-community/gemma-4-e4b-it-4bit"}]
    assert m["model_provenance"]["dashboard_reads_call_models"] is False
    assert any(x["publication_id"] == b["publication_id"] for x in m["publication_log"])


def test_export_excludes_sessions_secrets_and_live_files(export):
    m = manifest(export)
    names = set(m["payload"]["members"])
    assert all(n.startswith(("data/", "bindings/")) for n in names)
    assert not any(x in n for n in names for x in ("login-codes", ".env", "/run/", "agy-traces", "-wal", "-shm"))
    with tarfile.open(Path(export["export_dir"]) / "payload.tar.gz") as tar:
        db = tar.extractfile("data/control.sqlite3").read()
    tmp = Path(export["export_dir"]).parent / "peek.sqlite3"
    tmp.write_bytes(db)
    c = sqlite3.connect(tmp)
    assert {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("sessions", "contexts", "cursors", "asset_tokens", "rate_reservations")} == dict.fromkeys(
        ("sessions", "contexts", "cursors", "asset_tokens", "rate_reservations"), 0)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    c.close()
    tmp.unlink()


def test_export_refuses_a_wrong_expected_binding(tmp_path):
    from sdbackend.cloud.publication import PILOT, ExportRefused, export_serving_publication
    with pytest.raises(ExportRefused, match="publication_id"):
        export_serving_publication(STORE, BACKEND / PILOT["corpus"], BACKEND / PILOT["policy"], tmp_path,
                                   expect={"publication_id": "IPUB-000000000000000000000000"})
    assert not [p for p in tmp_path.iterdir() if not p.name.startswith(".")]     # nothing half-written


def test_partial_publication_is_refused_unless_disclosed(export, tmp_path):
    """A publication with pending eligible cases is not exported as complete (bindings checked on a copy)."""
    from sdbackend.cloud.publication import ExportRefused, validate_bindings
    from sdbackend.canonical import canonical_bytes, canonical_sha256
    with tarfile.open(Path(export["export_dir"]) / "payload.tar.gz") as tar:
        tar.extractall(tmp_path, filter="data")
    c = sqlite3.connect(tmp_path / "data" / "control.sqlite3")
    c.execute("DROP TRIGGER objects_no_update")
    pid = manifest(export)["bindings"]["publication_id"]
    body = json.loads(c.execute("SELECT body FROM objects WHERE kind='intel_publication' AND object_id=?", (pid,)).fetchone()[0])
    body["coverage"]["pending_memories"] = ["case-x"]
    c.execute("UPDATE objects SET body=?, sha256=? WHERE kind='intel_publication' AND object_id=?",
              (canonical_bytes(body).decode(), canonical_sha256(body), pid))
    c.execute("UPDATE publication_pointers SET target_sha256=? WHERE pointer_key='intel:current'", (canonical_sha256(body),))
    corpus = json.loads((tmp_path / "bindings/corpus.json").read_text())
    policy = json.loads((tmp_path / "bindings/policy.json").read_text())
    with pytest.raises(ExportRefused, match="partial"):
        validate_bindings(c, corpus, policy)
    assert validate_bindings(c, corpus, policy, allow_pending=True)["coverage"]["partial_publication"] is True
    c.close()


# ------------------------------------------------------------------ restore
def test_restore_into_empty_directory_serves_the_same_publication(export, tmp_path, serving_env):
    r = restore(export, tmp_path / "r")
    assert r["read_path"] == {"served_release": "REL-8c0fb856c850adfe3674",
                              "current_publication": "IPUB-f183e075f92f77c9a7a977e9", "ok": True}
    assert r["files_verified"] == manifest(export)["payload"]["member_count"] and r["blobs_verified"] > 800
    assert (tmp_path / "r" / "RESTORE-RECEIPT.json").is_file()


def test_restore_refuses_non_empty_target_and_tampered_artifacts(export, tmp_path, serving_env):
    from sdbackend.cloud.publication import RestoreRefused, restore_export
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "x").write_text("x")
    with pytest.raises(RestoreRefused, match="not empty"):
        restore(export, tmp_path / "busy")
    with pytest.raises(RestoreRefused, match="pointer"):
        restore_export(Path(export["export_dir"]), tmp_path / "m", manifest_sha256="0" * 64)
    bad = tmp_path / "bad"
    shutil.copytree(export["export_dir"], bad)
    data = bytearray((bad / "payload.tar.gz").read_bytes())
    data[len(data) // 2] ^= 0xFF
    (bad / "payload.tar.gz").write_bytes(bytes(data))
    with pytest.raises(RestoreRefused, match="payload digest"):
        restore_export(bad, tmp_path / "t1")
    # a payload that matches its manifest digest but carries an extra member is still refused
    extra = tmp_path / "extra"
    shutil.copytree(export["export_dir"], extra)
    raw = gzip.decompress((extra / "payload.tar.gz").read_bytes())
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(fileobj=buf, mode="w") as dst:
        for m in src.getmembers():
            dst.addfile(m, src.extractfile(m))
        info = tarfile.TarInfo("data/login-codes.json")
        info.size = 2
        dst.addfile(info, io.BytesIO(b"{}"))
    (extra / "payload.tar.gz").write_bytes(gzip.compress(buf.getvalue()))
    m = json.loads((extra / "ARTHA-EXPORT-MANIFEST.json").read_text())
    m["payload"]["sha256"] = hashlib.sha256((extra / "payload.tar.gz").read_bytes()).hexdigest()
    (extra / "ARTHA-EXPORT-MANIFEST.json").write_text(json.dumps(m))
    with pytest.raises(RestoreRefused, match="members differ"):
        restore_export(extra, tmp_path / "t2")
    assert not (tmp_path / "t2" / "RESTORE-RECEIPT.json").exists()


# ------------------------------------------------------------------ shared serving state across instances
def _instance(receipt, fs):
    from fastapi.testclient import TestClient

    from sdbackend.api.app import create_app
    from sdbackend.api.state import FirestoreServingState
    from sdbackend.config import load_settings
    from sdbackend.runtime import make_runtime
    s = dataclasses.replace(load_settings(), data_dir=Path(receipt["data_dir"]),
                            demo_corpus_path=Path(receipt["corpus_file"]), publication_policy="synthetic-demo-v1",
                            allow_unreviewed_sources=False)
    rt = make_runtime(s)
    rt.state = FirestoreServingState(fs)
    return TestClient(create_app(rt))


def _op(client, body):
    csrf = client.cookies.get("sdb_csrf")
    r = client.post("/v1/operations", json=body, headers={"X-CSRF-Token": csrf} if csrf else {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def two_instances(export, tmp_path, serving_env):
    from sdbackend.cloud.publication import serving_env as env_for
    ra, rb = restore(export, tmp_path / "a"), restore(export, tmp_path / "b")
    for k, v in env_for(ra).items():
        if v:
            serving_env.setenv(k, v)
    fs = FakeFirestore()
    return fs, ra, rb, _instance(ra, fs), _instance(rb, fs)


def test_session_context_cursor_and_asset_token_work_across_instances(two_instances, export, tmp_path):
    fs, ra, rb, a, b = two_instances
    ov = _op(a, {"operation": "intelligence.get", "context_id": None,
                 "selection": {"temporal_policy": "demo-held-day-v1", "section": "overview"}, "cursor": None,
                 "page_size": 50})
    assert ov["status"] == "ok" and set(a.cookies.keys()) >= {"sdb_session", "sdb_csrf"}
    ctx = ov["context"]["context_id"]
    b.cookies = a.cookies                                     # the next request lands on another instance
    iss = _op(b, {"operation": "intelligence.get", "context_id": ctx,
                  "selection": {"temporal_policy": "demo-held-day-v1", "section": "issues"}, "cursor": None,
                  "page_size": 50})
    assert iss["status"] == "ok" and iss["context"]["context_id"] == ctx
    top = max(iss["payload"]["items"], key=lambda i: i["membership"]["included"]["value"])
    cid = top["membership"]["included"]["contributors"][0]
    p1 = _op(a, {"operation": "case.memory.get", "context_id": ctx, "case_id": cid, "memory_revision_id": None,
                 "cursor": None, "page_size": 1})
    assert p1["status"] == "ok" and p1["payload"]["next_cursor"]
    p2 = _op(b, {"operation": "case.memory.get", "context_id": ctx, "case_id": cid, "memory_revision_id": None,
                 "cursor": p1["payload"]["next_cursor"], "page_size": 1})
    assert p2["status"] == "ok" and p2["payload"]["items"][0] != p1["payload"]["items"][0]
    claim = p1["payload"]["items"][0]
    src = _op(a, {"operation": "source.get", "context_id": ctx, "source_id": claim["source_id"],
                  "content_sha256": claim["content_sha256"], "citation": claim["ref"]})
    assert src["payload"]["citation_state"] == "valid"
    av = src["payload"]["extractions"][0]["permitted_asset_version_id"]
    act = _op(a, {"operation": "asset.get", "context_id": ctx, "asset_version_id": av, "action": "inline"})
    tok = act["payload"]
    got = b.get(f"/v1/assets/{tok['retrieval_token']}")
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == tok["bytes_sha256"]
    # a cold start (a new instance from a fresh restore) keeps the same session and context
    c = _instance(restore(export, tmp_path / "c"), fs)
    c.cookies = a.cookies
    again = _op(c, {"operation": "intelligence.get", "context_id": ctx,
                    "selection": {"temporal_policy": "demo-held-day-v1", "section": "coverage"}, "cursor": None,
                    "page_size": 50})
    assert again["status"] == "ok" and again["context"]["context_id"] == ctx
    # bearer secrets never appear in Firestore: documents are keyed by digests
    values = fs.all_values()
    secrets_ = [a.cookies["sdb_session"], tok["retrieval_token"], p1["payload"]["next_cursor"]]
    assert not any(s in v for s in secrets_ for v in values)


def test_hosted_mode_has_no_sign_in_and_assigns_the_demo_identity(two_instances):
    fs, ra, rb, a, b = two_instances
    assert a.get("/v1/session/signin").status_code == 404
    assert a.post("/v1/session", json={"principal_id": "leader.demo", "login_code": "x"}).status_code == 404
    who = a.get("/v1/session").json()
    assert who["principal_id"] == "leader.demo"
    ready = a.get("/readyz").json()
    assert ready["status"] == "ready" and ready["serving_state"] == "firestore"
    assert ready["unreviewed_sources_enabled"] is False


def test_health_stays_responsive_while_an_operation_runs(two_instances, monkeypatch):
    """A slow read must not block the event loop: Cloud Run's liveness probe on /healthz keeps answering."""
    import threading
    import time

    from sdbackend.api.ops import Operations
    fs, ra, rb, a, b = two_instances
    real, started = Operations.handle, threading.Event()

    def slow(self, principal_id, body):
        started.set()
        time.sleep(1.5)
        return real(self, principal_id, body)
    monkeypatch.setattr(Operations, "handle", slow)
    with _instance(ra, fs) as c:                              # one shared event loop, as under uvicorn
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", c.post("/v1/operations", json={
            "operation": "intelligence.get", "context_id": None, "cursor": None, "page_size": 5,
            "selection": {"temporal_policy": "demo-held-day-v1", "section": "coverage"}})))
        t.start()
        assert started.wait(5)
        t0 = time.monotonic()
        assert c.get("/healthz").status_code == 200
        assert time.monotonic() - t0 < 0.5 and t.is_alive()   # answered while the operation was still running
        t.join(10)
    assert out["r"].status_code == 200 and out["r"].json()["status"] == "ok"


# ------------------------------------------------------------------ upload / activate / rollback
def _uploaded(export, store):
    from sdbackend.cloud.remote import upload_export
    return upload_export(store, Path(export["export_dir"]))


def test_upload_is_create_only_and_verified(export, tmp_path):
    from sdbackend.cloud.remote import verify_remote
    store = FakeObjectStore(tmp_path)
    rec = _uploaded(export, store)
    assert rec["manifest"]["sha256"] == export["manifest_sha256"] and rec["payload"]["sha256"] == export["payload_sha256"]
    assert verify_remote(store, rec)["coverage"]["assessed_case_memories"] == 20
    with pytest.raises(Exception, match="exists"):
        _uploaded(export, store)


def test_activation_is_atomic_and_rollback_restores_the_previous_set(export, tmp_path):
    from sdbackend.cloud.remote import ActivationRefused, activate, pointer_status, rollback
    store, fs = FakeObjectStore(tmp_path), FakeFirestore()
    rec1 = _uploaded(export, store)
    first = activate(fs, store, rec1, expected_generation=0)
    assert first["generation"] == 1 and pointer_status(fs)["active"]["export_id"] == rec1["export_id"]
    with pytest.raises(ActivationRefused, match="already active"):
        activate(fs, store, rec1, expected_generation=1)
    with pytest.raises(ActivationRefused, match="generation"):
        activate(fs, store, rec1, expected_generation=0)          # stale compare-and-swap: nothing moves
    assert pointer_status(fs)["generation"] == 1
    # malformed / tampered candidates never move the pointer
    bad = json.loads(json.dumps(rec1))
    bad["payload"]["generation"] = store.overwrite_for_test(bad["payload"]["object"], b"not a payload")
    with pytest.raises(Exception):
        activate(fs, store, bad, expected_generation=1)
    wrong = {**rec1, "publication_id": "IPUB-other"}
    with pytest.raises(ActivationRefused, match="publication_id"):
        activate(fs, store, wrong, expected_generation=1)
    assert pointer_status(fs)["generation"] == 1 and pointer_status(fs)["active"] == rec1
    with pytest.raises(ActivationRefused, match="no previous"):
        rollback(fs, store, expected_generation=1)


def test_rollback_after_two_activations(export, tmp_path):
    from sdbackend.cloud.remote import activate, pointer_status, rollback
    store, fs = FakeObjectStore(tmp_path), FakeFirestore()
    rec1 = _uploaded(export, store)
    activate(fs, store, rec1, expected_generation=0)
    # successor stand-in: a restored copy with one extra harmless operational row, exported again (new export id)
    from sdbackend.cloud.publication import export_serving_publication, restore_export
    r = restore_export(Path(export["export_dir"]), tmp_path / "copy", verify_read_path=False)
    c = sqlite3.connect(r["data_dir"] + "/control.sqlite3")
    c.execute("INSERT INTO provider_events(provider, model, kind, detail, at) VALUES ('none','none','note','successor test','x')")
    c.commit()
    c.close()
    succ = export_serving_publication(Path(r["data_dir"]), Path(r["corpus_file"]), Path(r["policy_file"]),
                                      tmp_path / "succ")
    assert succ["export_id"] != rec1["export_id"]
    rec2 = _uploaded(succ, store)
    activate(fs, store, rec2, expected_generation=1)
    assert pointer_status(fs)["active"]["export_id"] == rec2["export_id"]
    back = rollback(fs, store, expected_generation=2)
    assert back == {"active": rec1["export_id"], "generation": 3, "previous": rec2["export_id"]}
    assert [h["action"] for h in pointer_status(fs)["history"]] == ["activate", "activate", "rollback"]


def test_boot_restores_the_active_pointer_and_refuses_a_tampered_object(export, tmp_path, serving_env, monkeypatch):
    from sdbackend.cloud import boot, remote
    from sdbackend.cloud.publication import RestoreRefused
    store, fs = FakeObjectStore(tmp_path / "objs"), FakeFirestore()
    (tmp_path / "objs").mkdir()
    monkeypatch.setattr("sdbackend.api.state.firestore_client", lambda: fs)
    monkeypatch.setattr(remote, "GcsObjectStore", lambda bucket: store)
    monkeypatch.setenv("ARTHA_PUBLICATION_SOURCE", "firestore")
    monkeypatch.setenv("ARTHA_BUCKET", "test-bucket")
    monkeypatch.setenv("ARTHA_RESTORE_ROOT", str(tmp_path / "root"))
    with pytest.raises(SystemExit, match="no active publication pointer"):
        boot.resolve_and_restore()
    rec = _uploaded(export, store)
    remote.activate(fs, store, rec, expected_generation=0)
    receipt = boot.resolve_and_restore()
    assert receipt["bindings"]["publication_id"] == "IPUB-f183e075f92f77c9a7a977e9"
    assert receipt["boot"]["pointer_generation"] == 1 and receipt["read_path"]["ok"] is True
    monkeypatch.setenv("ARTHA_EXPECTED_EXPORT_ID", "AEXP-some-other-export")
    with pytest.raises(SystemExit, match="expects AEXP-some-other-export"):
        boot.resolve_and_restore()
    monkeypatch.setenv("ARTHA_EXPECTED_EXPORT_ID", rec["export_id"])
    assert boot.resolve_and_restore()["export_id"] == rec["export_id"]
    store.corrupt_generation_for_test(rec["payload"]["object"], rec["payload"]["generation"], b"tampered")
    with pytest.raises(RestoreRefused, match="digest"):
        boot.resolve_and_restore()
