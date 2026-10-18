"""Data-side receiver preflight for a case-11 delivery, using the UNMODIFIED AI/backend receiver and ingest code in place, against a
disposable store. Not an AI/backend intake, not a receipt into the served store and not acceptance of anything.

Run with the backend interpreter (its dependencies), e.g.
  SDB_DATA_DIR=<empty scratch dir> ../ai-backend/.venv/bin/python tools/receiver_preflight.py <candidate export dir> <out json> [--baseline <slice export dir>]

Steps, all in the disposable store:
 1. optional baseline: receive + ingest the earlier accepted-slice export (as AI did), recording asset/source identities;
 2. import the REAL existing Principal source sign-offs with the backend's own `import_original_signoff` (allowlisted role, every hash
    binding verified against the package each sign-off binds) so dispositions are realistic; nothing new is decided;
 3. receive + ingest the candidate; record dispositions, extraction states per original, XLSX/CSV/TXT extraction facts;
 4. ingest the identical candidate again (duplicate-safe reimport) and compare release, sources and object counts;
 5. compare identities of originals shared with the baseline."""
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SD = HERE.parents[3]
BACKEND = SD / "Implementation/ai-backend"
sys.path.insert(0, str(BACKEND))

from sdbackend.acceptance import import_original_signoff  # noqa: E402
from sdbackend.ingest.build import ingest  # noqa: E402
from sdbackend.intake.delivery import receive  # noqa: E402
from sdbackend.runtime import make_runtime  # noqa: E402

CODE = ["sdbackend/intake/delivery.py", "sdbackend/ingest/build.py", "sdbackend/extract/registry.py", "sdbackend/extract/xlsx.py",
        "sdbackend/extract/textual.py", "sdbackend/extract/eml.py", "sdbackend/extract/pdf.py", "sdbackend/acceptance.py",
        "sdbackend/engine/native_work.py", "sdbackend/contracts.py", "config/reviewers.json"]
SIGNOFFS = [  # (sign-off, the intake bundle it binds)
    ("Data/reviews/case-11-rehearsal-v3-2026-10-15/REHEARSAL-SOURCE-SIGNOFF.json", "Data/synthesis/pilot-v2-releases/case-11-rehearsal-v3/intake-bundle"),
    ("Data/reviews/case-11-style-microbatch-v2-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json", "Data/synthesis/pilot-v2-releases/case-11-style-microbatch-v2/intake-bundle"),
    ("Data/reviews/case-11-remaining-cards-v1-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json", "Data/synthesis/pilot-v2-releases/case-11-remaining-cards-v1/intake-bundle"),
]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def identities(rt, release_id):
    """{original_id: {asset_version_id, source_id, extraction state/omissions}} for supplied originals of one release."""
    rel = rt.db.get_object("release", release_id)
    out = {}
    for ent in rel["entries"]:
        if ent["kind"] != "asset":
            continue
        a = rt.db.get_object("asset", ent["immutable_id"])
        if a is None or a.get("representation_role") not in ("supplied_original", "preview") or a.get("derived_from_asset_version_ids"):
            continue
        ext = rt.db.get_object("extraction", a["extraction_ids"][0]) if a["extraction_ids"] else None
        out[a["filename"]] = {"asset_version_id": a["asset_version_id"], "logical_document_id": a["logical_document_id"], "version": a["version"],
                              "bytes_sha256": a["bytes_sha256"], "media_type": a["media_type"], "source_ids": a["source_ids"],
                              "scenario_available_at": a["scenario_available_at"],
                              "extraction": ext and {"state": ext["state"], "extractor": ext["extractor"], "omissions": ext["omissions"]}}
    return out


def main(candidate, out, baseline=None):
    data_dir = Path(os.environ["SDB_DATA_DIR"])
    assert data_dir.is_dir() and not any(data_dir.iterdir()), "SDB_DATA_DIR must be an existing EMPTY scratch directory"
    rt = make_runtime()
    report = {"kind": "data_run_receiver_preflight",
              "run_by": "Synthetic Data coding assistant (Data side), unmodified AI/backend receiver/ingest code in place, disposable store",
              "not": "not an AI/backend intake into the served store, not a receipt AI should rely on, not acceptance of anything",
              "receiver_code_sha256": {p: sha((BACKEND / p).read_bytes()) for p in CODE},
              "candidate": str(Path(candidate).resolve()).replace(str(SD.parent) + "/", ""),
              "candidate_manifest_sha256": sha((Path(candidate) / "intake-bundle/delivery-manifest.json").read_bytes())}
    base_ids = None
    if baseline:
        d0 = receive(Path(baseline) / "intake-bundle", rt=rt)
        r0 = ingest(rt, d0)
        base_ids = identities(rt, r0["release_id"])
        report["baseline"] = {"export": Path(baseline).name, "manifest_sha256": d0.manifest_sha256, "release": {k: r0[k] for k in r0 if k != "limitations"}}
    imported = []
    for so, bundle in SIGNOFFS:
        res = import_original_signoff(rt, SD / so, SD / bundle, reviewer_id="principal-data-engineer")
        imported.append({"signoff": so, "signoff_sha256": sha((SD / so).read_bytes()), "bundle": bundle,
                         "result": {k: res[k] for k in res if k in ("decision_id", "imported", "subjects", "acceptance_ids")} if isinstance(res, dict) else str(res)})
    report["signoffs_imported_into_disposable_store"] = imported
    d1 = receive(Path(candidate) / "intake-bundle", rt=rt)
    report["receipt"] = d1.receipt
    r1 = ingest(rt, d1)
    report["ingest"] = r1
    ids1 = identities(rt, r1["release_id"])
    report["originals"] = ids1
    counts1 = {k: len(rt.db.list_objects(k)) for k in ("asset", "extraction", "source", "attachment", "release", "native_record")}
    d2 = receive(Path(candidate) / "intake-bundle", rt=rt)
    r2 = ingest(rt, d2)
    counts2 = {k: len(rt.db.list_objects(k)) for k in counts1}
    report["reimport"] = {"release_id_same": r2["release_id"] == r1["release_id"], "source_set_same": r2["source_set_version"] == r1["source_set_version"],
                          "object_counts_after_first": counts1, "object_counts_after_second": counts2,
                          "identities_same": identities(rt, r2["release_id"]) == ids1}
    if base_ids is not None:
        shared = sorted(set(base_ids) & set(ids1))
        report["identity_vs_baseline"] = {"shared_files": len(shared),
                                          "same_asset_version_and_source_ids": sum(base_ids[f]["asset_version_id"] == ids1[f]["asset_version_id"]
                                                                                    and base_ids[f]["source_ids"] == ids1[f]["source_ids"] for f in shared),
                                          "differences": [f for f in shared if base_ids[f]["asset_version_id"] != ids1[f]["asset_version_id"]
                                                          or base_ids[f]["source_ids"] != ids1[f]["source_ids"]]}
    Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({"receipt_summary": d1.receipt["summary"], "ingest": {k: r1[k] for k in r1 if k != "limitations"},
                      "reimport": report["reimport"], "identity_vs_baseline": report.get("identity_vs_baseline")}, indent=1))


if __name__ == "__main__":
    a = sys.argv[1:]
    base = a[a.index("--baseline") + 1] if "--baseline" in a else None
    main(a[0], a[1], base)
