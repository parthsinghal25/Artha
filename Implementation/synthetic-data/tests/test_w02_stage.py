"""W02: stage ledger, external-decision validation, concurrency and scope audit. Every adverse case uses a disposable copy
of the pack set and a disposable reviews folder; the real pack sets, gates and histories are never mutated."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import write_decision
from sdgen import progress
from sdgen.progress import ConflictError, GateRefused
from sdgen.workpack2 import OUTPUTS_DIRNAME, pack_digest

CODE = Path(__file__).resolve().parents[1]
MB = "style_microbatch"


@pytest.fixture()
def ledger(pack_set_copy):
    progress.init(pack_set_copy)
    return pack_set_copy


def approve(ledger, reviews, case=11, stage=MB, **kw):
    f = write_decision(reviews, ledger, case, stage, **kw)
    return progress.authorise(case, stage, f, ledger, reviews_root=reviews)


def put_output(ledger, case, stage, name="out.json", text="{}"):
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == case)
    d = ledger.parent / OUTPUTS_DIRNAME / job["pack"] / stage
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    pack = next(ledger.glob(f"{case:02d}-C-*"))
    cards = next(x for x in json.loads((pack / "STAGE.json").read_text())["stages"] if x["stage"] == stage)["cards"]
    (d / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": case, "cards": [{"card": c, "status": "fixture", "questions": []} for c in cards]}))
    return d


def start(ledger, reviews, case=11, stage=MB):
    return progress.set_state(case, stage, "working", ledger, reviews_root=reviews)


# ------------------------------------------------------------- reproduced independent bypasses
def test_pending_review_file_for_another_case_does_not_authorise(ledger, reviews):
    """Reproduces the independent probe: a pending-review JSON about case 40 authorised case 11."""
    pending = reviews / "pending-review.json"
    pending.write_text('{"status":"pending","case":40}')
    with pytest.raises(GateRefused, match="expected 'authoring_stage_signoff'"):
        progress.authorise(11, MB, pending, ledger, reviews_root=reviews)
    assert progress.status(ledger)["authorisation_present"] is False


def test_omitting_the_stage_cannot_start_work(ledger, reviews):
    """Reproduces: omitting `stage` started work. The stage is now a required argument of every transition."""
    approve(ledger, reviews)
    with pytest.raises(TypeError):
        progress.set_state(11, "working", root=ledger)  # noqa: no stage given
    with pytest.raises(GateRefused, match="stage is required"):
        progress.set_state(11, None, "working", ledger, reviews_root=reviews)
    with pytest.raises(GateRefused, match="no stage"):
        progress.set_state(11, "style_microbatch_typo", "working", ledger, reviews_root=reviews)


def test_pending_file_is_not_an_independent_acceptance(ledger, reviews):
    """Reproduces: the same pending file was accepted as an independent review record."""
    approve(ledger, reviews)
    start(ledger, reviews)
    put_output(ledger, 11, MB)
    progress.set_state(11, MB, "submitted", ledger)
    pending = reviews / "pending-review.json"
    pending.write_text('{"status":"pending","case":40}')
    with pytest.raises(GateRefused, match="expected 'authoring_stage_acceptance'"):
        progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=pending, reviews_root=reviews)
    assert progress.status(ledger)["active"] == []
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    assert job["stages"][MB]["state"] == "submitted"


def test_source_acceptance_record_is_not_stage_permission(ledger, reviews):
    """The accepted-originals review states authoring_stage_authorised=false; a different kind of review is never a launch approval."""
    rec = reviews / "REHEARSAL-SOURCE-SIGNOFF.json"
    rec.write_text(json.dumps({"kind": "principal_data_original_artifact_review", "decision": "accepted", "authoring_stage_authorised": False,
                               "authorised_cases_and_stages": [], "batch_id": "case11-rehearsal", "package_version": 3}))
    with pytest.raises(GateRefused, match="different kind of review"):
        progress.authorise(11, MB, rec, ledger, reviews_root=reviews)


# ------------------------------------------------------------- approval validation
@pytest.mark.parametrize("over,msg", [
    ({"case": 12}, "case=12"),
    ({"stage": "remaining_cards"}, "stage='remaining_cards'"),
    ({"pack_digest": "0" * 64}, "pack_digest"),
    ({"pack_set_version": 1}, "pack_set_version=1"),
    ({"pack": "12-C-other"}, "pack='12-C-other'"),
    ({"decision": "pending"}, "decision is 'pending'"),
    ({"decision": "rejected"}, "decision is 'rejected'"),
    ({"schema_version": 2}, "schema_version"),
    ({"reviewer": "Synthetic Data coding assistant"}, "not an independent reviewer"),
    ({"reviewer": "Claude"}, "not an independent reviewer"),
    ({"reviewer_role": "supervisor"}, "not an independent reviewer"),
    ({"reviewer": ""}, "reviewer"),
    ({"decided_at": "2999-01-01T00:00:00Z"}, "future"),
    ({"decided_at": "not a date"}, "decided_at"),
    ({"decided_at": ...}, "decided_at"),
])
def test_approval_record_must_be_bound_issued_and_independent(ledger, reviews, over, msg):
    f = write_decision(reviews, ledger, 11, MB, fields=over)
    with pytest.raises(GateRefused, match=msg):
        progress.authorise(11, MB, f, ledger, reviews_root=reviews)
    assert progress.status(ledger)["authorisation_present"] is False


def test_record_outside_the_reviews_folder_is_refused(ledger, reviews, tmp_path):
    f = write_decision(tmp_path, ledger, 11, MB, name="inside-work-area.json")
    with pytest.raises(GateRefused, match="not under the reviews folder"):
        progress.authorise(11, MB, f, ledger, reviews_root=reviews)
    # a sibling folder whose name merely starts with the reviews folder's name is also outside it
    sib = tmp_path / "reviews-extra"
    sib.mkdir()
    g = write_decision(sib, ledger, 11, MB)
    with pytest.raises(GateRefused, match="not under the reviews folder"):
        progress.authorise(11, MB, g, ledger, reviews_root=reviews)


def test_missing_or_garbled_record_is_refused(ledger, reviews):
    with pytest.raises(GateRefused, match="not found"):
        progress.authorise(11, MB, reviews / "nope.json", ledger, reviews_root=reviews)
    bad = reviews / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(GateRefused, match="not valid JSON"):
        progress.authorise(11, MB, bad, ledger, reviews_root=reviews)


def test_valid_approval_is_recorded_but_does_not_start_work(ledger, reviews):
    ap = approve(ledger, reviews)
    assert ap["pack_digest"] and len(ap["sha256"]) == 64
    st = progress.status(ledger)
    assert st["approved_stages"] == [(11, MB)] and st["active"] == []
    with pytest.raises(GateRefused, match="already has an approval"):
        approve(ledger, reviews, name="again.json")


def test_start_without_approval_is_refused_for_every_case(ledger, reviews):
    for case, stage in ((11, MB), (12, "whole_journey")):
        with pytest.raises(GateRefused, match="no approval"):
            progress.set_state(case, stage, "working", ledger, reviews_root=reviews)


def test_case_32_is_blocked_even_with_a_well_formed_approval(ledger, reviews):
    f = write_decision(reviews, ledger, 32, "whole_journey")
    with pytest.raises(GateRefused, match="blocked"):
        progress.authorise(32, "whole_journey", f, ledger, reviews_root=reviews)


def test_non_launchable_stages_cannot_be_approved(ledger, reviews):
    for stage in ("rehearsal_repair", "remaining_cards", "complete_case_review"):
        f = write_decision(reviews, ledger, 11, stage)
        with pytest.raises(GateRefused, match="not launchable"):
            progress.authorise(11, stage, f, ledger, reviews_root=reviews)


def test_approval_changed_after_recording_is_refused_on_start_and_resume(ledger, reviews):
    f = write_decision(reviews, ledger, 11, MB)
    progress.authorise(11, MB, f, ledger, reviews_root=reviews)
    f.write_text(f.read_text().replace('"approved"', '"approved" ').replace("fixture", "fixture2"))
    with pytest.raises(GateRefused, match="changed after it was recorded"):
        start(ledger, reviews)


def test_pack_modified_after_approval_is_refused(ledger, reviews):
    approve(ledger, reviews)
    pack = next(ledger.glob("11-C-*"))
    (pack / "STAGE.json").write_text((pack / "STAGE.json").read_text() + " ")
    with pytest.raises(GateRefused, match="pack files changed"):
        start(ledger, reviews)


# ------------------------------------------------------------- stage lifecycle
def test_full_lifecycle_requires_output_and_bound_acceptance(ledger, reviews):
    approve(ledger, reviews)
    start(ledger, reviews)
    with pytest.raises(GateRefused, match="nothing to submit"):
        progress.set_state(11, MB, "submitted", ledger)
    put_output(ledger, 11, MB, "11-D01.json", '{"fixture": 1}')
    st = progress.set_state(11, MB, "submitted", ledger)
    digest = st["output_digest"]
    assert digest and st["submitted_at"]
    with pytest.raises(GateRefused, match="self-accept"):
        progress.set_state(11, MB, "independently_accepted", ledger)
    with pytest.raises(GateRefused, match="not a permitted transition"):
        progress.set_state(11, MB, "working", ledger, reviews_root=reviews)
    wrong = write_decision(reviews, ledger, 11, MB, kind="authoring_stage_acceptance", decision="accepted", output_digest="f" * 64, name="wrong.json",
                           decided_at="2999-01-01T00:00:00Z")
    with pytest.raises(GateRefused):
        progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=wrong, reviews_root=reviews)
    wrong2 = write_decision(reviews, ledger, 11, MB, kind="authoring_stage_acceptance", decision="accepted", output_digest="f" * 64, name="wrong2.json")
    with pytest.raises(GateRefused, match="different output digest"):
        progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=wrong2, reviews_root=reviews)
    from datetime import datetime, timedelta, timezone
    decided = datetime.now(timezone.utc) + timedelta(days=1)  # after the submission, whatever today's date is
    ok = write_decision(reviews, ledger, 11, MB, kind="authoring_stage_acceptance", decision="accepted", output_digest=digest, name="ok.json",
                        decided_at=decided.strftime("%Y-%m-%dT%H:%M:%SZ"))
    res = progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=ok, reviews_root=reviews, now=decided + timedelta(days=1))
    assert res["state"] == "independently_accepted" and res["acceptance"]["sha256"]
    assert progress.verify_ledger(ledger)


def test_acceptance_cannot_predate_the_submission(ledger, reviews):
    approve(ledger, reviews)
    start(ledger, reviews)
    put_output(ledger, 11, MB)
    digest = progress.set_state(11, MB, "submitted", ledger)["output_digest"]
    early = write_decision(reviews, ledger, 11, MB, kind="authoring_stage_acceptance", decision="accepted", output_digest=digest, decided_at="2020-01-01T00:00:00Z")
    with pytest.raises(GateRefused, match="predates"):
        progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=early, reviews_root=reviews)


def test_repair_route_needs_a_note_and_resumes_with_the_gate_rechecked(ledger, reviews):
    approve(ledger, reviews)
    start(ledger, reviews)
    put_output(ledger, 11, MB)
    progress.set_state(11, MB, "submitted", ledger)
    with pytest.raises(GateRefused, match="needs a note"):
        progress.set_state(11, MB, "repair_required", ledger)
    progress.set_state(11, MB, "repair_required", ledger, note="11-D13 amount wording")
    # resume re-validates the approval: tamper first
    f = next(reviews.glob("authoring_stage_signoff*.json"))
    f.write_text(f.read_text() + " ")
    with pytest.raises(GateRefused, match="changed after it was recorded"):
        start(ledger, reviews)
    f.write_text(f.read_text().rstrip())
    assert start(ledger, reviews)["state"] == "working"


def _two_stage_set(pack_set_copy):
    """Disposable variant where case 11's second stage is launchable (the real pack set keeps it blocked)."""
    pack = next(pack_set_copy.glob("11-C-*"))
    st = json.loads((pack / "STAGE.json").read_text())
    for s in st["stages"]:
        if s["stage"] == "remaining_cards":
            s["launchable"] = True
            s.pop("blocked_reason", None)
    (pack / "STAGE.json").write_text(json.dumps(st, indent=1) + "\n")
    idx = json.loads((pack_set_copy / "PACKS-INDEX.json").read_text())
    for row in idx["packs"]:
        if row["case"] == 11:
            row["pack_digest"] = pack_digest(pack)[0]
    (pack_set_copy / "PACKS-INDEX.json").write_text(json.dumps(idx, indent=1) + "\n")
    return pack_set_copy


