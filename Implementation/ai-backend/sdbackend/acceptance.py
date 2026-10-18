"""Host-controlled acceptance registry (R01/R05).

Independent decisions enter only by operator import of a reviewer's decision document (`sdb-review-decision@1.0.0`).
The importer checks:
* the reviewer is in the backend-owned trusted allowlist (`config/reviewers.json`, never sourced from deliveries,
  model output or requests) and is authorised for that gate;
* the contract baseline is the adopted Shared freeze;
* every subject names an exact kind + ID + SHA-256 (bytes, canonical record or closure digest);
* results are pass | fail | repair_required | pending | withdrawn.

Each subject becomes an immutable `acceptance` object bound to that exact digest. The effective decision for a
subject is the latest imported one (by decided_at, then import order); a later withdrawal/fail supersedes a pass.
Changed bytes never inherit an approval because the digest differs. Code, models and tests can create mechanical
records (`independent: false`) but cannot create an independent pass: only an allowlisted reviewer's imported
decision does. Specimen reviewers (tests) are refused unless `SDB_ALLOW_SPECIMEN_ACCEPTANCE=1`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256, derived_id, utc_now_iso
from .config import BACKEND_ROOT
from .contracts import EXPECTED_MANIFEST_SHA256

DECISION_FORMAT = ("sdb-review-decision", ("1.0.0",))
GATES = {
    "source": {"supplied_original"},
    "native_input": {"native_record"},
    "extraction": {"extraction", "derived_projection"},
    "diagnostic_evaluation": {"stage_s03", "stage_s04", "stage_s05", "evaluation_report"},
    "result_integrity": {"analysis_closure"},
}
RESULTS = ("pass", "fail", "repair_required", "pending", "withdrawn")
SHA = "0123456789abcdef"


class DecisionRejected(RuntimeError):
    pass


def reviewer_registry_path() -> Path:
    return Path(os.environ.get("SDB_REVIEWER_REGISTRY") or BACKEND_ROOT / "config" / "reviewers.json")


def load_reviewers() -> dict:
    path = reviewer_registry_path()
    if not path.is_file():
        return {"registry_id": "absent", "reviewers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def specimen_allowed() -> bool:
    return os.environ.get("SDB_ALLOW_SPECIMEN_ACCEPTANCE", "").lower() in ("1", "true", "yes")


def _is_sha(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(ch in SHA for ch in v)


def _instant(v: Any) -> bool:
    try:
        return isinstance(v, str) and datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_decision(doc: Any) -> tuple[dict, list[str]]:
    """Return (reviewer entry, problems). Malformed decisions yield problems, never exceptions."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return {}, ["decision document is not a JSON object"]
    fmt, versions = DECISION_FORMAT
    if doc.get("decision_format") != fmt or doc.get("format_version") not in versions:
        problems.append(f"unsupported decision format {doc.get('decision_format')}@{doc.get('format_version')}")
    for k in ("decision_id", "reviewer_id", "gate", "decided_at"):
        if not isinstance(doc.get(k), str) or not doc.get(k):
            problems.append(f"missing {k}")
    if doc.get("independent") is not True:
        problems.append("decision does not declare independence")
    if doc.get("contract_baseline") != EXPECTED_MANIFEST_SHA256:
        problems.append("contract baseline is not the adopted Shared 1.0.0 freeze")
    if not _instant(doc.get("decided_at")):
        problems.append("decided_at is not an offset instant")
    gate = doc.get("gate")
    if gate not in GATES:
        problems.append(f"unknown gate {gate}")
    rep = doc.get("evidence_report")
    if not (isinstance(rep, dict) and isinstance(rep.get("name"), str) and _is_sha(rep.get("sha256"))):
        problems.append("evidence_report must name the review report and its sha256")
    reviewers = load_reviewers().get("reviewers", {})
    reviewer = reviewers.get(doc.get("reviewer_id")) if isinstance(doc.get("reviewer_id"), str) else None
    if reviewer is None:
        problems.append("reviewer is not in the trusted reviewer allowlist")
    else:
        if gate not in reviewer.get("gates", []):
            problems.append(f"reviewer is not authorised for gate {gate}")
        if reviewer.get("specimen") and not specimen_allowed():
            problems.append("specimen reviewer refused outside isolated tests")
    subjects = doc.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        problems.append("subjects must be a non-empty list")
        subjects = []
    seen = set()
    for i, s in enumerate(subjects):
        if not isinstance(s, dict):
            problems.append(f"subject {i} is not an object")
            continue
        if gate in GATES and s.get("subject_kind") not in GATES[gate]:
            problems.append(f"subject {i}: kind {s.get('subject_kind')} not reviewable under gate {gate}")
        if not isinstance(s.get("subject_id"), str) or not s.get("subject_id"):
            problems.append(f"subject {i}: missing subject_id")
        if not _is_sha(s.get("subject_sha256")):
            problems.append(f"subject {i}: subject_sha256 must be an exact lowercase sha256")
        if s.get("result") not in RESULTS:
            problems.append(f"subject {i}: result must be one of {RESULTS}")
        key = (s.get("subject_kind"), s.get("subject_id"), s.get("subject_sha256"))
        if key in seen:
            problems.append(f"subject {i}: duplicated")
        seen.add(key)
    return reviewer or {}, problems


