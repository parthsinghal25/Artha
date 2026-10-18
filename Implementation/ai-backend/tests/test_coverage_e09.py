"""E09: bounded discovery coverage travels from S05 into the published set, issue cells, contributors and intervals
(isolated specimens through real publish/query handlers)."""
from __future__ import annotations

from conftest import data_dir, rt  # noqa: F401
from test_analytical_r10 import three_cases  # noqa: F401
from test_followup_f01_f06 import _ctx, _publish_findings
from test_ingest_and_api import FILTERS as API_FILTERS
from specimens import case_row
from sdbackend.engine import results


def test_bounded_discovery_keeps_issue_cells_partial_but_contributors_exact(three_cases, tmp_path):  # noqa: F811
    from sdbackend.api.ops import Operations
    rows = [case_row(c, "2026-03-01") for c in ("C-A", "C-B", "C-C")]
    rt, reviewed, aset = _publish_findings(three_cases, natives=rows, tmp=tmp_path)
    cov = aset["discovery_coverage"]
    assert cov is not None and cov["exhaustive"] is False
    assert cov["examined_passages"] <= cov["candidate_passages"]
    ops = Operations(rt)
    ctx = _ctx(ops, reviewed["release_id"])
    tax = rt.db.get_object("taxonomy", aset["taxonomy_version"])
    node = tax["nodes"][0]["node_id"]

    def q(measure, **f):
        got = ops.handle("leader.demo", dict(
            operation="analysis.query", context_id=ctx["context_id"], query_id="E09",
            definition=dict(registry_id=results.REG, measure_id=measure, definition_version="0.2.0"),
            filters=dict(API_FILTERS, granularity="none", **f), population_reference=None))
        assert got["status"] == "ok", got
        return got["payload"]

    out = q("issues.linked_cases", issue_population_mode="all_linked_to_date", selection_operator="any",
            issue_ids=[node])
    cell = out["cells"][0]
    assert cell["point"]["value"] == 2                         # the examined subset is still counted exactly
    assert cell["point"]["state"] == "partial" and cell["coverage"]["assessment_state"] == "partial"
    assert "not an exhaustive affected population" in cell["point"]["reason"]
    assert any(l.startswith("discovery incomplete:") for l in cell["coverage"]["limitations"])
    contrib = ops.handle("leader.demo", dict(operation="contributors.list", context_id=ctx["context_id"],
                                             result_id=out["result_id"], cell_key=cell["key"], cursor=None,
                                             page_size=50))
    assert sorted(i["entity_id"] for i in contrib["payload"]["items"]) == ["C-A", "C-B"]
    # a measure that does not depend on issue discovery is not marked by it
    plain = q("cases.new")["cells"][0]
    assert not any(l.startswith("discovery incomplete:") for l in plain["coverage"]["limitations"])


def test_no_recorded_coverage_is_never_exhaustive():
    from sdbackend.engine.issues import discovery_gap
    assert discovery_gap({"taxonomy_version": "TAXV-1"})               # published issues, no coverage object
    assert discovery_gap({"taxonomy_version": None}) == []             # no issue analysis at all
    assert discovery_gap({"taxonomy_version": "T", "discovery_coverage": {"incomplete_reasons": [],
                                                                          "exhaustive": False}})
