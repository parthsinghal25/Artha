import argparse
import json
import sys
from pathlib import Path

from .atomic import PublishUnsupported
from .build import Batch, ReleaseExistsError, InputUnavailable, build


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sdgen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render and publish a batch into --out-root (refuses an existing release)")
    b.add_argument("--batch", default="case11-rehearsal")
    b.add_argument("--authored-version", type=int, default=1)
    b.add_argument("--package-version", type=int, required=True)
    b.add_argument("--out-root", default=None)
    c = sub.add_parser("check", help="verify a package, non-destructively")
    c.add_argument("package_dir")
    c.add_argument("--write", action="store_true", help="write restricted/review/CHECK-RESULTS.json (refused if it exists)")
    c.add_argument("--output", default=None, help="write the result JSON to a path outside the package")
    c.add_argument("--no-reconstruct", action="store_true")
    c.add_argument("--base-root", default=None)
    r = sub.add_parser("release", help="stage, check and atomically publish a new package version")
    r.add_argument("--batch", default="case11-rehearsal")
    r.add_argument("--authored-version", type=int, required=True)
    r.add_argument("--package-version", type=int, required=True)
    r.add_argument("--out-root", default=None)
    r.add_argument("--ruling", action="append", default=[])
    r.add_argument("--tests-junit", default=None)
    r.add_argument("--snapshot", action="append", default=[], help="PACKAGE=path/to/snapshot.json; repeatable")
    r.add_argument("--author-wall-clock", default=None)
    w = sub.add_parser("workpacks", help="build pack set 2 (case-scoped worker packs) into a NEW directory (authorises nothing)")
    w.add_argument("--out-root", default=None)
    w.add_argument("--case", type=int, action="append")
    w.add_argument("--set-current", action="store_true", help="also write the current-pack-set pointer (refuses to overwrite one)")
    ac = sub.add_parser("author-check", help="check a worker-returned card file against its pack (not acceptance)")
    ac.add_argument("authored")
    ac.add_argument("--pack", required=True)
    ac.add_argument("--stage", required=True, help="the stage the card is being written in (mandatory)")
    cc = sub.add_parser("card-context", help="print what the check derives from the trusted card (read-only)")
    cc.add_argument("--pack", required=True)
    cc.add_argument("--card", required=True)
    pk = sub.add_parser("package-stage", help="supervisor: author-check, render and publish a stage's returned cards as a NEW package (no overwrite)")
    pk.add_argument("--pack", required=True)
    pk.add_argument("--stage", required=True)
    pk.add_argument("--outputs", required=True)
    pk.add_argument("--out-root", required=True)
    pk.add_argument("--name", required=True)
    pk.add_argument("--accepted-package", default=None, help="delivered package whose accepted attachments are reused byte for byte")
    ex = sub.add_parser("export-slice", help="Data: publish a NEW pre-extraction-delivery-manifest 0.2.0 export of the accepted case-11 originals (no overwrite)")
    ex.add_argument("--out-root", required=True)
    ex.add_argument("--name", required=True)
    rc = sub.add_parser("render-card", help="supervisor: render a VALID return into NEW output folder (refuses an existing folder)")
    rc.add_argument("authored")
    rc.add_argument("--pack", required=True)
    rc.add_argument("--stage", required=True)
    rc.add_argument("--out", required=True)
    rc.add_argument("--accepted-package", default=None, help="delivered package whose accepted attachments are reused byte for byte")
    au = sub.add_parser("authored-check", help="check authored input end to end (renders in memory; publishes nothing)")
    au.add_argument("input_dir")
    au.add_argument("--member", default=None, help="show only findings mentioning this member/case/source id fragment")
    ab = sub.add_parser("authored-build", help="build a NEW candidate from authored input (refuses errors and existing/preserved paths)")
    ab.add_argument("input_dir")
    ab.add_argument("--out-root", default=None)
    av = sub.add_parser("authored-verify", help="re-verify a published authored candidate, read-only")
    av.add_argument("candidate_dir")
    ad = sub.add_parser("authored-diff", help="compare two authored candidates original by original")
    ad.add_argument("old")
    ad.add_argument("new")
    e = sub.add_parser("effort", help="append to the effort log")
    e.add_argument("role")
    e.add_argument("note")
    e.add_argument("--active-minutes", type=float)
    e.add_argument("--wall-clock-minutes", type=float)
    e.add_argument("--api-input-tokens", type=int)
    e.add_argument("--api-output-tokens", type=int)
    e.add_argument("--api-cost-usd", type=float)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build":
            batch = build(a.batch, a.authored_version, a.package_version, a.out_root)
            print(json.dumps({"package": str(batch.dir), "files": len(batch.files), "timings": batch.timings}, indent=2))
        elif a.cmd == "check":
            from .checks import run_checks
            res = run_checks(a.package_dir, write=a.write, deep=not a.no_reconstruct, output=a.output, base_root=a.base_root)
            print(json.dumps(res["summary"], indent=2))
            s = res["summary"]["by_layer"]
            hard = sum(s[l]["fail"] for l in ("integrity", "provenance", "semantic"))
            sys.exit(1 if hard else 3 if s["reconstruction"]["fail"] else 0)
        elif a.cmd == "release":
            _release(a)
        elif a.cmd == "workpacks":
            from .workpack2 import build_all
            m = build_all(a.out_root, a.case)
            if a.set_current:
                from .workpack2 import write_current_pointer
                write_current_pointer(a.out_root, supersede=True)
            print(json.dumps({"packs": len(m), "all_checks_ok": all(x["all_checks_ok"] for x in m), "launch_authorised": False}, indent=2))
        elif a.cmd == "author-check":
            from .authoring_check import author_check
            res = author_check(a.authored, a.pack, a.stage)
            print(json.dumps(res, indent=1))
            sys.exit(0 if all(r["ok"] for r in res) else 1)
        elif a.cmd == "card-context":
            _card_context(a)
        elif a.cmd == "render-card":
            _render_card(a)
        elif a.cmd == "package-stage":
            from .microbatch import build_package
            d = build_package(a.pack, a.stage, a.outputs, a.out_root, a.name, a.accepted_package)
            print(json.dumps({"published": str(d)}, indent=1))
        elif a.cmd == "export-slice":
            from .receiving_export import case11_export
            d = case11_export(a.out_root, a.name)
            print(json.dumps({"published": str(d)}, indent=1))
        elif a.cmd in ("authored-check", "authored-build"):
            from .authored.export import build as authored_build
            res = authored_build(a.input_dir, getattr(a, "out_root", None), dry_run=a.cmd == "authored-check")
            if getattr(a, "member", None):
                res["findings"] = [f for f in res["findings"] or [] if a.member in f["subject"] or a.member in f["message"]]
            print(json.dumps(res, indent=1, default=str))
            sys.exit(0 if res["ok"] else 1)
        elif a.cmd == "authored-verify":
            from .authored.verify import verify_candidate
            res = verify_candidate(a.candidate_dir)
            print(json.dumps(res, indent=1, default=str))
            sys.exit(1 if res["failed"] else 0)
        elif a.cmd == "authored-diff":
            from .authored.verify import diff_candidates
            print(json.dumps(diff_candidates(a.old, a.new), indent=1))
        elif a.cmd == "effort":
            from .effort import log_entry
            print(json.dumps(log_entry(a.role, a.note, a.active_minutes, a.wall_clock_minutes, a.api_input_tokens,
                                       a.api_output_tokens, a.api_cost_usd)))
    except (ReleaseExistsError, InputUnavailable, PublishUnsupported) as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        sys.exit(2)
    except Exception as ex:  # noqa
        if type(ex).__name__ in ("ReviewRecordProtected", "PackagingRefused", "ContractGap", "GateRefused", "ExportRefused", "BuildRefused"):
            print(f"REFUSED: {ex}", file=sys.stderr)
            sys.exit(2)
        raise


