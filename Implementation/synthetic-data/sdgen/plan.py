"""Read-only access to the repaired v2.1 authoring plans. Plans are instructions, never evidence."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .paths import PLAN_DIR


def pack_path(case_number: int) -> "Path":
    matches = sorted((PLAN_DIR / "assistant-packs").glob(f"{case_number:02d}-C-*.json"))
    if len(matches) != 1:
        raise FileNotFoundError(f"assistant pack for case {case_number}: {matches}")
    return matches[0]


class CasePlan:
    def __init__(self, case_number: int, path=None):
        """`path` loads a plan copy (e.g. the one inside a worker pack) instead of the workspace planning folder."""
        self.path = path if path is not None else pack_path(case_number)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.pack = json.loads(raw)
        self.case_id = self.pack["case_id"]
        self.request_reference = self.pack["request_reference"]
        self.artifacts = {a["artifact_ref"]: a for a in self.pack["artifacts"]}
        self.assets = {a["asset_id"]: a for a in self.pack["assets"]}
        self.assets_by_name = {a["filename"]: a for a in self.pack["assets"]}
        self.actors = {a["id"]: a for a in self.pack["actors"]}

    def actor(self, actor_id):
        return self.actors[actor_id]

    def artifact(self, ref):
        return self.artifacts[ref]

    def assertion(self, ref, key):
        for a in self.artifacts[ref]["source_assertions"]:
            if a["key"] == key:
                return a["value"]
        return None

    def later_artifacts(self, ref):
        t = parse_dt(self.artifacts[ref]["event_at"])
        return [a for a in self.artifacts.values() if parse_dt(a["event_at"]) > t]


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)
