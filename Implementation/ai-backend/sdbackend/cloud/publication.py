"""Deployment export of one complete intel publication, and its verified restore into an empty directory.

Export (`export_serving_publication`): a consistent SQLite online backup of the store (read-only source connection),
plus every registered original/derived blob, sealed packages, and the exact corpus and access-policy bindings. The
copy has its serving/authentication state removed and is VACUUMed: `sessions`, `contexts`, `cursors`,
`asset_tokens` and `rate_reservations` are emptied. Login codes, `.env`, run/PID files, Antigravity traces and the
WAL/SHM files are never copied. The bindings are validated together on the snapshot copy, never on the live store:
release → publication → analysis set → taxonomy → memories/assessments/registry → blobs. Live pointers are not
changed.

The result is one immutable directory `<out>/<export_id>/` holding `payload.tar.gz` (deterministic tar, mtime 0) and
`ARTHA-EXPORT-MANIFEST.json`. The manifest pins the payload SHA-256 and every member's SHA-256. It also carries the
identifiers, cutoff, coverage, publication log and model provenance.

Restore (`restore_export`) needs an empty target. It verifies the manifest digest when one is supplied, then the
payload digest and every member (no missing or extra files). It then runs SQLite `integrity_check`, re-verifies
each blob against its content address, checks the revoked tables are empty, and finally re-validates the same
bindings through the backend's own read path (`served_release`, `intel:current`). Any failure raises
`RestoreRefused`; nothing partial is reported as ready.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

from ..canonical import canonical_sha256, utc_now_iso

MANIFEST_NAME = "ARTHA-EXPORT-MANIFEST.json"
PAYLOAD_NAME = "payload.tar.gz"
EXPORT_KIND = "artha-serving-export"
EXPORT_VERSION = "1.0.0"
REVOKED_TABLES = ("sessions", "contexts", "asset_tokens", "cursors", "rate_reservations")
NEVER_COPIED = ["login-codes.json", ".env", "run/ (PID and server/worker logs)", "agy-traces/",
                "control.sqlite3-wal / -shm (folded into the online backup)"]

# The reviewed pilot (default export input). A newer publication must be named explicitly and pass the same checks.
PILOT = {"store": "var/demo/p4-pilot-v1", "corpus": "config/demo/p4-pilot-v1.corpus.json",
         "policy": "config/demo/policy.json", "release_id": "REL-8c0fb856c850adfe3674",
         "publication_id": "IPUB-f183e075f92f77c9a7a977e9", "analysis_set_version": "ASET-ecc8f4a04020c04cde6c9bab",
         "taxonomy_version": "TAXV-b90d96b59fd4796d5a640873", "intelligence_schema_version": "1.1.0",
         "assessed": 20, "received": 25}


class ExportRefused(RuntimeError):
    pass


class RestoreRefused(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _obj(conn, kind, oid):
    r = conn.execute("SELECT body, sha256 FROM objects WHERE kind=? AND object_id=?", (kind, oid)).fetchone()
    return (None, None) if r is None else (json.loads(r[0]), r[1])


def _pointer(conn, key):
    r = conn.execute("SELECT pointer_key, target_kind, target_id, target_sha256, generation, acceptance_id, "
                     "authority_revision, updated_at FROM publication_pointers WHERE pointer_key=?", (key,)).fetchone()
    return None if r is None else dict(zip(("pointer_key", "target_kind", "target_id", "target_sha256", "generation",
                                            "acceptance_id", "authority_revision", "updated_at"), r))


def _display(path: Path) -> str:
    from ..config import BACKEND_ROOT
    return str(path.relative_to(BACKEND_ROOT)) if path.is_relative_to(BACKEND_ROOT) else path.name


def _intel_schema_version() -> tuple[str, str]:
    p = Path(__file__).resolve().parents[1] / "intel" / "schemas" / "intelligence-v1.schema.json"
    return json.loads(p.read_text(encoding="utf-8"))["version"], sha256_file(p)


def validate_bindings(conn, corpus_body: dict, policy_body: dict, expect: dict | None = None, *,
                      allow_pending: bool = False) -> dict:
    """Check that one publication is complete and coherent in a SQLite store (normally the snapshot copy).
    Returns the binding summary; raises ExportRefused with every problem."""
    from ..intel.corpus import basis_summary
    from ..policy import permission_revision
    problems: list[str] = []
    rel_ptr, pub_ptr = _pointer(conn, "release:demo"), _pointer(conn, "intel:current")
    if rel_ptr is None or pub_ptr is None:
        raise ExportRefused("store has no release:demo / intel:current pointer: not a published demo store")
    release, release_sha = _obj(conn, "release", rel_ptr["target_id"])
    control, _ = _obj(conn, "release_control", rel_ptr["target_id"])
    pub, pub_sha = _obj(conn, "intel_publication", pub_ptr["target_id"])
    if release is None or release_sha != rel_ptr["target_sha256"]:
        problems.append("release:demo target missing or its digest differs from the pointer")
    if pub is None or pub_sha != pub_ptr["target_sha256"]:
        problems.append("intel:current target missing or its digest differs from the pointer")
    if problems:
        raise ExportRefused("; ".join(problems))
    want_demo = {"corpus_id": corpus_body["corpus_id"], "publication_policy": corpus_body["publication_policy"],
                 "delivery_manifest_sha256": corpus_body["delivery_manifest_sha256"],
                 "source_quality_basis": basis_summary(corpus_body["source_quality_basis"])}
    demo = (control or {}).get("demo") or {}
    if (control or {}).get("status") != "synthetic_demo" or {k: demo.get(k) for k in want_demo} != want_demo:
        problems.append("release control is not synthetic_demo bound to this corpus file")
    closure = pub["closure"]
    if closure["release_id"] != release["release_id"] or closure["release_sha256"] != release_sha:
        problems.append("publication closure binds a different release")
    aset_id = pub["analysis_set_version"]
    an_ptr = _pointer(conn, f"analysis:{release['release_id']}")
    aset, aset_sha = _obj(conn, "analysis_set", aset_id)
    if an_ptr is None or an_ptr["target_id"] != aset_id or aset is None or aset_sha != an_ptr["target_sha256"]:
        problems.append("analysis pointer does not bind the publication's analysis set")
    if aset is not None and aset.get("closure_sha256") != pub.get("closure_sha256"):
        problems.append("analysis set closure differs from the publication closure")
    taxonomy = (aset or {}).get("taxonomy_version")
    if taxonomy is None or _obj(conn, "taxonomy", taxonomy)[0] is None:
        problems.append("taxonomy version missing")
    missing = {"case_memory": [m for m in closure["memory_ids"] if _obj(conn, "case_memory", m)[0] is None],
               "membership_assessment": [a for a in closure["assessment_ids"]
                                         if _obj(conn, "membership_assessment", a)[0] is None],
               "case_view": [v for v in pub.get("case_views", {}).values() if _obj(conn, "case_view", v)[0] is None]}
    if closure.get("registry_id") and _obj(conn, "issue_registry", closure["registry_id"])[0] is None:
        missing["issue_registry"] = [closure["registry_id"]]
    for k, v in missing.items():
        if v:
            problems.append(f"{len(v)} {k} object(s) named by the publication are missing, e.g. {v[:2]}")
    if set(pub.get("memory_by_case", {}).values()) - set(closure["memory_ids"]):
        problems.append("memory_by_case names memories outside the closure")
    failed = [k for k, v in (pub.get("checks") or {}).items() if v is not True]
    if failed or not pub.get("checks"):
        problems.append(f"publication mechanical checks not all true: {failed}")
    if pub.get("publication_policy") != "synthetic-demo-v1":
        problems.append("publication is not under synthetic-demo-v1")
    cov = pub["coverage"]
    pending = list(cov.get("pending_memories") or [])
    if (pending or cov.get("unassessed_pairs")) and not allow_pending:
        problems.append(f"publication is partial: {len(pending)} eligible case(s) pending memory, "
                        f"{len(cov.get('unassessed_pairs') or [])} unassessed pair(s); pass allow_pending to "
                        "export it as a disclosed partial publication")
    schema_version, schema_sha = _intel_schema_version()
    summary = {
        "release_id": release["release_id"], "release_sha256": release_sha,
        "release_status": control.get("status"), "source_set_version": control.get("source_set_version"),
        "publication_id": pub["publication_id"], "publication_sha256": pub_sha,
        "publication_policy": pub["publication_policy"], "published_at": pub.get("published_at"),
        "closure_sha256": pub.get("closure_sha256"), "analysis_set_version": aset_id,
        "analysis_set_sha256": aset_sha, "taxonomy_version": taxonomy, "registry_id": closure.get("registry_id"),
        "intelligence_schema_version": schema_version, "intelligence_schema_sha256": schema_sha,
        "measures_version": closure.get("measures_version"), "publisher_version": closure.get("publisher_version"),
        "corpus_id": corpus_body["corpus_id"], "delivery_manifest_sha256": corpus_body["delivery_manifest_sha256"],
        "permission_revision": permission_revision(policy_body),
        "pointer_authority_revision": pub_ptr["authority_revision"],
        "pointers": {p["pointer_key"]: {k: p[k] for k in ("target_id", "target_sha256", "generation")}
                     for p in (rel_ptr, an_ptr, pub_ptr) if p}}
    if summary["permission_revision"] != summary["pointer_authority_revision"]:
        problems.append("access-policy file differs from the policy the publication was published under")
    for k, v in (expect or {}).items():
        actual = {"assessed": cov.get("memories"), "received": cov.get("cases_in_view")}.get(k, summary.get(k))
        if k in ("store", "corpus", "policy"):
            continue
        if actual != v:
            problems.append(f"expected {k}={v!r}, store has {actual!r}")
    if problems:
        raise ExportRefused("; ".join(problems))
    cutoff = {"view_date": closure["view_date"], "knowledge_end_exclusive": closure["knowledge_end_exclusive"],
              "temporal_policy": closure["temporal_policy"], "timezone": "Australia/Melbourne",
              "assessed_evidence_as_of": (aset or {}).get("assessed_evidence_as_of")}
    coverage = {"received_cases_in_view": cov.get("cases_in_view"), "eligible_in_view": cov.get("eligible_in_view"),
                "assessed_case_memories": cov.get("memories"), "pending_memories": len(pending),
                "not_eligible_unassessed": len(cov.get("not_eligible_unassessed") or []),
                "unassessed_pairs": len(cov.get("unassessed_pairs") or []),
                "extraction_partial_or_failed_sources": list(cov.get("extraction_partial_or_failed") or []),
                "issues": len(pub.get("issues") or []), "partial_publication": bool(pending),
                "model_assessed": pub.get("model_assessed"), "human_review": pub.get("human_review")}
    producers = Counter()
    for m in closure["memory_ids"]:
        body = _obj(conn, "case_memory", m)[0]
        p = body.get("producer") or {}
        producers[(p.get("provider"), p.get("model"))] += 1
    registry = _obj(conn, "issue_registry", closure["registry_id"])[0] if closure.get("registry_id") else None
    provenance = {"case_memories": [{"provider": p, "model": m, "memories": n} for (p, m), n in sorted(producers.items())],
                  "collective_discovery": {k: (registry or {}).get(k) for k in ("discovery_prompt", "model_assessed")},
                  "usage_ledger": [dict(zip(("provider", "model", "status", "calls"), r)) for r in conn.execute(
                      "SELECT provider, model, status, count(*) FROM usage_ledger GROUP BY 1,2,3 ORDER BY 1,2,3")],
                  "dashboard_reads_call_models": False}
    log = []
    for (body,) in conn.execute("SELECT body FROM objects WHERE kind='publication_event'"):
        e = json.loads(body)
        if e["pointer_key"] == "intel:current":
            p, _ = _obj(conn, "intel_publication", e["target_id"])
            log.append({"publication_id": e["target_id"], "generation": e["generation"], "published_at": e["published_at"],
                        "view_date": (p or {}).get("closure", {}).get("view_date"), "present": p is not None})
    log.sort(key=lambda x: (x["published_at"], x["generation"]))
    if not all(x["present"] for x in log):
        raise ExportRefused("publication log names a publication that is not in the store")
    return {"bindings": summary, "cutoff": cutoff, "coverage": coverage, "model_provenance": provenance,
            "publication_log": log}


def _deterministic_tar(stage: Path, out: Path) -> dict:
    members = {}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in sorted(x for x in stage.rglob("*") if x.is_file()):
            rel = p.relative_to(stage).as_posix()
            data = p.read_bytes()
            members[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            info = tarfile.TarInfo(rel)
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = len(data), 0, 0o644, 0, 0, "", ""
            tar.addfile(info, io.BytesIO(data))
    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=6, filename="") as gz:
            gz.write(raw.getvalue())
    return members


def export_serving_publication(store_dir: Path, corpus_path: Path, policy_path: Path, out_root: Path, *,
                               expect: dict | None = None, allow_pending: bool = False) -> dict:
    store_dir, out_root = Path(store_dir).resolve(), Path(out_root).resolve()
    db = store_dir / "control.sqlite3"
    if not db.is_file():
        raise ExportRefused(f"no store at {store_dir}")
    if out_root == store_dir or store_dir in out_root.parents:
        raise ExportRefused("export output must be outside the store directory")
    corpus_bytes, policy_bytes = Path(corpus_path).read_bytes(), Path(policy_path).read_bytes()
    corpus_body, policy_body = json.loads(corpus_bytes), json.loads(policy_bytes)
    out_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_root, prefix=".export-") as tmp:
        stage = Path(tmp) / "stage"
        (stage / "data").mkdir(parents=True)
        (stage / "bindings").mkdir()
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)          # never writes to the live store
        dst = sqlite3.connect(stage / "data" / "control.sqlite3")
        with dst:
            src.backup(dst)
        src.close()
        revoked = {}
        with dst:
            for t in REVOKED_TABLES:
                revoked[t] = dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                dst.execute(f"DELETE FROM {t}")
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.execute("VACUUM")
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ExportRefused(f"snapshot integrity_check: {integrity}")
        facts = validate_bindings(dst, corpus_body, policy_body, expect, allow_pending=allow_pending)
        blob_rows = dst.execute("SELECT sha256, rel_path FROM blobs ORDER BY sha256").fetchall()
        releases = [json.loads(r[0]) for r in dst.execute("SELECT body FROM objects WHERE kind='release'")]
        schema_version = dst.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        dst.close()
        bad = []
        for sha, rel in blob_rows:
            source = store_dir / "blobs" / rel
            target = stage / "data" / "blobs" / rel
            if not source.is_file() or sha256_file(source) != sha:
                bad.append(sha)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        needed = {e["bytes_sha256"] for r in releases for e in r["entries"] if e.get("bytes_sha256")}
        absent = sorted(needed - {sha for sha, _ in blob_rows if sha not in bad})
        if bad or absent:
            raise ExportRefused(f"{len(bad)} blob(s) fail their content address and {len(absent)} release "
                                f"entry blob(s) are not registered, e.g. {(bad + absent)[:3]}")
        if (store_dir / "packages").is_dir():
            shutil.copytree(store_dir / "packages", stage / "data" / "packages",
                            ignore=shutil.ignore_patterns(".tmp-*"))
        (stage / "bindings" / "corpus.json").write_bytes(corpus_bytes)
        (stage / "bindings" / "policy.json").write_bytes(policy_bytes)
        payload_tmp = Path(tmp) / PAYLOAD_NAME
        members = _deterministic_tar(stage, payload_tmp)
        payload_sha = sha256_file(payload_tmp)
        content = {"members": members, "bindings": facts["bindings"]}
        export_id = f"AEXP-{facts['bindings']['publication_id'][5:17]}-{canonical_sha256(content)[:16]}"
        final = out_root / export_id
        if final.exists():
            raise ExportRefused(f"{final} already exists (exports are immutable; the same content was exported)")
        manifest = {
            "kind": EXPORT_KIND, "version": EXPORT_VERSION, "export_id": export_id, "created_at": utc_now_iso(),
            "product": "Artha (synthetic demo)", "purpose": "read-only serving copy; not an evaluation export",
            "source": {"store": _display(store_dir),
                       "corpus_file_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
                       "policy_file_sha256": hashlib.sha256(policy_bytes).hexdigest(),
                       "store_schema_version": schema_version, "read_mode": "sqlite online backup, read-only source"},
            **facts,
            "payload": {"file": PAYLOAD_NAME, "sha256": payload_sha, "bytes": payload_tmp.stat().st_size,
                        "members": members, "member_count": len(members),
                        "uncompressed_bytes": sum(m["bytes"] for m in members.values()),
                        "blobs": len(blob_rows)},
            "exclusions": {"revoked_rows_removed": revoked, "never_copied": NEVER_COPIED,
                           "private_authoring_or_evaluation_answers": "not present in the serving store; none copied"},
            "restore_layout": {"data_dir": "data", "corpus_file": "bindings/corpus.json",
                               "policy_file": "bindings/policy.json"}}
        stage_out = Path(tmp) / "out"
        stage_out.mkdir()
        shutil.move(str(payload_tmp), stage_out / PAYLOAD_NAME)
        (stage_out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.rename(stage_out, final)
    return {"export_dir": str(final), "export_id": export_id, "manifest_sha256": sha256_file(final / MANIFEST_NAME),
            "payload_sha256": payload_sha, "payload_bytes": manifest["payload"]["bytes"],
            "uncompressed_bytes": manifest["payload"]["uncompressed_bytes"], "bindings": facts["bindings"],
            "coverage": facts["coverage"], "revoked_rows_removed": revoked}


# ------------------------------------------------------------------ restore
def _safe_extract(payload: Path, target: Path, members: dict) -> None:
    with tarfile.open(payload, "r:gz") as tar:
        names = [m.name for m in tar.getmembers()]
        if sorted(names) != sorted(members) or any(not m.isfile() for m in tar.getmembers()):
            raise RestoreRefused("payload members differ from the manifest (missing, extra or non-file entries)")
        tar.extractall(target, filter="data")
    bad = [n for n, meta in members.items() if sha256_file(target / n) != meta["sha256"]]
    if bad:
        raise RestoreRefused(f"{len(bad)} restored file(s) fail their manifest digest, e.g. {bad[:3]}")


def restore_export(export_dir: Path, target: Path, *, manifest_sha256: str | None = None,
                   verify_read_path: bool = True) -> dict:
    """Restore into an EMPTY target. `manifest_sha256` (from the activated pointer) is required in the cloud path."""
    export_dir, target = Path(export_dir), Path(target)
    mpath, payload = export_dir / MANIFEST_NAME, export_dir / PAYLOAD_NAME
    if not mpath.is_file() or not payload.is_file():
        raise RestoreRefused(f"incomplete export at {export_dir}: manifest or payload missing")
    if manifest_sha256 is not None and sha256_file(mpath) != manifest_sha256:
        raise RestoreRefused("manifest digest differs from the activated publication pointer")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    if manifest.get("kind") != EXPORT_KIND or manifest.get("version") != EXPORT_VERSION:
        raise RestoreRefused("not an artha-serving-export 1.0.0 manifest")
    if sha256_file(payload) != manifest["payload"]["sha256"]:
        raise RestoreRefused("payload digest differs from the manifest")
    if target.exists() and any(target.iterdir()):
        raise RestoreRefused(f"restore target {target} is not empty")
    target.mkdir(parents=True, exist_ok=True)
    _safe_extract(payload, target, manifest["payload"]["members"])
    data = target / "data"
    conn = sqlite3.connect(data / "control.sqlite3")
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        leftover = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in REVOKED_TABLES}
        blob_rows = conn.execute("SELECT sha256, rel_path FROM blobs").fetchall()
        blob_bad = [s for s, rel in blob_rows if not (data / "blobs" / rel).is_file()
                    or sha256_file(data / "blobs" / rel) != s]
        corpus = json.loads((target / "bindings" / "corpus.json").read_text(encoding="utf-8"))
        policy = json.loads((target / "bindings" / "policy.json").read_text(encoding="utf-8"))
        facts = validate_bindings(conn, corpus, policy, allow_pending=manifest["coverage"]["partial_publication"])
    except ExportRefused as exc:
        raise RestoreRefused(f"restored bindings do not validate: {exc}") from exc
    finally:
        conn.close()
    if integrity != "ok" or any(leftover.values()) or blob_bad:
        raise RestoreRefused(f"restored store failed checks: integrity={integrity}, serving rows={leftover}, "
                             f"blob failures={len(blob_bad)}")
    if facts["bindings"] != manifest["bindings"] or facts["coverage"] != manifest["coverage"]:
        raise RestoreRefused("restored publication bindings/coverage differ from the manifest")
    receipt = {"export_id": manifest["export_id"], "restored_at": utc_now_iso(), "sqlite_integrity": integrity,
               "files_verified": len(manifest["payload"]["members"]), "blobs_verified": len(blob_rows),
               "bindings": facts["bindings"], "coverage": facts["coverage"], "cutoff": facts["cutoff"],
               "data_dir": str(data), "corpus_file": str(target / "bindings" / "corpus.json"),
               "policy_file": str(target / "bindings" / "policy.json")}
    if verify_read_path:
        receipt["read_path"] = verify_served(receipt)
    (target / "RESTORE-RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return receipt


def serving_env(receipt: dict) -> dict:
    """Environment that makes the backend serve exactly the restored publication (no review bypass flags)."""
    return {"SDB_DATA_DIR": receipt["data_dir"], "SDB_DEMO_CORPUS": receipt["corpus_file"],
            "SDB_POLICY_PATH": receipt["policy_file"], "SDB_PUBLICATION_POLICY": "synthetic-demo-v1",
            "SDB_ALLOW_UNREVIEWED_SOURCES": "", "SDB_ALLOW_SPECIMEN_ACCEPTANCE": ""}


def verify_served(receipt: dict) -> dict:
    """Open the restored store through the backend's own read path and confirm it serves the manifest's bindings."""
    import dataclasses

    from ..api.repo import served_release
    from ..config import load_settings
    from ..intel.publish import current
    from ..runtime import make_runtime
    env = {**serving_env(receipt), "SDB_SERVING_STATE": "sqlite"}   # verification never touches shared state
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        s = dataclasses.replace(load_settings(), data_dir=Path(env["SDB_DATA_DIR"]),
                                demo_corpus_path=Path(env["SDB_DEMO_CORPUS"]),
                                publication_policy="synthetic-demo-v1", allow_unreviewed_sources=False)
        rt = make_runtime(s)
        rv = served_release(rt)
        pub = current(rt)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    b = receipt["bindings"]
    ok = rv is not None and rv.release_id == b["release_id"] and pub is not None and \
        pub["publication_id"] == b["publication_id"] and pub["analysis_set_version"] == b["analysis_set_version"]
    if not ok:
        raise RestoreRefused("restored store does not serve the manifest's release/publication through the read path")
    return {"served_release": rv.release_id, "current_publication": pub["publication_id"], "ok": True}
