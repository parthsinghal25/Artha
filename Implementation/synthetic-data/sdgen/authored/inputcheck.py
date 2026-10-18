"""Reference, identity, shape and clock checks over authored input (before anything is rendered)."""
from __future__ import annotations

import re
from datetime import date

from .model import (CHANNELS, CONTENT_MODES, DERIVED_RELATIONS, FAMILY, FIXTURE_PREFIX, FORMATS, IDENT, MODE_FORMATS, PARTY_ROLES,
                    SAFE_FILENAME, SOURCE_KINDS, TABLE_PROFILE, Clock, Report, definitely_after)

# Identities that must never enter a new corpus: existing Case 11 and the rejected trial corpus.
FORBIDDEN_IDS = {"C-e1da28538652e263887a", "MR-3f85043fe7f9c98e19f3", "P-9f26fedfe7a19f7bbc3e", "P-ce6e59163db72077a829"}
FORBIDDEN_PREFIXES = ("C-TRIAL-", "MEM-")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
MONTH_NO = {m: i for i, m in enumerate(MONTHS.split("|"), 1)}
MONTH_NO.update({m[:3]: i for m, i in list(MONTH_NO.items())})
DATE_MENTION = re.compile(rf"\b(?:(20\d\d)-(\d\d)-(\d\d)|(\d{{1,2}}) ({MONTHS}|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.? (20\d\d))\b")


def as_of(inp, sid) -> Clock | None:
    """The time a source's content was fixed: created_at, else event_at, else recorded_at, else available_at."""
    cl = inp.clocks.get(sid, {})
    for k in ("created_at", "event_at", "recorded_at", "available_at"):
        c = cl.get(k)
        if c is not None and c.known:
            return c
    return None


