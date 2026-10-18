"""FIN01: real stage inputs, disposable assembly fixtures; no real review decisions."""
import json

import pytest

from sdgen import progress, receiving_export as rx, source_repair as sr


def manifest(package):
    return json.loads((package / "intake-bundle/delivery-manifest.json").read_text())


SUCCESSOR = manifest(rx.CASE11_SUCCESSOR)
PRIOR = {o["original_id"]: o for o in manifest(rx.CASE11_REMAINING_V1["package"])["originals"]}


@pytest.fixture(scope="module")
def plan():
    return sr.load_repair()[0]


@pytest.mark.parametrize("stage", SUCCESSOR["originals"], ids=lambda o: o["original_id"])
def test_adapter_preserves_real_stage_identity_and_metadata(stage, plan):
    oid = stage["original_id"]
    src = {"signed": {oid: (stage["file"], stage["sha256"])}, "manifest": SUCCESSOR}
    data = (rx.CASE11_SUCCESSOR / "intake-bundle" / stage["file"]).read_bytes()
    entry = rx.entry_from_plan(src, oid, plan, data, stage, {})
    candidate = next(o for o in manifest(rx.ROOT / "Data/synthesis/receiving-exports/case-11-complete-candidate-v1")["originals"]
                     if o["original_id"] == oid)
    # The full transport metadata (bytes, dates, ownership, MIME facts, copy lineage)
    # must agree with the already reviewed candidate, for v1 and v2 alike.
    assert all(candidate[k] == v for k, v in entry.items() if k != "lineage")
    # Inherited entries keep their richer signed-manifest carried_forward audit;
    # build_export copies those from the earlier package rather than adapting again.
    assert all(candidate["lineage"][k] == v for k, v in entry["lineage"].items())
    if stage.get("version") == 2:
        assert entry["version"] == 2
        assert entry["lineage"]["relation"] == "new_document_version"
        assert entry["lineage"]["supersedes"] == stage["supersedes"]
        assert entry["lineage"]["supersedes"]["sha256"] == PRIOR[oid]["sha256"]
    else:
        assert entry["version"] == 1
        assert entry["lineage"]["relation"] == "original"
        assert "supersedes" not in entry["lineage"]
        prior = PRIOR[oid]
        assert entry == rx.entry_from_plan(src, oid, plan, data, prior, {})


def test_normal_assembly_preserves_candidate_substantive_metadata(plan, tmp_path):
    # Labelled fixture ONLY, confined to tmp_path; never a real sign-off or ledger acceptance.
    repaired = [o for o in SUCCESSOR["originals"] if o.get("version") == 2]
    assert len(repaired) == 5
    assert sum(o["kind"] == "attachment_original" for o in repaired) == 2
    decision = tmp_path / "FIXTURE-ONLY-source-signoff.json"
    decision.write_text(json.dumps({"decision": "accepted", "reviewer": "assembly fixture ONLY",
                                   "bound_manifest_sha256": rx.CASE11_SUCCESSOR_MANIFEST_SHA256,
                                   "originals": repaired}))
    sources = (*rx.CASE11_SOURCES, rx.CASE11_REMAINING_V1,
               {"package": rx.CASE11_SUCCESSOR, "manifest_sha256": rx.CASE11_SUCCESSOR_MANIFEST_SHA256,
                "signoff": decision, "stage_acceptance": None})
    acceptance = json.loads(rx.CASE11_SOURCES[1]["stage_acceptance"].read_text())
    assert progress.tree_digest(rx.CASE11_ACCEPTED_RETURNS) == acceptance["output_digest"]
    returns = [json.loads(f.read_text()) for f in rx.CASE11_ACCEPTED_RETURNS.glob("11-D*.json")]
    titles = {d["card"]: d["letter"]["title"] for d in returns if d.get("kind") == "letter_with_cover_email"}
    result = rx.build_export(plan, tmp_path, "FIXTURE-ONLY-normal-export", sources, titles)
    normal = manifest(result)
    candidate = manifest(rx.ROOT / "Data/synthesis/receiving-exports/case-11-complete-candidate-v1")
    substantive = lambda o: {k: v for k, v in o.items() if k != "review_provenance"}
    assert {o["original_id"]: substantive(o) for o in normal["originals"]} == {
        o["original_id"]: substantive(o) for o in candidate["originals"]}
    for key in ("native_administrative_inputs", "attachment_occurrences", "advertised_unheld", "participants", "previews"):
        # Candidate carries the slice first; normal assembly sorts all messages.
        # Compare exact records as sorted lists so repeated occurrences still count.
        canonical = lambda row: json.dumps(row, sort_keys=True)
        assert sorted(normal[key], key=canonical) == sorted(candidate[key], key=canonical)
    for o in normal["originals"]:
        assert (result / "intake-bundle" / o["file"]).read_bytes() == (
            rx.ROOT / "Data/synthesis/receiving-exports/case-11-complete-candidate-v1/intake-bundle" / o["file"]).read_bytes()


def test_normal_assembly_still_refuses_pending_source_decisions(plan, tmp_path):
    decision = tmp_path / "FIXTURE-ONLY-pending.json"
    decision.write_text(json.dumps({"decision": "pending", "bound_manifest_sha256": rx.CASE11_SUCCESSOR_MANIFEST_SHA256}))
    with pytest.raises(rx.ExportRefused, match="not an acceptance"):
        rx.build_export(plan, tmp_path, "must-not-publish", [{"package": rx.CASE11_SUCCESSOR,
                        "manifest_sha256": rx.CASE11_SUCCESSOR_MANIFEST_SHA256, "signoff": decision}])
    assert not (tmp_path / "must-not-publish").exists()
