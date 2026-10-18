"""Operator CLI: `python -m sdbackend <command>`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import BACKEND_ROOT


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_intake(args) -> int:
    from .ingest.build import ingest
    from .intake.delivery import receive
    from .runtime import make_runtime
    rt = make_runtime()
    delivery = receive(Path(args.bundle), rt=rt)
    reports = BACKEND_ROOT / "reports" / "intake"
    reports.mkdir(parents=True, exist_ok=True)
    name = f"{delivery.receipt['delivery']['batch_id']}-v{delivery.receipt['delivery']['package_version']}-receipt.json"
    (reports / name).write_text(json.dumps(delivery.receipt, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {"receipt": str(reports / name), **delivery.receipt["summary"]}
    if not args.receipt_only:
        summary["release"] = ingest(rt, delivery)
    _print(summary)
    return 0


def cmd_status(args) -> int:
    from .runtime import make_runtime
    rt = make_runtime()
    out = {}
    for key in ("release:current", "release:candidate"):
        row = rt.db.get_pointer(key)
        out[key] = None if row is None else {**dict(row), "control": rt.db.get_object("release_control", row["target_id"])}
    _print(out)
    return 0


def cmd_logins(args) -> int:
    from .api.auth import login_codes
    from .runtime import make_runtime
    print("Local demo login codes (keep private; regenerate by deleting <data_dir>/login-codes.json):")
    for principal, code in login_codes(make_runtime()).items():
        print(f"  {principal}: {code}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .config import load_settings
    s = load_settings()
    uvicorn.run("sdbackend.api.app:create_app", factory=True, host=args.host or s.host, port=args.port or s.port,
                log_level="info", access_log=False)
    return 0


def cmd_smoke(args) -> int:
    from .models.smoke import run_smoke
    _print(run_smoke(args.provider, write_report=True))
    return 0


def cmd_cloud(args) -> int:
    from .cloud.cli import main as cloud_main
    return cloud_main(args.cloud_args)


def cmd_backup(args) -> int:
    from .ops.backup import backup
    _print(backup(Path(args.out)))
    return 0


def cmd_restore(args) -> int:
    from .ops.backup import restore
    _print(restore(Path(args.archive), Path(args.target), force=args.force))
    return 0


def cmd_analyse(args) -> int:
    from .pipeline.runner import run_release_analysis
    _print(run_release_analysis(args.release, provider=args.provider, stages=args.stages.split(","),
                                max_sources=args.max_sources, resume=not args.fresh))
    return 0


def cmd_import_original_signoff(args) -> int:
    from .acceptance import DecisionRejected, import_original_signoff
    from .runtime import make_runtime
    try:
        _print(import_original_signoff(make_runtime(), Path(args.signoff), Path(args.bundle), reviewer_id=args.reviewer))
    except DecisionRejected as exc:
        print(f"refused: {exc}")
        return 2
    return 0


def cmd_import_decision(args) -> int:
    from .acceptance import DecisionRejected, import_decision
    from .runtime import make_runtime
    try:
        _print(import_decision(make_runtime(), Path(args.decision)))
    except DecisionRejected as exc:
        _print({"refused": str(exc)})
        return 3
    return 0


def cmd_prepare_analysis(args) -> int:
    from .engine.publish import PublicationRefused, prepare_closure
    from .pipeline.runner import resolve_release
    from .runtime import make_runtime
    rt = make_runtime()
    rv = resolve_release(rt, args.release)
    try:
        _print(prepare_closure(rt, release_id=rv.release_id, assessed_evidence_as_of=args.assessed_evidence_as_of,
                               stage_ids=args.stage or []))
    except PublicationRefused as exc:
        _print({"refused": str(exc)})
        return 3
    return 0


def cmd_publish_analysis(args) -> int:
    from .engine.publish import PublicationRefused, publish_analysis_set
    from .runtime import make_runtime
    try:
        aset = publish_analysis_set(make_runtime(), candidate_id=args.candidate, notes=args.note or [])
    except PublicationRefused as exc:
        _print({"refused": str(exc)})
        return 3
    _print({"published": aset["analysis_set_version"], "supported_definitions": aset["supported_definitions"]})
    return 0


def cmd_intel(args) -> int:
    from .intel.cli import main as intel_main
    return intel_main(args.intel_args)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sdbackend")
    sub = p.add_subparsers(dest="cmd", required=True)
    it = sub.add_parser("intel", help="portfolio-intelligence demo store commands (see `intel --help`)")
    it.add_argument("intel_args", nargs=argparse.REMAINDER)
    it.set_defaults(fn=cmd_intel)
    cl = sub.add_parser("cloud", help="hosted read demo: export/restore/upload/activate/rollback (see `cloud --help`)")
    cl.add_argument("cloud_args", nargs=argparse.REMAINDER)
    cl.set_defaults(fn=cmd_cloud)
    a = sub.add_parser("intake", help="validate a Data delivery, write a receipt and ingest a source release")
    a.add_argument("bundle")
    a.add_argument("--receipt-only", action="store_true")
    a.set_defaults(fn=cmd_intake)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("logins", help="print local demo principals' login codes").set_defaults(fn=cmd_logins)
    s = sub.add_parser("serve")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    m = sub.add_parser("smoke", help="operational model smoke test on explicitly synthetic inputs")
    m.add_argument("--provider", choices=["gemini", "gemma"], required=True)
    m.set_defaults(fn=cmd_smoke)
    b = sub.add_parser("backup")
    b.add_argument("--out", default=str(BACKEND_ROOT / "var" / "backups"))
    b.set_defaults(fn=cmd_backup)
    r = sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--target", required=True)
    r.add_argument("--force", action="store_true")
    r.set_defaults(fn=cmd_restore)
    an = sub.add_parser("analyse", help="run controlled source/case analysis on a release")
    an.add_argument("--release", default="candidate", help="release id, 'current' or 'candidate'")
    an.add_argument("--provider", default="auto", choices=["auto", "gemini", "gemma"])
    an.add_argument("--stages", default="source",
                    help="comma list of source (S02), ledger (S04), case (S03), collective (S05)")
    an.add_argument("--max-sources", type=int, default=None)
    an.add_argument("--fresh", action="store_true")
    an.set_defaults(fn=cmd_analyse)
    idc = sub.add_parser("import-decision", help="import an independent reviewer decision (sdb-review-decision@1.0.0)")
    idc.add_argument("decision")
    idc.set_defaults(fn=cmd_import_decision)
    sg = sub.add_parser("import-original-signoff", help="verify Data's hash-bound original-artifact sign-off and "
                        "import it as source-gate decisions (reviewer must already be allowlisted)")
    sg.add_argument("--signoff", required=True)
    sg.add_argument("--bundle", required=True, help="the intake-bundle directory the sign-off binds")
    sg.add_argument("--reviewer", required=True, help="allowlisted reviewer id in config/reviewers.json")
    sg.set_defaults(fn=cmd_import_original_signoff)
    pr = sub.add_parser("prepare-analysis", help="compute an analysis-set closure and list the decisions it needs")
    pr.add_argument("--release", default="current")
    pr.add_argument("--assessed-evidence-as-of", required=True)
    pr.add_argument("--stage", action="append", help="staged S03/S04/S05 output id proposed for publication")
    pr.set_defaults(fn=cmd_prepare_analysis)
    pa = sub.add_parser("publish-analysis", help="publish a prepared closure once every required decision passes")
    pa.add_argument("--candidate", required=True)
    pa.add_argument("--note", action="append")
    pa.set_defaults(fn=cmd_publish_analysis)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