def subject_key(gate: str, kind: str, sid: str, sha: str) -> str:
    return f"{gate}|{kind}|{sid}|{sha}"


def import_decision(rt, doc_or_path: dict | str | Path) -> dict:
    """Validate and seal one reviewer decision. Idempotent for identical documents; a different document reusing a
    decision_id is refused."""
    if isinstance(doc_or_path, (str, Path)):
        try:
            doc = json.loads(Path(doc_or_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DecisionRejected(f"decision file unreadable: {type(exc).__name__}") from exc
    else:
        doc = doc_or_path
    reviewer, problems = validate_decision(doc)
    if problems:
        raise DecisionRejected("; ".join(problems[:6]))
    doc_sha = canonical_sha256(doc)
    existing = rt.db.get_object("review_decision", doc["decision_id"])
    if existing is not None:
        if existing["decision_sha256"] != doc_sha:
            raise DecisionRejected(f"decision {doc['decision_id']} was already imported with different content")
        return {"decision_id": doc["decision_id"], "imported": False, "acceptance_ids": existing["acceptance_ids"]}
    order = rt.db.connect().execute("SELECT COUNT(*) AS n FROM objects WHERE kind='review_decision'").fetchone()["n"]
    ids = []
    for s in doc["subjects"]:
        body = {"subject_kind": s["subject_kind"], "subject_id": s["subject_id"], "subject_sha256": s["subject_sha256"],
                "contract_baseline": doc["contract_baseline"], "gate": doc["gate"], "result": s["result"],
                "checker": doc["reviewer_id"], "independent": True, "specimen": bool(reviewer.get("specimen")),
                "limitations": sorted(s.get("limitations") or []), "decided_at": doc["decided_at"],
                "decision_id": doc["decision_id"], "decision_sha256": doc_sha,
                "evidence_report_sha256": doc["evidence_report"]["sha256"], "import_order": order,
                "context": s.get("context") or {}}
        aid = derived_id("ACC", body)
        rt.db.put_object("acceptance", aid, {"acceptance_id": aid, **body},
                         links={"subject": [subject_key(doc["gate"], s["subject_kind"], s["subject_id"],
                                                        s["subject_sha256"])],
                                "subject_id": [f"{doc['gate']}|{s['subject_kind']}|{s['subject_id']}"]})
        ids.append(aid)
    rt.db.put_object("review_decision", doc["decision_id"], {
        "decision_id": doc["decision_id"], "decision_sha256": doc_sha, "reviewer_id": doc["reviewer_id"],
        "gate": doc["gate"], "imported_at": utc_now_iso(), "acceptance_ids": ids, "document": doc})
    return {"decision_id": doc["decision_id"], "imported": True, "acceptance_ids": ids}


def _sort_key(a: dict):
    return (datetime.fromisoformat(a["decided_at"].replace("Z", "+00:00")), a.get("import_order", 0))


def history(rt, gate: str, kind: str, sid: str) -> list[dict]:
    """All imported decisions for a subject ID under a gate (any digest), oldest first."""
    ids = rt.db.linked("acceptance", "subject_id", f"{gate}|{kind}|{sid}")
    return sorted((rt.db.get_object("acceptance", i) for i in ids), key=_sort_key)


def effective(rt, gate: str, kind: str, sid: str, sha: str) -> dict | None:
    """Latest decision for the EXACT subject digest; a later decision on another digest of the same ID (e.g. a
    withdrawal after changed bytes) does not approve this digest either way."""
    ids = rt.db.linked("acceptance", "subject", subject_key(gate, kind, sid, sha))
    recs = sorted((rt.db.get_object("acceptance", i) for i in ids), key=_sort_key)
    recs = [r for r in recs if r and r.get("independent") and (not r.get("specimen") or specimen_allowed())]
    return recs[-1] if recs else None


def is_pass(rec: dict | None) -> bool:
    return bool(rec) and rec.get("result") == "pass" and rec.get("independent") is True


def still_effective(rt, acceptance_id: str) -> bool:
    """True if this imported pass is still the effective decision for its exact subject (not withdrawn since)."""
    rec = rt.db.get_object("acceptance", acceptance_id)
    if rec is None:
        return False
    if not rec.get("independent"):
        return rec.get("result") == "pass"            # mechanical record: content-addressed, never withdrawn
    eff = effective(rt, rec["gate"], rec["subject_kind"], rec["subject_id"], rec["subject_sha256"])
    return eff is not None and eff["acceptance_id"] == acceptance_id and is_pass(eff)


def mechanical(rt, *, subject_kind: str, subject_id: str, subject_sha256: str, gate: str, result: str,
               checker: str, limitations: list[str], decided_at: str | None = None) -> str:
    """Code-produced (non-independent) record: byte checks, pending placeholders, integrity mechanics."""
    body = {"subject_kind": subject_kind, "subject_id": subject_id, "subject_sha256": subject_sha256,
            "contract_baseline": EXPECTED_MANIFEST_SHA256, "gate": gate, "result": result, "checker": checker,
            "independent": False, "limitations": sorted(limitations)}
    acceptance_id = derived_id("ACC", body)
    if rt.db.object_sha("acceptance", acceptance_id) is None:
        rt.db.put_object("acceptance", acceptance_id, {"acceptance_id": acceptance_id, **body,
                                                       "decided_at": decided_at or utc_now_iso()})
    return acceptance_id


SIGNOFF_KIND = "principal_data_original_artifact_review"


def import_original_signoff(rt, signoff_path: str | Path, bundle_dir: str | Path, *, reviewer_id: str) -> dict:
    """Map Data's hash-bound original-artifact sign-off onto `source` gate decisions, verifying every binding.

    Trust is not created here: `reviewer_id` must already be on the backend allowlist (validate_decision) with the
    `source` gate. The sign-off must be `accepted`, bind the exact delivery-manifest bytes, and every listed original
    must match the manifest entry and the delivered bytes. Only the `source` gate is affected — the sign-off itself
    states that production extraction acceptance, gold and full-case acceptance are pending. The review date is
    day-precision, so the decision instant is the end of that Melbourne day (never an invented earlier time).
    """
    import hashlib
    from datetime import date as _date, timedelta
    from .engine.core import melbourne_midnight
    raw = Path(signoff_path).read_bytes()
    doc = json.loads(raw)
    if doc.get("kind") != SIGNOFF_KIND or doc.get("decision") != "accepted":
        raise DecisionRejected(f"not an accepted {SIGNOFF_KIND}")
    bundle = Path(bundle_dir)
    manifest_bytes = (bundle / "delivery-manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != doc.get("bound_manifest_sha256"):
        raise DecisionRejected("sign-off is bound to a different delivery manifest")
    manifest = json.loads(manifest_bytes)
    entries = {o["original_id"]: o for o in manifest["originals"]}
    subjects = []
    for o in doc.get("originals", []):
        e = entries.get(o.get("original_id"))
        if e is None or e["file"] != o.get("file") or e["sha256"] != o.get("sha256"):
            raise DecisionRejected(f"original {o.get('original_id')} does not match the bound manifest")
        if hashlib.sha256((bundle / e["file"]).read_bytes()).hexdigest() != o["sha256"]:
            raise DecisionRejected(f"delivered bytes of {o['original_id']} differ from the signed digest")
        subjects.append({"subject_kind": "supplied_original", "subject_id": o["original_id"],
                         "subject_sha256": o["sha256"], "result": "pass",
                         "limitations": [str(doc.get("accepted_scope", ""))[:300],
                                         "production extraction acceptance: "
                                         f"{doc.get('production_extraction_source_acceptance', 'unstated')}"]})
    if not subjects:
        raise DecisionRejected("sign-off lists no originals")
    day = _date.fromisoformat(doc["review_date"])
    decision = {"decision_format": DECISION_FORMAT[0], "format_version": DECISION_FORMAT[1][0],
                "decision_id": derived_id("DEC-SIGNOFF", hashlib.sha256(raw).hexdigest()),
                "reviewer_id": reviewer_id, "independent": True,
                "decided_at": melbourne_midnight(day + timedelta(days=1)).isoformat(),
                "contract_baseline": EXPECTED_MANIFEST_SHA256, "gate": "source",
                "evidence_report": {"name": Path(signoff_path).name, "sha256": hashlib.sha256(raw).hexdigest()},
                "subjects": subjects}
    return import_decision(rt, decision)