def test_micro_batch_and_remaining_cards_have_separate_lifecycles(pack_set_copy, reviews):
    ledger = _two_stage_set(pack_set_copy)
    progress.init(ledger)
    rem = write_decision(reviews, ledger, 11, "remaining_cards")
    progress.authorise(11, "remaining_cards", rem, ledger, reviews_root=reviews)
    # approved, but the prerequisite stage is not accepted: cannot start
    with pytest.raises(GateRefused, match="needs stage 'style_microbatch' independently accepted"):
        progress.set_state(11, "remaining_cards", "working", ledger, reviews_root=reviews)
    approve(ledger, reviews)
    start(ledger, reviews)
    with pytest.raises(GateRefused):  # remaining still cannot start while micro-batch is merely working
        progress.set_state(11, "remaining_cards", "working", ledger, reviews_root=reviews)
    put_output(ledger, 11, MB)
    digest = progress.set_state(11, MB, "submitted", ledger)["output_digest"]
    acc = write_decision(reviews, ledger, 11, MB, kind="authoring_stage_acceptance", decision="accepted", output_digest=digest, decided_at="2026-11-30T10:00:00Z")  # fixed date after the real-clock submission and before `now`
    # acceptance of the seven-card stage unlocks ONLY this case's own, separately approved, remaining stage
    progress.set_state(11, MB, "independently_accepted", ledger, acceptance_record=acc, reviews_root=reviews,
                       now=__import__("datetime").datetime(2026, 12, 1, tzinfo=__import__("datetime").timezone.utc))
    with pytest.raises(GateRefused, match="no approval"):
        progress.set_state(12, "whole_journey", "working", ledger, reviews_root=reviews)  # an unrelated case stays locked
    assert progress.set_state(11, "remaining_cards", "working", ledger, reviews_root=reviews)["state"] == "working"
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    assert job["stages"][MB]["state"] == "independently_accepted" and job["stages"]["remaining_cards"]["state"] == "working"


