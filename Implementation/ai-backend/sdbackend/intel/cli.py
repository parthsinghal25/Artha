"""`python -m sdbackend intel <command>` — demo-store operations (run through scripts/demo.sh)."""
from __future__ import annotations

import argparse
import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def main(argv) -> int:
    p = argparse.ArgumentParser(prog="sdbackend intel")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("intake", help="receive + ingest the corpus bundle into the demo store")
    sub.add_parser("tables", help="deterministic ledger/work/lifecycle projection summary")
    pl = sub.add_parser("plan", help="dry run: classify cases and estimate model calls for a knowledge view")
    pl.add_argument("--as-of", required=True, help="local date D; view = end of D (demo-held-day-v1)")
    up = sub.add_parser("update", help="run (or resume) the incremental step for a knowledge view")
    up.add_argument("--as-of", required=True)
    up.add_argument("--provider", default="gemma", choices=["gemma", "gemini", "auto", "agy", "vertex"])
    up.add_argument("--max-requests", type=int, default=None)
    up.add_argument("--max-wall-seconds", type=float, default=None)
    up.add_argument("--memory-only", action="store_true", help="skip collective discovery and publication")
    up.add_argument("--no-publish", action="store_true")
    up.add_argument("--limit-cases", type=int, default=None, help="bounded trial: at most N model cases")
    up.add_argument("--collective-only", action="store_true", help="skip case-memory work; discover/assess/"
                    "publish over existing memories (eligible cases without one are published as pending)")
    up.add_argument("--assess-shard", default=None, help="i/N: assess only this deterministic case partition "
                    "(with --collective-only --no-publish; discovery must already be done)")
    up.add_argument("--publish-only", action="store_true", help="no model calls: publish existing memories and "
                    "persisted assessments (missing pairs are reported unassessed)")
    up.add_argument("--shard", default=None, help="i/N: process only this deterministic case partition "
                    "(parallel --memory-only workers; no two shards share a case)")
    sub.add_parser("status", help="heads, steps and publication pointer")
    fc = sub.add_parser("forecast", help="measured scale forecast (no model calls)")
    fc.add_argument("--cases", type=int, default=500)
    fc.add_argument("--chars-per-case", type=float, default=None)
    sub.add_parser("report", help="compile delivery facts from the store (no model calls)")
    sub.add_parser("evaluate", help="mechanical indicators by subset; diagnostic evaluation not_performed")
    ex = sub.add_parser("export", help="frozen prediction export + manifest for offline evaluation (outside the store)")
    ex.add_argument("--out", required=True)
    ex.add_argument("--subset", default="development", choices=["development", "early_evaluation"])
    ex.add_argument("--allow-early-evaluation", action="store_true")
    eo = sub.add_parser("evaluate-offline", help="score a frozen export against independent adjudicated judgements")
    eo.add_argument("--export", required=True)
    eo.add_argument("--judgements", default=None, help="independent-review JSONL; omitted → not_performed")
    eo.add_argument("--out", default=None)
    pf = sub.add_parser("preflight", help="check a larger corpus file is present and bindable (no model calls)")
    pf.add_argument("--corpus", default="config/demo/combined500-v1.corpus.json")
    pf.add_argument("--no-ocr", action="store_true", help="skip the deterministic OCR check of image originals")
    args = p.parse_args(argv)
    from ..runtime import make_runtime
    from .corpus import demo_release_view, load_corpus
    rt = make_runtime()
    corpus = load_corpus(rt.settings.demo_corpus_path)
    if args.cmd == "intake":
        from .intake import run_intake
        _print(run_intake(rt, corpus))
    elif args.cmd == "plan":
        from .worker import Context, plan
        _print(plan(Context(rt, corpus, args.as_of)))
    elif args.cmd == "update":
        from .worker import run_step
        import sys
        rep = run_step(rt, corpus, args.as_of, provider=args.provider, max_requests=args.max_requests,
                       max_wall_s=args.max_wall_seconds, collective=not args.memory_only,
                       publish=not (args.memory_only or args.no_publish),
                       log=lambda m: print(m, file=sys.stderr, flush=True),
                       shard=tuple(int(x) for x in args.shard.split("/")) if args.shard else None,
                       limit_cases=args.limit_cases, collective_only=args.collective_only,
                       assess_shard=tuple(int(x) for x in args.assess_shard.split("/")) if args.assess_shard else None,
                       publish_only=args.publish_only)
        _print({k: rep.get(k) for k in ("step_id", "state", "stopped", "memory", "collective", "publication",
                                        "usage", "wall_seconds")} | {"counts": rep["plan"]["counts"]})
    elif args.cmd == "status":
        from .worker import ensure_schema
        ensure_schema(rt)
        c = rt.db.connect()
        _print({"heads": [dict(r) for r in c.execute("SELECT case_id, memory_id, revision, knowledge_end FROM intel_heads")],
                "steps": [{k: r[k] for k in ("step_id", "view_date", "state", "updated_at")}
                          for r in c.execute("SELECT * FROM intel_steps ORDER BY updated_at")],
                "pointer": dict(rt.db.get_pointer("intel:current") or {}) or None})
    elif args.cmd == "forecast":
        from .forecast import forecast
        _print(forecast(rt, args.cases, args.chars_per_case))
    elif args.cmd == "report":
        from .report import write
        _print(write(rt, corpus))
    elif args.cmd == "evaluate":
        from .evaluate import evaluate
        _print(evaluate(rt, corpus))
    elif args.cmd == "export":
        from .evaluate import export_frozen
        _print(export_frozen(rt, corpus, args.out, subset=args.subset,
                             allow_early_evaluation=args.allow_early_evaluation))
    elif args.cmd == "evaluate-offline":
        from .evaluate import evaluate_offline
        from .intake import crosswalk_for
        from .publish import current
        rep = evaluate_offline(args.export, args.judgements, crosswalk_for(rt, current(rt)["closure"]["release_id"]),
                               out_path=args.out)
        _print({k: v for k, v in rep.items() if k != "rows"})
    elif args.cmd == "preflight":
        from .preflight import preflight_combined
        _print(preflight_combined(args.corpus, rt=rt, ocr=not args.no_ocr))
    elif args.cmd == "tables":
        from .tables import project_release
        _print(project_release(rt, demo_release_view(rt))["summary"])
    return 0
