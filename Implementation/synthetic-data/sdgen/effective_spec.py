"""Effective asset specifications: an approved Data ruling applied to a frozen plan's asset, in memory, once.

The frozen plan is never edited. An approved clarification carrying `effective_asset_projection` names one held asset and says
exactly how its runtime rows differ from the planned rows (columns dropped from the runtime file, rows excluded). `project()` derives
the effective asset from the frozen asset and REFUSES unless the result equals the ruling's own expected rows, count and seconds,
and the asset's identity, fields and dates are the planned ones. `apply()` puts the effective asset into a loaded CasePlan, so the
required-value checks, the renderer and packaging all read the same definition. The removed values stay in the private ruling.

This waives no guard: the staging-label check still runs on the rendered file; it passes only because the ruled file no longer carries
the authoring column."""
import copy
import hashlib
import json
import re

PROJECTION_KEY = "effective_asset_projection"
_ROLE_RE = re.compile(r"^/rows/(\d+)/(.+)$")


class RulingMismatch(RuntimeError):
    """The ruling and the frozen plan disagree; nothing is projected."""


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def projections(clarifications):
    """Approved clarifications that define an effective asset."""
    return [c for c in clarifications if c.get("status") == "approved" and c.get("approved_by") and c.get("approved_on") and c.get(PROJECTION_KEY)]


def project(asset, clar, art=None):
    """Effective copy of `asset` under one approved clarification. Raises RulingMismatch on any disagreement."""
    proj = clar[PROJECTION_KEY]
    cid = clar["id"]
    if clar.get("asset_id") != asset["asset_id"] or clar.get("filename") != asset["filename"]:
        raise RulingMismatch(f"{cid} names {clar.get('asset_id')}/{clar.get('filename')}, not {asset['asset_id']}/{asset['filename']}")
    if proj.get("format") != asset.get("format") or proj.get("media_type") != asset.get("media_type"):
        raise RulingMismatch(f"{cid}: format/media type differ from the planned asset (a ruling never converts the file)")
    spec = asset["document_spec"]
    if proj.get("fields") != spec.get("fields"):
        raise RulingMismatch(f"{cid}: export fields differ from the planned asset")
    for k in ("created_at", "available_at"):
        if proj.get(k) != asset.get(k):
            raise RulingMismatch(f"{cid}: {k} {proj.get(k)!r} differs from the planned {asset.get(k)!r} (availability is never moved)")
    if art is not None and proj.get("case_received_at") not in (None, art["event_at"]):
        raise RulingMismatch(f"{cid}: case_received_at {proj.get('case_received_at')!r} differs from card event_at {art['event_at']!r}")
    drop = list(proj.get("drop_runtime_columns") or [])
    excluded = list(proj.get("excluded_work_ids") or [])
    rows = spec.get("rows") or []
    ids = [r.get("work_id") for r in rows]
    missing = [w for w in excluded if w not in ids]
    if missing:
        raise RulingMismatch(f"{cid}: excluded rows {missing} are not in the planned asset")
    private = clar.get("private_original_rows_excluded")
    if private is not None and sorted(map(_canon, private)) != sorted(_canon(r) for r in rows if r.get("work_id") in excluded):
        raise RulingMismatch(f"{cid}: the private record of excluded rows differs from the planned rows")
    kept = [(i, {k: v for k, v in r.items() if k not in drop}) for i, r in enumerate(rows) if r.get("work_id") not in excluded]
    eff_rows = [r for _, r in kept]
    if eff_rows != proj.get("expected_rows"):
        raise RulingMismatch(f"{cid}: the projected rows differ from the ruling's expected rows (values, columns or order)")
    if len(eff_rows) != proj.get("expected_row_count") or sum(r["seconds"] for r in eff_rows) != proj.get("expected_seconds"):
        raise RulingMismatch(f"{cid}: projected row count/seconds differ from the ruling")
    crosswalk = clar.get("private_retained_work_to_card_crosswalk")
    if crosswalk is not None and {rows[i]["work_id"]: rows[i].get("related_artifact") for i, _ in kept} != crosswalk:
        raise RulingMismatch(f"{cid}: the private retained crosswalk differs from the planned related_artifact values")
    # temporal roles: a retained cell keeps the role of its original cell, re-indexed; roles of removed cells are dropped
    roles = {}
    old_to_new = {i: n for n, (i, _) in enumerate(kept)}
    for ptr, role in (spec.get("field_temporal_roles") or {}).items():
        m = _ROLE_RE.match(ptr)
        if not m:
            roles[ptr] = role
            continue
        i, col = int(m.group(1)), m.group(2)
        if i in old_to_new and col not in drop:
            roles[f"/rows/{old_to_new[i]}/{col}"] = role
    eff = copy.deepcopy(asset)
    eff["document_spec"]["rows"] = eff_rows
    eff["document_spec"]["field_temporal_roles"] = roles
    eff["effective_spec"] = {"clarification": cid, "approved_by": clar["approved_by"], "approved_on": clar["approved_on"],
                             "planned_asset_sha256": hashlib.sha256(_canon(asset).encode()).hexdigest(),
                             "dropped_runtime_columns": drop, "excluded_work_ids": excluded,
                             "row_count": len(eff_rows), "seconds": sum(r["seconds"] for r in eff_rows)}
    return eff