def test_one_active_stage_per_case(pack_set_copy, reviews):
    ledger = _two_stage_set(pack_set_copy)
    progress.init(ledger)
    approve(ledger, reviews)
    rem = write_decision(reviews, ledger, 11, "remaining_cards")
    progress.authorise(11, "remaining_cards", rem, ledger, reviews_root=reviews)
    start(ledger, reviews)
    job = json.loads((ledger / "PROGRESS.json").read_text())
    assert [k for k, s in job["jobs"][10]["stages"].items() if s["state"] == "working"] == [MB]


# ------------------------------------------------------------- concurrency cap on starts AND resumes
def _approve_cases(ledger, reviews, cases):
    for c in cases:
        f = write_decision(reviews, ledger, c, "whole_journey")
        progress.authorise(c, "whole_journey", f, ledger, reviews_root=reviews)


def test_worker_cap_applies_to_starts_and_to_resumes(ledger, reviews):
    approve(ledger, reviews)
    _approve_cases(ledger, reviews, [12, 13])
    start(ledger, reviews)
    start(ledger, reviews, 12, "whole_journey")
    with pytest.raises(GateRefused, match="already 2 active workers"):
        start(ledger, reviews, 13, "whole_journey")
    # a worker that finishes frees its slot ...
    put_output(ledger, 12, "whole_journey")
    progress.set_state(12, "whole_journey", "submitted", ledger)
    progress.set_state(12, "whole_journey", "repair_required", ledger, note="repair")
    start(ledger, reviews, 13, "whole_journey")
    # ... and a RESUME from repair is refused while two others are active
    with pytest.raises(GateRefused, match="already 2 active workers"):
        start(ledger, reviews, 12, "whole_journey")
    # question_raised -> working keeps the worker's own slot
    progress.set_state(13, "whole_journey", "question_raised", ledger, note="q")
    assert start(ledger, reviews, 13, "whole_journey")["state"] == "working"