def check_input(inp, rep: Report):
    end, start = inp.window[1], inp.window[0]
    fixture = inp.batch.get("purpose") == "fixture"
    ids = ([("case", c) for c in inp.cases] + [("party", p) for p in inp.parties] + [("source", s) for s in inp.sources]
           + [("member", p["member_reference"]) for p in inp.parties.values() if p.get("member_reference")])
    for kind, i in ids:
        if not isinstance(i, str):
            continue
        if fixture != i.startswith(FIXTURE_PREFIX):
            rep.add("fixture_identity", f"{kind}:{i}",
                    "fixture ids must start with FX- and corpus ids must not" if fixture else "FX- ids are fixtures, excluded from the corpus",
                    "mint ids for the right batch purpose")
        if i in FORBIDDEN_IDS or i.startswith(FORBIDDEN_PREFIXES):
            rep.add("forbidden_identity", f"{kind}:{i}", "Case 11 and rejected trial identities never enter a new batch",
                    "mint new opaque ids")
    members = {p["member_reference"] for p in inp.parties.values() if p.get("member_reference")}

    for pid, p in inp.parties.items():
        s = f"party:{pid}"
        if p.get("party_role") not in PARTY_ROLES:
            rep.add("party_role", s, f"party_role {p.get('party_role')!r} not in {sorted(PARTY_ROLES)}", "")
        if p.get("party_role") == "member" and not p.get("member_reference"):
            rep.add("member_reference", s, "a member party needs member_reference", "")
        if not isinstance(p.get("support_preferences"), list):
            rep.add("support_preferences", s, "support_preferences must be a list ([] = none recorded)", "")
        _native_clock_order(inp, ("party", pid), s, rep)

    for cid, c in inp.cases.items():
        s = f"case:{cid}"
        for pid in c.get("party_ids") or []:
            if pid not in inp.parties:
                rep.add("dangling_party_ref", s, f"party {pid} is not in parties.jsonl", "add the party or fix the id")
        if not any(inp.parties.get(p, {}).get("member_reference") for p in c.get("party_ids") or []):
            rep.add("case_without_member", s, "no party of the case carries a member_reference",
                    "a deceased member still remains the member party")
        if c.get("channel") not in CHANNELS:
            rep.add("case_channel", s, f"channel {c.get('channel')!r} not in {sorted(CHANNELS)}", "")
        if c.get("manager_reference") and c["manager_reference"] not in inp.participants:
            rep.add("dangling_actor_ref", s, f"manager {c['manager_reference']} is not a participant", "")
        try:
            orig = date.fromisoformat(c["original_receipt_date"]) if c.get("original_receipt_date") else None
            comp = date.fromisoformat(c["complaints_receipt_date"]) if c.get("complaints_receipt_date") else None
        except (TypeError, ValueError):
            rep.add("case_dates", s, "receipt dates must be YYYY-MM-DD or null", "")
            continue
        if orig and comp and comp < orig:
            rep.add("case_dates", s, "complaints_receipt_date is before original_receipt_date", "")
        if comp and start and not (start <= comp < end):
            rep.add("case_outside_window", s, f"complaints receipt {comp} outside the observation window", "")
        av = inp.native_clocks.get(("case", cid), {}).get("available_at")
        if comp and av is not None and av.known and av.local_day() < comp:
            rep.add("case_available_before_receipt", s, f"case row available {av.value} before receipt {comp}", "")
        _native_clock_order(inp, ("case", cid), s, rep)

    filenames = {}
    for sid, src in inp.sources.items():
        s = f"source:{sid}"
        kind, fmt, content = src.get("kind"), src.get("format"), src.get("content") or {}
        if kind not in SOURCE_KINDS:
            rep.add("source_kind", s, f"kind {kind!r} not in {sorted(SOURCE_KINDS)}", "")
        if fmt not in FORMATS:
            rep.add("source_format", s, f"format {fmt!r} not in {sorted(FORMATS)}", "declare the original's real format")
            continue
        mode = content.get("mode")
        if mode not in CONTENT_MODES:
            rep.add("content_mode", s, f"content.mode {mode!r} not in {sorted(CONTENT_MODES)}", "")
        elif fmt not in MODE_FORMATS[mode]:
            rep.add("content_mode", s, f"mode {mode} cannot produce {fmt}", f"{mode} produces {sorted(MODE_FORMATS[mode])}")
        if kind in TABLE_PROFILE and fmt not in ("CSV", "XLSX"):
            rep.add("table_format", s, f"{kind} must be CSV or XLSX", "")
        if mode == "snapshot" and kind not in TABLE_PROFILE:
            rep.add("snapshot_kind", s, "snapshot only for financial_export/work_export/lifecycle_export", "")
        if mode == "pdf_document" and not content.get("title"):
            rep.add("missing_field", s, "pdf_document needs title", "")
        fn = src.get("filename") or ""
        if not SAFE_FILENAME.match(fn) or not fn.lower().endswith(FORMATS[fmt][1]):
            rep.add("filename", s, f"filename {fn!r} unsafe or extension does not match {fmt}",
                    f"use letters/digits/._-() and {FORMATS[fmt][1]}")
        key = (FORMATS[fmt][2], fn.lower())
        if key in filenames:
            rep.add("duplicate_filename", s, f"filename {fn} also used by {filenames[key]}", "filenames are unique per folder")
        filenames[key] = sid
        if not FAMILY.match(str(src.get("source_family") or "")):
            rep.add("source_family", s, "source_family must be S01..S12", "see the family list in the guide")
        if not src.get("case_ids") and not src.get("association_note"):
            rep.add("association_missing", s, "no case_ids and no association_note",
                    "link the case(s) or state explicitly why the association is unknown")
        for c in src.get("case_ids") or []:
            if c not in inp.cases:
                rep.add("dangling_case_ref", s, f"case {c} is not in cases.jsonl", "add the case or fix the id")
        for m in src.get("member_references") or []:
            if m not in members:
                rep.add("dangling_member_ref", s, f"member {m} is not on any party", "")
        for a in src.get("afca_matter_ids") or []:
            if not IDENT.match(str(a)):
                rep.add("bad_identifier", s, f"AFCA matter id {a!r}", "")
        for role in ("author", "owner"):
            if src.get(role) and src[role] not in inp.participants:
                rep.add("dangling_actor_ref", s, f"{role} {src[role]} is not a participant", "add to participants.jsonl")
        for r in (src.get("recipients") or []) + (src.get("cc") or []):
            if r not in inp.participants:
                rep.add("dangling_actor_ref", s, f"recipient {r} is not a participant", "")
        if fmt == "EML":
            if not src.get("recipients"):
                rep.add("missing_field", s, "an email needs recipients", "")
            for r in [src.get("author")] + (src.get("recipients") or []) + (src.get("cc") or []):
                if r in inp.participants and not inp.participants[r].get("email"):
                    rep.add("participant_email", s, f"participant {r} has no email address", "add email to the participant")
        _source_clocks(inp, sid, s, rep)
        _attachments(inp, sid, s, rep)
        if src.get("supersedes_source_id"):
            prev = inp.sources.get(src["supersedes_source_id"])
            if prev is None:
                rep.add("dangling_source_ref", s, f"supersedes unknown source {src['supersedes_source_id']}", "")
            elif int(src.get("version", 1)) <= int(prev.get("version", 1)):
                rep.add("version_order", s, "a superseding version must have a higher version number", "")
        if src.get("copy_of_source_id") and src["copy_of_source_id"] not in inp.sources:
            rep.add("dangling_source_ref", s, f"copy_of unknown source {src['copy_of_source_id']}", "")
        rel = src.get("derived_from")
        if rel is not None:
            if not isinstance(rel, dict) or rel.get("source_id") not in inp.sources or rel.get("relation") not in DERIVED_RELATIONS:
                rep.add("derived_from", s, f"derived_from {rel!r} needs an existing source_id and relation in {sorted(DERIVED_RELATIONS)}", "")
            if src.get("copy_of_source_id"):
                rep.add("derived_from", s, "a source is either an exact copy or derived, not both", "keep the truthful one")
        c = content.get("in_reply_to_source_id")
        if c and c not in inp.sources:
            rep.add("dangling_source_ref", s, f"in_reply_to unknown source {c}", "")


