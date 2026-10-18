"""V08 combined-corpus preflight on synthetic mini-bundles (no model, no store, no private material)."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from sdbackend.intel.preflight import preflight_combined


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bundle(root: Path, tag: str, n_members: int, *, afca=None, table_rows=None):
    """n members, one case each, one email per case (+ optional work table on the first case)."""
    (root / "originals").mkdir(parents=True)
    (root / "native").mkdir()
    cases, parties, originals = [], [], []
    for i in range(n_members):
        cid, pid, mref = f"{tag}-case-{i}", f"{tag}-party-{i}", f"{tag}-member-{i}"
        parties.append({"kind": "party", "data": {"party_id": pid, "member_reference": mref, "party_role": "member"}})
        cases.append({"kind": "case", "envelope": {"available_at": "2026-01-01T00:00:00+11:00"},
                      "data": {"case_id": cid, "party_ids": [pid], "complaints_receipt_date": "2026-01-02"}})
        body = f"Email for {cid}".encode()
        f = f"originals/{tag}-{i}.eml"
        (root / f).write_bytes(body)
        originals.append({"original_id": f"{tag}-o-{i}", "file": f, "filename": f.split("/")[-1], "kind": "email",
                          "declared_media_type": "message/rfc822", "size_bytes": len(body), "sha256": _sha(body),
                          "source_family": "S03", "author": f"{tag}-actor",
                          "references": {"case_ids": [cid], "member_references": [mref],
                                         "afca_matter_ids": [afca] if (afca and i == 0) else []},
                          "scenario_time": {"available_at": {"value": "2026-01-03T10:00:00+11:00", "precision": "second"},
                                            "event_at": {"value": "2026-01-03T09:00:00+11:00", "precision": "second"}}})
    if table_rows is not None:
        csv = "work_id,case_id,linked_case_ids,date,duration_seconds,afca_matter_id\n" + "".join(
            ",".join(r) + "\n" for r in table_rows)
        f = f"originals/{tag}-work.csv"
        (root / f).write_text(csv)
        originals.append({"original_id": f"{tag}-work", "file": f, "filename": "work.csv", "kind": "work_export",
                          "declared_media_type": "text/csv", "size_bytes": len(csv), "sha256": _sha(csv.encode()),
                          "source_family": "S10", "author": f"{tag}-actor",
                          "references": {"case_ids": [f"{tag}-case-0", f"{tag}-case-1"], "member_references": [],
                                         "afca_matter_ids": [afca] if afca else []},
                          "scenario_time": {"available_at": {"value": "2026-02-01", "precision": "day"}}})
    for name, rows in (("case", cases), ("party", parties)):
        (root / "native" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    nat = [{"file": f"native/{n}.jsonl", "sha256": _sha((root / "native" / f"{n}.jsonl").read_bytes())}
           for n in ("case", "party")]
    manifest = {"originals": originals, "participants": [{"actor_ref": f"{tag}-actor"}],
                "native_administrative_inputs": nat}
    (root / "delivery-manifest.json").write_text(json.dumps(manifest))
    (root / "INTAKE-FILES.sha256").write_text("".join(f"{o['sha256']}  {o['file']}\n" for o in originals))
    return _sha((root / "delivery-manifest.json").read_bytes()), [f"{tag}-member-{i}" for i in range(n_members)]


def _split(path: Path, dev, hold):
    path.write_text(json.dumps({"members": [{"member_reference": m, "split": "development"} for m in dev] +
                                          [{"member_reference": m, "split": "holdout"} for m in hold]}))
    return _sha(path.read_bytes())


@pytest.fixture
def world(tmp_path):
    a_sha, a_mem = _bundle(tmp_path / "a", "A", 4, afca="M1",
                           table_rows=[["W1", "A-case-0", "A-case-1", "2026-01-05", "60", "M1"]])
    b_sha, b_mem = _bundle(tmp_path / "b", "B", 6)
    basis = tmp_path / "decision.json"
    basis.write_text("{}")
    sa, sb = _split(tmp_path / "sa.json", a_mem[:3], a_mem[3:]), _split(tmp_path / "sb.json", b_mem[:5], b_mem[5:])
    _bundle(tmp_path / "case11", "C11", 1)
    corpus = {"corpus_id": "t", "bundles": [
        {"bundle_id": "a", "path": str(tmp_path / "a"), "delivery_manifest_sha256": a_sha},
        {"bundle_id": "b", "path": str(tmp_path / "b"), "delivery_manifest_sha256": b_sha}],
        "pilot_bundle_id": "a",
        "source_quality_basis": [{"path": str(basis), "sha256": _sha(b"{}"), "decision": "accepted"}],
        "expected_counts": {"members": 10, "cases": 10, "originals": 11, "afca_matters": 1},
        "expected_split": {"development": 8, "holdout": 2},
        "splits": [{"path": str(tmp_path / "sa.json"), "sha256": sa}, {"path": str(tmp_path / "sb.json"), "sha256": sb}],
        "worklist": {"early_evaluation_member_references": [a_mem[0]]},
        "excluded_corpora": [{"label": "case11", "path": str(tmp_path / "case11")},
                             {"label": "first80", "reason": "contained snapshot"}],
        "observation_window": {"start": "2025-10-01", "end_exclusive": "2026-10-01"}}
    return tmp_path, corpus


def _run(tmp_path, corpus):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus))
    return preflight_combined(str(p), ocr=False)


def test_preflight_passes_and_reports_the_four_states_separately(world):
    tmp, corpus = world
    r = _run(tmp, corpus)
    assert r["state"] == "passed", r["problems"]
    assert r["combined_counts"] == corpus["expected_counts"]
    assert r["states"]["preflight_passed"] == "yes"
    assert r["states"]["runtime_merged"].startswith("no") and r["states"]["inference_run"].startswith("no")
    assert r["forecast"]["eligible_cases"] == 8 and r["forecast"]["holdout_cases_unassessed"] == 2
    assert r["bundles"][0]["table_links"]["full_link_occurrences"] == 1
    assert r["bundles"][0]["table_links"]["predicted_omissions"] == 0


@pytest.mark.parametrize("breakage, expect", [
    ("manifest_pin", "delivery manifest digest"),
    ("tampered_original", "hash mismatches"),
    ("split_counts", "split does not reconcile"),
    ("early_eval_in_holdout", "split does not reconcile"),
    ("counts", "combined counts"),
    ("collision", "identifier collisions"),
    ("excluded_overlap", "overlaps the bound population"),
    ("table_link", "table link occurrences"),
    ("basis_changed", "source-quality basis"),
])
def test_preflight_fails_closed(world, breakage, expect):
    tmp, corpus = world
    c = copy.deepcopy(corpus)
    if breakage == "manifest_pin":
        c["bundles"][0]["delivery_manifest_sha256"] = "0" * 64
    elif breakage == "tampered_original":
        (tmp / "b" / "originals" / "B-0.eml").write_bytes(b"changed")
    elif breakage == "split_counts":
        c["expected_split"] = {"development": 7, "holdout": 3}
    elif breakage == "early_eval_in_holdout":
        c["worklist"]["early_evaluation_member_references"] = ["A-member-3"]
    elif breakage == "counts":
        c["expected_counts"] = {**c["expected_counts"], "cases": 650}
    elif breakage == "collision":
        c["bundles"].append({**c["bundles"][1], "bundle_id": "b-again"})
    elif breakage == "excluded_overlap":
        c["excluded_corpora"][0]["path"] = str(tmp / "b")
    elif breakage == "table_link":
        m = json.loads((tmp / "a" / "delivery-manifest.json").read_text())
        csv = "work_id,case_id,linked_case_ids,date,duration_seconds,afca_matter_id\nW1,A-case-0,A-case-3,2026-01-05,60,M9\n"
        f = tmp / "a" / "originals" / "A-work.csv"
        f.write_text(csv)
        m["originals"][-1].update(sha256=_sha(csv.encode()), size_bytes=len(csv))
        (tmp / "a" / "delivery-manifest.json").write_text(json.dumps(m))
        (tmp / "a" / "INTAKE-FILES.sha256").write_text("".join(f"{o['sha256']}  {o['file']}\n" for o in m["originals"]))
        c["bundles"][0]["delivery_manifest_sha256"] = _sha((tmp / "a" / "delivery-manifest.json").read_bytes())
    elif breakage == "basis_changed":
        (tmp / "decision.json").write_text('{"changed": true}')
    r = _run(tmp, c)
    assert r["state"] == "failed" and r["states"]["preflight_passed"] == "no"
    assert any(expect in p for p in r["problems"]), r["problems"]


def test_missing_corpus_file_is_unrun_not_a_claim_about_delivery(tmp_path):
    r = preflight_combined(str(tmp_path / "absent.json"))
    assert r["state"] == "unrun" and "delivery" not in r["reason"].replace("not present", "")
    from sdbackend.intel.forecast import preflight
    old = preflight(str(tmp_path / "absent.json"))
    assert old["state"] == "unrun" and "no accepted delivery" not in old["reason"]
