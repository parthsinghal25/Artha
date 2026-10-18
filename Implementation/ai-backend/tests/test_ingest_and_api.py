import json

import pytest
from fastapi.testclient import TestClient

from sdbackend.contracts import load_contracts

CASE = "C-e1da28538652e263887a"


def _expected_sources():
    from sdbackend.extract.image import ocr_runtime_status
    return 12 if ocr_runtime_status()["available"] else 11


EXPECTED_SOURCES = _expected_sources()


def test_ingest_real_rehearsal_is_contract_valid_and_idempotent(ingested, rehearsal_bundle):
    rt, summary = ingested
    assert summary["status"] == "unreviewed_candidate"          # no independent review exists
    from sdbackend.extract.image import ocr_runtime_status
    if ocr_runtime_status()["available"]:
        assert summary["sources"] == 12 and summary["extractions"]["failed"] == 0
    else:   # OCR unavailable in this runtime: the receipt image stays a visible failed file, never a fake source
        assert summary["sources"] == 11 and summary["extractions"]["failed"] == 1
        assert any("OCR unavailable" in l for l in summary["limitations"])
    release = rt.db.get_object("release", summary["release_id"])
    c = load_contracts()
    assert not c.errors("release", release)
    for a in rt.db.list_objects("asset"):
        assert not c.errors("asset", a)
    for att in rt.db.list_objects("attachment"):
        assert not c.errors("attachment", att)
    from sdbackend.canonical import source_content_sha256
    for s in rt.db.list_objects("source"):
        assert source_content_sha256(s["data"]) == s["data"]["content_sha256"]
    # every release entry's blob exists and matches its digest
    for e in release["entries"]:
        assert rt.blobs.exists(e["bytes_sha256"])
    # reused attachment bytes are one asset version, several occurrences
    dc = [a for a in rt.db.list_objects("asset") if a["filename"] == "DC-11.pdf"]
    assert len(dc) == 1
    occ = rt.db.linked("attachment", "child_asset", dc[0]["asset_version_id"])
    assert len(occ) == 2
    # advertised-but-unheld edge: null child, no fake bytes
    unheld = [a for a in rt.db.list_objects("attachment") if a["relation"] == "advertised_unheld"]
    assert len(unheld) == 1 and unheld[0]["child_asset_version_id"] is None
    # idempotent
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    assert ingest(rt, receive(rehearsal_bundle))["release_id"] == summary["release_id"]


def test_tampered_delivery_byte_rejected(tmp_path, rehearsal_bundle):
    import shutil
    from sdbackend.intake.delivery import DeliveryRejected, receive
    copy = tmp_path / "bundle"
    shutil.copytree(rehearsal_bundle, copy)
    target = copy / "originals" / "attachments" / "11-07-receiving-document-checklist.csv"
    target.write_bytes(target.read_bytes().replace(b"outstanding", b"accepted!!!"))
    with pytest.raises(DeliveryRejected):     # INTAKE-FILES lists manifest digest; manifest unchanged ...
        m = copy / "delivery-manifest.json"
        m.write_bytes(m.read_bytes() + b" ")
        receive(copy)
    m.write_bytes(m.read_bytes()[:-1])
    d = receive(copy)
    row = [e for e in d.receipt["originals"] if e["file"].endswith("checklist.csv")][0]
    assert row["disposition"] == "rejected" and "differs" in " ".join(row["reasons"])


@pytest.fixture()
def client(ingested):
    rt, summary = ingested
    from sdbackend.api.app import create_app
    from sdbackend.api.auth import login_codes
    app = create_app(rt)
    c = TestClient(app, base_url="http://127.0.0.1:8080")
    codes = login_codes(rt)

    def signin(principal):
        r = c.post("/v1/session", json={"principal_id": principal, "login_code": codes[principal]})
        assert r.status_code == 200
        return {"X-CSRF-Token": r.json()["csrf_token"]}
    return c, summary["release_id"], signin, rt


def _ctx(c, h, rel, cutoff="2026-10-01T00:00:00+10:00"):
    r = c.post("/v1/operations", headers=h, json={"operation": "context.resolve", "preferred_view_id": None,
                                                  "release_id": rel, "evidence_as_of": cutoff,
                                                  "analysis_mode": "reconstructed", "analysis_as_of": None,
                                                  "population_reference": None}).json()
    return r


