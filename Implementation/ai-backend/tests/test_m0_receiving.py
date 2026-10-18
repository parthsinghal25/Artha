"""M0 receiving compatibility: every format in the accepted case-11 slice, including the accepted D02 portal JSON.
Accepted original bytes are read unmodified from the Data delivery and received into an isolated store only."""
import hashlib
from pathlib import Path

import pytest

from sdbackend.extract.jsondoc import extract_json
from sdbackend.extract.registry import extract, validate_bytes
from specimens import mini_bundle

MB2 = Path(__file__).resolve().parents[3] / "Data/synthesis/pilot-v2-releases/case-11-style-microbatch-v2/intake-bundle"
PORTAL = MB2 / "originals/portal/2025-12-31_0930_beth-martin_to_member-services-team.json"
CASE = "C-e1da28538652e263887a"
MEDIA = {".eml": "message/rfc822", ".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
         ".jpg": "image/jpeg", ".json": "application/json"}


@pytest.mark.skipif(not MB2.exists(), reason="accepted microbatch-v2 delivery not present")
def test_every_format_in_the_accepted_slice_validates_and_extracts():
    from sdbackend.extract.image import ocr_runtime_status
    seen = {}
    for f in sorted((MB2 / "originals").rglob("*")):
        if not f.is_file():
            continue
        media = MEDIA[f.suffix.lower()]
        data = f.read_bytes()
        ok, detected, reason = validate_bytes(data, media)
        assert ok, (f.name, reason)
        profile, out = extract(data, media)
        expected = {"complete", "partial"} if media != "image/jpeg" or ocr_runtime_status()["available"] else {"failed"}
        assert out.state in expected, (f.name, out.state, out.failure_reason)
        seen[media] = seen.get(media, 0) + 1
    assert set(seen) == set(MEDIA.values())                  # EML, PDF, CSV, TXT, JPEG and JSON all present


@pytest.mark.skipif(not PORTAL.exists(), reason="accepted D02 portal original not present")
def test_portal_json_is_verbatim_strict_and_navigable():
    data = PORTAL.read_bytes()
    out = extract_json(data)
    assert out.state == "complete" and out.text == data.decode("utf-8")      # exact original, no projection
    ptrs = {p["pointer"]: p for p in out.headers["json_pointers"]}
    p = ptrs["/attachments/0/filename"]
    assert out.text[p["start"]:p["end"]] == "DC-11.pdf"
    free = ptrs["/free_text"]
    assert out.text[free["start"]:free["end"]].startswith("Request REQ-2025-11-01.")
    assert extract_json(b'{"a": 1, "a": 2}').state == "failed"               # duplicate key
    assert extract_json(b"\xef\xbb\xbf{}").state == "failed"                  # BOM
    assert extract_json(b'{"x": NaN}').state == "failed"
    assert validate_bytes(b"not json at all", "application/json")[0] is False  # declared JSON, bytes disagree
    assert validate_bytes(data, "text/plain")[0] is False                    # JSON is not relabelled as text


@pytest.mark.skipif(not PORTAL.exists(), reason="accepted D02 portal original not present")
def test_portal_json_received_served_as_full_source_and_exact_original(rt, tmp_path):
    from sdbackend.api.ops import Operations
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    data = PORTAL.read_bytes()
    bundle = mini_bundle(tmp_path, [{"original_id": "ORIG-D02", "rel": "originals/portal/d02.json", "data": data,
                                     "media": "application/json", "case": CASE,
                                     "available": "2025-12-31T09:30:00+11:00", "kind": "portal_submission"}])
    summary = ingest(rt, receive(bundle, rt=rt))
    assert summary["extractions"]["failed"] == 0
    ops = Operations(rt)
    ctx = ops.handle("leader.demo", dict(operation="context.resolve", preferred_view_id=None,
                                         release_id=summary["release_id"], evidence_as_of="2026-10-01T00:00:00+10:00",
                                         analysis_mode="reconstructed", analysis_as_of=None,
                                         population_reference=None))["context"]
    items = ops.handle("leader.demo", dict(operation="case.sources.list", context_id=ctx["context_id"], case_id=CASE,
                                           source_families=[], cursor=None, page_size=10))["payload"]["items"]
    assert len(items) == 1
    src = ops.handle("leader.demo", dict(operation="source.get", context_id=ctx["context_id"],
                                         source_id=items[0]["source_id"],
                                         content_sha256=items[0]["content_sha256"], citation=None))
    assert src["status"] == "ok", src.get("error")
    payload = src["payload"]
    text = payload["record"]["data"]["text"]
    held = rt.db.get_object("held_original", "ORIG-D02")
    assert held["bytes_sha256"] == hashlib.sha256(data).hexdigest() and held["media_type"] == "application/json"
    assert text[held["projection_header_codepoints"]:] == data.decode("utf-8")   # verbatim after declared header
    assert text.startswith("[Derived text projection sdb-json-projection-1.0.0")
    assert rt.blobs.get(data and held["bytes_sha256"]) == data                   # exact original retained
    action = next(a for a in payload["permitted_asset_actions"] if a["representation"] == "preview")
    assert action["media_type"] == "text/plain"
    assert not any(a["representation"] == "original" for a in payload["permitted_asset_actions"])   # never relabelled
    _ctx_out, body = ops.redeem("leader.demo", action["retrieval_token"])
    assert body == text.encode("utf-8")
    assert ops.handle("leader.demo", dict(operation="analysis.query", context_id=ctx["context_id"], query_id="Q",
                                          definition=dict(registry_id="runtime-measures-0.2.0", measure_id="cases.new",
                                                          definition_version="0.2.0"),
                                          filters={}, population_reference=None))["status"] == "unavailable"


