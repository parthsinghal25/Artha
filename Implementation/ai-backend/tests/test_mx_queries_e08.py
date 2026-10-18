"""E08: adopted MX parameters, dimensions and per-period grouping through the real handlers (isolated specimens on
the accepted rehearsal-v3 source bytes; facts and decisions are test specimens)."""
from __future__ import annotations

import pytest

from conftest import data_dir, rt, rehearsal_bundle  # noqa: F401
from test_analytical_r10 import CASE, _fact, ledger  # noqa: F401
from test_ingest_and_api import FILTERS
from specimens import publish_native

SHARED = "shared-measures-1.0.0"


@pytest.fixture
def mx(rt, tmp_path):
    from sdbackend.api.ops import Operations
    from sdbackend.engine.resolvers import resolve
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    from specimens import mini_bundle, specimen_bundle, case_row, approve_originals, decide
    from test_producers_e07 import TO_MEMBER, TO_STAFF
    import json, hashlib
    base = mini_bundle(tmp_path, [
        {"original_id":"ORIG-MEMBER", "rel":"originals/member.eml", "data":(TO_MEMBER.replace("Subject: x", "Date: Wed, 07 Jan 2026 10:00:00 +1100\nSubject: x")).encode(),
         "media":"message/rfc822", "case":CASE, "available":"2026-01-07T16:00:00+11:00"},
        {"original_id":"ORIG-INTERNAL", "rel":"originals/internal.eml", "data":(TO_STAFF.replace("Subject: x", "Date: Thu, 08 Jan 2026 10:00:00 +1100\nSubject: x")).encode(),
         "media":"message/rfc822", "case":CASE, "available":"2026-01-08T16:00:00+11:00"}], name="mx-spec")
    mpath=base/"delivery-manifest.json"
    manifest=json.loads(mpath.read_text())
    for o in manifest["originals"]:
        o["author"]="STAFF-CM03"
    raw=json.dumps(manifest,indent=1).encode(); mpath.write_bytes(raw)
    sums=base/"INTAKE-FILES.sha256"
    lines=[l for l in sums.read_text().splitlines() if not l.endswith("delivery-manifest.json")]
    sums.write_text("\n".join(lines+[hashlib.sha256(raw).hexdigest()+"  delivery-manifest.json"])+"\n")
    bundle=specimen_bundle(tmp_path,base,[case_row(CASE,"2026-01-01")],name="mx-native")
    delivery=receive(bundle,rt=rt); approve_originals(rt,delivery)
    decide(rt,"native_input",[{"subject_kind":"native_record","subject_id":n.record["envelope"]["record_id"],
                               "subject_sha256":n.record_sha256} for n in delivery.natives])
    summary=ingest(rt,receive(bundle,rt=rt)); runtime=rt
    srcs=[s["data"] for s in runtime.db.list_objects("source")]
    to_member=next(s for s in srcs if "member-mail.example" in s["text"])
    to_staff=next(s for s in srcs if s is not to_member)
    runtime.db.put_object("stage_s02", "STAGE-S02-MX", {"facts": [
        _fact(to_member, "UPD", "commitment", "send the next update", span=(to_member["text"].index("I will"),len(to_member["text"]))),
        _fact(to_member, "UPD", "commitment_due_date", "2026-01-12"),
        _fact(to_staff, "FILE", "commitment", "send you the file", span=(to_staff["text"].index("I will"),len(to_staff["text"]))),
        _fact(to_staff, "FILE", "commitment_due_date", "2026-02-20")]})
    st = resolve(runtime, ["STAGE-S02-MX"], release_source_ids=[s["source_id"] for s in srcs])
    assert {m["scope"] for m in st["mx_records"]} == {"member_facing", "internal"}
    aset, _ = publish_native(runtime, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(runtime)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    return ops, ctx, aset


def _q(ops, ctx, measure, **f):
    return ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="E08",
                                          definition={"registry_id": SHARED, "measure_id": measure,
                                                      "definition_version": "1.0.0"},
                                          filters={**FILTERS, **f}, population_reference=None))


def _unknown(result, prefix=""):
    return next(c for c in result["cells"] if c["key"] == f"{prefix}unknown")["point"]["value"]


def test_commitment_scope_parameter_is_honoured_not_dropped(mx):
    ops, ctx, aset = mx
    assert {"registry_id": SHARED, "measure_id": "mx.commitments.follow_through", "definition_version": "1.0.0"} \
        in ctx["supported_definitions"]
    member = _q(ops, ctx, "mx.commitments.follow_through", granularity="none",
                parameters={"commitment_scope": "member_facing"})
    internal = _q(ops, ctx, "mx.commitments.follow_through", granularity="none",
                  parameters={"commitment_scope": "internal"})
    default = _q(ops, ctx, "mx.commitments.follow_through", granularity="none")
    assert member["status"] == internal["status"] == default["status"] == "ok"
    assert _unknown(member["payload"]) == 1 and _unknown(internal["payload"]) == 1     # due elapsed, unconfirmed
    assert member["payload"]["result_id"] != internal["payload"]["result_id"]
    pred = member["payload"]["cells"][0]["population"]["predicate"]
    assert pred["extension_parameters"] == {"commitment_scope": "member_facing"}
    assert _unknown(default["payload"]) == 1                                          # default = member-facing
    bad = _q(ops, ctx, "mx.commitments.follow_through", granularity="none", parameters={"dimension": "service"})
    assert bad["status"] != "ok" and bad["error"]["code"] == "INVALID_REQUEST"         # undeclared parameter


def test_follow_through_by_period_uses_original_due_cohort(mx):
    ops, ctx, _ = mx
    got = _q(ops, ctx, "mx.commitments.follow_through", granularity="monthly",
             parameters={"commitment_scope": "member_facing"})
    assert got["status"] == "ok", got
    res = got["payload"]
    assert res["shape"] == "matrix"
    jan = sum(c["point"]["value"] or 0 for c in res["cells"] if c["key"].startswith("2026-01-01."))
    feb = sum(c["point"]["value"] or 0 for c in res["cells"] if c["key"].startswith("2026-02-01."))
    assert (jan, feb) == (1, 0)                       # the 12 Jan due sits in January; the internal one is out of scope


def test_response_dimension_is_never_substituted(mx):
    ops, ctx, aset = mx
    assert aset["supported_response_dimensions"] == [] and ctx["supported_response_dimensions"] == []
    got = _q(ops, ctx, "mx.responses.expressed_positions", granularity="none", parameters={"dimension": "decision"})
    assert got["status"] != "ok"                      # not advertised here: unavailable, never another dimension

def test_bounded_commitment_extraction_cannot_report_exhaustive_known_zero(mx):
    ops,ctx,aset=mx
    got=_q(ops,ctx,'mx.commitments.follow_through',granularity='none')
    assert got['status']=='ok',got
    assert all(c['point']['state']=='partial' for c in got['payload']['cells'])
    assert all(any('commitment coverage' in s for s in c['coverage']['limitations']) for c in got['payload']['cells'])
