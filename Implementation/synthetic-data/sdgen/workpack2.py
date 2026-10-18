"""Pack set 2: corrected, versioned, self-contained worker packs (W01).

Differences from the first pack set (which stays untouched in Data/synthesis/worker-packs/):
  * ONE current guide (shared/WORKER-GUIDE.md) replaces the superseded writer guide + schema notes;
  * exact return schemas (shared/RETURN-SCHEMAS.json/.md) generated from sdgen.contract, the same objects the author check
    and the packaging adapter use;
  * approved-clarification file, accepted-prior-originals context (case 11), per-pack digest and per-input hashes;
  * stage-aware STAGE.json and per-stage owned output folders;
  * no out-of-pack links and no references to retired workflows.
Building a pack authorises nothing."""
import email
import glob
import hashlib
import json
import re
import shutil
from email import policy
from pathlib import Path

from . import contract
from .paths import PLAN_DIR, RELEASE_ROOT, ROOT
from .workpack import ADDENDUM, MICROBATCH, PACK_BANNED, REHEARSAL, SYNTH, _plans, _style_for, common_references, sha

PACK_SET_VERSION = 3
PACK_SET_DIRNAME = "worker-packs-v3"
OUTPUTS_DIRNAME = "worker-outputs-v3"
CLARIFICATIONS_SOURCE = ROOT / "Data/reviews/worker-readiness-v2-2026-10-15/CASE-11-APPROVED-CLARIFICATIONS.json"
CURRENT_POINTER = SYNTH / "WORKER-PACKS-CURRENT.json"
BLOCKED_CASES = {32: "Case 32 cards contain a future-knowledge defect (tracker D09). Blocked until the Data Engineer issues a corrected, versioned brief and it is reviewed."}
OUT_OF_PACK = re.compile(r"(?:\.\./|Artha|Data/(?:synthesis|reviews)|pilot-v2-planning|\bNONTECHNICAL\b|stage compiler|721-source|twelve procedure briefs)")
ACCEPTED_PRIOR_SOURCE = RELEASE_ROOT / "case-11-rehearsal-v3"

# --------------------------------------------------------------------------- guide
SKELETONS = {
    "email": {"card": "<card id>", "revision": 1, "kind": "email", "greeting": "<greeting line, or \"\" if the profile omits it>",
              "blocks": [{"p": "<paragraph you write>"}], "signoff": ["<sign-off line>", "<sender name>"],
              "in_reply_to_card": "<optional: earlier card id>", "style_completion": "<see style_completion below>"},
    "letter_with_cover_email": {"card": "<card id>", "revision": 1, "kind": "letter_with_cover_email", "greeting": "<cover greeting>",
                                "blocks": [{"p": "<cover paragraph>"}], "signoff": ["<cover sign-off>", "<sender name>"],
                                "letter": {"title": "<letter title, also used as its Subject line>", "salutation": "<Dear ...,>",
                                           "blocks": [{"h": "<heading>"}, {"p": "<paragraph>"}, {"ul": ["<bullet>"]}],
                                           "signoff": ["<Yours sincerely,>", "", "<sender name>", "<sender role>"]},
                                "style_completion": "<see below>"},
    "call_summary": {"card": "<card id>", "revision": 1, "kind": "call_summary",
                     "conversation": [{"n": 1, "speaker": "<participant name>", "text": "<spoken turn>"}, {"n": 2, "speaker": "<other participant>", "text": "<spoken turn>"},
                                      {"n": 3, "speaker": "<participant name>", "text": "<spoken turn>"}, {"n": 4, "speaker": "<other participant>", "text": "<spoken turn>"}],
                     "summary": "<third-person summary you write FROM the conversation>",
                     "follow_up_items": ["<next step, or none>"],
                     "fidelity": {"claims": [{"claim": "<what the summary asserts>", "turns": [1], "check_strings": ["<exact words in those turns>"]}]},
                     "style_completion": "<see below>"},
    "portal_json": {"card": "<card id>", "revision": 1, "kind": "portal_json", "free_text": "<the member's own words as typed into the form>",
                    "style_completion": "<see below>"},
    "register_export": {"card": "<card id>", "revision": 1, "kind": "register_export", "style_completion": "<see below; applied_profile S0, no typos>"},
    "note": {"card": "<card id>", "revision": 1, "kind": "note", "blocks": [{"p": "<note text>"}], "style_completion": "<see below>"},
    "ledger_export": {"card": "<card id>", "revision": 1, "kind": "ledger_export", "style_completion": "<see below; applied_profile S0, no typos>"},
}
STYLE_COMPLETION_SKELETON = {"card": "<card id>", "applied_profile": "<profile id from your style row>", "features_used": ["<feature you actually used>"],
                             "typos_used": [{"from": "please", "to": "pleae"}], "protected_values_checked": True, "concerns": []}


def returns_markdown():
    lines = ["# Return shapes (exact)", "",
             "Each card is returned as ONE JSON file named `<card>.json` in your owned folder, next to your completed copy of `RETURN-CHECKLIST.json` (the pack's own file is an immutable template). The shapes below are the only accepted ones;",
             "a call needs AT LEAST FOUR numbered turns, so the skeleton shows four;",
             "unknown fields are rejected. `style_completion` is a restricted row about your own work, never part of a document.", "",
             "```json", json.dumps(STYLE_COMPLETION_SKELETON, indent=1), "```", "",
             "`typos_used` lists every deliberate misspelling OCCURRENCE (five uses of the same misspelling = five entries) and is empty when none is used.",
             "Optional on any return: `required_tokens` (extra exact strings you want checked) and `must_not_match` (extra forbidden regular expressions). "
             "They can only add checks; the check also derives its own required values from the card.", ""]
    for kind, sk in SKELETONS.items():
        lines += [f"## `{kind}`", "", "Supervisor supplies (you never write these): " + "; ".join(contract.CENTRAL[kind]) + ".", "",
                  "```json", json.dumps(sk, indent=1, ensure_ascii=False), "```", ""]
    lines += ["Block forms for `blocks`: `{\"p\": text}`, `{\"ul\": [text, ...]}`, `{\"ol\": [text, ...]}` and `{\"quote\": exact passage, \"from_card\": earlier card id}`. "
              "A quote must be an exact passage of accepted text listed in `context/ACCEPTED-PRIOR-ORIGINALS.json` for an EARLIER card, compared character for character (spaces, tabs and blank lines must match; only line endings are normalised, CRLF = LF); style rules never apply inside it. "
              "Letter blocks use `{\"h\": heading}`, `{\"p\": text}` and `{\"ul\": [...]}`.", ""]
    return "\n".join(lines)


