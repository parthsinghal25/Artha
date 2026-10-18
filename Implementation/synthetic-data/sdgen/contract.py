"""The ONE worker return contract (W01) and the trusted-brief requirement derivation (W03).

What a worker returns is described by JSON Schemas here; the pack's RETURN-SCHEMAS.json/.md and the guide are generated
from the same objects, `author_check` validates against them, and `card_adapter` turns a valid return into renderer input
by adding the supervisor-supplied (central) values. So the documented shape, the checked shape and the rendered shape
cannot drift apart.

Requirements (required values, allowed kind, forbidden tokens) are derived from the pack's own card
(`source_assertions`, `writer_constraints`, actors, assets) and from APPROVED clarification records - never from the
writer's own `required_tokens` list, which can only ADD checks. Derived checks are mechanical: they catch missing or wrong
fixed values, wrong kinds, future-knowledge tokens and foreign references. They do not establish meaning, voice, chronology
or factual support; independent review still decides those."""
import re
from datetime import date, datetime

from .textfmt import MONTHS

# ------------------------------------------------------------------ kinds
# plan artifact kind -> authored return kinds allowed for it (before the attachment filter)
KIND_MAP = {
    "email": ["email"],
    "portal": ["portal_json"],
    "register": ["register_export"],
    "call": ["call_summary"],
    "approval": ["email", "note"],
    "response": ["letter_with_cover_email"],
    "note": ["note"],
    # an external body's message: the worker writes the EML text; the notice/outcome PDF it carries is a held asset rendered
    # from its locked spec (no authored PDF)
    "external": ["email"],
    # a native ledger/effort export: the held CSV/XLSX asset IS the original, rendered from its locked rows; nothing to write
    "ledger": ["ledger_export"],
}
# plan kinds/formats for which no renderer or return shape exists yet: an explicit contract gap, never a guess
UNSUPPORTED_PLAN_KINDS = {}
NATIVE_KINDS = ("register_export", "ledger_export")
# a staging card reference ("11-D03") is an authoring label; it never belongs in a native record
STAGING_CARD_REF = re.compile(r"\b\d{2}-D\d{2}\b")
UNSUPPORTED_FORMATS = {
    "Scanned or text-layer PDF letter": "standalone letter without a covering email has no return shape yet",
}
EMAIL_KINDS = ("email", "letter_with_cover_email")

APPROVED_TYPOS = {"please": "pleae", "thanks": "thnaks", "message": "mesage", "question": "quesiton", "attachment": "attachement"}
# a misspelling occurrence: the approved wrong form, optionally pluralised ("mesages")
TYPO_RE = re.compile(r"\b(pleae|thnaks|mesage|quesiton|attachement)(s?)\b", re.I)

# ------------------------------------------------------------------ schemas (what the WORKER returns)
STYLE_COMPLETION = {
    "type": "object", "additionalProperties": False,
    "required": ["card", "applied_profile", "features_used", "typos_used", "protected_values_checked", "concerns"],
    "properties": {
        "card": {"type": "string"}, "applied_profile": {"type": "string"},
        "features_used": {"type": "array", "items": {"type": "string"}},
        "typos_used": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "to"],
            "properties": {"from": {"enum": sorted(APPROVED_TYPOS)}, "to": {"enum": sorted(APPROVED_TYPOS.values())}}}},
        "protected_values_checked": {"type": "boolean"},
        "concerns": {"type": "array", "items": {"type": "string"}}}}

_BLOCK = {"oneOf": [
    {"type": "object", "additionalProperties": False, "required": ["p"], "properties": {"p": {"type": "string", "minLength": 1}}},
    {"type": "object", "additionalProperties": False, "required": ["ul"], "properties": {"ul": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}},
    {"type": "object", "additionalProperties": False, "required": ["ol"], "properties": {"ol": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}},
    {"type": "object", "additionalProperties": False, "required": ["quote", "from_card"],
     "properties": {"quote": {"type": "string", "minLength": 1}, "from_card": {"type": "string"}}}]}

