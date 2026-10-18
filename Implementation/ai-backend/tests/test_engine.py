import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from sdbackend.engine.core import (MELBOURNE, age_days, binding_consistent, deadline_outcome, distribution,
                                   endpoint_age_date, ledger_union, parse_instant, r7, seal_binding, selection_digest)

EXAMPLES = Path(__file__).resolve().parents[3] / "Shared" / "contracts" / "v1" / "CONTRACT-EXAMPLES.json"


def test_r7_matches_definition():
    xs = [1, 2, 3, 4]
    assert r7(xs, 0.5) == 2.5
    assert r7(xs, 0.25) == pytest.approx(1.75)
    assert r7(xs, 0.9) == pytest.approx(3.7)
    assert r7([7], 0.9) == 7
    assert r7([], 0.5) is None


def test_distribution_reports_missing_and_never_zero_fills():
    d = distribution([10, None, 2, 6, None])
    assert (d["total_n"], d["known_n"], d["missing_n"]) == (5, 3, 2)
    assert d["median"] == 6 and d["min"] == 2 and d["max"] == 10
    empty = distribution([None])
    assert empty["median"] is None and empty["known_n"] == 0


def test_exclusive_midnight_endpoint_uses_previous_local_date_across_dst():
    # DST ends in Melbourne on 5 April 2026 (AEDT +11 -> AEST +10)
    endpoint = datetime(2026, 4, 6, 0, 0, tzinfo=MELBOURNE)
    assert endpoint_age_date(endpoint, "exclusive_stock_endpoint") == date(2026, 4, 5)
    assert endpoint_age_date(endpoint, "inclusive_cutoff") == date(2026, 4, 6)
    assert age_days(date(2026, 4, 4), endpoint, "exclusive_stock_endpoint") == 1
    # UTC instant that is midnight Melbourne after DST change
    utc = parse_instant("2026-04-05T14:00:00Z")
    assert endpoint_age_date(utc, "exclusive_stock_endpoint") == date(2026, 4, 5)


def test_negative_or_missing_age_is_not_clamped():
    endpoint = parse_instant("2026-01-01T00:00:00+11:00")
    assert age_days(None, endpoint, "inclusive_cutoff") is None
    assert age_days(date(2026, 2, 1), endpoint, "inclusive_cutoff") is None


def test_population_digest_reproduces_frozen_examples():
    ex = json.loads(EXAMPLES.read_text())
    for cell in ex["two_endpoint_result"]["cells"]:
        b = cell["population"]
        assert selection_digest(b) == b["predicate_sha256"]
        assert binding_consistent(b)
    tampered = json.loads(json.dumps(ex["two_endpoint_result"]["cells"][0]["population"]))
    tampered["versions"]["analysis_set_version"] = "other"
    assert not binding_consistent(tampered)
    assert binding_consistent(seal_binding(tampered))


def test_digest_excludes_actual_clocks():
    b = json.loads(EXAMPLES.read_text())["two_endpoint_result"]["cells"][0]["population"]
    b2 = dict(b, computed_at="2030-01-01T00:00:00Z", published_at="2030-01-01T00:00:00Z")
    assert selection_digest(b2) == selection_digest(b)


DUE = parse_instant("2026-06-01T00:00:00+10:00")   # "by 31 May" -> exclusive next-day boundary
CUT = parse_instant("2026-09-30T00:00:00+10:00")


@pytest.mark.parametrize("completion,unmet,expected", [
    ({"precision": "instant", "instant": "2026-05-31T23:59:00+10:00"}, False, "on_time"),
    ({"precision": "instant", "instant": "2026-06-01T00:00:00+10:00"}, False, "late"),
    ({"precision": "day", "date": "2026-05-31"}, False, "on_time"),
    ({"precision": "day", "date": "2026-06-01"}, False, "late"),
    (None, True, "explicitly_unmet"),
    (None, False, "unknown"),          # missing confirmation alone is not explicitly unmet
])
def test_deadline_outcomes(completion, unmet, expected):
    assert deadline_outcome(applicable=True, due_boundary=DUE, boundary_rule="exclusive_next_day",
                            completion=completion, explicitly_unmet=unmet, cutoff=CUT) == expected


def test_day_only_completion_straddling_exact_instant_due_is_unknown():
    due = parse_instant("2026-05-31T12:00:00+10:00")
    assert deadline_outcome(applicable=True, due_boundary=due, boundary_rule="inclusive_instant",
                            completion={"precision": "day", "date": "2026-05-31"}, explicitly_unmet=False,
                            cutoff=CUT) == "unknown"


def test_not_yet_due_vague_and_not_applicable():
    early = parse_instant("2026-05-01T00:00:00+10:00")
    assert deadline_outcome(applicable=True, due_boundary=DUE, boundary_rule="exclusive_next_day", completion=None,
                            explicitly_unmet=False, cutoff=early) == "not_yet_due"
    assert deadline_outcome(applicable=True, due_boundary=None, boundary_rule=None, completion=None,
                            explicitly_unmet=False, cutoff=CUT) == "unknown"
    assert deadline_outcome(applicable=False, due_boundary=DUE, boundary_rule="exclusive_next_day", completion=None,
                            explicitly_unmet=False, cutoff=CUT) == "not_applicable"


def test_ledger_union_dedupes_shared_ids_and_discloses_unknown():
    rows = [{"id": "PAY-1", "cents": 1250}, {"id": "PAY-1", "cents": 1250}, {"id": "PAY-2", "cents": -250},
            {"id": "PAY-3", "cents": None}]
    out = ledger_union(rows, "id", "cents")
    assert out["sum"] == 1000 and out["known_n"] == 2 and out["unknown_n"] == 1 and out["state"] == "partial"
    with pytest.raises(ValueError):
        ledger_union([{"id": "A", "c": 1}, {"id": "A", "c": 2}], "id", "c")
    with pytest.raises(TypeError):
        ledger_union([{"id": "A", "c": 1.5}], "id", "c")