def unsupported_sentence():
    gaps = list(contract.UNSUPPORTED_PLAN_KINDS.values()) + list(contract.UNSUPPORTED_FORMATS.values())
    if not gaps:
        return "Every plan kind of this member has a return shape; if a card still seems to need one that does not exist, raise a question."
    return ("Cards whose plan kind or format has no return shape yet (" + "; ".join(gaps) + ") are not in your stage; if you meet one, raise a question.")


def guide_markdown(case, pd, stage_doc, style):
    name = pd["story"]["name"]
    launch = [s for s in stage_doc["stages"] if s.get("launchable")]
    mb = case == 11
    cards = "\n".join(f"- {c}" for s in launch for c in s["cards"])
    prof_ids = ", ".join(sorted(style["profiles"]))
    g = f"""# Worker guide - member case {case:02d} ({name}), complaint {pd['case_id']}

This is the only instruction document you need. Everything it refers to is inside your pack folder. It replaces every earlier
writing guide. If something here conflicts with a file you were told to read elsewhere, stop and raise a question.

## 1. Your job
You write the ordinary documents of ONE member's complaint journey, card by card, as that card's author would have written them
on that card's date: emails, portal text, call dialogue and its short summary, letters, notes. The supervisor renders everything
else (headers, attachments, native exports, letter layout). You do not decide outcomes, justify the complaint, diagnose causes,
calculate money or mark anything accepted.

You only write cards in a stage the supervisor has told you is authorised. Cards in your stage:

{cards}

## 2. What is in your pack
- `plan/assistant-pack.json` - the frozen fact cards (people, dates, subjects, recipients, attachments, assertions, constraints).
- `plan/case-study.md` - this member's story. It contains LATER events: a card may never use knowledge from after its own date.
- `plan/APPROVED-CLARIFICATIONS.json` - rulings issued after the plan was frozen. Only entries with status `approved` count.
- `style/ARTIFACT-STYLE-ASSIGNMENTS.json`, `style/STYLE-PROFILES.json` - your writing assignment for each card (section 6).
- `shared/RETURN-SCHEMAS.md` / `.json` - the exact shape of each return (section 5).
- `shared/COMMON-REFERENCES.json` - organisation directory facts and the calendar rule.
- `context/ACCEPTED-PRIOR-ORIGINALS.json` (when present) - accepted earlier documents of this member you may read and quote exactly.
- `assets-to-render.json` - which attachments the supervisor produces. `RETURN-CHECKLIST.json`, `STAGE.json`, `INSTRUCTIONS.md`.

## 3. Hard rules
1. Facts come only from the card (`source_assertions`, `writer_constraints`, people, attachments, dates) and from accepted earlier
   documents. Nothing else is a fact. Invent no people, outcomes, dates, payments, attachments, uploads, contacts, reasons or
   document contents.
2. Later knowledge: a card may use only what exists on or before its own `event_at`. A card that depends on a document not yet written
   (its `depends_on_artifact_refs`) may refer to that document only as the card's own assertions state it, as something reported or
   expected - never as text you imagine it contains.
3. Protected values are never changed, misspelt or paraphrased: names and roles; case, request, account, document, item, approval and
   version references; filenames; amounts, currency and units; dates; promises, conditions and due expressions; negations and
   unknowns; who said what; and the distinctions between received, valid, approved, attempted, executed, paid and closed.
4. Quoted text is copied exactly (`{{"quote": ..., "from_card": ...}}` block) and keeps its original wording, spacing and mistakes. The check compares it character for character with the accepted text; only line endings (CRLF = LF) are treated as equal.
5. Document text never mentions this guide, profiles, roughness, assignments, plans, cards or checks. Ordinary words such as "profile" or
   "stage" are fine when the sentence is about the member's affairs.
6. You never mark anything accepted. Passing the check below is not acceptance.
7. Pack files are immutable. Never edit anything inside the pack folder, including `RETURN-CHECKLIST.json`.

## 4. Missing procedure facts - never invent them
No stand-alone procedure or policy corpus has been supplied to this pack. The procedure-like facts available to you are exactly the
card's own `writer_constraints`, `source_assertions`, `work_entries`, `external_matters`, the case story and approved clarifications.
If a card seems to need a rule, timing standard, regulatory clause, authority limit or template that none of those supply, do not write
one: raise a question (section 8) and leave that card unfinished. Treat a missing rule as an open dependency, not a gap to fill.

## 5. What you write and what the supervisor supplies
Exact shapes, with a minimal skeleton for each kind, are in `shared/RETURN-SCHEMAS.md`. In short:

| Return kind | You write | Supervisor supplies |
|---|---|---|
""" + "\n".join({
        "email": "| `email` | greeting, paragraphs/lists/quotes, sign-off | " + "; ".join(contract.CENTRAL["email"]) + " |",
        "letter_with_cover_email": "| `letter_with_cover_email` | cover email text, letter title, salutation, headings/paragraphs, sign-off | " + "; ".join(contract.CENTRAL["letter_with_cover_email"]) + " |",
        "call_summary": "| `call_summary` | the private dialogue, then the summary written from it, follow-up items, fidelity claims | " + "; ".join(contract.CENTRAL["call_summary"]) + " |",
        "portal_json": "| `portal_json` | the member's free text only | " + "; ".join(contract.CENTRAL["portal_json"]) + " |",
        "register_export": "| `register_export` | nothing but the style row (attest profile S0, no errors) | " + "; ".join(contract.CENTRAL["register_export"]) + " |",
        "note": "| `note` | the note paragraphs | " + "; ".join(contract.CENTRAL["note"]) + " |",
        "ledger_export": "| `ledger_export` | nothing but the style row (attest profile S0, no errors) | " + "; ".join(contract.CENTRAL["ledger_export"]) + " |"}.values()) + f"""

Which kind a card needs follows from the card (plan kind and attachments); the check rejects any other kind. {unsupported_sentence()}

Attachments: name each file attached to an email by its exact filename in the message text (the check requires it). Never describe contents the card does
not give. Attachments are rendered or reused byte for byte by the supervisor; you never write or alter them.

A call: write the private dialogue first (numbered turns, only the participants listed on the card), then write the summary from that dialogue in the third
person as the summary service would, keeping the concern, attribution, interruptions, promises and ending the card assigns. Every `check_strings` entry must
occur in the turns you cite. The dialogue is private and never enters the intake.

## 6. Writing style
Each card has a row in `style/ARTIFACT-STYLE-ASSIGNMENTS.json` (profile id: one of {prof_ids}). The profile (in `style/STYLE-PROFILES.json`) sets
voice, difficulty and a typo ceiling. Do not choose your own roughness. Ceilings are maximums, never quotas; if the assigned features cannot be applied
without changing consequential meaning, stop that card and raise a question.

- Clean = correct grammar with the individual voice. Light = one or two informal features. Moderate = uneven lengths, one fragment, modest punctuation or
  capitalisation variation, one brief repetition of an already authorised concern. Dense but recoverable = fragments, uneven paragraphs, up to two repetitions
  already present in the facts, meaning still recoverable. A non-clean profile uses at least one assigned feature (two for moderate/dense where the card allows).
- Deliberate misspellings: only please->pleae, thanks->thnaks, message->mesage, question->quesiton, attachment->attachement; outside quotations, names,
  references, filenames, amounts, dates and required phrases; never every occurrence. The ceiling counts OCCURRENCES, not distinct spellings - three uses of "pleae"
  are three typos. Declare each one in `typos_used`. A ceiling of 0 means none. Abbreviations (pls, FYI, re) are style devices, not misspellings.
- Native or literal records (register export, form fields, exports) carry no deliberate errors anywhere.
- Formal letters keep correct grammar in every legal or financial qualification; never soften a negation or uncertainty.
- Never add a "bad customer", hostility, frustration, language-proficiency, urgency or personal detail that the card does not already authorise.
- Source-specific limits: member email/portal text may be short, lower-case or uneven; internal messages may be terse ("pls", "FYI") but keep object, authority and
  attribution explicit and never add a chase, queue, deadline or completed action; a vendor summary is generally cleaner than speech and does not clean up disputed
  content into fact; an approval or note stays concise but must keep decision, object, scope, condition, actor and state clear, and never merges approval with
  execution, or closure with fulfilment.
- A request is not the recipient's promise; "the receipt shows" is not "was accepted"; "reported" is not "observed".

## 7. Check your work
Run from the implementation folder (the supervisor gives you the exact command in `INSTRUCTIONS.md`):

    .venv/bin/python -m sdgen author-check <your-file.json> --pack <this pack folder> --stage <stage>

It validates the return shape and kind, derives the fixed values the CARD requires (request/approval/case references, amounts, dates, decision-maker names,
attachment filenames, native register values) and fails if one is missing, derives forbidden items (a complaint id before the complaint existed, other members'
identifiers, tokens that first appear in later-dated documents), counts misspelling occurrences against the ceiling and against your declaration, verifies
quotes are exact, checks call fidelity strings, and looks for leaked control vocabulary. It does NOT judge meaning, voice, chronology or whether facts are
supported. Your `protected_values_checked` flag is recorded as your attestation only. An independent reviewer decides acceptance.

## 8. Questions and stopping
Raise a question instead of guessing: record it in your checklist copy for that card and stop that card. Raise one when a fixed value is missing or
contradicts another, a rule is absent (section 4), the style cannot be applied safely, or the card needs a return shape that does not exist.

## 9. Folders and the checklist copy
Write only inside your owned folder for the stage (named in `INSTRUCTIONS.md`). Copy the pack's `RETURN-CHECKLIST.json` to `<owned folder>/RETURN-CHECKLIST.json`, complete that copy (status, authored file, questions for each card of your stage), and leave the pack's file untouched. A submission without the completed copy is refused. Do not read or write anywhere else. You have no access to other members,
evaluation material or any earlier conversation. This is an instruction, not a sandbox: the supervisor audits writes but cannot technically prevent a read.
"""
    if mb:
        g += """
## 10. This stage: seven existing cards, then (later, separately) seventeen more
The five accepted rehearsal documents (11-D05, 11-D06, 11-D10, 11-D15, 11-D17) are fixed and exempt from style edits; you never rewrite them. This stage is the
writing-style micro-batch: seven of the remaining 24 case-11 cards, written first so the voices can be reviewed before the other seventeen. They are not extra
documents.

| Card | Required contrast | Notes |
|---|---|---|
| 11-D01 | Mildly informal member email | Initial request; the exact three attachments named; no complaint id yet and no invented frustration. |
| 11-D02 | More fragmented member portal text | Repeat only already authorised concerns; no new upload or contact. The form envelope is supplied. |
| 11-D03 | Native register control | Deterministic export of the locked register values; nothing for you to write. |
| 11-D07 | Terse internal operational email | Questions, references and the requested reply date exact; a request is not a promise by the recipient. |
| 11-D11 | Compressed vendor call summary | Later call; its private dialogue first. It depends on 11-D08, which is not written yet: use only the facts the card lists as held. |
| 11-D13 | Concise professional approval | Authority, scope, conditions unambiguous; approval is not execution or payment; A$100.00 only as the card states. |
| 11-D20 | Clear formal response | Properly scoped facts; no later outcome. Letter plus cover email. |

`context/ACCEPTED-PRIOR-ORIGINALS.json` lists the accepted earlier documents of this member that each card may read and quote (each card sees only those dated before it).
Approved rulings for this stage (see `plan/APPROVED-CLARIFICATIONS.json`, issued by the Principal Data Engineer): the 11-D03 register export carries the locked values plus the approved member reference, product,
channel, tag and manager display name, all supplied by the supervisor; 11-D13 is an email decision with its existing authority attachment (conditional remedy only, no execution, payment or benefit approval, no invented verification
rule, payee or validity decision); the 11-D11 summary need not repeat the request reference because the supplied header carries it - never invent spoken metadata.
"""
    return g