_COMMON = {
    "card": {"type": "string"}, "revision": {"type": "integer", "minimum": 1}, "kind": {"type": "string"},
    "style_completion": STYLE_COMPLETION,
    "required_tokens": {"type": "array", "items": {"type": "string"}},
    "must_not_match": {"type": "array", "items": {"type": "string"}},
    "authoring_meta": {"type": "object"},
}
_COMMON_REQ = ["card", "revision", "kind", "style_completion"]

_EMAIL_PROPS = {
    "greeting": {"type": "string"},
    "blocks": {"type": "array", "minItems": 1, "items": _BLOCK},
    "signoff": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    "in_reply_to_card": {"type": "string"},
    "references_cards": {"type": "array", "items": {"type": "string"}},
}
_LETTER_BLOCK = {"oneOf": [
    {"type": "object", "additionalProperties": False, "required": ["h"], "properties": {"h": {"type": "string", "minLength": 1}}},
    {"type": "object", "additionalProperties": False, "required": ["p"], "properties": {"p": {"type": "string", "minLength": 1}}},
    {"type": "object", "additionalProperties": False, "required": ["ul"], "properties": {"ul": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}}]}

SCHEMAS = {
    "email": {"type": "object", "additionalProperties": False, "required": _COMMON_REQ + ["greeting", "blocks", "signoff"],
              "properties": {**_COMMON, **_EMAIL_PROPS}},
    "letter_with_cover_email": {
        "type": "object", "additionalProperties": False, "required": _COMMON_REQ + ["greeting", "blocks", "signoff", "letter"],
        "properties": {**_COMMON, **_EMAIL_PROPS, "letter": {
            "type": "object", "additionalProperties": False, "required": ["title", "salutation", "blocks", "signoff"],
            "properties": {"title": {"type": "string", "minLength": 1}, "salutation": {"type": "string", "minLength": 1},
                           "blocks": {"type": "array", "minItems": 1, "items": _LETTER_BLOCK},
                           "signoff": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}}},
    "call_summary": {
        "type": "object", "additionalProperties": False, "required": _COMMON_REQ + ["conversation", "summary", "follow_up_items", "fidelity"],
        "properties": {**_COMMON,
                       "conversation": {"type": "array", "minItems": 4, "items": {
                           "type": "object", "additionalProperties": False, "required": ["n", "speaker", "text"],
                           "properties": {"n": {"type": "integer", "minimum": 1}, "speaker": {"type": "string"}, "text": {"type": "string", "minLength": 1}}}},
                       "summary": {"type": "string", "minLength": 1},
                       "follow_up_items": {"type": "array", "items": {"type": "string"}},
                       "fidelity": {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {
                           "claims": {"type": "array", "minItems": 1, "items": {
                               "type": "object", "additionalProperties": False, "required": ["claim", "turns", "check_strings"],
                               "properties": {"claim": {"type": "string"}, "turns": {"type": "array", "minItems": 1, "items": {"type": "integer"}},
                                              "check_strings": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}}}}}}},
    "note": {"type": "object", "additionalProperties": False, "required": _COMMON_REQ + ["blocks"],
             "properties": {**_COMMON, "blocks": {"type": "array", "minItems": 1, "items": _BLOCK}}},
    "portal_json": {"type": "object", "additionalProperties": False, "required": _COMMON_REQ + ["free_text"],
                    "properties": {**_COMMON, "free_text": {"type": "string", "minLength": 1}}},
    "register_export": {"type": "object", "additionalProperties": False, "required": _COMMON_REQ, "properties": {**_COMMON}},
    "ledger_export": {"type": "object", "additionalProperties": False, "required": _COMMON_REQ, "properties": {**_COMMON}},
}

# values the SUPERVISOR supplies for each kind (never written by the worker); documented in the guide
CENTRAL = {
    "email": ["From/To/Subject/Date/Message-ID headers", "attachment MIME parts (rendered or reused byte-exact)"],
    "letter_with_cover_email": ["all email headers and attachments", "letterhead, date line, addressee, reference block (complaint/request/member/subject), footer", "letter file name"],
    "call_summary": ["service banner", "case, request reference, call date/start and summary-generated time", "participant names"],
    "note": ["Author, Date, Case and Subject header lines"],
    "portal_json": ["form name, submitted_at, reference, channel, submitted_by, fields.subject, attachments (filename, type, size, sha256)"],
    "register_export": ["every column and value (locked register assertion + request reference)"],
    "ledger_export": ["the whole export file: every column and row of the card's held ledger/effort asset, from its locked spec"],
}

_RE_REF = [
    re.compile(r"\bC-[0-9a-f]{20}\b"),
    re.compile(r"\b[A-Z]{2,5}-\d{4}-\d{2}-\d{2}\b"),   # REQ-2025-11-01
    re.compile(r"\b[A-Z]{2,5}-\d{2}-\d{2}\b"),          # APR-11-01
    re.compile(r"\b(?:DC|ID|RF)-\d{2}\b"),
    re.compile(r"\bDOC-\d{2}-[A-Z0-9]+\b"),
    re.compile(r"\bUP-\d{2}-\d+\b"),
]
_RE_ANY_CASE = re.compile(r"\bC-[0-9a-f]{20}\b")
_RE_ANY_REQ = re.compile(r"\bREQ-\d{4}-\d{2}-\d{2}\b")
_RE_MONEY = re.compile(r"(?<![\w$])A?\$\s?[\d,]+\.\d{2}\b")
_RE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def reference_tokens(text):
    out = []
    for rx in _RE_REF:
        out += rx.findall(text or "")
    return sorted(set(out))


# ------------------------------------------------------------------ requirement groups
class Group:
    """One required fixed value: satisfied when ANY pattern matches the searched text."""

    def __init__(self, label, patterns, source, where="text", minor=None):
        self.label, self.patterns, self.source, self.where, self.minor = label, patterns, source, where, minor

    def found_in(self, text):
        return any(re.search(p, text, re.I) for p in self.patterns)

    def as_dict(self):
        return {"label": self.label, "source": self.source, "where": self.where}


def token_group(label, token, source, where="text"):
    return Group(label, [r"(?<![\w-])" + re.escape(token) + r"(?![\w-])"], source, where)


def date_group(label, iso, source):
    d = date.fromisoformat(iso[:10])
    m, mshort = MONTHS[d.month - 1], MONTHS[d.month - 1][:3]
    pats = [re.escape(iso[:10]),
            rf"\b0?{d.day}(?:st|nd|rd|th)?\s+(?:{m}|{mshort}\.?)\b",
            rf"\b(?:{m}|{mshort}\.?)\s+0?{d.day}(?:st|nd|rd|th)?\b",
            rf"\b0?{d.day}/0?{d.month}(?:/(?:{d.year}|{d.year % 100:02d}))?\b"]
    return Group(label, pats, source)


def money_group(label, minor, currency, source):
    major, cents = divmod(int(minor), 100)
    plain = f"{major:,}.{cents:02d}"
    pats = [rf"(?<![\w.])A?\$\s?{re.escape(plain)}\b", rf"\b{currency}\s?\$?{re.escape(plain)}\b", rf"\b{re.escape(plain)}\s?{currency}\b"]
    if "," in plain:
        pats += [rf"(?<![\w.])A?\$\s?{re.escape(plain.replace(',', ''))}\b"]
    return Group(label, pats, source, minor=int(minor))


def _actor_name(plan, actor_id):
    a = plan.actors.get(actor_id)
    return a["name"] if a else None


_PROHIBITION = re.compile(r"\b(do not|don't|never|must not|should not|avoid|invent no|no invented)\b|^\s*no\b", re.I)


def positive_clauses(text):
    """Clauses of a constraint text that are not prohibitions. A reference named only inside "do not confuse that with DC-11"
    is not a positive claim to insert, so it is not turned into a required value."""
    return [c for c in re.split(r"(?<=[.;])\s+", text or "") if c.strip() and not _PROHIBITION.search(c)]


def approved_clarifications(clars):
    return [c for c in clars if c.get("status") == "approved" and c.get("approved_by") and c.get("approved_on")]


def native_extra_columns(art, clarifications):
    """Approved native register columns for this card, in issue order. The ONE trusted resolution used by both the
    author check and the renderer, so what is checked is what is emitted."""
    extra = {}
    for c in clarifications or ():
        if c.get("card") == art["artifact_ref"]:
            extra.update(c.get("native_columns") or {})
    return extra


class Requirements:
    def __init__(self):
        self.required, self.forbidden, self.native, self.human, self.notes = [], [], [], [], []
        self.header = []  # fixed values that must appear in the centrally rendered header

    def as_dict(self):
        return {"required_in_text": [g.as_dict() for g in self.required],
                "required_in_native_record": [g.as_dict() for g in self.native],
                "required_in_rendered_header": [g.as_dict() for g in self.header],
                "forbidden": [{"label": l, "source": s} for l, _, s in self.forbidden],
                "not_mechanically_checkable_needs_independent_review": self.human, "notes": self.notes}


def _walk_assertion(plan, art, key, value, req, in_text):
    """Turn one assertion (possibly nested) into mechanical requirement groups or a human-review note."""
    src = f"{art['artifact_ref']} source_assertions.{key}"
    if isinstance(value, dict):
        for k, v in value.items():
            _walk_assertion(plan, art, f"{key}.{k}", v, req, in_text)
        return
    if isinstance(value, list):
        if key.endswith("attached_asset_ids"):
            return  # handled with filenames
        for v in value:
            _walk_assertion(plan, art, key, v, req, in_text)
        return
    leaf = key.split(".")[-1]
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str) and _RE_ISO.fullmatch(value) and leaf.endswith("_date"):
        req.required.append(date_group(f"{leaf} {value}", value, src))
    elif isinstance(value, str) and (leaf == "request_reference" or leaf.endswith("_reference") or leaf == "case_id"):
        req.required.append(token_group(f"{leaf} {value}", value, src))
    elif leaf in ("decision_maker", "approver", "signer") and isinstance(value, str) and _actor_name(plan, value):
        req.required.append(token_group(f"{leaf} name {_actor_name(plan, value)}", _actor_name(plan, value), src))
    elif leaf.endswith("_minor"):
        pass  # joined with its currency below
    elif leaf == "currency":
        pass
    elif isinstance(value, str) and leaf in ("decision", "conditions", "execution_state", "requested_service", "participant_concern",
                                              "contact_ending", "remedy_obligation"):
        req.human.append(f"{src}: meaning/attribution/state must be checked by an independent reviewer")
    else:
        req.human.append(f"{src}: no mechanical rule for this assertion type (needs independent review)")


