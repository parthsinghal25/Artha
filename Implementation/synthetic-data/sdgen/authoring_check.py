"""Author-side check of a worker's returned card file against its pack (W03).

Passing means only: the return has the declared shape, carries the fixed values the TRUSTED card requires, contains no
forbidden/foreign/future token, keeps within the profile's typo ceiling by counting occurrences, quotes earlier text exactly,
and leaks no control vocabulary. It is NOT acceptance and does not establish meaning, voice, chronology or factual support.
Writer-supplied `required_tokens`/`must_not_match` can only ADD checks; `protected_values_checked` is an attestation that is
recorded, never treated as evidence."""
import json
import re
from pathlib import Path

from .contract import (approved_clarifications, CONTROL_RE, EMAIL_KINDS, NATIVE_KINDS, SCHEMAS, allowed_kinds, authored_text, count_typos, derive_requirements,
                       private_text, quoted_text, schema_errors, APPROVED_TYPOS)
from .plan import CasePlan


def load_pack(pack_dir):
    pack = Path(pack_dir)
    plan_path = pack / "plan/assistant-pack.json"
    data = json.loads(plan_path.read_text())
    plan = CasePlan(data["case_number"], plan_path)
    sty = json.loads((pack / "style/ARTIFACT-STYLE-ASSIGNMENTS.json").read_text())
    prof = json.loads((pack / "style/STYLE-PROFILES.json").read_text())["profiles"]
    stage_doc = json.loads((pack / "STAGE.json").read_text())
    clar_path = pack / "plan/APPROVED-CLARIFICATIONS.json"
    clars = json.loads(clar_path.read_text()).get("clarifications", []) if clar_path.exists() else []
    prior_path = pack / "context/ACCEPTED-PRIOR-ORIGINALS.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {"originals": []}
    plan.clarifications = approved_clarifications(clars)  # the renderer reads the same approved list the check does
    # an approved effective-asset ruling replaces the planned asset in memory, for the check, the renderer and packaging alike
    from . import effective_spec
    eff_path = pack / "plan/EFFECTIVE-ASSET-SPECS.json"
    if effective_spec.projections(clars):
        if not eff_path.exists():
            raise effective_spec.RulingMismatch("an approved effective-asset ruling exists but the pack has no plan/EFFECTIVE-ASSET-SPECS.json")
        if json.loads(eff_path.read_text()) != effective_spec.document(data, plan.sha256, clars):
            raise effective_spec.RulingMismatch("plan/EFFECTIVE-ASSET-SPECS.json differs from the projection of the frozen plan and the approved ruling")
        effective_spec.apply(plan, clars)
    elif eff_path.exists():
        raise effective_spec.RulingMismatch("plan/EFFECTIVE-ASSET-SPECS.json exists without an approved ruling that defines it")
    return plan, sty, prof, stage_doc, clars, prior


def _eol(t):
    """The one documented convention for quotation: CRLF and LF are equal. Spaces, tabs and blank lines are compared exactly."""
    return t.replace("\r\n", "\n")


def _norm(t):
    return re.sub(r"\s+", " ", t)