def instructions_markdown(case, pd, stage_doc, out_rel):
    launch = [s for s in stage_doc["stages"] if s.get("launchable")]
    stages = "\n".join(f"- stage `{s['stage']}`: write only {', '.join(s['cards'])}; owned folder `{out_rel}/{s['stage']}/`" for s in launch)
    return f"""# Work pack - member case {case:02d} ({pd['story']['name']}), complaint {pd['case_id']}  [pack set {PACK_SET_VERSION}]

**Launch status: not authorised.** Building this pack authorises nothing. You write only after the supervisor names one of the stages below as authorised.

Read `shared/WORKER-GUIDE.md` first; it is complete. Then `shared/RETURN-SCHEMAS.md`, the case study and the assistant pack.

{stages}

Return one JSON file per card (`<card>.json`) inside your stage's owned folder, run the check (guide section 7) on each, and put a completed COPY of `RETURN-CHECKLIST.json`
in that same owned folder as `{out_rel}/<stage>/RETURN-CHECKLIST.json`. Never edit the pack's own `RETURN-CHECKLIST.json`; it is an immutable template.
Check command (the supervisor's launch message gives the real folders):
`.venv/bin/python -m sdgen author-check <card>.json --pack <pack folder> --stage <stage>`

Use only this pack folder and your owned folder. Do not mark anything accepted.
"""