REVIEWS = Path(__file__).resolve().parents[3] / "Data/reviews"
V3 = Path(__file__).resolve().parents[3] / "Data/synthesis/pilot-v2-releases/case-11-rehearsal-v3/intake-bundle"
KIND = {"email": ("email", "message/rfc822"), "portal_json": ("portal_submission", "application/json"),
        "register_export": ("attachment_original", "text/csv"), "call_summary": ("call_summary", "text/plain"),
        "letter": ("letter", "application/pdf")}


def _test_conversion(tmp_path):
    """TEST-ONLY receiver-format view of the accepted microbatch-v2 bytes with identical original IDs and digests.
    It is NOT Data's transport export (pending, Data-owned) and its clocks/metadata are unreviewed."""
    import json as _json
    m = _json.loads((MB2 / "delivery-manifest.json").read_text())
    rows = []
    for o in m["originals"]:
        kind, media = KIND.get(o["kind"], ("attachment_original", o.get("media_type")))
        rows.append({"original_id": o["original_id"], "rel": o["file"], "data": (MB2 / o["file"]).read_bytes(),
                     "media": media, "case": CASE, "kind": kind,
                     "available": o.get("event_at") or "2025-12-21T09:30:00+11:00"})
    return mini_bundle(tmp_path, rows, name="mb2-test-conversion")


@pytest.mark.skipif(not (MB2.exists() and V3.exists()), reason="accepted deliveries not present")
def test_real_signoffs_bind_by_preserved_package_and_identical_ids(rt, tmp_path, monkeypatch):
    from sdbackend.acceptance import DecisionRejected, effective, import_original_signoff, is_pass
    from sdbackend.ingest.build import ingest
    from sdbackend.intake.delivery import receive
    monkeypatch.setenv("SDB_REVIEWER_REGISTRY", str(Path(__file__).resolve().parents[1] / "config/reviewers.json"))
    v3 = import_original_signoff(rt, REVIEWS / "case-11-rehearsal-v3-2026-10-15/REHEARSAL-SOURCE-SIGNOFF.json", V3,
                                 reviewer_id="principal-data-engineer")
    mb = import_original_signoff(rt, REVIEWS / "case-11-style-microbatch-v2-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json",
                                 MB2, reviewer_id="principal-data-engineer")
    assert v3 and mb
    with pytest.raises(DecisionRejected):           # a role without the source gate cannot decide sources
        import_original_signoff(rt, REVIEWS / "case-11-style-microbatch-v2-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json",
                                MB2, reviewer_id="principal-ai-engineer")
    with pytest.raises(DecisionRejected):           # the sign-off binds the authoring package, not another bundle
        import_original_signoff(rt, REVIEWS / "case-11-style-microbatch-v2-2026-10-15/ORIGINAL-SOURCE-SIGNOFF.json",
                                V3, reviewer_id="principal-data-engineer")
    summary = ingest(rt, receive(_test_conversion(tmp_path), rt=rt))
    assert summary["extractions"]["failed"] in (0, 1)            # JPEG OCR may be unavailable in this runtime
    m = __import__("json").loads((MB2 / "delivery-manifest.json").read_text())
    for o in m["originals"]:
        dec = effective(rt, "source", "supplied_original", o["original_id"], o["sha256"])
        assert is_pass(dec), o["original_id"]                   # identical IDs/hashes find the real decision
    rel = rt.db.get_object("release", summary["release_id"])
    held = rt.db.get_object("held_original", "ORIG-C-e1da-microbatch-11-D02")
    proj_entry = next(e for e in rel["entries"] if e["kind"] == "asset"
                      and e["immutable_id"] == held["derived_asset_version_id"])
    proj_acc = rt.db.get_object("acceptance", proj_entry["acceptance_id"])
    assert not proj_acc.get("independent")          # derived projection still needs its own extraction decision