def ledger_assets(plan, art):
    """The held CSV/XLSX export assets a ledger card consists of (its plan attachments)."""
    return [plan.assets[aid] for aid in art.get("attachments", []) if plan.assets[aid].get("delivery_state") == "held"]


def derive_requirements(plan, art, authored_kind, clarifications=(), attachments_named=None):
    """Mechanical requirements from the card itself plus approved clarifications for that card."""
    req = Requirements()
    ref = art["artifact_ref"]
    asserts = art.get("source_assertions", [])
    for a in asserts:
        _walk_assertion(plan, art, a["key"], a["value"], req, True)
    # money: a *_minor assertion with its sibling currency
    for a in asserts:
        if isinstance(a["value"], dict):
            for k, v in a["value"].items():
                if k.endswith("_minor") and isinstance(v, int):
                    req.required.append(money_group(f"{k} {v} {a['value'].get('currency', 'AUD')}", v, a["value"].get("currency", "AUD"),
                                                    f"{ref} source_assertions.{a['key']}.{k}"))
    # fixed references / amounts / dates named in the card's own writer_constraints
    cons = " ".join(positive_clauses(" ".join(art.get("writer_constraints", []))))
    have = {g.label for g in req.required}
    for tok in reference_tokens(cons):
        if f"reference {tok}" not in " ".join(have) and not any(tok in g.label for g in req.required):
            req.required.append(token_group(f"reference {tok}", tok, f"{ref} writer_constraints"))
    for m in _RE_MONEY.findall(cons):
        minor = int(re.sub(r"[^\d]", "", m))
        if not any(g.minor == minor for g in req.required):
            req.required.append(money_group(f"amount {m}", minor, "AUD", f"{ref} writer_constraints"))
    # a register is a native record: its values must be present verbatim in the export, not in prose
    if authored_kind == "register_export":
        reg = next((a["value"] for a in asserts if a["key"] == "register"), {})
        from .render_generic import flatten
        for k, v in flatten(reg).items():
            if v not in (None, ""):
                req.native.append(token_group(f"register {k}", str(v), f"{ref} source_assertions.register"))
        reqref = next((a["value"] for a in asserts if a["key"] == "request_reference"), None)
        if reqref:
            req.native.append(token_group(f"request_reference {reqref}", reqref, f"{ref} source_assertions.request_reference"))
        req.required = []  # prose rules do not apply to a native register
        req.human = [h for h in req.human if "register" not in h]
        req.notes.append("writer_constraints for this card name more register fields than the locked register assertion carries; "
                         "only the assertion values are emitted unless an approved clarification adds columns")
    # a ledger/effort export is the card's held asset itself: every locked field and row value must be in the rendered file
    if authored_kind == "ledger_export":
        for a in ledger_assets(plan, art):
            spec = a["document_spec"]
            for k, v in (spec.get("fields") or {}).items():
                req.native.append(token_group(f"{a['filename']} {k}", str(v), f"{ref} asset {a['asset_id']}.fields"))
            for i, row in enumerate(spec.get("rows") or []):
                for k, v in row.items():
                    req.native.append(token_group(f"{a['filename']} row {i + 1} {k}", str(v), f"{ref} asset {a['asset_id']}.rows"))
        req.required = []  # prose rules do not apply to a native export
        req.human = []
    # attached held files are named by filename in message text (portal/native carry them centrally)
    if authored_kind in EMAIL_KINDS and attachments_named:
        for fn in attachments_named:
            req.required.append(token_group(f"attachment filename {fn}", fn, f"{ref} attachments"))
    # forbidden: the complaint id before the complaint existed; foreign case/request ids; tokens first known later
    cr = getattr(plan, "pack", {}).get("complaints_receipt")
    if cr and art["event_at"][:10] < str(cr)[:10]:
        req.forbidden.append((f"complaint id {plan.case_id} (card predates the complaint receipt {cr})", re.compile(re.escape(plan.case_id)), f"{ref} event_at < complaints_receipt"))
    own = {plan.case_id, plan.request_reference}
    req.forbidden.append(("a case id other than this member's", _ForeignId(_RE_ANY_CASE, own), "pack scope"))
    req.forbidden.append(("a request reference other than this member's", _ForeignId(_RE_ANY_REQ, own), "pack scope"))
    for tok, why in future_tokens(plan, art).items():
        req.forbidden.append((f"later-dated token {tok} ({why})", re.compile(r"(?<![\w-])" + re.escape(tok) + r"(?![\w-])"), f"{ref} future knowledge"))
    # approved clarifications for this card
    for c in clarifications:
        if c.get("card") != ref:
            continue
        for t in c.get("required_tokens", []):
            req.required.append(token_group(f"clarification {c['id']}: {t}", t, f"approved clarification {c['id']}"))
        for w in c.get("waive_required", []):
            gone = [g for g in req.required if w in g.label]
            req.required = [g for g in req.required if w not in g.label]
            req.header += [Group(g.label, g.patterns, f"approved clarification {c['id']} (validated in the rendered header)", "header") for g in gone
                           if g.label.startswith("request_reference")]
            req.notes.append(f"approved clarification {c['id']} waives derived requirement matching {w!r}")
        for t in c.get("forbidden_tokens", []):
            req.forbidden.append((f"clarification {c['id']}: {t}", re.compile(re.escape(t)), f"approved clarification {c['id']}"))
        if authored_kind == "register_export":
            for k, v in native_extra_columns(art, [c]).items():
                req.native.append(token_group(f"clarification {c['id']} column {k}", str(v), f"approved clarification {c['id']}"))
    for mc in art.get("must_not_claim", []):
        req.human.append(f"{ref} must_not_claim: {mc[:140]}")
    seen, uniq = set(), []
    for g in req.required:  # the same fixed value can arrive from an assertion and from a nested field
        if (g.label, g.where) not in seen:
            seen.add((g.label, g.where))
            uniq.append(g)
    req.required = uniq
    return req