# --------------------------------------------------------------------------- context
def _plain_body(path):
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=policy.default)
    return msg.get_body(preferencelist=("plain",)).get_content()


def accepted_prior_originals(pd, stage_cards):
    """Accepted case-11 originals that precede at least one stage card, with their text. Bound to the v3 manifest hash."""
    man_path = ACCEPTED_PRIOR_SOURCE / "intake-bundle/delivery-manifest.json"
    man = json.loads(man_path.read_text())
    arts = {a["artifact_ref"]: a for a in pd["artifacts"]}
    out = []
    for o in man["originals"]:
        ref = o.get("staging_card_ref")
        if o["kind"] not in ("email", "call_summary") or ref not in arts:
            continue
        usable = [c for c in stage_cards if arts[c]["event_at"] > arts[ref]["event_at"]]
        if not usable:
            continue
        f = ACCEPTED_PRIOR_SOURCE / "intake-bundle" / o["file"]
        text = _plain_body(f) if o["kind"] == "email" else f.read_text(encoding="utf-8")
        out.append({"card": ref, "kind": o["kind"], "event_at": arts[ref]["event_at"], "subject": arts[ref]["subject"],
                    "author": pd["actors"][[a["id"] for a in pd["actors"]].index(arts[ref]["author"])]["name"],
                    "original_id": o["original_id"], "file_sha256": o["sha256"], "usable_before_cards": usable, "text": text})
    return {"kind": "accepted_prior_originals_for_context_and_exact_quotation",
            "bound_to_manifest": {"package": "case-11-rehearsal-v3", "delivery_manifest_sha256": sha(man_path.read_bytes())},
            "note": "Accepted, immutable documents of this member. Read them for continuity; quote them only with a quote block. Each card may use only those listed for it; "
                    "no document dated after a card appears here for that card.",
            "originals": sorted(out, key=lambda x: x["event_at"])}


# --------------------------------------------------------------------------- stage doc
def stage_doc_for(case, pd):
    cards = [a["artifact_ref"] for a in pd["artifacts"]]
    if case in BLOCKED_CASES:
        return {"case": case, "pack_set_version": PACK_SET_VERSION, "stages": [
            {"stage": "whole_journey", "cards": cards, "launchable": False, "blocked_reason": BLOCKED_CASES[case]}]}
    if case == 11:
        rest = [c for c in cards if c not in MICROBATCH + REHEARSAL]
        return {"case": 11, "pack_set_version": PACK_SET_VERSION, "stages": [
            {"stage": "rehearsal_repair", "cards": REHEARSAL, "launchable": False,
             "state": "accepted originals (rehearsal v3) preserved unchanged; exempt from style edits"},
            {"stage": "style_microbatch", "cards": MICROBATCH, "launchable": True, "requires_accepted_stage": None},
            {"stage": "remaining_cards", "cards": rest, "launchable": False, "requires_accepted_stage": "style_microbatch",
             "blocked_reason": "needs the accepted micro-batch text as context; a later pack-set version will make this stage launchable"},
            {"stage": "complete_case_review", "cards": [], "launchable": False, "blocked_reason": "independent review after all 24 remaining cards"}]}
    return {"case": case, "pack_set_version": PACK_SET_VERSION, "stages": [
        {"stage": "whole_journey", "cards": cards, "launchable": True, "requires_accepted_stage": None,
         "note": "only after the case-11 stages have passed independent review and the Data Engineer authorises this case"}]}


