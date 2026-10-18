#!/usr/bin/env python3
"""Progress of an Antigravity CLI run (no model calls; reads only task/ledger state and invocation traces — never
memory contents).   .venv-intel/bin/python scripts/agy_progress.py [store-name]"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = sys.argv[1] if len(sys.argv) > 1 else "fixed500-agy-v1"
STORE = ROOT / "var" / "demo" / NAME
db = sqlite3.connect(f"file:{STORE / 'control.sqlite3'}?mode=ro", uri=True, timeout=30)
db.row_factory = sqlite3.Row
tasks = collections.Counter((r["task_kind"], r["state"]) for r in db.execute("SELECT task_kind, state FROM tasks"))
ledger = collections.Counter(r["status"] for r in db.execute(
    "SELECT status FROM usage_ledger WHERE provider='antigravity-cli'"))
heads = db.execute("SELECT COUNT(*) FROM intel_heads").fetchone()[0] if db.execute(
    "SELECT name FROM sqlite_master WHERE name='intel_heads'").fetchone() else 0
calls, quota = collections.Counter(), None
tokens = 0
for f in sorted((STORE / "agy-traces").glob("*.jsonl")):
    for line in f.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["kind"] == "quota":
            quota = (r["at"], r["table"].get("Gemini Models"))
        else:
            calls[r.get("status")] += 1
            tokens += (r.get("usage") or {}).get("total_tokens") or 0
print(json.dumps({"store": NAME, "case_memories_sealed": heads,
                  "tasks": {f"{k}:{s}": n for (k, s), n in sorted(tasks.items())},
                  "ledger": dict(ledger), "invocations": dict(calls), "tokens_total": tokens,
                  "last_quota": quota}, indent=1))