class _ForeignId:
    """Forbidden-pattern adapter: any match of `rx` that is not in `own`."""

    def __init__(self, rx, own):
        self.rx, self.own = rx, own

    def search(self, text):
        for m in self.rx.finditer(text):
            if m.group(0) not in self.own:
                return m
        return None


def future_tokens(plan, art):
    """Reference-like tokens and filenames first appearing in artifacts/assets dated AFTER this card, minus anything known
    at or before it. Mechanical future-knowledge guard; it cannot see paraphrased future facts."""
    t0 = datetime.fromisoformat(art["event_at"])
    known, later = set(), {}

    def toks_of_art(a):
        out = set(reference_tokens(" ".join(a.get("writer_constraints", [])) + " " + a.get("subject", "")))
        for s in a.get("source_assertions", []):
            out |= set(reference_tokens(_flat_text(s["value"])))
        return out

    for a in plan.pack["artifacts"]:
        when = datetime.fromisoformat(a["event_at"])
        if when <= t0:
            known |= toks_of_art(a)
        else:
            later[a["artifact_ref"]] = toks_of_art(a)
    own_att = set(art.get("attachments", []))
    for asset in plan.pack["assets"]:
        created = datetime.fromisoformat(asset["created_at"])
        spec_toks = set(reference_tokens(_flat_text(asset.get("document_spec", {}).get("fields", {}))))
        names = {asset["filename"]}
        if created <= t0 or asset["asset_id"] in own_att:
            known |= spec_toks | names
        else:
            later[asset["asset_id"]] = spec_toks | names
    out = {}
    for who, toks in later.items():
        for t in toks:
            if t not in known and t not in {plan.case_id, plan.request_reference}:
                out[t] = f"first appears in {who}"
    return out


