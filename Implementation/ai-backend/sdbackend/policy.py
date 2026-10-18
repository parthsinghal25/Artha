"""Server-owned authorisation (Shared DECISIONS §2).

Authenticated principal + current policy file → view, capabilities, scope token and permission revision. Requests
and model arguments never grant access. Multi-case documents require authority for every represented case.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .canonical import canonical_sha256, derived_id
from .config import BACKEND_ROOT

POLICY_PATH = BACKEND_ROOT / "config" / "policy.json"


@dataclass(frozen=True)
class Authority:
    principal_id: str
    view_id: str
    capabilities: tuple[str, ...]
    permission_revision: str
    entitlement_policy_id: str
    case_scope: str
    denied_case_ids: frozenset[str]
    denied_source_ids: frozenset[str]
    denied_asset_ids: frozenset[str]

    @property
    def scope_token(self) -> str:
        return derived_id("SCOPE", self.principal_id, self.view_id, self.permission_revision)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def case_permitted(self, case_id: str) -> bool:
        if self.case_scope == "none":
            return False
        return case_id not in self.denied_case_ids

    def cases_permitted(self, case_ids: list[str]) -> bool:
        """Default multi-case rule: authority for every represented case. A record with no case is only visible
        to a view with corpus-wide scope."""
        if self.case_scope == "none":
            return False
        return all(self.case_permitted(c) for c in case_ids)

    def view_permitted(self, access: dict | None) -> bool:
        """Record-level allowlist: the record's own `access.view_ids` must name this view. Absent or malformed
        access metadata denies (fail closed); a withheld record is never served."""
        if not isinstance(access, dict):
            return False
        return self.view_id in (access.get("view_ids") or []) and access.get("redaction_state") != "withheld"

    def record_permitted(self, record: dict) -> bool:
        """Any v0.2 input/analysis record: envelope access allowlist + every represented case."""
        env, data = record.get("envelope", {}), record.get("data", {})
        cases = list(data.get("case_ids") or []) + ([data["case_id"]] if data.get("case_id") else [])
        return self.view_permitted(env.get("access")) and self.cases_permitted(sorted(set(cases)))

    def source_permitted(self, record: dict) -> bool:
        """`record` is the full v0.2 source input record (envelope + data)."""
        source = record["data"]
        return (self.can("sources") and source["source_id"] not in self.denied_source_ids
                and self.view_permitted(record["envelope"].get("access")) and self.cases_permitted(source["case_ids"]))

    def asset_permitted(self, asset: dict) -> bool:
        access = (asset.get("policy") or {}).get("access")
        return (self.can("sources") and asset["asset_version_id"] not in self.denied_asset_ids
                and self.view_permitted(access) and self.cases_permitted(asset["case_ids"]))


def policy_path() -> Path:
    """`SDB_POLICY_PATH` isolates the policy file for an isolated store/configuration (E11): `SDB_DATA_DIR` alone
    leaves `config/policy.json` shared. Resolved per call, never cached."""
    return Path(os.environ.get("SDB_POLICY_PATH") or POLICY_PATH)


def load_policy(path: Path | None = None) -> dict:
    return json.loads(Path(path or policy_path()).read_text(encoding="utf-8"))


def permission_revision(policy: dict | None = None) -> str:
    return derived_id("PERM", canonical_sha256(policy or load_policy()), length=16)


def resolve_authority(principal_id: str, policy: dict | None = None) -> Authority | None:
    policy = policy or load_policy()
    principal = policy["principals"].get(principal_id)
    if principal is None:
        return None
    view = policy["views"][principal["view_id"]]
    return Authority(
        principal_id=principal_id, view_id=principal["view_id"], capabilities=tuple(principal["capabilities"]),
        permission_revision=permission_revision(policy), entitlement_policy_id=policy["policy_id"],
        case_scope=view["case_scope"], denied_case_ids=frozenset(view["denied_case_ids"]),
        denied_source_ids=frozenset(view["denied_source_ids"]),
        denied_asset_ids=frozenset(view["denied_asset_version_ids"]))


def record_access(view_ids: list[str] | None = None) -> dict:
    """v0.2 `access` for accepted records: the views whose policy may consider the record."""
    policy = load_policy()
    return {"view_ids": sorted(view_ids or [v for v, cfg in policy["views"].items() if cfg["case_scope"] != "none"]),
            "classification": "standard", "redaction_state": "none"}


def policy_binding(view_ids: list[str] | None = None) -> dict:
    policy = load_policy()
    return {"access": record_access(view_ids), "entitlement_policy_id": policy["policy_id"],
            "permission_revision": permission_revision(policy)}


def pipeline_authority(view_id: str = "complaints-leadership") -> Authority:
    """Service authority for the offline pipeline: the view's case scope, read-only, no download."""
    policy = load_policy()
    view = policy["views"][view_id]
    return Authority(principal_id="pipeline-service", view_id=view_id,
                     capabilities=("aggregate", "contributors", "cases", "sources", "originals"),
                     permission_revision=permission_revision(policy), entitlement_policy_id=policy["policy_id"],
                     case_scope=view["case_scope"], denied_case_ids=frozenset(view["denied_case_ids"]),
                     denied_source_ids=frozenset(view["denied_source_ids"]),
                     denied_asset_ids=frozenset(view["denied_asset_version_ids"]))