def _card_context(a):
    from .authoring_check import approved_clarifications, load_pack
    from .contract import EMAIL_KINDS, allowed_kinds, derive_requirements
    plan, sty, prof, stage_doc, clars, prior = load_pack(a.pack)
    art = plan.artifact(a.card)
    kinds, gap = allowed_kinds(art)
    out = {"card": a.card, "event_at": art["event_at"], "allowed_return_kinds": kinds, "contract_gap": gap}
    if kinds:
        named = [plan.assets[x]["filename"] for x in art.get("attachments", []) if plan.assets[x].get("delivery_state") == "held"] if kinds[0] in EMAIL_KINDS else []
        out["derived"] = derive_requirements(plan, art, kinds[0], approved_clarifications(clars), named).as_dict()
    print(json.dumps(out, indent=1))


def _render_card(a):
    from .authoring_check import author_check, load_pack
    from .card_adapter import render_card
    res = author_check(a.authored, a.pack, a.stage)
    if not all(r["ok"] for r in res):
        print(json.dumps([r for r in res if not r["ok"]], indent=1), file=sys.stderr)
        sys.exit(1)
    plan = load_pack(a.pack)[0]
    d = json.loads(Path(a.authored).read_text())
    accepted = None
    if a.accepted_package:
        from .card_adapter import accepted_attachment_bytes
        bound = json.loads((Path(a.pack) / "context/ACCEPTED-PRIOR-ORIGINALS.json").read_text())["bound_to_manifest"]["delivery_manifest_sha256"] \
            if (Path(a.pack) / "context/ACCEPTED-PRIOR-ORIGINALS.json").exists() else None
        accepted = accepted_attachment_bytes(a.accepted_package, bound)
    rc = render_card(d, plan, plan.artifact(d["card"]), accepted)
    out = Path(a.out)
    out.mkdir(parents=True)  # refuses an existing folder
    for rel, data in {**rc.files, **rc.private}.items():
        f = out / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    print(json.dumps({"rendered": sorted({**rc.files, **rc.private}), "out": str(out)}, indent=1))


def _release(a):
    from .release import release
    batch, res, ok, published = release(a.batch, a.authored_version, a.package_version, a.out_root, a.ruling, a.tests_junit,
                                        dict(x.split("=", 1) for x in a.snapshot), a.author_wall_clock)
    if not published:
        print(json.dumps({"staged_not_published": str(batch.stage), "checks": res["summary"], "closure_tests_ok": ok}, indent=2))
        sys.exit(1)
    print(json.dumps({"published": str(batch.dir), "checks": res["summary"], "timings": batch.timings}, indent=2))


if __name__ == "__main__":
    main()
