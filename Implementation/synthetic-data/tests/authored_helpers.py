"""Helpers for authored-path tests: a mutable copy of the canonical FX example (fictional, excluded from the corpus)."""
import json
import shutil
from pathlib import Path

from sdgen.authored.export import build

EXAMPLE = Path(__file__).resolve().parents[1] / "authoring-input/examples/fx-five-members"


class Input:
    def __init__(self, tmp_path):
        self.root = tmp_path / "input"
        shutil.copytree(EXAMPLE, self.root)

    def rows(self, rel):
        return [json.loads(x) for x in (self.root / rel).read_text().splitlines() if x.strip()]

    def write(self, rel, rows):
        (self.root / rel).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def edit(self, rel, key, value, fn):
        rows = self.rows(rel)
        hit = [r for r in rows if r.get(key) == value]
        assert hit, f"{value} not in {rel}"
        for r in hit:
            fn(r)
        self.write(rel, rows)

    def source(self, sid, fn):
        self.edit("runtime/sources.jsonl", "source_id", sid, fn)

    def event(self, eid, fn):
        self.edit("private/events.jsonl", "event_id", eid, fn)

    def add(self, rel, row):
        self.write(rel, self.rows(rel) + [row])

    def batch(self, fn):
        p = self.root / "BATCH.json"
        b = json.loads(p.read_text())
        fn(b)
        p.write_text(json.dumps(b))

    def check(self):
        return build(self.root, dry_run=True)


def codes(res):
    return {f["code"] for f in res["findings"] if f["severity"] == "error"} | \
        {c["check"] for c in (res["checks"] or []) if c["status"] == "fail"}
