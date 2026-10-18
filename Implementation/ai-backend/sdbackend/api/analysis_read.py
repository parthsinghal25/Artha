"""Read access to published, accepted analysis sets (immutable `analysis_set` objects behind the
`analysis:<release_id>` pointer, with an immutable publication log for as-reported history).

Nothing here infers. Every population reference is validated in full (result in the context's pinned analysis
set, cell, population ID, digest, binding self-consistency, contributor-set digest) and must be compatible with the
reading context (view, permission revision, evidence cutoff/boundary, analysis set) or be the context's own origin.
A child context's origin population is authoritative: a missing request reference never widens it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..canonical import canonical_sha256
from ..engine.core import binding_consistent
from ..runtime import Runtime


def analysis_pointer_key(release_id: str) -> str:
    return f"analysis:{release_id}"


def _parse(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _valid(rt: Runtime, aset: dict | None) -> dict | None:
    """An analysis set is servable only while every acceptance it relied on is still effective (withdrawal or a
    later failing decision makes the last result incompatible → refused, never silently served)."""
    if aset is None:
        return None
    if rt.db.linked("withdrawal", "withdrawn", aset["analysis_set_version"]):
        return None
    from ..acceptance import still_effective
    deps = aset.get("dependency_closure")
    if deps is None:                                  # legacy set: every acceptance is a set-wide dependency
        if not all(still_effective(rt, a) for a in aset.get("acceptance_ids", [])):
            return None
        return aset
    # set-wide: only the closure's own result-integrity decision and the mechanical integrity record. Source,
    # extraction, native and stage decisions are revalidated per dependent output (dependencies_ok), so a withdrawal
    # refuses exactly the dependent results/records and leaves compatible unrelated outputs servable (F01).
    if not all(still_effective(rt, a) for a in (deps.get("result_integrity"), aset.get("integrity_acceptance_id")) if a):
        return None
    if deps.get("result_integrity") is None:
        return None
    return aset


def dependencies_ok(rt: Runtime, aset: dict, ids) -> bool:
    """Every accepted input an output used is still effective, transitively: a native row's own decision; an analysis
    record's (or case view's) stage decision, plus every source and extraction that stage consumed."""
    deps = aset.get("dependency_closure")
    if deps is None:
        return True                                   # legacy sets are validated set-wide in _valid
    from ..acceptance import still_effective
    memo: dict[str, bool] = {}

    def ok(acc):
        if acc is None:
            return False
        if acc not in memo:
            memo[acc] = still_effective(rt, acc)
        return memo[acc]

    def stage_ok(sid):
        st = deps["stages"].get(sid)
        return st is not None and ok(st["acceptance_id"]) and \
            all(ok(deps["sources"].get(x)) for x in st["sources"]) and \
            all(ok(deps["extractions"].get(e)) for e in st["extractions"])
    for rid in ids:
        if rid.startswith("stage:"):
            if not stage_ok(rid[len("stage:"):]):
                return False
        elif rid in deps["natives"]:
            if not ok(deps["natives"][rid]):
                return False
        elif rid in deps["records"]:
            if not stage_ok(deps["records"][rid]):
                return False
        else:
            return False                              # not part of the accepted closure: never authorised
    return True


def analysis_set_for(rt: Runtime, release_id: str) -> dict | None:
    row = rt.db.get_pointer(analysis_pointer_key(release_id))
    if row is None:
        return None
    aset = rt.db.get_object("analysis_set", row["target_id"])
    if aset is None or rt.db.object_sha("analysis_set", row["target_id"]) != row["target_sha256"]:
        return None
    return _valid(rt, aset)


def publications(rt: Runtime, release_id: str) -> list[dict]:
    ids = rt.db.linked("publication_event", "release", release_id)
    events_by_id = rt.db.get_objects_batched("publication_event", ids)
    events = [events_by_id.get(i) for i in ids]
    return sorted((e for e in events if e), key=lambda e: (_parse(e["published_at"]), e["generation"]))


def analysis_set_at(rt: Runtime, release_id: str, analysis_as_of: str) -> dict | None:
    """The analysis set actually current at the as-reported instant (parsed instants, publication log)."""
    t = _parse(analysis_as_of)
    current = None
    for e in publications(rt, release_id):
        if _parse(e["published_at"]) <= t:
            current = e
    if current is None or current["target_kind"] != "analysis_set":
        return None
    return _valid(rt, rt.db.get_object("analysis_set", current["target_id"]))


def pinned_set(rt: Runtime, ctx: dict) -> dict | None:
    """The immutable set the context was resolved against (not a newly advanced pointer)."""
    version = ctx.get("analysis_set_version")
    if not version:
        return None
    if not any(e["target_id"] == version for e in publications(rt, ctx["release_id"])):
        return None
    return _valid(rt, rt.db.get_object("analysis_set", version))


def registered(contracts, d: dict) -> bool:
    if d["registry_id"] == "runtime-measures-0.2.0":
        return any(m["metric_id"] == d["measure_id"] and m["definition_version"] == d["definition_version"]
                   for m in contracts.metric_registry["metrics"])
    if d["registry_id"] == "shared-measures-1.0.0":
        return d["definition_version"] == contracts.measure_additions["definition_version"] and any(
            m["measure_id"] == d["measure_id"] for m in contracts.measure_additions["definitions"])
    return False


# ------------------------------------------------------------------ populations
def _result(rt: Runtime, result_id: str) -> dict | None:
    return rt.db.get_object("result", result_id)


def resolve_reference(rt: Runtime, ref: dict) -> tuple[dict, dict, dict] | None:
    """(result, cell, contributor_set) for an exactly matching, self-consistent reference; else None."""
    result = _result(rt, ref["result_id"])
    if result is None:
        return None
    cell = next((c for c in result.get("cells", []) if c.get("key") == ref["cell_key"]), None)
    if cell is None:
        return None
    b = cell.get("population") or {}
    if (b.get("population_id") != ref["population_id"] or cell.get("population_id") != ref["population_id"]
            or b.get("predicate_sha256") != ref["predicate_sha256"] or not binding_consistent(b)):
        return None
    cset = rt.db.get_object("contributor_set", cell.get("contributor_set_id") or "")
    if cset is None or cset["predicate_sha256"] != b["predicate_sha256"] or cset["population_id"] != b["population_id"]:
        return None
    return result, cell, cset


def compatible(stored_ctx: dict, ctx: dict) -> bool:
    keys = ("view_id", "permission_revision", "evidence_as_of", "evidence_boundary", "analysis_set_version",
            "release_id")
    return all(stored_ctx.get(k) == ctx.get(k) for k in keys)


def _same_ref(a: dict | None, b: dict | None) -> bool:
    return a is not None and b is not None and canonical_sha256(a) == canonical_sha256(b)


def members(repo, cset: dict) -> list[str] | None:
    """Frozen contributor case IDs, or None if the principal cannot see every contributor (never a reduced set)."""
    if not all(repo.auth.case_permitted(c) for i in cset["items"] for c in i["case_ids"]):
        return None
    return sorted({c for i in cset["items"] for c in i["case_ids"]} | {i["entity_id"] for i in cset["items"]
                                                                       if not i["case_ids"]})


def child_origin(rt: Runtime, ref: dict, auth) -> tuple[dict, dict, dict] | None:
    """For context.resolve: validated (result, cell, binding) whose calculation scope matches this principal."""
    found = resolve_reference(rt, ref)
    if found is None:
        return None
    result, cell, _ = found
    rctx = result["result_context"]["context"]
    if rctx["view_id"] != auth.view_id or rctx["permission_revision"] != auth.permission_revision:
        return None
    return result, cell, cell["population"]


def population_restriction(rt: Runtime, aset: dict, repo, ctx: dict, req_ref: dict | None):
    """(restriction case IDs | None, origin descriptor | None), or raises LookupError for an incompatible ref."""
    restriction, origin = None, None
    if ctx.get("origin_population_reference"):
        found = resolve_reference(rt, ctx["origin_population_reference"])
        if found is None or found[0]["result_context"]["versions"]["analysis_set_version"] != aset["analysis_set_version"]:
            raise LookupError("origin population unavailable")
        m = members(repo, found[2])
        if m is None:
            raise LookupError("origin population not fully visible")
        restriction, origin = set(m), [ctx["origin_population_reference"]]
    if req_ref is not None and not _same_ref(req_ref, ctx.get("origin_population_reference")):
        found = resolve_reference(rt, req_ref)
        if found is None:
            raise LookupError("population reference does not match a published population")
        result, cell, cset = found
        stored = result["result_context"]["context"]
        lineage_ok = compatible(stored, ctx) or (
            ctx.get("origin_population_reference") is not None
            and _same_ref(stored.get("origin_population_reference"), ctx["origin_population_reference"])
            and stored["view_id"] == ctx["view_id"] and stored["permission_revision"] == ctx["permission_revision"]
            and stored["analysis_set_version"] == ctx["analysis_set_version"])
        if not lineage_ok:
            raise LookupError("population reference is incompatible with this context")
        m = members(repo, cset)
        if m is None:
            raise LookupError("population not fully visible")
        restriction = set(m) if restriction is None else restriction & set(m)
        origin = (origin or []) + [req_ref]
    return (None if restriction is None else sorted(restriction)), origin


def find_result(rt: Runtime, aset: dict, repo, ctx: dict, req: dict) -> dict | None:
    from ..engine.results import compute
    restriction, origin = population_restriction(rt, aset, repo, ctx, req["population_reference"])
    return compute(rt, aset, repo, ctx, req, restriction=restriction, origin=origin)


def contributors(rt: Runtime, aset: dict, repo, ctx: dict, req: dict):
    result = _result(rt, req["result_id"])
    if result is None or result["result_context"]["versions"]["analysis_set_version"] != aset["analysis_set_version"]:
        return None
    stored = result["result_context"]["context"]
    origin = ctx.get("origin_population_reference")
    own_origin = origin is not None and origin["result_id"] == req["result_id"] and origin["cell_key"] == req["cell_key"]
    descendant = origin is not None and _same_ref(stored.get("origin_population_reference"), origin) and \
        stored["view_id"] == ctx["view_id"] and stored["permission_revision"] == ctx["permission_revision"]
    if not (compatible(stored, ctx) or own_origin or descendant):
        return None                                  # e.g. later contributors requested from an earlier context
    from ..engine.results import result_dependency_ids
    if not dependencies_ok(rt, aset, result_dependency_ids(rt, result)):
        return None                                  # a withdrawn input: never page its former contributors
    cell = next((c for c in result.get("cells", []) if c.get("key") == req["cell_key"]), None)
    if cell is None or not cell.get("contributor_set_id"):
        return None
    ref = {"result_id": result["result_id"], "cell_key": cell["key"], "population_id": cell["population_id"],
           "predicate_sha256": cell["population"]["predicate_sha256"]}
    found = resolve_reference(rt, ref)
    if found is None:
        return None
    cset = found[2]
    if members(repo, cset) is None:
        return None                                  # never serve a reduced page under the unchanged count
    for item in cset["items"]:
        if any(repo.source_record(r["source_id"]) is None for r in item["source_refs"]):
            return None
    items = cset["items"]
    return items, {"result_id": result["result_id"], "cell_key": cell["key"], "population_id": cell["population_id"],
                   "contributor_set_id": cell["contributor_set_id"], "predicate_sha256": cell["population"]["predicate_sha256"],
                   "population": cell["population"], "total_count": len(items)}


def as_reported_available(rt: Runtime, release_id: str, analysis_as_of: str | None) -> dict | None:
    return None if analysis_as_of is None else analysis_set_at(rt, release_id, analysis_as_of)


# ------------------------------------------------------------------ records
def case_payload(rt: Runtime, aset: dict, repo, ctx: dict, req: dict):
    entry = aset.get("cases", {}).get(req["case_id"])
    if entry is None:
        return None
    payload = rt.db.get_object("case_view", entry)
    if payload is None or not repo.auth.record_permitted(payload["case_record"]):
        return None
    if not dependencies_ok(rt, aset, [payload["case_record"]["envelope"]["record_id"], f"stage:{payload['stage_id']}"]):
        return None
    if repo.cutoff < _parse(payload["state_projection"]["assessed_evidence_as_of"]):
        return None              # a later assessment never labels an earlier reconstruction
    events = [e for e in payload["events"] if all(repo.source_record(r["source_id"]) for r in e["source_refs"])]
    hidden = len(events) != len(payload["events"])
    base = {k: payload[k] for k in ("case_record", "state_projection", "completeness", "limitations")}
    if hidden:
        base = {**base, "completeness": "partial",
                "limitations": base["limitations"] + ["some events cite evidence outside this context; withheld"]}
    return events, base


def findings(rt: Runtime, aset: dict, repo, req: dict, population: list[str] | None) -> list[dict]:
    items = []
    kinds = set(req["record_kinds"])
    dispositions = set(req["membership_dispositions"])
    record_ids = aset.get("record_ids", [])
    records_by_id = rt.db.get_objects_batched("analysis_record", record_ids)
    for oid in record_ids:
        rec = records_by_id.get(oid)
        if rec is None or not repo.auth.record_permitted(rec) or not repo.available(rec["envelope"]["available_at"]):
            continue
        if not dependencies_ok(rt, aset, [oid]):
            continue                                 # its stage/source/extraction decision was withdrawn
        kind = rec.get("kind")
        if kinds and kind not in kinds:
            continue
        if not record_matches(rec, req["case_id"], dispositions, population):
            continue
        if not all(repo.source_record(s) for s in _record_sources(rec)):
            continue
        items.append(rec)
    return items


def memberships(rec: dict) -> dict[str, str]:
    """Typed case→disposition links of an accepted analysis record. A `finding` links cases only through
    `data.memberships[]` (state included/excluded/uncertain); other record kinds link through `case_id`/`case_ids`
    and carry no disposition."""
    data = rec.get("data", rec)
    out: dict[str, str] = {}
    for m in data.get("memberships") or []:
        if m.get("case_id"):
            out[m["case_id"]] = m.get("state", "uncertain")
    return out


def _record_cases(rec: dict) -> set[str]:
    data = rec.get("data", rec)
    out = set(data.get("case_ids", []) or [])
    if data.get("case_id"):
        out.add(data["case_id"])
    return out | set(memberships(rec))


def record_matches(rec: dict, case_id: str | None, dispositions: set[str], population: list[str] | None) -> bool:
    """Case / population / membership-disposition selection (F02). For a finding the disposition is evaluated on
    the membership of the selected case(s): a finding is returned for case X with disposition D only if X's own
    membership has state D; within a population, if some member of the population has state D. Records without
    memberships match on their direct case links and, if a disposition is requested, do not match (they have
    none)."""
    mem = memberships(rec)
    pop = None if population is None else set(population)
    if mem:
        relevant = {c: st for c, st in mem.items()
                    if (not case_id or c == case_id) and (pop is None or c in pop)}
        if not relevant:
            return False
        return not dispositions or any(st in dispositions for st in relevant.values())
    cases = _record_cases(rec)
    if case_id and case_id not in cases:
        return False
    if pop is not None and not (cases & pop):
        return False
    if dispositions:
        data = rec.get("data") or {}
        return (data.get("disposition") or data.get("membership_state")) in dispositions
    return True


def _record_sources(rec: Any) -> set[str]:
    found: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            if "source_id" in x and "content_sha256" in x and "anchor" in x:
                found.add(x["source_id"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(rec)
    return found


def taxonomy(rt: Runtime, aset: dict, version: str):
    if aset.get("taxonomy_version") != version:
        return None
    return rt.db.get_object("taxonomy", version)


def entity(rt: Runtime, aset: dict, repo, req: dict, population: list[str] | None):
    oid = aset.get("entities", {}).get(f"{req['entity_kind']}:{req['entity_id']}")
    if oid is None:
        return None
    if req["immutable_record_id"] and req["immutable_record_id"] != oid:
        return None
    rec = rt.db.get_object("analysis_record", oid)
    if rec is None or not repo.auth.record_permitted(rec) or not all(repo.source_record(s) for s in _record_sources(rec)):
        return None
    if not dependencies_ok(rt, aset, [oid]):
        return None
    if population is not None and not (_record_cases(rec) & set(population)):
        return None
    r_ids = aset.get("related", {}).get(oid, [])
    r_by_id = rt.db.get_objects_batched("analysis_record", r_ids)
    related = [r_by_id.get(r) for r in r_ids]
    related = [r for r in related if r and repo.auth.record_permitted(r)
               and all(repo.source_record(s) for s in _record_sources(r))
               and dependencies_ok(rt, aset, [r["envelope"]["record_id"]])]
    header = {"resolved_entity_id": req["entity_id"], "immutable_record_id": oid, "entity_kind": req["entity_kind"],
              "record": rec, "source_refs": sorted(_refs(rec), key=lambda r: (r["source_id"], str(r["anchor"]))),
              "population_reference": None}
    return related, header


def _refs(rec):
    out, seen = [], set()

    def walk(x):
        if isinstance(x, dict):
            if "source_id" in x and "content_sha256" in x and "anchor" in x:
                key = canonical_sha256(x)
                if key not in seen:
                    seen.add(key)
                    out.append(x)
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(rec)
    return out