def _flat_text(v):
    if isinstance(v, dict):
        return " ".join(_flat_text(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(_flat_text(x) for x in v)
    return "" if v is None else str(v)


def allowed_kinds(art):
    """Authored return kinds permitted for this card, from the trusted plan kind/format and its attachments."""
    pk = art["kind"]
    if pk in UNSUPPORTED_PLAN_KINDS:
        return [], UNSUPPORTED_PLAN_KINDS[pk]
    if art.get("format") in UNSUPPORTED_FORMATS:
        return [], UNSUPPORTED_FORMATS[art["format"]]
    kinds = list(KIND_MAP.get(pk, []))
    if art.get("attachments") and "note" in kinds:
        kinds.remove("note")  # a note cannot carry the attached files
    if not kinds:
        return [], f"plan kind {pk!r} has no return shape"
    return kinds, None


def schema_errors(authored):
    """Plain-language problems with a return's structure ([] when valid)."""
    import jsonschema
    kind = authored.get("kind") if isinstance(authored, dict) else None
    if kind not in SCHEMAS:
        return [f"kind {kind!r} is not one of {sorted(SCHEMAS)}"]
    out = []
    for e in sorted(jsonschema.Draft202012Validator(SCHEMAS[kind]).iter_errors(authored), key=lambda e: list(map(str, e.absolute_path))):
        where = "/".join(map(str, e.absolute_path)) or "(top level)"
        if e.validator == "required":
            out.append(f"{where}: missing required field(s): {', '.join(sorted(set(e.validator_value) - set(e.instance)))}")
        elif e.validator == "additionalProperties":
            extra = sorted(set(e.instance) - set(e.schema.get("properties", {})))
            out.append(f"{where}: unexpected field(s) {extra} (not part of the {kind} return)")
        elif e.validator == "oneOf":
            out.append(f"{where}: not a valid block; use exactly one of {{p}}, {{ul}}, {{ol}}, {{quote, from_card}}" if "quote" in str(e.schema) else f"{where}: not a valid block")
        else:
            out.append(f"{where}: {e.message}")
    return out


def authored_text(d):
    """All NEWLY authored prose in a return, for style and leak checks. Quote blocks are excluded (they are copies)."""
    parts = [d.get("greeting", "")]

    def blocks(bs):
        for b in bs:
            if "p" in b:
                parts.append(b["p"])
            for k in ("ul", "ol"):
                parts.extend(b.get(k, []))
            if "h" in b:
                parts.append(b["h"])
    blocks(d.get("blocks", []))
    parts += d.get("signoff", [])
    parts += [d.get("summary", ""), d.get("free_text", "")]
    parts += d.get("follow_up_items", [])
    if "letter" in d:
        parts += [d["letter"].get("title", ""), d["letter"].get("salutation", "")]
        blocks(d["letter"].get("blocks", []))
        parts += d["letter"].get("signoff", [])
    return "\n".join(p for p in parts if p)


def private_text(d):
    """Private call dialogue and fidelity claims (never in intake); scanned for control leakage and typos too."""
    t = " ".join(x.get("text", "") for x in d.get("conversation", []))
    return t


def quoted_text(d):
    return "\n".join(b["quote"] for b in d.get("blocks", []) if "quote" in b)


def count_typos(text):
    """Occurrences (not distinct spellings) of the approved deliberate misspellings, by wrong form."""
    c = {}
    for m in TYPO_RE.finditer(text):
        k = m.group(1).lower()
        c[k] = c.get(k, 0) + 1
    return c


# control vocabulary: specific to style/authoring controls, so ordinary uses of "profile" etc. are not rejected
CONTROL_RE = re.compile(
    r"\b(?:style|writing|applied|voice|roughness)\s+(?:profile|assignment|control|level)s?\b"
    r"|\bprofile\s+(?:M[1-4]|I[1-3]|F[12]|V[12]|A1|N1|S0)\b"
    r"|\broughness\b|\bstyle[_ ]completion\b|\bprotected[_ ]values?\b|\bwriter[_ ]instructions?\b|\bfact[_ ]card\b"
    r"|\bsource[_ ]assertions?\b|\bprivate (?:plan|dialogue|conversation)\b|\bauthoring (?:instruction|note|pack|brief)s?\b"
    r"|\bnaturalistic\b|\btypo (?:ceiling|budget)\b|\bmicro-?batch\b|\bstage gate\b|\bsynthetic (?:data|case|complaint)\b"
    r"|\bgold (?:answer|label)s?\b|\bexpected[_ ]diagnos\w*\b|\b(?:stratum|holdout|reviewer_only)\b", re.I)