def author_check(authored_path, pack_dir, stage):
    """Returns [{id, ok, detail}]. `stage` is mandatory: a card is only checkable inside a stage that lists it."""
    out = []

    def add(cid, ok, detail=""):
        out.append({"id": cid, "ok": bool(ok), "detail": detail})

    plan, sty, prof, stage_doc, clars, prior = load_pack(pack_dir)
    try:
        d = json.loads(Path(authored_path).read_text())
    except (OSError, ValueError) as e:
        add("A0", False, f"return is not readable JSON: {e}")
        return out
    if not isinstance(d, dict):
        add("A0", False, "return must be a JSON object")
        return out
    card = d.get("card")
    st = next((s for s in stage_doc["stages"] if s["stage"] == stage), None)
    add("A1a", st is not None and st.get("launchable") is True,
        f"stage {stage!r} is a launchable stage of this pack" + ("" if st else f" (stages: {[s['stage'] for s in stage_doc['stages']]})"))
    art = plan.artifacts.get(card)
    row = next((r for r in sty["cards"] if r["artifact_ref"] == card), None)
    add("A1b", art is not None and row is not None, f"card {card} belongs to this pack and has a style row")
    if not (st and st.get("launchable") and art and row):
        return out
    add("A1c", card in st["cards"], f"card {card} belongs to stage {stage}")
    add("A1d", not row.get("rehearsal_exact_repair_or_accepted_source_exempt"), "card is not an accepted rehearsal original (those are never re-authored)")
    # ---- shape and kind
    errs = schema_errors(d)
    if errs:
        for e in errs:
            add("A2", False, e)
        return out
    add("A2", True, f"{d['kind']}: structure matches the declared return schema")
    kinds, gap = allowed_kinds(art)
    add("A3", d["kind"] in kinds, f"kind {d['kind']} allowed for this card {kinds}" + (f" ({gap})" if gap else ""))
    if d["kind"] not in kinds:
        return out
    kind = d["kind"]
    add("A4a", d["card"] == card and (d.get("style_completion") or {}).get("card") == card, "card id repeated consistently in style_completion")
    # ---- references to other cards
    for field in ("in_reply_to_card",):
        if field in d:
            tgt = plan.artifacts.get(d[field])
            add("A4b", tgt is not None and tgt["event_at"] < art["event_at"], f"{field} {d[field]} is an earlier card of this member")
    for c in d.get("references_cards", []):
        tgt = plan.artifacts.get(c)
        add("A4b", tgt is not None and tgt["event_at"] < art["event_at"], f"references_cards {c} is an earlier card of this member")
    # ---- quoted originals (exact copies, only from accepted earlier text)
    # quotable text only: a letter's text is extracted from its PDF for reading, so it is never a basis for an exact quotation
    usable = {o["card"]: o for o in prior.get("originals", []) if card in o.get("usable_before_cards", []) and o.get("quotable", True)}
    for b in (x for x in d.get("blocks", []) if "quote" in x):
        src = usable.get(b["from_card"])
        add("A5", bool(src) and _eol(b["quote"]) in _eol(src["text"]),
            f"quote from {b['from_card']} is a character-exact passage of accepted text available to this card (line endings normalised only)"
            + ("" if src else f" (usable sources: {sorted(usable)})"))
    # ---- text under check: new prose only
    text = authored_text(d)
    priv = private_text(d)
    low = _norm(text).lower()
    # ---- derived (trusted) requirements
    named = []
    if kind in EMAIL_KINDS:
        named = [plan.assets[a]["filename"] for a in art.get("attachments", []) if plan.assets[a].get("delivery_state") == "held"]
    req = derive_requirements(plan, art, kind, approved_clarifications(clars), named)
    from .card_adapter import render_card
    if kind == "register_export":
        rendered = render_card(d, plan, art)  # the real renderer, not a checker-side reconstruction
        native = next(iter(rendered.files.values())).decode()
        header = native.splitlines()[0].split(",") if native else []
        for g in req.native:
            add("A6n", g.found_in(native), f"rendered native record carries value: {g.label}")
        from .contract import native_extra_columns
        for col in native_extra_columns(art, approved_clarifications(clars)):
            add("A6n", col in header, f"rendered native record has the approved column header: {col}")
    if kind == "ledger_export":
        from .card_adapter import ContractGap, native_cells
        try:
            rendered = render_card(d, plan, art)  # the real renderer: the export file is the original
        except ContractGap as e:
            add("A6x", False, f"the native export cannot be rendered as planned: {e}")
            rendered = None
        if rendered is not None:
            from .contract import ledger_assets
            media = {f"originals/ledgers/{a['filename']}": a["media_type"] for a in ledger_assets(plan, art)}
            cells = [c for rel, data in rendered.files.items() for c in native_cells(data, media[rel])]
            add("A6x", True, f"rendered {len(rendered.files)} native export file(s) with no staging/authoring label")
            for g in req.native:
                add("A6n", any(g.found_in(c) for c in cells), f"rendered native export carries value: {g.label}")
    haystack = _norm(text + "\n" + quoted_text(d))
    for g in req.required:
        add("A6", g.found_in(haystack), f"required fixed value present ({g.source}): {g.label}")
    allhay = haystack + "\n" + priv
    for label, rx, source in req.forbidden:
        add("A7", rx.search(allhay) is None, f"forbidden ({source}): {label}")
    # writer-added checks (additive only)
    for tok in d.get("required_tokens", []):
        add("A8a", _norm(tok).lower() in haystack.lower(), f"writer-declared required token present: {tok}")
    for pat in d.get("must_not_match", []):
        try:
            add("A8b", not re.search(pat, allhay, re.I), f"writer-declared forbidden pattern absent: {pat}")
        except re.error as e:
            add("A8b", False, f"writer-declared pattern is not a valid regular expression: {e}")
    # ---- call-specific consistency
    if kind == "call_summary":
        rendered = render_card(d, plan, art)
        head = next(v for k, v in rendered.files.items() if k.endswith(".txt")).decode().split("\nSUMMARY")[0]
        add("A6h", plan.request_reference in head and plan.case_id in head,
            "the rendered native header carries the trusted case and request references (not repeated in the summary prose)")
        for g in req.header:
            add("A6h", g.found_in(head), f"rendered header carries waived value: {g.label} ({g.source})")
        names = {plan.actor(p)["name"] for p in art["participants"]} | set(art["participants"])
        turns = {t["n"]: t for t in d["conversation"]}
        add("A9a", sorted(turns) == list(range(1, len(turns) + 1)), "conversation turns are numbered 1..n without gaps")
        add("A9b", {t["speaker"] for t in d["conversation"]} <= names, f"every speaker is one of the card's participants {sorted(names)}")
        bad = []
        for cl in d["fidelity"]["claims"]:
            for n in cl["turns"]:
                if n not in turns:
                    bad.append(f"claim cites missing turn {n}")
                    continue
                for s in cl["check_strings"]:
                    if not any(_norm(s).lower() in _norm(turns[m]["text"]).lower() for m in cl["turns"] if m in turns):
                        bad.append(f"check string {s!r} not in cited turns {cl['turns']}")
        add("A9c", not bad, "fidelity check_strings occur in the turns they cite" + (": " + "; ".join(sorted(set(bad))) if bad else ""))
        words = len(d["summary"].split())
        add("A9d", 20 <= words, f"summary has {words} words (the summary must be written from the conversation, not a stub)")
    # ---- style: typo occurrences vs ceiling vs declaration
    sc = d["style_completion"]
    add("A10a", sc.get("applied_profile") == row["profile_id"], f"style_completion binds profile {row['profile_id']}")
    cap = prof[row["profile_id"]].get("max_harmless_typos", 0)
    declared = {}
    for t in sc.get("typos_used", []):
        ok = APPROVED_TYPOS.get(t["from"]) == t["to"]
        add("A10b", ok, f"declared typo {t['from']}->{t['to']} is on the approved list")
        declared[t["to"]] = declared.get(t["to"], 0) + 1
    found = count_typos(text)
    total = sum(found.values())
    add("A10c", total <= cap, f"{total} misspelling occurrence(s) in newly authored text {found}; ceiling {cap} (counts occurrences, not distinct spellings)")
    add("A10d", found == declared, f"occurrences in the text {found} equal those declared {declared}")
    if kind in NATIVE_KINDS or row["document_class"] in ("native_register_export", "ledger_export", "native_or_structured"):
        add("A10e", total == 0 and not sc.get("typos_used"), "native/literal record has no deliberate errors")
    nonclean = prof[row["profile_id"]].get("difficulty") not in ("clean",)
    if kind not in NATIVE_KINDS and nonclean:
        add("A10f", len(sc.get("features_used", [])) >= 1, "a non-clean profile declares at least one used feature (attestation; not evidence)")
    # ---- control vocabulary anywhere in text or private dialogue
    m = CONTROL_RE.search(text + "\n" + priv)
    add("A11", m is None, "no control/authoring vocabulary in any document text or dialogue" + (f": {m.group(0)!r}" if m else ""))
    # ---- attestation recorded, never evidence
    add("A12", sc.get("protected_values_checked") is True, "author attests protected values checked (attestation only; derived checks above are the evidence)")
    return out
