"""Acceptance 5: the normal receiver, extractors and registered ledger/work profiles are exercised for real (backend
interpreter, unmodified code, no store). Wrong pins, missing MIME/filename/clocks and malformed ledger headers fail."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from authored_helpers import EXAMPLE
from sdgen.authored.export import build
from sdgen.authored.verify import mechanical_checks, read_tree

TOOL = Path(__file__).resolve().parents[1] / "tools/authored_receiver_check.py"
BACKEND_PY = Path(__file__).resolve().parents[2] / "ai-backend/.venv/bin/python"
needs_backend = pytest.mark.skipif(not BACKEND_PY.exists(), reason="backend interpreter not present")


@pytest.fixture(scope="module")
def cand(tmp_path_factory):
    out = tmp_path_factory.mktemp("c")
    assert build(EXAMPLE, out)["ok"]
    return out / "fx-five-members-p1"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def mutated(cand, tmp_path, fn):
    """Copy the intake bundle, apply fn(bundle_dir, manifest), then recompute every listed hash so only the intended
    defect remains."""
    b = tmp_path / "bundle"
    shutil.copytree(cand / "runtime/intake-bundle", b)
    m = json.loads((b / "delivery-manifest.json").read_text())
    fn(b, m)
    for e in m["originals"]:
        if (b / e["file"]).is_file():
            data = (b / e["file"]).read_bytes()
            e["sha256"], e["size_bytes"] = sha(data), len(data)
    (b / "delivery-manifest.json").write_text(json.dumps(m, indent=1))
    lines = sorted(f"{sha(p.read_bytes())}  {p.relative_to(b)}" for p in b.rglob("*") if p.is_file() and p.name != "INTAKE-FILES.sha256")
    (b / "INTAKE-FILES.sha256").write_text("\n".join(lines) + "\n")
    return b


def run_tool(bundle, tmp_path):
    out = tmp_path / "rc.json"
    p = subprocess.run([str(BACKEND_PY), str(TOOL), str(bundle), str(out)], capture_output=True, text=True, timeout=300)
    return p.returncode, (json.loads(out.read_text()) if out.exists() else {"stdout": p.stdout, "stderr": p.stderr})


def ours(bundle):
    files = read_tree(bundle)
    res, _, _, _ = mechanical_checks(files, json.loads(files["delivery-manifest.json"]), [], None)
    return {i["check"]: i for i in res.items}


@needs_backend
def test_real_receiver_stages_every_original_and_native_row(cand, tmp_path):
    rc, r = run_tool(cand / "runtime/intake-bundle", tmp_path)
    assert rc == 0, r
    s = r["summary"]["originals"]
    assert s["originals_listed"] == s["byte_checks_passed"] == s["staged_pending_review"] == 29
    assert s["accepted_by_independent_review"] == 0 and s["rejected"] == 0
    assert all(row["state"] == "staged_pending_review" for n in r["receiver"]["native"] for row in n["rows"])
    assert set(r["receiver"]["attachment_occurrences"]) == {"bytes_reference_consistent"}
    # the current backend gives each source one case and no AFCA links: exactly the shared rows are not projected
    assert r["summary"]["rows_lost_to_current_mapping"] == ["FX-FIN-0401", "FX-FIN-0402", "FX-WRK-0401"]
    t = {x["original_id"]: x for x in r["tables"]}
    assert t["FX-SRC-0504"]["current_ingest_links"]["projected"] == ["FX-FIN-0501", "FX-FIN-0502", "FX-FIN-0503", "FX-FIN-0504"]
    assert "AFCA reference is not one of this source's AFCA links" in t["FX-SRC-0406"]["current_ingest_links"]["unresolved"][0]["reason"]


@needs_backend
def test_wrong_contract_pin_is_refused(cand, tmp_path):
    b = mutated(cand, tmp_path, lambda b, m: m["batch"]["contract_pin"].update(manifest_content_sha256="0" * 64))
    rc, r = run_tool(b, tmp_path)
    assert rc == 1 and "different Shared contract" in r["receiver"]["rejected"]
    assert ours(b)["manifest_shape_and_pin"]["status"] == "fail"


@needs_backend
def test_wrong_declared_mime_is_rejected_by_receiver(cand, tmp_path):
    def f(b, m):
        e = next(o for o in m["originals"] if o["original_id"] == "FX-SRC-0403")
        e["declared_media_type"] = "text/csv"
    b = mutated(cand, tmp_path, f)
    rc, r = run_tool(b, tmp_path)
    row = next(o for o in r["receiver"]["originals"] if o["original_id"] == "FX-SRC-0403")
    assert rc == 1 and row["disposition"] == "rejected" and "disagrees" in " ".join(row["reasons"])
    assert ours(b)["manifest_shape_and_pin"]["status"] == "fail"


def test_omitted_mime_filename_and_clocks_fail_with_actionable_messages(cand, tmp_path):
    def f(b, m):
        e = next(o for o in m["originals"] if o["original_id"] == "FX-SRC-0105")
        e.pop("declared_media_type")
        e.pop("filename")
        e["scenario_time"]["available_at"] = {"value": None, "precision": "unknown", "reason": "dropped"}
    c = ours(mutated(cand, tmp_path, f))["manifest_shape_and_pin"]
    assert c["status"] == "fail"
    text = " ".join(c["failures"])
    assert "FX-SRC-0105" in text and "declared_media_type" in text and "filename" in text and "available_at" in text


@needs_backend
def test_malformed_ledger_header_is_not_a_ledger_for_the_receiver(cand, tmp_path):
    def f(b, m):
        e = next(o for o in m["originals"] if o["original_id"] == "FX-SRC-0504")
        p = b / e["file"]
        p.write_bytes(p.read_bytes().replace(b"amount_minor", b"amount", 1))
    b = mutated(cand, tmp_path, f)
    rc, r = run_tool(b, tmp_path)
    assert rc == 1 and r["ok"] is False
    t = next(x for x in r["tables"] if x["original_id"] == "FX-SRC-0504")
    assert t["current_ingest_links"]["profile_recognised"] is False and t["current_ingest_links"]["projected"] == []
    c = ours(b)["ledger_work_lifecycle_tables"]
    assert c["status"] == "fail" and "amount_minor" in " ".join(c["failures"])