def test_file_edit_cannot_raise_the_first_trial_cap(ledger, reviews):
    approve(ledger, reviews)
    _approve_cases(ledger, reviews, [12, 13])
    start(ledger, reviews)
    start(ledger, reviews, 12, "whole_journey")
    p = ledger / "PROGRESS.json"
    st = json.loads(p.read_text())
    st["max_concurrent_workers"] = 40
    p.write_text(json.dumps(st))
    with pytest.raises(progress.LedgerIntegrityError, match="state payload differs"):
        start(ledger, reviews, 13, "whole_journey")  # the edited cap never takes effect: the edit is detected first
    # even an effective in-code cap is clamped to the first-trial limit
    from sdgen.progress import MAX_PARALLEL_FIRST_TRIAL
    assert MAX_PARALLEL_FIRST_TRIAL == 2


def _spawn(code, *args):
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), str(CODE), *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


START_SCRIPT = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sdgen import progress
ledger, reviews, case, barrier = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
while not barrier.exists():
    time.sleep(0.002)
try:
    progress.set_state(case, "whole_journey", "working", ledger, reviews_root=reviews)
    print("started")
except progress.GateRefused as e:
    print("refused")
"""


def test_concurrent_starts_in_separate_processes_respect_the_cap_and_lose_no_update(ledger, reviews, tmp_path):
    cases = [12, 13, 14, 15, 16, 17]
    _approve_cases(ledger, reviews, cases)
    barrier = tmp_path / "go"
    procs = [_spawn(START_SCRIPT, ledger, reviews, c, barrier) for c in cases]
    barrier.touch()
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert sorted(outs) == ["refused"] * 4 + ["started"] * 2, outs
    st = progress.status(ledger)
    assert len(st["active"]) == 2 and st["ledger_intact"]
    events = (ledger / "PROGRESS-EVENTS.jsonl").read_text().splitlines()
    # init + 6 authorisations + 2 starts; refused attempts leave no event and no half-written state
    assert len(events) == 1 + 6 + 2 and st["revision"] == len(events)


SUBMIT_SCRIPT = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sdgen import progress
ledger, case, barrier = Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
while not barrier.exists():
    time.sleep(0.002)
progress.set_state(case, "whole_journey", "submitted", ledger)
print("ok")
"""


