"""E05/E06 over the ACTUAL accepted case-11 table originals (D29 effort XLSX, D19 remedy ledger, D28 fee extract).

The bytes are read from Data's accepted package (skipped when absent) and received into an isolated pytest store
through the production intake/extraction/projection path. Receipt times and decisions here are TEST SPECIMENS: the
real availability comes from Data's consolidated 0.2.0 export and the real decisions from the Principals. The prose
note is a specimen too. Expected totals are verification targets from the Data handoff, never runtime inputs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import data_dir, rt  # noqa: F401  (pytest fixtures)
from specimens import approve_originals, case_row, decide, mini_bundle, publish_native, specimen_bundle
from test_analytical_r10 import CASE, ONE, _fact

PKG = Path(__file__).resolve().parents[3] / "Data/synthesis/pilot-v2-releases/case-11-remaining-cards-v1/intake-bundle"
XLSX = PKG / "originals/ledgers/11-16-complaints-effort-export.xlsx"
REMEDY = PKG / "originals/ledgers/11-11-case-remedy-or-refund-ledger.csv"
FEES = PKG / "originals/ledgers/11-15-external-case-fee-invoice-and-payment-extract.csv"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORK_AVAILABLE = "2026-03-27T23:15:00+11:00"          # specimen receipt instant (handoff: 27 Mar 23:15 Melbourne)
NOTE = b"Delegate decision recorded: remedy approved for A$100.00 on 13 January 2026 (compensation).\n"

pytestmark = pytest.mark.skipif(not (XLSX.is_file() and REMEDY.is_file() and FEES.is_file()),
                                reason="accepted case-11 table originals not present in this checkout")


@pytest.fixture()
def case11_tables(rt, tmp_path):  # noqa: F811
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    xlsx = XLSX.read_bytes()
    base = mini_bundle(tmp_path, [
        {"original_id": "ORIG-D29", "rel": "originals/ledgers/" + XLSX.name, "data": xlsx, "media": XLSX_MEDIA,
         "case": CASE, "available": WORK_AVAILABLE},
        # a byte-identical copy received later (e.g. forwarded export): must not multiply work
        {"original_id": "ORIG-D29-COPY", "rel": "originals/copies/" + XLSX.name, "data": xlsx, "media": XLSX_MEDIA,
         "case": CASE, "available": "2026-04-02T10:00:00+11:00"},
        {"original_id": "ORIG-D19", "rel": "originals/ledgers/" + REMEDY.name, "data": REMEDY.read_bytes(),
         "media": "text/csv", "case": CASE, "available": "2026-03-18T23:10:00+11:00"},
        {"original_id": "ORIG-D28", "rel": "originals/ledgers/" + FEES.name, "data": FEES.read_bytes(),
         "media": "text/csv", "case": CASE, "available": "2026-02-11T23:10:00+11:00"},
        {"original_id": "ORIG-NOTE", "rel": "originals/notes/delegate.txt", "data": NOTE, "media": "text/plain",
         "case": CASE, "available": "2026-01-13T17:00:00+11:00"},
    ], name="case11-tables")
    bundle = specimen_bundle(tmp_path, base, [case_row(CASE, "2025-12-31")], name="case11-tables-natives")
    d = receive(bundle, rt=rt)
    approve_originals(rt, d)
    decide(rt, "native_input", [{"subject_kind": "native_record", "subject_id": n.record["envelope"]["record_id"],
                                 "subject_sha256": n.record_sha256} for n in d.natives])
    summary = ingest(rt, receive(bundle, rt=rt))
    srcs = [s["data"] for s in rt.db.list_objects("source")]
    note = next(s for s in srcs if s["text"].startswith("Delegate"))
    rt.db.put_object("stage_s02", "STAGE-S02-NOTE", {
        "source_binding": {"source_id": note["source_id"], "content_sha256": note["content_sha256"]},
        "facts": [_fact(note, "APPROVAL", "financial_amount_minor", 10000, day="2026-01-13"),
                  _fact(note, "APPROVAL", "financial_kind", "approved"),
                  _fact(note, "APPROVAL", "financial_category", "compensation"),
                  _fact(note, "WRONG", "financial_amount_minor", 9999, day="2026-01-13"),       # unmatched mention
                  _fact(note, "WRONG", "financial_kind", "payment"),
                  _fact(note, "WRONG", "financial_category", "compensation")]})
    return rt, summary, srcs


def _stage(rt, srcs):
    from sdbackend.engine.resolvers import native_candidates, resolve
    natives = native_candidates(rt, [s["source_id"] for s in srcs])
    return resolve(rt, ["STAGE-S02-NOTE"], native_source_ids=natives), natives


def test_d29_xlsx_projects_22_recorded_entries_once(case11_tables):
    rt, summary, srcs = case11_tables
    st, natives = _stage(rt, srcs)
    assert len(natives) == 4                                    # two XLSX copies + two ledgers; never the prose note
    work = [r for r in st["analysis_records"] if r["kind"] == "work_entry"]
    assert len(work) == 22 and sum(w["data"]["duration_seconds"] for w in work) == 19920
    assert all(w["data"]["work_id"].startswith("WORK-R2-") for w in work)       # stable native identities
    assert {w["data"]["phase"] for w in work} == {"unknown"}                      # raw phases not in the frozen enum
    assert {w["data"]["contributor_role"] for w in work} == {"unknown"}           # actor is not a role
    assert all(w["data"]["afca_matter_ids"] == [] for w in work)                  # no inferred AFCA association
    assert {w["envelope"]["available_at"] for w in work} == {WORK_AVAILABLE}      # earliest copy, not the row date
    rows = {r["work_id"]: r for r in st["native_rows"] if r["kind"] == "work_entry"}
    assert {r["raw_phase"] for r in rows.values()} == {"complaint_handling", "external_dispute"}
    assert {r["actor"] for r in rows.values()} >= {"STAFF-CM03", "STAFF-DB01"}
    from sdbackend.citations import check_citation
    by_id = {s["source_id"]: s for s in srcs}
    for w in work:
        ref = w["data"]["evidence_refs"][0]
        src = by_id[ref["source_id"]]
        assert check_citation(src, ref) is None
        a = ref["anchor"]
        assert int(src["text"][a["start_codepoint"]:a["end_codepoint"]]) == w["data"]["duration_seconds"]


def test_ledgers_keep_native_identity_and_reconcile_prose(case11_tables):
    rt, summary, srcs = case11_tables
    st, _ = _stage(rt, srcs)
    evs = {r["envelope"]["record_id"]: r for r in st["analysis_records"] if r["kind"] == "financial_event"}
    native = {k: v["data"] for k, v in evs.items() if k.startswith("FIN-R2-")}
    assert {(k, d["obligation_id"], d["kind"], d["amount_minor"], d["effective_date"]) for k, d in native.items()} == {
        ("FIN-R2-0ebee9c9c59cf1c165", "OBL-11-REMEDY", "approved", 10000, "2026-01-13"),
        ("FIN-R2-6d000bbdb85e4bfbc5", "OBL-11-REMEDY", "payment", 10000, "2026-03-18"),
        ("FIN-R2-30925f3b64e51fc718", "INV-11-AFCA", "invoiced", 250000, "2026-01-28"),
        ("FIN-R2-e44516db3317a0d480", "INV-11-AFCA", "payment", 250000, "2026-02-11")}
    obs = {r["data"]["obligation_id"]: r["data"] for r in st["analysis_records"] if r["kind"] == "obligation"}
    assert obs["OBL-11-REMEDY"]["category"] == "compensation" and obs["INV-11-AFCA"]["category"] == "afca_fee"
    assert obs["INV-11-AFCA"]["afca_matter_ids"] == []                            # not inferred from the category
    # the prose approval is an EARLIER VERSION of the same native event, superseded by the ledger row
    early = [r for k, r in evs.items() if not k.startswith("FIN-R2-")]
    assert len(early) == 1 and early[0]["data"]["financial_event_id"] == "FIN-R2-0ebee9c9c59cf1c165"
    assert evs["FIN-R2-0ebee9c9c59cf1c165"]["envelope"]["supersedes_record_id"] == early[0]["envelope"]["record_id"]
    assert any("does not match a ledger event" in u["reason"] for u in st["unresolved"])
    assert st["prose_reconciliation"] and not any(r["kind"] == "financial_event" and r["data"]["amount_minor"] == 9999
                                                   for r in st["analysis_records"])


def _ctx(ops, release_id, at):
    got = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None, release_id=release_id,
                                         evidence_as_of=at, analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))
    assert got["status"] == "ok", got
    return got["context"]


def _value(ops, ctx, measure):
    got = ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="E05",
                                         definition={"registry_id": "runtime-measures-0.2.0", "measure_id": measure,
                                                     "definition_version": "0.2.0"},
                                         filters=ONE, population_reference=None))
    assert got["status"] == "ok", got
    cell = got["payload"]["cells"][0]
    ids = ops.handle("leader.demo", dict(operation="contributors.list", context_id=ctx["context_id"],
                                         result_id=got["payload"]["result_id"], cell_key=cell["key"], cursor=None,
                                         page_size=100))["payload"]["items"]
    return cell["point"]["value"], {i["entity_id"] for i in ids}


def test_published_impact_and_earlier_cutoff(case11_tables):
    rt, summary, srcs = case11_tables
    from sdbackend.api.ops import Operations
    st, _ = _stage(rt, srcs)
    publish_native(rt, summary["release_id"], stages=[st["stage_id"]])
    ops = Operations(rt)
    late = _ctx(ops, summary["release_id"], "2026-10-01T00:00:00+10:00")
    hours, wids = _value(ops, late, "work.recorded_hours")
    assert round(hours * 3600) == 19920 and len(wids) == 22
    assert _value(ops, late, "cash.remedies_paid") == (10000, {"FIN-R2-6d000bbdb85e4bfbc5"})
    assert _value(ops, late, "cash.expenses_paid") == (250000, {"FIN-R2-e44516db3317a0d480"})
    assert _value(ops, late, "cash.expenses_and_remedies_paid")[0] == 260000      # approvals/invoices are not cash
    # one minute before the export was received: none of its (older-dated) rows is known yet
    early = _ctx(ops, summary["release_id"], "2026-03-27T23:14:00+11:00")
    assert _value(ops, early, "work.recorded_hours")[1] == set()
    assert _value(ops, early, "cash.remedies_paid") == (10000, {"FIN-R2-6d000bbdb85e4bfbc5"})
    feb = _ctx(ops, summary["release_id"], "2026-02-01T00:00:00+11:00")
    assert _value(ops, feb, "cash.expenses_paid") == (0, set())                  # fee extract arrives 11 Feb


def test_same_delivery_reimport_is_identical(case11_tables):
    rt, summary, srcs = case11_tables
    st1, _ = _stage(rt, srcs)
    st2, _ = _stage(rt, srcs)
    assert st1["stage_id"] == st2["stage_id"]
    assert [r["envelope"]["record_id"] for r in st1["analysis_records"]] == \
        [r["envelope"]["record_id"] for r in st2["analysis_records"]]

def test_conditioned_approval_remains_uncertain_before_later_ledger(case11_tables):
    runtime,summary,srcs=case11_tables
    from sdbackend.canonical import source_content_sha256
    from sdbackend.engine.resolvers import resolve,native_candidates
    from sdbackend.api.ops import Operations
    from sdbackend.pipeline.passages import passages
    # Separate isolated source. An earlier conditional authorization is neither payment nor established debt.
    note=next(s for s in runtime.db.list_objects('source') if s['data']['text'].startswith('Delegate'))
    import copy
    note=copy.deepcopy(note)
    d=note['data']; d['source_id']='SRC-CONDITIONAL-SPEC'
    d['text']='Under OBL-11-REMEDY the remedy is authorised at A$100.00, conditional on an applicable amount-setting decision.'
    d['content_sha256']=source_content_sha256(d)
    note['envelope']['record_id']=d['source_id']; note['envelope']['logical_id']=d['source_id']
    runtime.db.put_object('source',d['source_id'],note)
    runtime.db.put_object('stage_s02','STAGE-CONDITION-SPEC',{'source_binding':{'source_id':d['source_id'],'content_sha256':d['content_sha256']},'facts':[]})
    st=resolve(runtime,['STAGE-CONDITION-SPEC'],native_source_ids=native_candidates(runtime,[s['source_id'] for s in srcs]),
               release_source_ids=[d['source_id']])
    assert st.get('financial_history_uncertainties'), 'Earlier conditional approval disappeared behind the later ledger'
    u=st['financial_history_uncertainties'][0]
    assert u['obligation_id']=='OBL-11-REMEDY' and u['state']=='uncertain'
    assert u['available_at']==note['envelope']['available_at']
    ref=u['evidence_refs'][0]; a=ref['anchor']
    assert 'conditional' in d['text'][a['start_codepoint']:a['end_codepoint']]
