"""Isolated specimen tests of the deterministic result engine and drill-through.

Native case rows, deliveries and reviewer decisions here are TEST SPECIMENS in temporary stores (see specimens.py).
They are not Data's native register rows, not real reviews and never presented as findings.
"""
import pytest

from sdbackend.contracts import load_contracts
from sdbackend.engine.core import binding_consistent
from sdbackend.engine.publish import PublicationRefused, prepare_closure, publish_analysis_set

from specimens import approve_required, case_row, ingest_specimen, publish_native
from test_ingest_and_api import FILTERS, _ctx, op  # noqa: F401

DEF = {"registry_id": "runtime-measures-0.2.0", "measure_id": "cases.new", "definition_version": "0.2.0"}
SPECIMEN_DATES = {"SPEC-CASE-1": "2026-01-15", "SPEC-CASE-2": "2026-01-31", "SPEC-CASE-3": "2026-03-02",
                  "SPEC-CASE-4": None}


@pytest.fixture()
def published(rt, rehearsal_bundle, tmp_path):
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [case_row(k, v) for k, v in SPECIMEN_DATES.items()])
    aset, _ = publish_native(rt, summary["release_id"])
    return rt, summary, aset


@pytest.fixture()
def pclient(published):
    rt, summary, aset = published
    from fastapi.testclient import TestClient
    from sdbackend.api.app import create_app
    from sdbackend.api.auth import login_codes
    c = TestClient(create_app(rt), base_url="http://127.0.0.1:8080")
    codes = login_codes(rt)

    def signin(principal):
        r = c.post("/v1/session", json={"principal_id": principal, "login_code": codes[principal]})
        return {"X-CSRF-Token": r.json()["csrf_token"]}
    return c, summary["release_id"], signin, rt


def test_publication_requires_exact_independent_decisions(rt, rehearsal_bundle, tmp_path):
    summary, _ = ingest_specimen(rt, tmp_path, rehearsal_bundle, [case_row("SPEC-CASE-1", "2026-01-01")],
                                 approve_natives=False)
    prepared = prepare_closure(rt, release_id=summary["release_id"], assessed_evidence_as_of="2026-10-01T00:00:00+10:00")
    with pytest.raises(PublicationRefused, match="native_input"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])
    approve_required(rt, prepared, skip=("result_integrity",))
    with pytest.raises(PublicationRefused, match="result_integrity"):
        publish_analysis_set(rt, candidate_id=prepared["candidate_id"])
    assert rt.db.get_pointer(f"analysis:{summary['release_id']}") is None      # refusal leaves no pointer


def test_cases_new_series_and_drill_through(pclient):
    c, rel, signin, rt = pclient
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    assert ctx["analysis_set_version"] and DEF in ctx["supported_definitions"]
    res = op(c, h, {"operation": "analysis.query", "context_id": ctx["context_id"], "query_id": "Q1",
                    "definition": DEF, "filters": FILTERS, "population_reference": None})
    assert res["status"] == "ok", res.get("error")
    result = res["payload"]
    cells = {cell["key"]: cell for cell in result["cells"]}
    assert len(cells) == 12
    assert cells["2026-01-01"]["point"]["value"] == 2 and cells["2026-03-01"]["point"]["value"] == 1
    assert cells["2026-02-01"]["point"]["value"] == 0
    assert cells["2026-01-01"]["coverage"]["missing_count"] == 1      # SPEC-CASE-4 unknown receipt date disclosed
    assert all(binding_consistent(cell["population"]) for cell in result["cells"])
    assert len({cell["population"]["predicate_sha256"] for cell in result["cells"]}) == 12

    jan = cells["2026-01-01"]
    seen, cursor = [], None
    while True:
        page = op(c, h, {"operation": "contributors.list", "context_id": ctx["context_id"],
                         "result_id": result["result_id"], "cell_key": "2026-01-01", "cursor": cursor, "page_size": 1})
        assert page["payload"]["total_count"] == 2
        assert page["payload"]["predicate_sha256"] == jan["population"]["predicate_sha256"]
        seen += [i["entity_id"] for i in page["payload"]["items"]]
        cursor = page["payload"]["next_cursor"]
        if cursor is None:
            break
    assert seen == ["SPEC-CASE-1", "SPEC-CASE-2"]

    ref = {"result_id": result["result_id"], "cell_key": "2026-01-01", "population_id": jan["population_id"],
           "predicate_sha256": jan["population"]["predicate_sha256"]}
    child = op(c, h, {"operation": "context.resolve", "preferred_view_id": None, "release_id": rel,
                      "evidence_as_of": "2026-06-01T00:00:00+10:00", "analysis_mode": "reconstructed",
                      "analysis_as_of": None, "population_reference": ref})
    assert child["status"] == "ok", child.get("error")
    cctx = child["context"]
    assert cctx["origin_population_reference"] == ref
    assert cctx["evidence_as_of"] == jan["population"]["effective_evidence_as_of"]   # inherited, not the request's

    narrowed = op(c, h, {"operation": "analysis.query", "context_id": cctx["context_id"], "query_id": "Q2",
                         "definition": DEF, "filters": dict(FILTERS, granularity="none"), "population_reference": ref})
    assert narrowed["payload"]["cells"][0]["point"]["value"] == 2

    tampered = dict(ref, predicate_sha256="0" * 64)
    bad = op(c, h, {"operation": "context.resolve", "preferred_view_id": None, "release_id": rel,
                    "evidence_as_of": "2026-06-01T00:00:00+10:00", "analysis_mode": "reconstructed",
                    "analysis_as_of": None, "population_reference": tampered})
    assert bad["error"]["code"] == "RESOURCE_UNAVAILABLE"


def test_unsupported_filters_and_partial_bucket(pclient):
    c, rel, signin, rt = pclient
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel, cutoff="2026-03-15T12:00:00+11:00")["context"]
    res = op(c, h, {"operation": "analysis.query", "context_id": ctx["context_id"], "query_id": "Q3",
                    "definition": DEF, "filters": FILTERS, "population_reference": None})
    cells = {cell["key"]: cell for cell in res["payload"]["cells"]}
    march = cells["2026-03-01"]
    assert march["partial_bucket"] and march["point"]["state"] == "partial"
    april = cells["2026-04-01"]                     # wholly after the cutoff: unavailable, not a known zero
    assert april["point"]["state"] == "unavailable" and april["point"]["value"] is None
    bad = op(c, h, {"operation": "analysis.query", "context_id": ctx["context_id"], "query_id": "Q4",
                    "definition": DEF, "filters": dict(FILTERS, age_bands=["0_7"]), "population_reference": None})
    assert bad["status"] == "unavailable" and bad["error"]["code"] == "INVALID_REQUEST"


def test_restricted_principal_cannot_list_contributors(pclient):
    c, rel, signin, rt = pclient
    h = signin("leader.demo")
    ctx = _ctx(c, h, rel)["context"]
    res = op(c, h, {"operation": "analysis.query", "context_id": ctx["context_id"], "query_id": "Q5",
                    "definition": DEF, "filters": FILTERS, "population_reference": None})
    assert not load_contracts().errors("result", res["payload"])
    rh = signin("restricted.demo")
    rctx = _ctx(c, rh, rel)["context"]
    denied = op(c, rh, {"operation": "contributors.list", "context_id": rctx["context_id"],
                        "result_id": res["payload"]["result_id"], "cell_key": "2026-01-01", "cursor": None,
                        "page_size": 10})
    assert denied["error"]["code"] == "RESOURCE_UNAVAILABLE"
