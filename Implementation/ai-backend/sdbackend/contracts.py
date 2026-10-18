"""Frozen contract loading and offline validation.

Shared 1.0.0 (review-2) and the pinned Data v0.2 baseline are read from their accepted locations, verified against
the adopted manifest digests, and registered in an offline `referencing` registry. Nothing is fetched from the
network and no accepted byte is rewritten.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .canonical import canonical_sha256
from .config import load_settings

EXPECTED_MANIFEST_SHA256 = "b0b22c6742de24c4bc8643cb6c988624fa4b093db407896071a7e9a64acc1585"
SHARED_ID = "urn:example-super:shared:1.0.0"
RUNTIME_ID = "urn:example-super:runtime:0.2.0"
CONTRACT_VERSION = "1.0.0"
RUNTIME_SCHEMA_VERSION = "0.2.0"
BASELINE_REGISTRY_ID = "runtime-measures-0.2.0"
COMPANION_REGISTRY_ID = "shared-measures-1.0.0"


class ContractIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Contracts:
    shared: dict
    runtime: dict
    metric_registry: dict
    measure_additions: dict
    manifest: dict
    registry: Registry

    def validator(self, definition: str, *, document: str = "shared") -> Draft202012Validator:
        base = SHARED_ID if document == "shared" else RUNTIME_ID
        return _validator(self, f"{base}#/$defs/{definition}")

    def errors(self, definition: str, instance: Any, *, document: str = "shared") -> list[str]:
        v = self.validator(definition, document=document)
        return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:300]}"
                for e in sorted(v.iter_errors(instance), key=lambda e: list(map(str, e.absolute_path)))]

    def check(self, definition: str, instance: Any, *, document: str = "shared") -> None:
        errs = self.errors(definition, instance, document=document)
        if errs:
            raise ContractIntegrityError(f"{document}#{definition} invalid: " + "; ".join(errs[:5]))

    def enum(self, definition: str, *path: str, document: str = "runtime") -> list[str]:
        node = (self.runtime if document == "runtime" else self.shared)["$defs"][definition]
        for p in path:
            node = node["properties"][p] if "properties" in node else node[p]
        return list(node["enum"])


_VALIDATORS: dict[tuple[int, str], Draft202012Validator] = {}


def _validator(contracts: Contracts, ref: str) -> Draft202012Validator:
    key = (id(contracts), ref)
    if key not in _VALIDATORS:
        _VALIDATORS[key] = Draft202012Validator({"$ref": ref}, registry=contracts.registry,
                                                format_checker=FormatChecker())
    return _VALIDATORS[key]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_freeze(shared_dir: Path, data_dir: Path) -> dict:
    """Exact check of every manifest entry: a missing file fails exactly like a changed one (no `is_file` bypass).

    Returns {"shared": {name: "ok"}, "data": {name: "ok"}} or raises ContractIntegrityError naming each problem.
    """
    manifest_path = shared_dir / "MANIFEST.json"
    if not manifest_path.is_file():
        raise ContractIntegrityError("Shared MANIFEST.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = {k: v for k, v in manifest.items() if k != "manifest_content_sha256"}
    if not (canonical_sha256(body) == manifest.get("manifest_content_sha256") == EXPECTED_MANIFEST_SHA256):
        raise ContractIntegrityError("Shared manifest digest does not match the adopted 1.0.0 freeze")
    problems, report = [], {"shared": {}, "data": {}}
    for group, folder, entries in (("shared", shared_dir, manifest.get("files", {})),
                                   ("data", data_dir, manifest.get("pinned_data_baseline", {}))):
        if not entries:
            problems.append(f"manifest lists no {group} files")
        for name, digest in sorted(entries.items()):
            path = folder / name
            if not path.is_file():
                problems.append(f"{group} frozen file {name} is missing")
                report[group][name] = "missing"
            elif _file_sha256(path) != digest:
                problems.append(f"{group} frozen file {name} differs from its adopted digest")
                report[group][name] = "changed"
            else:
                report[group][name] = "ok"
    for required in ("shared.schema.json", "measure-additions.json"):
        if required not in manifest.get("files", {}):
            problems.append(f"manifest does not pin required file {required}")
    if problems:
        raise ContractIntegrityError("incomplete or changed contract freeze: " + "; ".join(problems))
    return {"manifest": manifest, "report": report}


@lru_cache(maxsize=1)
def load_contracts() -> Contracts:
    settings = load_settings()
    shared_dir, data_dir = settings.shared_contract_dir, settings.data_contract_dir
    manifest = verify_freeze(shared_dir, data_dir)["manifest"]

    shared = json.loads((shared_dir / "shared.schema.json").read_text(encoding="utf-8"))
    runtime = json.loads((data_dir / "runtime.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resources([
        (SHARED_ID, Resource(contents=shared, specification=DRAFT202012)),
        (RUNTIME_ID, Resource(contents=runtime, specification=DRAFT202012)),
    ])
    return Contracts(
        shared=shared, runtime=runtime,
        metric_registry=json.loads((data_dir / "metric-registry.json").read_text(encoding="utf-8")),
        measure_additions=json.loads((shared_dir / "measure-additions.json").read_text(encoding="utf-8")),
        manifest=manifest, registry=registry)