# --------------------------------------------------------------------------- build
def pack_digest(root):
    """Digest of every pack file except the manifest itself (so the manifest can record it)."""
    root = Path(root)
    files = {str(f.relative_to(root)): sha(f.read_bytes()) for f in sorted(root.rglob("*")) if f.is_file() and f.name != "PACK-MANIFEST.json"}
    return sha(json.dumps(files, sort_keys=True).encode()), files


def approved_clarification_doc(case, plan_raw):
    """Entries for this member. Case 11 carries the Principal Data Engineer's approved rulings with provenance, bound to the frozen plan hash."""
    base = {"kind": "approved_clarifications", "version": 1,
            "entry_shape": {"id": "CL-<n>", "card": "<card id>", "status": "approved", "approved_by": "<name and role>", "approved_on": "<date>",
                            "required_tokens": ["<optional exact strings>"], "forbidden_tokens": ["<optional>"], "waive_required": ["<derived requirement label to waive>"],
                            "native_columns": {"<register column>": "<value>"}},
            "applied_by": "the author check and the renderer share one resolution of these entries"}
    if case != 11:
        return {**base, "clarifications": [], "note": "Empty: no clarification has been issued for this member."}
    raw = CLARIFICATIONS_SOURCE.read_bytes()
    src = json.loads(raw)
    if src["bound_plan_sha256"] != sha(plan_raw) or src["bound_case"] != case:
        raise RuntimeError("the approved clarifications are not bound to this frozen plan")
    return {**base, "clarifications": src["clarifications"],
            "provenance": {"source": "Artha/Data/reviews/worker-readiness-v2-2026-10-15/CASE-11-APPROVED-CLARIFICATIONS.json", "source_sha256": sha(raw),
                           "issued_by": src["issued_by"], "issued_on": src["issued_on"], "bound_plan_sha256": src["bound_plan_sha256"], "review": src["review"],
                           "note": "Approved authoring inputs. They authorise no worker and change no frozen plan."}}