def test_two_workers_finishing_together_both_persist(ledger, reviews, tmp_path):
    _approve_cases(ledger, reviews, [12, 13])
    start(ledger, reviews, 12, "whole_journey")
    start(ledger, reviews, 13, "whole_journey")
    put_output(ledger, 12, "whole_journey")
    put_output(ledger, 13, "whole_journey")
    barrier = tmp_path / "go"
    procs = [_spawn(SUBMIT_SCRIPT, ledger, c, barrier) for c in (12, 13)]
    barrier.touch()
    assert [p.communicate(timeout=60)[0].strip() for p in procs] == ["ok", "ok"]
    jobs = {j["case"]: j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"]}
    assert jobs[12]["stages"]["whole_journey"]["state"] == "submitted" and jobs[13]["stages"]["whole_journey"]["state"] == "submitted"
    assert progress.verify_ledger(ledger)


def test_stale_expected_revision_is_a_conflict_not_a_lost_update(ledger, reviews):
    _approve_cases(ledger, reviews, [12])
    rev = progress.status(ledger)["revision"]
    start(ledger, reviews, 12, "whole_journey")
    put_output(ledger, 12, "whole_journey")
    with pytest.raises(ConflictError):
        progress.set_state(12, "whole_journey", "submitted", ledger, expected_revision=rev)
    assert progress.set_state(12, "whole_journey", "submitted", ledger, expected_revision=rev + 1)["state"] == "submitted"


# ------------------------------------------------------------- history and no reset
def test_ledger_history_is_preserved_chained_and_cannot_be_reset(ledger, reviews):
    with pytest.raises(FileExistsError):
        progress.init(ledger)
    import inspect
    assert "force" not in inspect.signature(progress.init).parameters
    approve(ledger, reviews)
    start(ledger, reviews)
    assert progress.verify_ledger(ledger)
    events = (ledger / "PROGRESS-EVENTS.jsonl").read_text().splitlines()
    assert [json.loads(e)["action"] for e in events] == ["init", "authorise", "set_state"]
    assert progress.status(ledger)["integrity_problems"] == []


def test_withdrawal_moves_an_unused_approval_to_history(ledger, reviews):
    approve(ledger, reviews)
    with pytest.raises(GateRefused, match="reason"):
        progress.withdraw_approval(11, MB, " ", ledger)
    progress.withdraw_approval(11, MB, "pack will be rebuilt", ledger)
    job = next(j for j in json.loads((ledger / "PROGRESS.json").read_text())["jobs"] if j["case"] == 11)
    assert job["stages"][MB]["approval"] is None and job["stages"][MB]["approval_history"][0]["reason"] == "pack will be rebuilt"
    approve(ledger, reviews, name="second.json")
    start(ledger, reviews)
    with pytest.raises(GateRefused, match="unused approval"):
        progress.withdraw_approval(11, MB, "too late", ledger)


# ------------------------------------------------------------- scope audit
def test_sibling_prefix_write_is_outside_the_owned_folder(tmp_path):
    """Reproduces: a write under owned-other/ was treated as owned by owned/ (string prefix)."""
    (tmp_path / "owned").mkdir()
    (tmp_path / "owned-other").mkdir()
    (tmp_path / "owned-other/x").write_text("before")
    before = progress.snapshot_tree([tmp_path])
    (tmp_path / "owned-other/x").write_text("after")
    (tmp_path / "owned/mine.txt").write_text("fine")
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")
    assert rep["violations"] == [str(tmp_path / "owned-other/x")] and not rep["clean"]


def test_writes_inside_owned_folder_are_clean_including_nested_and_deleted(tmp_path):
    (tmp_path / "owned/deep").mkdir(parents=True)
    (tmp_path / "owned/old.txt").write_text("1")
    (tmp_path / "other").mkdir()
    (tmp_path / "other/a.txt").write_text("1")
    before = progress.snapshot_tree([tmp_path])
    (tmp_path / "owned/deep/new.txt").write_text("x")
    (tmp_path / "owned/old.txt").unlink()
    assert progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")["clean"]
    (tmp_path / "other/a.txt").unlink()
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")
    assert rep["violations"] == [str(tmp_path / "other/a.txt")]


def test_symlink_escape_from_the_owned_folder_is_flagged(tmp_path):
    (tmp_path / "owned").mkdir()
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret/x.txt").write_text("1")
    before = progress.snapshot_tree([tmp_path])
    os.symlink(tmp_path / "secret", tmp_path / "owned/link")
    os.symlink(tmp_path / "secret/x.txt", tmp_path / "owned/filelink")
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")
    assert sorted(rep["symlink_escapes"]) == sorted([str(tmp_path / "owned/link"), str(tmp_path / "owned/filelink")]) and not rep["clean"]
    # and a write made THROUGH the link lands outside the owned folder and is a violation
    (tmp_path / "owned/filelink").write_text("changed through the link")
    rep2 = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")
    assert str(tmp_path / "secret/x.txt") in rep2["violations"]


def test_concurrent_workers_changes_are_reported_unattributable_not_blamed(tmp_path):
    for n in ("a", "b"):
        (tmp_path / n).mkdir()
    before = progress.snapshot_tree([tmp_path])
    (tmp_path / "a/one.txt").write_text("x")
    (tmp_path / "b/two.txt").write_text("y")
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "a", concurrent_owned=[tmp_path / "b"])
    assert rep["violations"] == [] and rep["unattributable_to_a_writer"] == [str(tmp_path / "b/two.txt")] and rep["clean"]
    (tmp_path / "elsewhere.txt").write_text("z")
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "a", concurrent_owned=[tmp_path / "b"])
    assert rep["violations"] == [str(tmp_path / "elsewhere.txt")]


def test_hashes_audit_writes_not_reads_and_the_report_says_so(tmp_path):
    (tmp_path / "owned").mkdir()
    (tmp_path / "other-pack").mkdir()
    (tmp_path / "other-pack/private.txt").write_text("another member's material")
    before = progress.snapshot_tree([tmp_path])
    _ = (tmp_path / "other-pack/private.txt").read_text()  # a READ outside the pack
    rep = progress.scope_audit(before, progress.snapshot_tree([tmp_path]), tmp_path / "owned")
    assert rep["clean"] is True  # the audit cannot see the read ...
    assert rep["audits"] == "writes_only_not_reads" and "do not show that another pack was not read" in rep["note"]  # ... and says so


def test_snapshot_excludes_tool_caches_and_records_symlinks_without_following(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/big.py").write_text("x")
    (tmp_path / "d").mkdir()
    os.symlink(tmp_path / "d", tmp_path / "dirlink")
    snap = progress.snapshot_tree([tmp_path])
    assert not any(".venv" in k for k in snap) and snap[str(tmp_path / "dirlink")].startswith("symlink->")
