"""Explicit demo corpus selection (M1) and the `synthetic-demo-v1` publication binding.

A demo store is a separate data directory bound to ONE corpus file (`config/demo/<corpus>.corpus.json`) naming the
exact runtime bundle, its delivery-manifest digest, Data's source-quality decision (by file digest), the temporal
and association profiles and an explicit eligible worklist. Nothing here reads private authoring, review or split
material: the worklist is an allowlist of member references fixed by the coordinator before tuning.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import BACKEND_ROOT, ARTHA_ROOT

DEMO_POINTER = "release:demo"


class CorpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class Corpus:
    path: Path
    body: dict

    @property
    def corpus_id(self) -> str:
        return self.body["corpus_id"]

    @property
    def bundle(self) -> Path:
        return (ARTHA_ROOT / self.body["bundle"]).resolve()

    @property
    def eligible_members(self) -> list[str]:
        return list(self.body["worklist"]["eligible_member_references"])

    @property
    def early_evaluation_members(self) -> list[str]:
        return list(self.body["worklist"].get("early_evaluation_member_references", []))

    @property
    def composite(self) -> bool:
        return "bundles" in self.body

    def demo_binding(self) -> dict:
        b = self.body
        return {"corpus_id": b["corpus_id"], "publication_policy": b["publication_policy"],
                "delivery_manifest_sha256": b["delivery_manifest_sha256"],
                "source_quality_basis": basis_summary(b["source_quality_basis"])}


def basis_entries(basis) -> list[dict]:
    return list(basis) if isinstance(basis, list) else [basis]


def basis_summary(basis) -> dict:
    """One {path, sha256} label for a single Data decision, or for the ordered list of a composite corpus's
    decisions (each still verified by its own file digest)."""
    if isinstance(basis, dict):
        return basis
    from ..canonical import canonical_sha256
    return {"path": " + ".join(x["path"] for x in basis),
            "sha256": canonical_sha256([[x["path"], x["sha256"]] for x in basis])}


def composite_manifest_sha256(body: dict) -> str:
    """Identity of a composite delivery: the corpus id and every bundle's exact delivery-manifest digest, in order."""
    from ..canonical import canonical_sha256
    return canonical_sha256({"composite_corpus": body["corpus_id"],
                             "bundles": [[b["bundle_id"], b["delivery_manifest_sha256"]] for b in body["bundles"]]})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus(path: Path | str | None = None, *, verify: bool = True) -> Corpus:
    import os
    p = Path(path or os.environ.get("SDB_DEMO_CORPUS") or "")
    if not p.is_file():
        p = BACKEND_ROOT / str(path or "")
    if not p.is_file():
        raise CorpusError(f"demo corpus file not found: {path}")
    body = json.loads(p.read_text(encoding="utf-8"))
    c = Corpus(p.resolve(), body)
    if verify:
        if c.composite:
            for b in body["bundles"]:
                if _sha(ARTHA_ROOT / b["path"] / "delivery-manifest.json") != b["delivery_manifest_sha256"]:
                    raise CorpusError(f"bundle {b['bundle_id']} delivery manifest differs from the corpus file's digest")
            if body["delivery_manifest_sha256"] != composite_manifest_sha256(body):
                raise CorpusError("composite delivery digest does not match the corpus file's bundles")
        else:
            manifest = c.bundle / "delivery-manifest.json"
            if _sha(manifest) != body["delivery_manifest_sha256"]:
                raise CorpusError("bundle delivery manifest differs from the corpus file's digest")
        for x in basis_entries(body["source_quality_basis"]):
            basis = ARTHA_ROOT / x["path"]
            if not basis.is_file() or _sha(basis) != x["sha256"]:
                raise CorpusError("source-quality basis file missing or changed since the corpus was named")
        if body.get("publication_policy") != "synthetic-demo-v1":
            raise CorpusError("corpus file must declare publication_policy synthetic-demo-v1")
    return c


def demo_release_view(rt):
    """`release:demo` only when its control binds this process's corpus file (manifest + basis digests)."""
    from ..api.repo import ReleaseView
    row = rt.db.get_pointer(DEMO_POINTER)
    if row is None or rt.settings.demo_corpus_path is None:
        return None
    try:
        corpus = load_corpus(rt.settings.demo_corpus_path, verify=False)
    except CorpusError:
        return None
    release = rt.db.get_object("release", row["target_id"])
    control = rt.db.get_object("release_control", row["target_id"])
    if release is None or control is None or rt.db.object_sha("release", row["target_id"]) != row["target_sha256"]:
        return None
    demo = control.get("demo") or {}
    want = corpus.demo_binding()
    if control.get("status") != "synthetic_demo" or {k: demo.get(k) for k in want} != want:
        return None
    return ReleaseView(release, control)


# ------------------------------------------------------------- population facts (native rows only)
def case_members(rt, release_view) -> dict[str, list[str]]:
    """case_id → canonical member references, from accepted native case/party rows in the release (never from
    representatives, accounts or documents)."""
    natives = []
    for e in release_view.release["entries"]:
        if e["kind"] == "input":
            rec = rt.db.get_object("native_record", e["immutable_id"])
            if rec is not None:
                natives.append(rec)
    superseded = {n["envelope"]["supersedes_record_id"] for n in natives if n["envelope"].get("supersedes_record_id")}
    heads = [n for n in natives if n["envelope"]["record_id"] not in superseded]
    party_member = {n["data"]["party_id"]: n["data"].get("member_reference") for n in heads if n["kind"] == "party"
                    and n["data"].get("party_role") in ("member", "complainant", "claimant")}
    out = {}
    for n in heads:
        if n["kind"] == "case":
            out[n["data"]["case_id"]] = sorted({party_member[p] for p in n["data"].get("party_ids", [])
                                                if party_member.get(p)})
    return out


def native_cases(rt, release_view) -> dict[str, dict]:
    out = {}
    superseded = set()
    rows = []
    for e in release_view.release["entries"]:
        if e["kind"] == "input":
            rec = rt.db.get_object("native_record", e["immutable_id"])
            if rec is not None and rec["kind"] == "case":
                rows.append(rec)
                if rec["envelope"].get("supersedes_record_id"):
                    superseded.add(rec["envelope"]["supersedes_record_id"])
    for r in rows:
        if r["envelope"]["record_id"] not in superseded:
            out[r["data"]["case_id"]] = r
    return out


def eligible_cases(rt, release_view, corpus: Corpus) -> tuple[list[str], list[str]]:
    """(eligible case ids, not-eligible case ids). A case is eligible when every member party on it is on the
    explicit allowlist; a case touching any non-listed member stays unassessed (connected grouping preserved)."""
    allowed = set(corpus.eligible_members)
    members = case_members(rt, release_view)
    ok = sorted(c for c, ms in members.items() if ms and set(ms) <= allowed)
    return ok, sorted(set(members) - set(ok))


def reports_dir(corpus: "Corpus"):
    """Saved intelligence/report outputs; tests redirect with SDB_INTEL_REPORT_DIR so they never touch reports/."""
    import os
    from pathlib import Path
    d = Path(os.environ.get("SDB_INTEL_REPORT_DIR") or BACKEND_ROOT / "reports" / "intel" / corpus.corpus_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