def build_pack(case, plans, assign, profiles, common, out_root, outputs_root):
    p = plans[case]
    pd = p["data"]
    stem = p["path"].stem
    root = Path(out_root) / stem
    if root.exists():
        raise FileExistsError(f"{root} exists; packs are built only into a fresh directory")
    for sub in ("plan", "style", "shared", "context"):
        (root / sub).mkdir(parents=True)
    shutil.copy(p["path"], root / "plan/assistant-pack.json")
    cs = glob.glob(str(SYNTH / f"pilot-v2-planning-2026-10-13/case-studies/{case:02d}-C-*.md"))[0]
    shutil.copy(cs, root / "plan/case-study.md")
    clar_doc = approved_clarification_doc(case, p["raw"])
    (root / "plan/APPROVED-CLARIFICATIONS.json").write_text(json.dumps(clar_doc, indent=1) + "\n")
    style = _style_for(case, pd["case_id"], assign, profiles)
    (root / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").write_text(json.dumps({k: style[k] for k in ("kind", "version", "seed", "case_reference", "cards", "attachment_briefs")}, indent=1) + "\n")
    (root / "style/STYLE-PROFILES.json").write_text(json.dumps({k: style[k] for k in ("profiles", "actor_voices", "protected_content_rules", "allowed_error_scope")}, indent=1) + "\n")
    stage_doc = stage_doc_for(case, pd)
    (root / "STAGE.json").write_text(json.dumps(stage_doc, indent=1) + "\n")
    (root / "shared/WORKER-GUIDE.md").write_text(guide_markdown(case, pd, stage_doc, style))
    (root / "shared/RETURN-SCHEMAS.md").write_text(returns_markdown())
    (root / "shared/RETURN-SCHEMAS.json").write_text(json.dumps({"kinds": contract.SCHEMAS, "supervisor_supplied": contract.CENTRAL,
                                                               "kind_for_plan_kind": contract.KIND_MAP,
                                                               "unsupported_plan_kinds": contract.UNSUPPORTED_PLAN_KINDS,
                                                               "unsupported_formats": contract.UNSUPPORTED_FORMATS}, indent=1, sort_keys=True) + "\n")
    common = dict(common)
    common["procedures"] = {"status": "no stand-alone procedure corpus has been supplied",
                            "use": "Procedure-like facts available are exactly the case's own writer_constraints, source_assertions, work_entries, external_matters, story and approved clarifications. A rule not present there is an open dependency: raise a question, do not invent one."}
    (root / "shared/COMMON-REFERENCES.json").write_text(json.dumps(common, indent=1) + "\n")
    launch_cards = [c for s in stage_doc["stages"] if s.get("launchable") for c in s["cards"]]
    ctx = accepted_prior_originals(pd, launch_cards) if case == 11 else None
    if ctx:
        (root / "context/ACCEPTED-PRIOR-ORIGINALS.json").write_text(json.dumps(ctx, indent=1, ensure_ascii=False) + "\n")
    else:
        (root / "context").rmdir()
    assets = []
    for a in pd["assets"]:
        how = ("not_rendered_advertised_only_no_bytes" if a.get("delivery_state") != "held" else
               "authoring_required_eml" if a["media_type"] == "message/rfc822" else "supervisor_renders_from_locked_spec")
        assets.append({"asset_id": a["asset_id"], "filename": a["filename"], "media_type": a["media_type"], "delivery_state": a.get("delivery_state"),
                       "copy_of_asset_id": a.get("copy_of_asset_id"), "production": how,
                       "style_profile": next((b["profile_id"] for b in style["attachment_briefs"] if b["asset_ref"] == a["asset_id"]), None)})
    (root / "assets-to-render.json").write_text(json.dumps({"note": "Attachments are produced once by the supervisor; accepted ones are reused byte for byte.", "assets": assets}, indent=1) + "\n")
    (root / "RETURN-CHECKLIST.json").write_text(json.dumps({"case": case, "cards": [
        {"card": a["artifact_ref"], "kind": a["kind"], "event_at": a["event_at"], "status": "not_started", "authored_file": None, "questions": []}
        for a in pd["artifacts"] if a["artifact_ref"] in launch_cards],
        "note": "IMMUTABLE TEMPLATE. The worker copies this file into its owned output folder and completes the copy there; supervisor and independent reviewers decide acceptance."}, indent=1) + "\n")
    out_rel = f"{OUTPUTS_DIRNAME}/{stem}"
    (root / "INSTRUCTIONS.md").write_text(instructions_markdown(case, pd, stage_doc, out_rel))
    for s in stage_doc["stages"]:
        if s.get("launchable"):
            d = Path(outputs_root) / stem / s["stage"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "README.txt").write_text(f"Owned output folder for case {case:02d}, stage {s['stage']}. Only the worker assigned to that stage writes here.\n")
    return root


def verify_pack(root, plans):
    """Isolation and integrity checks for one built pack. Returns [(check, ok, detail)]."""
    from .workpack import verify_pack as v1
    root = Path(root)
    res = [r for r in v1(root, plans) if not r[0].startswith("generated pack files contain no control")]
    banned, outside = [], []
    for f in root.rglob("*"):
        if f.is_file() and f.name != "PACK-MANIFEST.json":
            txt = f.read_text(errors="ignore")
            rel = str(f.relative_to(root))
            if rel.startswith(("shared/", "STAGE", "INSTRUCTIONS")) or f.name == "assets-to-render.json":
                if PACK_BANNED.search(txt):
                    banned.append(rel)
                if OUT_OF_PACK.search(txt):
                    outside.append(rel)
    res.append(("generated pack files contain no control/evaluation vocabulary", not banned, str(banned)))
    res.append(("generated instructions have no out-of-pack links or retired-workflow references", not outside, str(outside)))
    guide = (root / "shared/WORKER-GUIDE.md").read_text()
    res.append(("exactly one writing guide; superseded guide files absent", not (root / "shared/NONTECHNICAL-ARTIFACT-WRITER-GUIDE.md").exists()
                and not (root / "shared/WRITER-INSTRUCTIONS.md").exists() and not (root / "shared/AUTHORED-SCHEMA.md").exists() and "superseded" not in guide.lower(), ""))
    st = json.loads((root / "STAGE.json").read_text())
    res.append(("stage document lists at least one stage and every launchable stage names its cards", all(s.get("cards") or not s.get("launchable") for s in st["stages"]), ""))
    return res


def build_all(out_root=None, cases=None):
    out_root = Path(out_root) if out_root else SYNTH / PACK_SET_DIRNAME
    outputs_root = out_root.parent / OUTPUTS_DIRNAME
    plans = _plans()
    assign = json.loads((ADDENDUM / "ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    profiles = json.loads((ADDENDUM / "STYLE-PROFILES.json").read_text())
    fp = {Path(x["path"]).name: x["sha256"] for x in assign["base_plan_fingerprints"]}
    for n, p in plans.items():
        if fp[p["path"].name] != sha(p["raw"]):
            raise RuntimeError(f"case {n}: plan changed since style assignment (fingerprint mismatch)")
    if out_root.exists():
        raise FileExistsError(f"{out_root} exists; a pack set is built once into a fresh directory")
    common = common_references(plans)
    out_root.mkdir(parents=True)
    index = []
    for n in sorted(cases or plans):
        root = build_pack(n, plans, assign, profiles, common, out_root, outputs_root)
        checks = verify_pack(root, plans)
        digest, files = pack_digest(root)
        entry = {"case": n, "pack": root.name, "pack_set_version": PACK_SET_VERSION, "pack_digest": digest, "files": files,
                 "input_hashes": {
                     "plan_sha256": sha((root / "plan/assistant-pack.json").read_bytes()),
                     "case_study_sha256": files["plan/case-study.md"], "style_assignments_sha256": files["style/ARTIFACT-STYLE-ASSIGNMENTS.json"],
                     "style_profiles_sha256": files["style/STYLE-PROFILES.json"], "clarifications_sha256": files["plan/APPROVED-CLARIFICATIONS.json"],
                     "guide_sha256": files["shared/WORKER-GUIDE.md"], "return_schemas_sha256": files["shared/RETURN-SCHEMAS.json"],
                     "accepted_prior_context_sha256": files.get("context/ACCEPTED-PRIOR-ORIGINALS.json")},
                 "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks], "launch_status": "not_authorised"}
        (root / "PACK-MANIFEST.json").write_text(json.dumps(entry, indent=1) + "\n")
        index.append({"case": n, "pack": root.name, "pack_digest": digest, "plan_sha256": entry["input_hashes"]["plan_sha256"],
                      "all_checks_ok": all(c["ok"] for c in entry["checks"]), "file_count": len(files) + 1,
                      "launchable_stages": [s["stage"] for s in json.loads((root / "STAGE.json").read_text())["stages"] if s.get("launchable")],
                      "blocked": BLOCKED_CASES.get(n)})
    body = {"kind": "worker_pack_index", "pack_set_version": PACK_SET_VERSION, "launch_authorised": False,
            "supersedes": "worker-packs-v2 (pack set 2) and worker-packs (pack set 1); both preserved unchanged", "packs": index}
    (out_root / "PACKS-INDEX.json").write_text(json.dumps(body, indent=1) + "\n")
    write_inventory(out_root)
    (out_root / "SUPERVISOR-LAUNCH-PROTOCOL.md").write_text(protocol_markdown())
    (out_root / "KNOWN-ISSUES.md").write_text(known_issues_markdown(plans))
    return index


def write_inventory(out_root):
    """PACK-INVENTORY.json: per-pack digest and the plan/style/clarification/guide/schema hashes (outside every pack digest)."""
    out_root = Path(out_root)
    idx = json.loads((out_root / "PACKS-INDEX.json").read_text())
    rows = []
    for r in idx["packs"]:
        man_path = out_root / r["pack"] / "PACK-MANIFEST.json"
        m = json.loads(man_path.read_text())
        rows.append({"case": r["case"], "pack": r["pack"], "pack_digest": r["pack_digest"], "manifest_sha256": sha(man_path.read_bytes()),
                     **m["input_hashes"], "launchable_stages": r["launchable_stages"], "blocked": r["blocked"], "files": r["file_count"]})
    inv = {"kind": "pack_set_inventory", "pack_set_version": idx["pack_set_version"], "packs_index_sha256": sha((out_root / "PACKS-INDEX.json").read_bytes()), "packs": rows}
    (out_root / "PACK-INVENTORY.json").write_text(json.dumps(inv, indent=1) + "\n")
    return inv


def write_current_pointer(out_root=None, pointer_path=None, history_note="pack set built and made current; no stage authorised", supersede=False):
    """Point at the current pack set. An existing pointer is never overwritten silently: with supersede=True the previous pointer is
    archived byte for byte beside it (WORKER-PACKS-CURRENT.history/) and listed in the new pointer's superseded/history entries."""
    out_root = Path(out_root) if out_root else SYNTH / PACK_SET_DIRNAME
    inv = json.loads((out_root / "PACK-INVENTORY.json").read_text())

    def rel(p):
        try:
            r = str(Path("Artha") / Path(p).resolve().relative_to(ROOT.resolve()))
        except ValueError:
            r = str(p)
        return r + ("/" if Path(p).is_dir() else "")
    target = Path(pointer_path) if pointer_path else CURRENT_POINTER
    superseded = [{"pack_set_version": 1, "path": "Artha/Data/synthesis/worker-packs/", "outputs_path": "Artha/Data/synthesis/worker-outputs/",
                   "status": "preserved byte for byte and superseded for any launch (conflicting guide, incomplete schemas, unvalidated gate); do not use"}]
    history = [{"event": history_note}]
    if target.exists():
        if not supersede:
            raise FileExistsError(f"{target} exists; pass supersede=True to archive it and write a new pointer revision")
        old_raw = target.read_bytes()
        old = json.loads(old_raw)
        hist_dir = target.parent / (target.stem + ".history")
        hist_dir.mkdir(exist_ok=True)
        arch = hist_dir / f"pointer-pack-set-{old['pack_set_version']}-sha256-{hashlib.sha256(old_raw).hexdigest()[:12]}.json"
        if not arch.exists():
            arch.write_bytes(old_raw)
        superseded = old.get("superseded", []) + [{"pack_set_version": old["pack_set_version"], "path": old["path"], "outputs_path": old["outputs_path"],
                                                    "packs_index_sha256": old["packs_index_sha256"], "pointer_archived_as": rel(arch),
                                                    "status": "preserved unchanged; superseded by the successor pack set (WR1-WR4 repairs); do not use"}]
        history = old.get("history", []) + history
    ptr = {"kind": "current_worker_pack_set", "pack_set_version": inv["pack_set_version"], "path": rel(out_root),
           "outputs_path": rel(out_root.parent / OUTPUTS_DIRNAME), "packs_index_sha256": inv["packs_index_sha256"],
           "pack_inventory": rel(out_root / "PACK-INVENTORY.json"), "progress_ledger": rel(out_root / "PROGRESS.json"),
           "launch_authorised": False, "authorised_stages": [],
           "readiness_review": "pending: submitted for independent review; no sign-off recorded",
           "superseded": superseded, "history": history}
    target.write_text(json.dumps(ptr, indent=1) + "\n")
    return ptr


def protocol_markdown():
    return f"""# Supervisor launch protocol - pack set {PACK_SET_VERSION}

Owner: Synthetic Data coding assistant (the only party that launches authoring workers). **Status: no stage is authorised.** The ledger lists no approval;
every job is `queued`. Building packs and tooling authorises nothing.

## Before any launch
1. Independent readiness review of this pack set and its tooling, then a separate stage sign-off from the Principal Data Engineer.
2. The sign-off is an external JSON record saved under `Data/reviews/` (never in a pack or output folder), shaped exactly like this and bound to the pack digest in `PACKS-INDEX.json`:
   `{{"kind": "authoring_stage_signoff", "schema_version": 1, "decision": "approved", "reviewer": "<name>", "reviewer_role": "<role>", "decided_at": "<ISO time, not in the future>",
   "case": 11, "stage": "style_microbatch", "pack": "<pack folder name>", "pack_digest": "<digest>", "pack_set_version": {PACK_SET_VERSION}}}`
   A different kind of review (for example the original-artifact acceptance), a pending record, a record for another case/stage/pack digest, one issued by the assistant, or one outside `Data/reviews/` is refused.
3. Record it: `progress.authorise(case, stage, <record path>, <pack-set root>)`. It validates and stores the record's sha256; it starts nothing.

## Launch (one fresh subagent per stage; at most two at once in the first trial)
- `progress.set_state(case, stage, "working", root)` - the stage is mandatory on every start AND resume. It first verifies the ledger's integrity (the state payload must match its last event), then re-validates the approval record, re-hashes the pack, checks the prerequisite stage, one active stage per case and the worker cap.
- Take `progress.snapshot_tree([Data/, Implementation/])` first. Spawn one subagent whose prompt contains only the pack's `INSTRUCTIONS.md` text, the pack folder path and the owned folder path
  (`worker-outputs-v2/<pack>/<stage>/`). Do not forward this conversation, other packs, review reports, tracker text or evaluation material. Fresh context is an instruction boundary, not a sandbox.
- Afterwards `progress.scope_audit(before, after, owned, concurrent_owned=[...])`. It audits WRITES by file hash. It cannot show that a worker did not READ another folder, and under concurrent workers a change
  inside another worker's folder is reported as unattributable. Any violation or symlink escape is a defect to report.

## While working
`queued -> working <-> question_raised -> submitted -> repair_required -> working ... -> independently_accepted`, separately for each stage of a pack.
- Check every returned card: `sdgen author-check <card>.json --pack <pack> --stage <stage>` (passing is not acceptance).
- `submitted` needs the worker's completed `RETURN-CHECKLIST.json` copy in the owned folder and records the digest of every file and directory there (no symlinks). At `independently_accepted` the folder and pack are re-hashed: any changed, added, deleted or missing file refuses the acceptance, moves the stage to `repair_required` (history kept) and needs resubmission and a fresh record. Acceptance needs an external `authoring_stage_acceptance` record (`decision: accepted`) bound to the same case/stage/pack digest AND the current output digest. The supervisor cannot self-accept.
- `sdgen render-card` renders one valid return into a NEW folder. `sdgen package-stage --pack --stage --outputs --out-root --name --accepted-package <v3>` author-checks every returned card, renders it, reuses accepted attachment bytes exactly, keeps private dialogue and controls under `restricted/`, verifies the staged package and publishes it with the kernel no-replace rename (any existing target is refused; the verified stage is kept). The package is pending independent review; it is not the rehearsal's 377-check package.
- `remaining_cards` stays non-launchable in this pack set; after the micro-batch is accepted a new pack-set version carries its accepted text as context.

## Not authorised now
Every stage, every case, the remaining seventeen case-11 cards, other members, private paired robustness prose, gold and the live generator. Pack sets 1 and 2 are superseded and must not be used.
"""


def known_issues_markdown(plans):
    kinds = {}
    for p in plans.values():
        for a in p["data"]["artifacts"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
            if a["format"] in contract.UNSUPPORTED_FORMATS:
                kinds["scanned_letter"] = kinds.get("scanned_letter", 0) + 1
    eml = sum(1 for p in plans.values() for a in p["data"]["assets"] if a.get("delivery_state") == "held" and a["media_type"] == "message/rfc822")
    return f"""# Known issues and limits - pack set {PACK_SET_VERSION} (recorded, not fixed here)

## Blocked or missing inputs
- **Case 32 (tracker D09, Data Engineer).** The 15 June reply card cannot know the 16 June closure, the 23 June clarification or the 24 June reopening, and the 23 June contact cannot know the later reopening. The pack is a faithful copy of the
  frozen plan; its only stage is non-launchable and the ledger refuses an approval for it until a corrected, versioned brief is issued and reviewed.
- **No procedure corpus.** Packs carry only each case's own work entries, matters, story and approved clarifications. A rule not there is an open dependency (guide section 4). `plan/APPROVED-CLARIFICATIONS.json` is empty for every member.
- **Case 11 rulings applied (approved 15 Oct 2026).** 11-D03 carries the approved member reference, product, channel, tag and manager name in the real CSV; 11-D13 is an email decision with its existing authority attachment; 11-D11's request reference is validated in the native header, not repeated in prose. Provenance and the source hash are in `plan/APPROVED-CLARIFICATIONS.json`.
- **11-D11 depends on 11-D08**, which is not written in this stage; the card's held facts are the only source.

## Contract gaps (explicit, not guessed)
- Plan kinds with no return shape or renderer: `ledger` ({kinds.get('ledger', 0)} cards across the forty plans, CSV/XLSX from financial events), `external` ({kinds.get('external', 0)} cards, EML plus PDF notice), standalone scanned/text-layer letters ({kinds.get('scanned_letter', 0)} cards).
  These cards cannot pass the author check's kind rule; they belong to later stages.
- {eml} held EML attachments in the plans need authored prose and have no worker return kind.
- Forwarded messages are not part of the worker contract (a quote block copies exact accepted text instead).
- Portal, register and note output is packaged by `sdgen.microbatch` (allowlist, private separation, exact accepted-attachment reuse, no-replace publication) with its own, smaller verification set; it is not the rehearsal's `Batch.release` and its 377 checks, and a package it publishes still needs independent review. The portal plain-text preview is not produced.
- The letterhead/footer exist only for complaints-team letters; any other sender is an explicit gap.

## Check limits
- Derived checks are mechanical: fixed references, amounts, dates, decision-maker names, filenames, native values, a complaint id before its receipt, foreign identifiers and tokens that first appear later. They do not see paraphrased future knowledge, meaning, voice, chronology or support.
- Required values taken from `writer_constraints` are conservative; a requirement that should not apply needs an approved clarification with `waive_required`.
- Only the five approved misspellings are counted; other misspellings cannot be detected (no dictionary). The control-vocabulary pattern is a heuristic.
- `plan/case-study.md` and the plan contain the member's whole planned story, later events included (the plan fingerprint binds the style assignments to the frozen plan). The guide forbids later knowledge; only reference tokens are guarded mechanically.
- Approval and acceptance records are validated for form, binding and issuer, not authenticated. The ledger now binds the state payload to its events and refuses to proceed after a detected edit, but anyone able to rewrite `PROGRESS.json` and the event log consistently is not stopped (no authentication). Pack set 2's ledger is a legacy format without state binding: it is read-only history and is never migrated in place.
- The ledger's lock is `fcntl.flock` on one host and filesystem; it was exercised on macOS only.

## Isolation
Fresh context per subagent is an instruction boundary. There is no per-folder OS sandbox. The scope audit compares file hashes before and after, so it audits writes, not reads.

## Attachments
Deterministic bytes are not realism. The content sweep shows every locked value of all 351 held non-EML attachments is readable in the output (after the CSV/XLSX header-versus-row column collision found by the sweep was fixed); layout was inspected by eye on a sample only.
PNG screenshot rows print values without column labels; the layout is functional rather than an authentic system screen.
"""