def op(c, h, body):
    r = c.post("/v1/operations", headers=h, json=body)
    assert r.status_code == 200
    body_out = r.json()
    assert not load_contracts().errors(f"{body['operation']}.response", body_out)
    return body_out


def test_auth_csrf_and_origin(client):
    c, rel, signin, rt = client
    # Under zero-login auto-authentication (ADR-001), empty requests auto-authenticate and fail with 400 (unknown operation)
    assert c.post("/v1/operations", json={}).status_code in (400, 401)
    assert c.post("/v1/session", json={"principal_id": "leader.demo", "login_code": "wrong"}).status_code == 401
    h = signin("leader.demo")
    # CSRF check fires when an invalid CSRF header is provided
    assert c.post("/v1/operations", headers={**h, "X-CSRF-Token": "invalid"}, json={"operation": "context.resolve"}).status_code == 403
    bad_origin = dict(h, Origin="http://evil.example")
    assert c.post("/v1/operations", headers=bad_origin, json={}).status_code == 403
    assert "sdb_csrf" in c.cookies


def test_source_and_file_inventories_and_paging(client):
    c, rel, signin, rt = client
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    seen, cursor = [], None
    while True:
        r = op(c, h, {"operation": "case.sources.list", "context_id": ctx["context_id"], "case_id": CASE,
                      "source_families": [], "cursor": cursor, "page_size": 5})
        seen += [i["source_id"] for i in r["payload"]["items"]]
        cursor = r["payload"]["next_cursor"]
        assert r["payload"]["total_permitted_count"] == EXPECTED_SOURCES
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == EXPECTED_SOURCES
    files = op(c, h, {"operation": "case.files.list", "context_id": ctx["context_id"], "case_id": CASE,
                      "media_types": [], "cursor": None, "page_size": 100})
    states = {i["asset"]["filename"]: i["extraction_state"] for i in files["payload"]["items"]}
    from sdbackend.extract.image import ocr_runtime_status
    # supported runtime: unreviewed OCR declared `partial`; OCR unavailable (e.g. sandboxed Vision): the asset stays
    # listed with an explicit `failed` extraction — never hidden, never a copied historic transcription (F06)
    expected = "partial" if ocr_runtime_status()["available"] else "failed"
    assert states["11-05-portal-upload-receipt.jpg"] == expected


def test_cursor_bound_to_stream(client):
    c, rel, signin, rt = client
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    r = op(c, h, {"operation": "case.sources.list", "context_id": ctx["context_id"], "case_id": CASE,
                  "source_families": [], "cursor": None, "page_size": 2})
    misuse = op(c, h, {"operation": "case.files.list", "context_id": ctx["context_id"], "case_id": CASE,
                       "media_types": [], "cursor": r["payload"]["next_cursor"], "page_size": 2})
    assert misuse["status"] == "unavailable" and misuse["error"]["code"] == "INVALID_REQUEST"


def test_evidence_cutoff_hides_future_sources(client):
    c, rel, signin, rt = client
    h = signin("leader.demo")
    early = _ctx(c, h, rel, "2026-01-05T00:00:00+11:00")["context"]
    r = op(c, h, {"operation": "case.sources.list", "context_id": early["context_id"], "case_id": CASE,
                  "source_families": [], "cursor": None, "page_size": 100})
    for item in r["payload"]["items"]:
        assert item["scenario_available_at"] <= "2026-01-05"
    assert r["payload"]["total_permitted_count"] < 12


def test_denied_and_nonexistent_are_indistinguishable(client):
    c, rel, signin, rt = client
    h = signin("restricted.demo")
    ctx = _ctx(c, h, rel)["context"]
    denied = op(c, h, {"operation": "case.sources.list", "context_id": ctx["context_id"], "case_id": CASE,
                       "source_families": [], "cursor": None, "page_size": 5})
    missing = op(c, h, {"operation": "case.sources.list", "context_id": ctx["context_id"], "case_id": "C-none",
                        "source_families": [], "cursor": None, "page_size": 5})
    assert denied["error"]["code"] == missing["error"]["code"] == "RESOURCE_UNAVAILABLE"
    assert denied["error"]["message"] == missing["error"]["message"]