def effective_assets(plan_pack, clarifications):
    """{asset_id: effective asset} for every approved projection, validated against the frozen plan."""
    assets = {a["asset_id"]: a for a in plan_pack["assets"]}
    arts = {a["artifact_ref"]: a for a in plan_pack["artifacts"]}
    out = {}
    for c in projections(clarifications):
        if c.get("asset_id") not in assets:
            raise RulingMismatch(f"{c['id']}: asset {c.get('asset_id')} is not in the plan")
        if c["asset_id"] in out:
            raise RulingMismatch(f"two approved projections for {c['asset_id']}; a conflict must be resolved explicitly")
        art = arts.get(c.get("card"))
        if art is None or c["asset_id"] not in art.get("attachments", []):
            raise RulingMismatch(f"{c['id']}: card {c.get('card')} does not carry asset {c['asset_id']}")
        out[c["asset_id"]] = project(assets[c["asset_id"]], c, art)
    return out


def effective_work_entries(plan_pack, clarifications):
    """The plan's work entries under the approved projections: excluded rows removed, dropped columns removed."""
    entries = plan_pack.get("work_entries") or []
    for c in projections(clarifications):
        p = c[PROJECTION_KEY]
        drop, excl = set(p.get("drop_runtime_columns") or []), set(p.get("excluded_work_ids") or [])
        entries = [{k: v for k, v in e.items() if k not in drop} for e in entries if e.get("work_id") not in excl]
    return entries


def card_work_seconds(plan_pack, clarifications):
    """Per card: planned work_duration_seconds and the internally recorded seconds of the effective export (via the private
    retained crosswalk). Every card must agree except where the ruling excluded external staff time, which is listed."""
    out = []
    for c in projections(clarifications):
        cross = c.get("private_retained_work_to_card_crosswalk") or {}
        rows = {r["work_id"]: r for r in c[PROJECTION_KEY]["expected_rows"]}
        excluded = {r["related_artifact"]: r for r in c.get("private_original_rows_excluded") or []}
        for art in plan_pack["artifacts"]:
            ref = art["artifact_ref"]
            internal = sum(rows[w]["seconds"] for w, card in cross.items() if card == ref)
            planned = art.get("work_duration_seconds", 0)
            ext = excluded.get(ref)
            if internal != planned and not (ext and internal + ext["seconds"] == planned):
                raise RulingMismatch(f"{c['id']}: {ref} planned {planned}s but the effective export records {internal}s")
            if internal != planned:
                out.append({"card": ref, "planned_work_duration_seconds": planned, "recorded_internal_seconds": internal,
                            "excluded_external_seconds": ext["seconds"], "reason": "external staff duration is not recorded fund work (ruling " + c["id"] + ")"})
    return out


def apply(plan, clarifications):
    """Put the effective assets into a loaded CasePlan (in memory; the plan file is untouched). Returns the effective assets."""
    eff = effective_assets(plan.pack, clarifications)
    if not eff:
        return eff
    plan.pack = copy.deepcopy(plan.pack)
    plan.pack["assets"] = [eff.get(a["asset_id"], a) for a in plan.pack["assets"]]
    plan.pack["work_entries"] = effective_work_entries(plan.pack, clarifications)
    plan.assets = {a["asset_id"]: a for a in plan.pack["assets"]}
    plan.assets_by_name = {a["filename"]: a for a in plan.pack["assets"]}
    plan.effective_assets = eff
    return eff


def document(plan_pack, plan_sha256, clarifications):
    """The pack's EFFECTIVE-ASSET-SPECS.json body: what the checks and renderer use, with provenance. Review aid, never runtime input."""
    eff = effective_assets(plan_pack, clarifications)
    return {"kind": "effective_asset_specifications", "version": 1, "bound_plan_sha256": plan_sha256,
            "note": ("Derived in memory from the frozen plan and the approved clarifications; the frozen plan is unchanged. The author check, renderer "
                     "and packager recompute this and refuse if it differs from this file. Never a runtime input or evaluation gold."),
            "assets": [{"asset_id": k, "filename": v["filename"], "format": v["format"], "media_type": v["media_type"], "created_at": v["created_at"],
                        "available_at": v["available_at"], "fields": v["document_spec"]["fields"], "rows": v["document_spec"]["rows"],
                        "field_temporal_roles": v["document_spec"]["field_temporal_roles"], "provenance": v["effective_spec"]} for k, v in sorted(eff.items())],
            "effective_work_entries": effective_work_entries(plan_pack, clarifications),
            "cards_whose_recorded_internal_effort_differs_from_planned_duration": card_work_seconds(plan_pack, clarifications)}
