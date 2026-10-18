"""Read repository over sealed release objects. Every accessor filters by authority and evidence cutoff.

The same functions back the public operations and the model's read tools, so the UI and the investigator see one
policy implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from ..policy import Authority
from ..runtime import Runtime


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ReleaseView:
    release: dict
    control: dict

    @property
    def release_id(self) -> str:
        return self.release["release_id"]

    def entry_ids(self, kind: str) -> list[str]:
        return [e["immutable_id"] for e in self.release["entries"] if e["kind"] == kind]


def served_release(rt: Runtime) -> ReleaseView | None:
    """The release the read service may serve: accepted `current`, else the unreviewed candidate only when the
    operator enabled SDB_ALLOW_UNREVIEWED_SOURCES. Under the explicit `synthetic-demo-v1` publication policy only
    `release:demo` is served, and only when its control binds the configured corpus file's manifest digest."""
    if getattr(rt.settings, "publication_policy", "independent-review-v1") == "synthetic-demo-v1":
        from ..intel.corpus import demo_release_view
        return demo_release_view(rt)
    for key, allowed in (("release:current", True), ("release:candidate", rt.settings.allow_unreviewed_sources)):
        row = rt.db.get_pointer(key)
        if row is not None and allowed:
            release = rt.db.get_object("release", row["target_id"])
            if release is not None and rt.db.object_sha("release", row["target_id"]) == row["target_sha256"]:
                return ReleaseView(release, rt.db.get_object("release_control", row["target_id"]))
    return None


class Repo:
    def __init__(self, rt: Runtime, rv: ReleaseView, authority: Authority, evidence_as_of: str, boundary: str,
                 day_precision: dict[str, str] | None = None):
        """`day_precision` (demo-held-day-v1 contexts only): object id → local date D for evidence whose supplied
        availability had day precision; such evidence is held in D's end-of-day view (exclusive boundary at the
        following midnight). Absent for legacy contexts, whose semantics are unchanged."""
        self.rt, self.rv, self.auth = rt, rv, authority
        self.cutoff = parse_dt(evidence_as_of)
        self.boundary = boundary
        self.day_precision = day_precision or {}
        self._entries = {(e["kind"], e["immutable_id"]) for e in rv.release["entries"]}

    # -- time eligibility --------------------------------------------------
    def available(self, at: str, object_id: str | None = None) -> bool:
        if object_id is not None and object_id in self.day_precision and self.boundary == "exclusive_stock_endpoint":
            from ..intel.temporal import end_of_day
            return end_of_day(self.day_precision[object_id]) <= self.cutoff
        t = parse_dt(at)
        return t < self.cutoff if self.boundary == "exclusive_stock_endpoint" else t <= self.cutoff

    def in_release(self, entry_kind: str, oid: str) -> bool:
        return (entry_kind, oid) in self._entries

    # -- sources -------------------------------------------------------------
    def source_record(self, source_id: str) -> dict | None:
        if not self.in_release("input", source_id):
            return None
        rec = self.rt.db.get_object("source", source_id)
        if rec is None or not self.auth.source_permitted(rec) or \
                not self.available(rec["envelope"]["available_at"], source_id):
            return None
        if not self._binding_effective(source_id):
            return None                               # its independent source pass was withdrawn/superseded (F01)
        return rec

    def _binding_effective(self, source_id: str) -> bool:
        """A release entry bound to an independent review pass is served only while that pass is still effective.
        Mechanical/pending bindings of an unreviewed candidate are unaffected (they never claimed review)."""
        memo = self.__dict__.setdefault("_binding_memo", {})
        if source_id not in memo:
            entry = next((e for e in self.rv.release["entries"]
                          if e["kind"] == "input" and e["immutable_id"] == source_id), None)
            acc = self.rt.db.get_object("acceptance", entry["acceptance_id"]) if entry and entry.get("acceptance_id") else None
            if acc is not None and acc.get("independent"):
                from ..acceptance import still_effective
                memo[source_id] = still_effective(self.rt, acc["acceptance_id"])
            else:
                memo[source_id] = True
        return memo[source_id]

    def case_source_ids(self, case_id: str) -> list[str]:
        if not self.auth.case_permitted(case_id) or not self.auth.can("cases"):
            return []
        ids = [s for s in self.rt.db.linked("source", "case", case_id) if self.in_release("input", s)]
        return sorted(s for s in ids if self.source_record(s) is not None)

    def all_source_ids(self) -> list[str]:
        return sorted(s for s in self.rv.entry_ids("input") if self.source_record(s) is not None)

    # -- assets / extraction / attachments ----------------------------------
    def asset(self, asset_id: str) -> dict | None:
        if not self.in_release("asset", asset_id):
            return None
        a = self.rt.db.get_object("asset", asset_id)
        if a is None or not self.auth.asset_permitted(a) or not self.available(a["scenario_available_at"], asset_id):
            return None
        return a

    def case_asset_ids(self, case_id: str) -> list[str]:
        if not self.auth.case_permitted(case_id) or not self.auth.can("cases"):
            return []
        ids = [a for a in self.rt.db.linked("asset", "case", case_id) if self.asset(a) is not None]
        return sorted(ids)

    def extraction(self, extraction_id: str) -> dict | None:
        if not self.in_release("extraction", extraction_id):
            return None
        return self.rt.db.get_object("extraction", extraction_id)

    def layout(self, extraction_id: str) -> dict | None:
        return self.rt.db.get_object("layout", extraction_id)

    def attachments_of_parent(self, source_id: str) -> list[dict]:
        out = []
        for aid in self.rt.db.linked("attachment", "parent_source", source_id):
            if self.in_release("attachment", aid):
                out.append(self.rt.db.get_object("attachment", aid))
        return sorted(out, key=lambda a: a["attachment_occurrence_id"])

    def attachments_of_child(self, asset_id: str) -> list[dict]:
        out = []
        for aid in self.rt.db.linked("attachment", "child_asset", asset_id):
            if self.in_release("attachment", aid):
                out.append(self.rt.db.get_object("attachment", aid))
        return out

    def attachment_permitted(self, att: dict) -> bool:
        parent = self.source_record(att["parent_source_id"])
        if parent is None or not self.available(att["scenario_available_at"], att["parent_source_id"]):
            return False
        return att["child_asset_version_id"] is None or self.asset(att["child_asset_version_id"]) is not None

    def container_fully_permitted(self, asset: dict) -> bool:
        """Whole-representation check: an original with embedded children is withheld if any child is denied."""
        for sid in asset["source_ids"]:
            for att in self.attachments_of_parent(sid):
                if att["child_asset_version_id"] and self.asset(att["child_asset_version_id"]) is None:
                    return False
        return True

    def locators_for_source(self, source_id: str) -> list[dict]:
        out = []
        for mid in self.rt.db.linked("locator", "source", source_id):
            if self.in_release("mapping", mid):
                out.append(self.rt.db.get_object("locator", mid))
        return sorted(out, key=lambda m: m["mapping_id"])