def test_asset_redemption_range_and_capabilities(client):
    c, rel, signin, rt = client
    h = signin("analyst.demo")               # no download capability
    ctx = _ctx(c, h, rel)["context"]
    files = op(c, h, {"operation": "case.files.list", "context_id": ctx["context_id"], "case_id": CASE,
                      "media_types": ["application/pdf"], "cursor": None, "page_size": 100})["payload"]["items"]
    asset = files[0]["asset"]
    assert asset["permitted_actions"] == ["inline"]
    dl = op(c, h, {"operation": "asset.get", "context_id": ctx["context_id"],
                   "asset_version_id": asset["asset_version_id"], "action": "download"})
    assert dl["error"]["code"] == "RESOURCE_UNAVAILABLE"
    inl = op(c, h, {"operation": "asset.get", "context_id": ctx["context_id"],
                    "asset_version_id": asset["asset_version_id"], "action": "inline"})["payload"]
    full = c.get(f"/v1/assets/{inl['retrieval_token']}")
    assert full.status_code == 200
    import hashlib
    assert hashlib.sha256(full.content).hexdigest() == asset["bytes_sha256"] == inl["bytes_sha256"]
    part = c.get(f"/v1/assets/{inl['retrieval_token']}", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == full.content[10:20]
    assert c.get(f"/v1/assets/{inl['retrieval_token']}", headers={"Range": "bytes=99999999-"}).status_code == 416
    # another principal cannot redeem the token
    signin("leader.demo")
    assert c.get(f"/v1/assets/{inl['retrieval_token']}").status_code == 404


def test_policy_change_invalidates_context_and_tokens(client, monkeypatch, tmp_path):
    c, rel, signin, rt = client
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    files = op(c, h, {"operation": "case.files.list", "context_id": ctx["context_id"], "case_id": CASE,
                      "media_types": [], "cursor": None, "page_size": 1})["payload"]["items"]
    tok = op(c, h, {"operation": "asset.get", "context_id": ctx["context_id"],
                    "asset_version_id": files[0]["asset"]["asset_version_id"], "action": "inline"})["payload"]
    import sdbackend.policy as policy
    p = json.loads(policy.POLICY_PATH.read_text())
    p["views"]["complaints-leadership"]["denied_case_ids"] = [CASE]
    alt = tmp_path / "policy.json"
    alt.write_text(json.dumps(p))
    monkeypatch.setattr(policy, "POLICY_PATH", alt)
    monkeypatch.setattr(policy.load_policy, "__defaults__", (alt,))
    r = op(c, h, {"operation": "case.files.list", "context_id": ctx["context_id"], "case_id": CASE,
                  "media_types": [], "cursor": None, "page_size": 1})
    assert r["error"]["code"] == "CONTEXT_EXPIRED"
    assert c.get(f"/v1/assets/{tok['retrieval_token']}").status_code in (404, 410)


def test_analysis_operations_are_honestly_not_calculated(client):
    c, rel, signin, rt = client
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    assert ctx["analysis_set_version"] is None and ctx["supported_definitions"] == []
    case = op(c, h, {"operation": "case.get", "context_id": ctx["context_id"], "case_id": CASE, "cursor": None,
                     "page_size": 10})
    assert case["error"]["code"] == "NOT_CALCULATED"
    unknown = op(c, h, {"operation": "analysis.query", "context_id": ctx["context_id"], "query_id": "Q",
                        "definition": {"registry_id": "runtime-measures-0.2.0", "measure_id": "no.such",
                                       "definition_version": "0.2.0"}, "filters": FILTERS, "population_reference": None})
    assert unknown["error"]["code"] == "DEFINITION_UNAVAILABLE"
    asrep = c.post("/v1/operations", headers=h, json={**_ctx_req(rel), "analysis_mode": "as_reported",
                                                      "analysis_as_of": "2026-10-01T00:00:00Z"}).json()
    assert asrep["error"]["code"] == "HISTORY_UNAVAILABLE"


def _ctx_req(rel):
    return {"operation": "context.resolve", "preferred_view_id": None, "release_id": rel,
            "evidence_as_of": "2026-10-01T00:00:00+10:00", "analysis_mode": "reconstructed", "analysis_as_of": None,
            "population_reference": None}


FILTERS = {"activity_start": "2025-10-01T00:00:00+10:00", "activity_end_exclusive": "2026-10-01T00:00:00+10:00",
           "receipt_cohort_start": None, "receipt_cohort_end_exclusive": None, "receipt_basis": "original_institution",
           "granularity": "monthly", "issue_ids": [], "selection_operator": "none",
           "issue_population_mode": "active_in_window", "case_ids": [], "afca_matter_ids": [], "case_statuses": [],
           "age_bands": [], "taxonomy_level": None, "combination_projection": "not_applicable", "parameters": {}}


def test_unreviewed_release_not_served_without_operator_flag(ingested, monkeypatch):
    rt, summary = ingested
    monkeypatch.setenv("SDB_ALLOW_UNREVIEWED_SOURCES", "0")
    from sdbackend.api.repo import served_release
    from sdbackend.runtime import make_runtime
    assert served_release(make_runtime()) is None


def test_backup_and_verified_restore(ingested, tmp_path):
    from sdbackend.ops.backup import backup, restore
    out = backup(tmp_path / "bk")
    rep = restore(__import__("pathlib").Path(out["archive"]), tmp_path / "restored")
    assert rep["result"] == "ok" and rep["sqlite_integrity"] == "ok"
    assert any(p["pointer_key"] == "release:candidate" for p in rep["pointers"])


def test_backup_revokes_sessions_and_restore_checks_compatibility(ingested, tmp_path, monkeypatch):
    rt, summary = ingested
    import sqlite3
    from pathlib import Path
    from fastapi.testclient import TestClient
    from sdbackend.api import auth
    from sdbackend.api.app import create_app
    from sdbackend.ops.backup import backup, restore
    client = TestClient(create_app(rt))
    code = auth.login_codes(rt)["leader.demo"]
    assert client.post("/v1/session", json={"principal_id": "leader.demo", "login_code": code}).status_code == 200
    live = rt.db.connect().execute("SELECT csrf_token FROM sessions").fetchall()
    assert live                                                       # an active session + CSRF secret exists
    out = backup(tmp_path / "bk")
    assert out["revoked_rows"]["sessions"] >= 1
    rep = restore(Path(out["archive"]), tmp_path / "restored")
    assert rep["result"] == "ok" and rep["session_state"]["policy"] == "revoked_at_backup"
    assert set(rep["session_state"]["remaining_rows"].values()) == {0}
    assert rep["compatibility"]["ok"] and any("specimen" in n for n in rep["compatibility"]["notes"])
    raw = (tmp_path / "restored" / "control.sqlite3").read_bytes()
    assert not any(row[0].encode() in raw for row in live)            # no CSRF secret survives in freed pages
    conn = sqlite3.connect(tmp_path / "restored" / "control.sqlite3")
    assert conn.execute("SELECT count(*) FROM objects WHERE kind='release'").fetchone()[0] >= 1


def test_forwarding_headers_never_establish_origin_trust(client):
    c, rel, signin, rt = client
    from sdbackend.api.auth import login_codes
    spoof = {"Origin": "http://evil.example", "X-Forwarded-Host": "evil.example"}   # matched each other before R11
    assert c.post("/v1/session", json={"principal_id": "leader.demo", "login_code": login_codes(rt)["leader.demo"]},
                  headers=spoof).status_code == 403
    h = signin("leader.demo")
    assert c.post("/v1/operations", json={"operation": "context.resolve"}, headers={**h, **spoof}).status_code == 403
    assert c.post("/v1/operations", headers={**h, "Origin": "http://127.0.0.1:8080"},
                  json={"operation": "context.resolve", "preferred_view_id": None, "release_id": rel,
                        "evidence_as_of": "2026-10-01T00:00:00+10:00", "analysis_mode": "reconstructed",
                        "analysis_as_of": None, "population_reference": None}).status_code == 200