def _native_clock_order(inp, key, s, rep):
    cl = inp.native_clocks.get(key, {})
    av = cl.get("available_at")
    for k in ("occurred_at", "recorded_at"):
        if definitely_after(cl.get(k), av):
            rep.add("clock_order", s, f"{k} is after available_at", "a record cannot be released before it exists")
    if av is not None and av.known and inp.window[1] and av.local_day() >= inp.window[1]:
        rep.add("available_outside_window", s, "available after the observation window", "")


def _source_clocks(inp, sid, s, rep):
    cl = inp.clocks.get(sid, {})
    av = cl.get("available_at")
    if av is None or not av.known:
        rep.add("missing_clock", s, "available_at (when the complaints team held it) is required and known",
                "use day precision if only the date is known")
        return
    start, end = inp.window
    if end and (av.local_day() >= end or av.local_day() < start):
        rep.add("available_outside_window", s, f"available_at {av.value} is outside the observation window",
                "deliver only in-window holdings; never clamp a date to fit")
    for k in ("event_at", "created_at", "recorded_at"):
        if definitely_after(cl.get(k), av):
            rep.add("inconsistent_clock", s, f"{k} {cl[k].value} is after available_at {av.value}", "")
    if definitely_after(cl.get("created_at"), cl.get("recorded_at")):
        rep.add("inconsistent_clock", s, "created_at is after recorded_at", "")
    if definitely_after(cl.get("event_at"), cl.get("recorded_at")):
        rep.add("inconsistent_clock", s, "event_at is after recorded_at", "")
    if as_of(inp, sid) is None:
        rep.add("missing_clock", s, "no known content clock", "")
    if av.precision in ("second", "minute") and av.start().astimezone(av.start().tzinfo).strftime("%H:%M:%S") == "00:00:00":
        rep.warn("midnight_boundary", s, f"available_at {av.value} is exactly local midnight",
                 "expectations treat it as the new day (half-open); the current backend compares <= end-of-day and would "
                 "count it in the previous day; use day precision or a real time unless the boundary is intended")


def _attachments(inp, sid, s, rep):
    src = inp.sources[sid]
    for a in src.get("attachments") or []:
        d = a.get("disposition")
        if d not in ("supplied", "reused", "superseded", "not_held", "mentioned"):
            rep.add("attachment_disposition", s, f"disposition {d!r}", "supplied, reused, superseded, not_held or mentioned")
            continue
        if d in ("supplied", "reused", "superseded"):
            aid = a.get("source_id")
            if aid not in inp.sources:
                rep.add("missing_attachment", s, f"attachment source {aid} is not held",
                        "add the attachment as a source, or mark it not_held/mentioned with its filename")
                continue
            if src.get("format") != "EML":
                rep.add("attachment_parent", s, "only email originals carry embedded attachments", "")
            if definitely_after(inp.clocks[aid].get("available_at"), inp.clocks[sid].get("available_at")):
                rep.add("attachment_clock", s, f"attachment {aid} becomes available after the message carrying it", "")
            if definitely_after(as_of(inp, aid), as_of(inp, sid)):
                rep.add("attachment_clock", s, f"attachment {aid} was created after the message carrying it", "")
        else:
            if not a.get("filename") or a.get("source_id"):
                rep.add("unheld_attachment", s, "not_held/mentioned attachments name a filename and no held source", "")


def scan_date_mentions(inp, sid, text, rep: Report):
    """Dates written in a body that are later than the source's own as-of. A promise ("by 5 September") is legitimate;
    a completed later fact is not. The tooling cannot tell which, so it asks the author to acknowledge each."""
    ref = as_of(inp, sid)
    if ref is None or not text:
        return
    ack = set(inp.sources[sid].get("future_dates_acknowledged") or [])
    for m in DATE_MENTION.finditer(text):
        try:
            if m.group(1):
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = date(int(m.group(6)), MONTH_NO[m.group(5)[:3]], int(m.group(4)))
        except (ValueError, KeyError):
            continue
        if d > ref.local_day() and d.isoformat() not in ack:
            rep.warn("future_date_mention", f"source:{sid}",
                     f"text mentions {m.group(0)!r}, later than the source's own date {ref.local_day()}",
                     "if it is a promise/deadline, list the date in future_dates_acknowledged; if it reports a later fact, move it to a later source")


def scan_case_mentions(inp, sid, text, rep: Report):
    """A body that names a case id before that complaint was received knows the future (v2: early notices naming
    later cases). A retrospective record must be dated after the receipt instead."""
    ref = as_of(inp, sid)
    if ref is None or not text:
        return
    for cid, c in inp.cases.items():
        if cid in text and c.get("complaints_receipt_date") and date.fromisoformat(c["complaints_receipt_date"]) > ref.local_day():
            rep.add("case_id_foreknowledge", f"source:{sid}",
                    f"text names {cid}, received {c['complaints_receipt_date']}, but the source is dated {ref.local_day()}",
                    "an earlier document cannot carry a later complaint's id; date the record when it was actually written")
