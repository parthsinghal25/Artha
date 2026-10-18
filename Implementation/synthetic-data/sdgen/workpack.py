"""Self-contained, case-scoped work packs for future authoring workers.

A pack holds ONLY one member's plan, story, dated artifact cards, people, attachment specs and style assignments plus the
shared writing rules and tools. Building a pack authorises nothing: PACK-MANIFEST.json carries launch_status
"not_authorised" until the Principal Data Engineer's sign-off is recorded in STAGE-GATE.json by the supervisor."""
import glob
import hashlib
import json
import re
import shutil
from pathlib import Path

from .paths import PLAN_DIR, ROOT

SYNTH = ROOT / "Data/synthesis"
ADDENDUM = SYNTH / "authoring-addenda/naturalistic-writing-v1"
PACKS = SYNTH / "worker-packs"
OUTPUTS = SYNTH / "worker-outputs"
WRITER_GUIDE = SYNTH / "NONTECHNICAL-ARTIFACT-WRITER-GUIDE.md"

# words that must never appear in a pack's own generated files (control/evaluation vocabulary)
PACK_BANNED = re.compile(r"\b(stratum|evidence[_ ]tier|holdout|reviewer_only|expected[_ ]diagnos\w*|gold answer)\b", re.I)
MICROBATCH = ["11-D01", "11-D02", "11-D03", "11-D07", "11-D11", "11-D13", "11-D20"]
REHEARSAL = ["11-D05", "11-D06", "11-D10", "11-D15", "11-D17"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _plans():
    out = {}
    for f in sorted(glob.glob(str(PLAN_DIR / "assistant-packs/*.json"))):
        raw = Path(f).read_bytes()
        d = json.loads(raw)
        out[d["case_number"]] = {"path": Path(f), "raw": raw, "data": d}
    return out


def common_references(plans):
    """Staff/external directory entries that appear in several packs with identical details (org directory facts)."""
    seen = {}
    for p in plans.values():
        for a in p["data"]["actors"]:
            if a["id"].startswith(("STAFF", "EXT")):
                seen.setdefault(a["id"], []).append({k: a.get(k) for k in ("name", "role", "email")})
    directory = {k: v[0] for k, v in sorted(seen.items()) if len(v) > 1 and all(x == v[0] for x in v)}
    conflicts = sorted(k for k, v in seen.items() if any(x != v[0] for x in v))
    return {
        "kind": "common_fictional_references",
        "note": "Organisation directory facts only. A case's own people list in its assistant pack remains controlling.",
        "staff_and_external_directory_shared_by_several_cases": directory,
        "ids_with_conflicting_details_across_cases": conflicts,
        "calendar_and_time": {
            "zone_rule": "Australia/Melbourne: +11:00 (AEDT) from the first Sunday in October to the first Sunday in April, +10:00 (AEST) otherwise; use the offset already on the card and never change it.",
            "scenario_dates": "Dates on cards are synthetic scenario dates, not wall-clock times.",
            "weekday": "State a weekday only if you derive it from the card date; if unsure omit it.",
            "no_public_holiday_table": "No holiday calendar has been supplied; do not claim a date is or is not a holiday.",
        },
        "procedures": {
            "status": "no stand-alone procedure corpus exists in the workspace",
            "use": "Procedure-like facts available to a worker are exactly the case's work_entries, external_matters, story and card constraints. Do not invent policy, timing standards or regulatory clauses.",
        },
    }


def _style_for(case, cid, assign, profiles):
    rows = [r for r in assign["narrative_or_operational_cards"] if r["case_reference"] == cid]
    briefs = [r for r in assign["attachment_briefs"] if r["case_reference"] == cid]
    used = sorted({r["profile_id"] for r in rows} | {b["profile_id"] for b in briefs})
    actors = {r["actor_voice_ref"] for r in rows}
    return {
        "kind": "case_scoped_private_style_controls_not_source_or_gold",
        "version": assign["version"], "seed": assign["seed"], "case_reference": cid,
        "cards": rows, "attachment_briefs": briefs,
        "profiles": {k: profiles["profiles"][k] for k in used},
        "actor_voices": [v for v in profiles["actor_voices"] if v["actor_ref"] in actors],
        "protected_content_rules": profiles["protected_content_rules"],
        "allowed_error_scope": profiles["allowed_error_scope"],
    }


def _instructions(case, pd, stage, outputs_rel):
    cards = [a["artifact_ref"] for a in pd["artifacts"]]
    return f"""# Work pack - member case {case:02d} ({pd['story']['name']}), complaint {pd['case_id']}

**Launch status: {stage['launch_status']}.** {stage['launch_note']}

You are the authoring worker for ONE member's journey. You own it end to end: the member/staff emails, private call dialogues
and their public summaries, approvals, actions, confirmations, notes and any other specified records for the {len(cards)} cards below.

## What you may use
- Only the files in this pack folder: `plan/` (assistant pack JSON, case study), `style/` (your style assignments and profiles),
  `shared/` (writing rules, authored-file schema, common references), `assets-to-render.json`, `RETURN-CHECKLIST.json`.
- Write only inside your owned output folder: `{outputs_rel}`.
- Do not read or write anywhere else. You have no access to other members' stories, expected answers, evaluation material or any conversation history.

## What you must do
1. Read `shared/WRITER-INSTRUCTIONS.md`, `shared/NONTECHNICAL-ARTIFACT-WRITER-GUIDE.md`, the case study and the assistant pack.
2. Work card by card in date order. Each card may contain only facts its own card permits at its own date. Never use later knowledge.
3. Apply each card's style assignment exactly (`style/ARTIFACT-STYLE-ASSIGNMENTS.json`, profiles in `style/STYLE-PROFILES.json`). Style limits are ceilings, never quotas. If a card cannot be written in its style without changing consequential meaning, stop that card and raise a question.
4. Return one authored JSON per card in the format in `shared/AUTHORED-SCHEMA.md`, with its restricted `style_completion` row. For a call, return the private conversation and then a summary written from that conversation.
5. Run `sdgen author-check` (command in the schema doc) on each file before submitting. Passing it is not acceptance.
6. Fill in `RETURN-CHECKLIST.json`. Raise any ambiguity as a question instead of inventing a date, outcome, signer, payment, reason or document.

## Hard rules
- No invented people, outcomes, dates, payments, attachments, upload events, contacts or missing evidence. Attachments are rendered by the supervisor from the locked specs in the pack; you only name them as the card allows.
- Quoted/forwarded text is reused exactly. Style applies to your own newly authored text only.
- Protected values (names, roles, references, filenames, amounts, dates, promises, negations, attribution, state distinctions) are never altered by style.
- Private dialogue, style rows and the story never appear in source text. Do not mention profiles, roughness, instructions or plans in any document.
- You never mark anything accepted. Review is independent.

## Cards in this pack
{chr(10).join('- ' + c for c in cards)}
"""


AUTHORED_SCHEMA = """# Authored file format (one JSON file per card)

Common keys: `card`, `revision` (start at 1), `kind`, `style_completion`.

| kind | required keys |
|---|---|
| `email` | `greeting`, `blocks` (list of `{"p": text}` or `{"ul": [..]}` / `{"ol": [..]}`), `signoff` (list of lines), optional `in_reply_to_card`, `references_cards`, `forward_of` (card whose body is quoted exactly), `required_tokens`, `must_not_match` |
| `letter_with_cover_email` | email keys plus `letter` (`title`, `blocks`) and `letter_filename` |
| `call_summary` | `conversation` (list of `{"n","speaker","text"}`), `summary` (60-180 words unless detail requires more), `follow_up`, `fidelity` (claims split spoken vs metadata, `required_content`) |
| `note` | `blocks` |
| `portal_json` | `form`, `reference`, `fields`, `free_text`, `attachments` |

`required_tokens` are exact strings that must appear (identifiers, filenames, amounts, key phrases). `must_not_match` are regular
expressions that must not match (scoped to this card). `style_completion`:

```json
{"card": "NN-Dxx", "applied_profile": "M3", "features_used": ["one fragment"], "typos_used": [{"from": "please", "to": "pleae"}],
 "protected_values_checked": true, "concerns": []}
```
Only these deliberate misspellings are permitted: please->pleae, thanks->thnaks, message->mesage, question->quesiton,
attachment->attachement, and only up to the profile's `max_harmless_typos`, never inside names, references, filenames, amounts,
dates or a required phrase. List every one you use.

Check a file (run from `Artha/Implementation/synthetic-data`, using its venv):

```
.venv/bin/python -m sdgen author-check <authored.json> --pack <this pack folder>
```
"""


def build_pack(case, plans, assign, profiles, common, out_root, launch_status, gate):
    p = plans[case]
    pd = p["data"]
    cid = pd["case_id"]
    stem = p["path"].stem
    root = Path(out_root) / stem
    if root.exists():
        raise FileExistsError(f"{root} exists; packs are rebuilt only into a fresh directory")
    (root / "plan").mkdir(parents=True)
    (root / "style").mkdir()
    (root / "shared").mkdir()
    shutil.copy(p["path"], root / "plan/assistant-pack.json")
    cs = glob.glob(str(SYNTH / f"pilot-v2-planning-2026-10-13/case-studies/{case:02d}-C-*.md"))[0]
    shutil.copy(cs, root / "plan/case-study.md")
    style = _style_for(case, cid, assign, profiles)
    (root / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").write_text(json.dumps({k: style[k] for k in ("kind", "version", "seed", "case_reference", "cards", "attachment_briefs")}, indent=1) + "\n")
    (root / "style/STYLE-PROFILES.json").write_text(json.dumps({k: style[k] for k in ("profiles", "actor_voices", "protected_content_rules", "allowed_error_scope")}, indent=1) + "\n")
    wi = (ADDENDUM / "WRITER-INSTRUCTIONS.md").read_text()
    if case != 11:  # section 5 is the case-11 micro-batch; other workers do not receive another member's card list
        wi = re.sub(r"## 5\. First naturalistic-writing gate.*?(?=\n## 6\.)", "## 5. Gate\n\nYour stage opens only after the supervisor tells you your whole journey is authorised.\n", wi, flags=re.S)
    (root / "shared/WRITER-INSTRUCTIONS.md").write_text(wi)
    shutil.copy(WRITER_GUIDE, root / "shared/NONTECHNICAL-ARTIFACT-WRITER-GUIDE.md")
    (root / "shared/AUTHORED-SCHEMA.md").write_text(AUTHORED_SCHEMA)
    (root / "shared/COMMON-REFERENCES.json").write_text(json.dumps(common, indent=1) + "\n")
    # assets: how each is produced
    assets = []
    for a in pd["assets"]:
        how = ("not_rendered_advertised_only_no_bytes" if a.get("delivery_state") != "held" else
               "authoring_required_eml" if a["media_type"] == "message/rfc822" else "supervisor_renders_from_locked_spec")
        assets.append({"asset_id": a["asset_id"], "filename": a["filename"], "media_type": a["media_type"], "delivery_state": a.get("delivery_state"),
                       "copy_of_asset_id": a.get("copy_of_asset_id"), "production": how,
                       "style_profile": next((b["profile_id"] for b in style["attachment_briefs"] if b["asset_ref"] == a["asset_id"]), None)})
    (root / "assets-to-render.json").write_text(json.dumps({"note": "Attachments are produced once by the supervisor; copies reuse identical bytes.", "assets": assets}, indent=1) + "\n")
    stage = {"launch_status": launch_status, "launch_note": gate["note"]}
    cards = []
    for a in pd["artifacts"]:
        cards.append({"card": a["artifact_ref"], "kind": a["kind"], "event_at": a["event_at"], "status": "not_started", "authored_file": None,
                      "style_completion_row": None, "protected_values_checked": None, "questions": []})
    (root / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": case, "cards": cards, "attachments_listed_in_assets_to_render": len(assets),
                                                            "note": "Worker fills statuses; supervisor and independent reviewers decide acceptance."}, indent=1) + "\n")
    stage_doc = stage_for(case, pd)
    (root / "STAGE.json").write_text(json.dumps(stage_doc, indent=1) + "\n")
    out = Path(out_root).parent / "worker-outputs" / stem
    (root / "INSTRUCTIONS.md").write_text(_instructions(case, pd, stage, str(out)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.txt").write_text(f"Owned output folder for case {case:02d}. Only the worker assigned to this case writes here.\n")
    return root


def stage_for(case, pd):
    cards = [a["artifact_ref"] for a in pd["artifacts"]]
    if case == 11:
        return {"case": 11, "stages": [
            {"stage": "rehearsal_repair", "cards": REHEARSAL, "state": "supervisor_published_v3_awaiting_independent_review", "worker_action": "none; exempt from style edits"},
            {"stage": "style_microbatch", "cards": MICROBATCH, "state": "blocked_until_rehearsal_acceptance_and_separate_signoff"},
            {"stage": "remaining_cards", "cards": [c for c in cards if c not in MICROBATCH + REHEARSAL], "state": "blocked_until_microbatch_accepted"},
            {"stage": "complete_case_review", "state": "after_all_24_remaining_cards"}]}
    return {"case": case, "stages": [{"stage": "whole_journey", "cards": cards, "state": "blocked_until_stage_signoff_after_case_11_review"}]}


def verify_pack(root, plans):
    """Isolation + integrity checks for one built pack. Returns a list of (check, ok, detail)."""
    root = Path(root)
    pd = json.loads((root / "plan/assistant-pack.json").read_text())
    case, res = pd["case_number"], []
    own = {pd["case_id"], pd["request_reference"], pd["member_reference"], pd["story"]["name"]}
    others = {}
    for n, p in plans.items():
        if n != case:
            q = p["data"]
            others[n] = [q["case_id"], q["request_reference"], q["member_reference"], q["story"]["name"]]
    files = [f for f in root.rglob("*") if f.is_file()]
    leaks, banned = [], []
    for f in files:
        txt = f.read_text(errors="ignore")
        for n, toks in others.items():
            for t in toks:
                if t in txt and t not in own:
                    leaks.append((str(f.relative_to(root)), n, t))
        if f.name in ("INSTRUCTIONS.md", "AUTHORED-SCHEMA.md", "COMMON-REFERENCES.json", "STAGE.json"):
            if PACK_BANNED.search(txt):
                banned.append(str(f.relative_to(root)))
    res.append(("no other member's case id, request, member reference or name appears in any pack file", not leaks, str(leaks[:3])))
    res.append(("generated pack files contain no control/evaluation vocabulary", not banned, str(banned)))
    cards = {a["artifact_ref"] for a in pd["artifacts"]}
    st = json.loads((root / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    res.append(("exactly one style row per card", {r["artifact_ref"] for r in st["cards"]} == cards and len(st["cards"]) == len(cards), f"{len(st['cards'])} rows / {len(cards)} cards"))
    res.append(("one attachment brief per asset", len(st["attachment_briefs"]) == len(pd["assets"]), f"{len(st['attachment_briefs'])}/{len(pd['assets'])}"))
    plan_sha = sha((root / "plan/assistant-pack.json").read_bytes())
    res.append(("style rows bind this plan's sha256", all(r["base_plan_sha256"] == plan_sha for r in st["cards"] + st["attachment_briefs"]), plan_sha[:12]))
    prof = json.loads((root / "style/STYLE-PROFILES.json").read_text())
    res.append(("every profile used is defined", {r["profile_id"] for r in st["cards"] + st["attachment_briefs"]} <= set(prof["profiles"]), ""))
    res.append(("no style controls leak into the assistant pack", "profile_id" not in (root / "plan/assistant-pack.json").read_text(), ""))
    return res


def build_all(out_root=None, cases=None):
    out_root = Path(out_root) if out_root else PACKS
    plans = _plans()
    assign = json.loads((ADDENDUM / "ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    profiles = json.loads((ADDENDUM / "STYLE-PROFILES.json").read_text())
    fp = {Path(x["path"]).name: x["sha256"] for x in assign["base_plan_fingerprints"]}
    for n, p in plans.items():
        if fp[p["path"].name] != sha(p["raw"]):
            raise RuntimeError(f"case {n}: plan changed since style assignment (fingerprint mismatch)")
    gate = {"note": "Building packs authorises nothing. No worker may be launched until STAGE-GATE.json lists the case and its stage as authorised by the Principal Data Engineer's sign-off."}
    common = common_references(plans)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest, report = [], {}
    for n in sorted(cases or plans):
        root = build_pack(n, plans, assign, profiles, common, out_root, "not_authorised", gate)
        checks = verify_pack(root, plans)
        files = {str(f.relative_to(root)): sha(f.read_bytes()) for f in sorted(root.rglob("*")) if f.is_file()}
        entry = {"case": n, "pack": root.name, "files": files, "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks],
                 "plan_sha256": sha(plans[n]["raw"]), "launch_status": "not_authorised"}
        (root / "PACK-MANIFEST.json").write_text(json.dumps(entry, indent=1) + "\n")
        manifest.append({k: entry[k] for k in ("case", "pack", "plan_sha256", "launch_status")} | {"all_checks_ok": all(c["ok"] for c in entry["checks"]), "file_count": len(files)})
        report[n] = all(c["ok"] for c in entry["checks"])
    (out_root / "PACKS-INDEX.json").write_text(json.dumps({"kind": "worker_pack_index", "launch_authorised": False, "packs": manifest}, indent=1) + "\n")
    return manifest
