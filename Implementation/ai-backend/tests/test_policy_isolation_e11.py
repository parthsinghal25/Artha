"""E11: an isolated configuration can mutate policy without touching the live `config/policy.json`.
`SDB_DATA_DIR` alone isolates the store only; `SDB_POLICY_PATH` isolates the policy file."""
from __future__ import annotations

import hashlib
import json

from conftest import data_dir, ingested, rehearsal_bundle, rt  # noqa: F401


def test_policy_path_override_isolates_mutation_tests(tmp_path, monkeypatch):
    from sdbackend import policy
    live = policy.POLICY_PATH
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    base_rev = policy.permission_revision()
    alt = tmp_path / "policy.json"
    body = json.loads(live.read_text())
    principal = next(iter(body["principals"]))
    body["principals"][principal]["capabilities"] = [c for c in body["principals"][principal]["capabilities"]
                                                     if c != "originals"]
    alt.write_text(json.dumps(body))
    monkeypatch.setenv("SDB_POLICY_PATH", str(alt))
    assert policy.policy_path() == alt
    assert policy.permission_revision() != base_rev                 # a real permission-revision change
    assert not policy.resolve_authority(principal).can("originals")
    monkeypatch.delenv("SDB_POLICY_PATH")
    assert policy.permission_revision() == base_rev
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before  # the live policy file was never written


def test_signin_form_page_lets_browsers_send_its_origin_and_null_stays_refused(ingested):
    """B-01: the HTML sign-in page must not use `no-referrer` (Chrome would post `Origin: null`)."""
    from fastapi.testclient import TestClient
    from sdbackend.api.app import create_app
    rt, _ = ingested
    c = TestClient(create_app(rt), base_url="http://127.0.0.1:8080")
    page = c.get("/v1/session/signin")
    assert page.status_code == 200 and page.headers["referrer-policy"] == "same-origin"
    form = {"principal_id": "leader.demo", "login_code": "wrong"}
    assert c.post("/v1/session", data=form, headers={"Origin": "null"}).status_code == 403
    assert c.post("/v1/session", data=form, headers={"Origin": "http://127.0.0.1:8080"}).status_code == 401
