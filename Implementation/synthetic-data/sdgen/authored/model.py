"""Authored input: loading, strict record shapes, scenario clocks and findings.

Input directory (see the author guide for the full contract):
  BATCH.json
  runtime/participants.jsonl  runtime/parties.jsonl  runtime/cases.jsonl  runtime/sources.jsonl  runtime/files/...
  private/events.jsonl  private/expected-judgements.jsonl  private/LEAK-TERMS.json  private/<anything else>

Only `runtime/` content can reach the delivery. `private/` is copied to the private review package untouched.
Unknown keys are errors (a misspelt `formt` must not silently become a default)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePosixPath

from ..tz import MELBOURNE, OffsetMismatch, melbourne_abbr

INPUT_FORMAT = "sdgen-authored-input"
INPUT_VERSION = "1.0.0"
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
FAMILY = re.compile(r"^S\d{2}$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,150}$")
FIXTURE_PREFIX = "FX-"

FORMATS = {  # format → (declared MIME, allowed extensions, originals subfolder)
    "EML": ("message/rfc822", (".eml",), "emails"),
    "TXT": ("text/plain", (".txt",), "notes"),
    "PDF": ("application/pdf", (".pdf",), "documents"),
    "CSV": ("text/csv", (".csv",), "tables"),
    "XLSX": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", (".xlsx",), "tables"),
    "PNG": ("image/png", (".png",), "images"),
    "JPG": ("image/jpeg", (".jpg", ".jpeg"), "images"),
}
SOURCE_KINDS = {"email", "call_summary", "case_note", "letter", "document", "attachment_original", "image",
                "financial_export", "work_export", "lifecycle_export", "register_export", "procedure", "afca_record",
                "other"}
TABLE_PROFILE = {"financial_export": "financial", "work_export": "work", "lifecycle_export": "lifecycle"}
CONTENT_MODES = {"supplied_file", "text", "email", "pdf_document", "table", "snapshot"}
MODE_FORMATS = {"supplied_file": set(FORMATS), "text": {"TXT"}, "email": {"EML"}, "pdf_document": {"PDF"},
                "table": {"CSV", "XLSX"}, "snapshot": {"CSV", "XLSX"}}
ATTACH_DISPOSITIONS = {"supplied", "reused", "superseded", "not_held", "mentioned"}
PRECISIONS = ("second", "minute", "day", "unknown")
CHANNELS = {"phone", "email", "portal", "letter", "afca", "other", "unknown"}
PARTY_ROLES = {"member", "beneficiary", "representative", "other"}
STATUS_CLAIM_KEYS = {"status", "accepted", "acceptance", "independent_review", "reviewed", "review_status",
                     "ai_processed", "ai_processing", "approved", "signoff"}

BATCH_REQ = {"input_format", "input_version", "batch_id", "package_version", "purpose", "timezone",
             "observation_window", "replay_checkpoints"}
BATCH_OPT = {"excluded_from_corpus", "statement", "deliberate_gaps", "population_note"}
PARTICIPANT_REQ = {"actor_ref", "display_name", "role"}
PARTICIPANT_OPT = {"email", "organisation"}
PARTY_REQ = {"party_id", "member_reference", "party_role", "support_preferences", "clocks"}
PARTY_OPT = {"revision"}
CASE_REQ = {"case_id", "party_ids", "broad_tags", "original_receipt_date", "complaints_receipt_date",
            "manager_reference", "product_or_journey", "channel", "clocks"}
CASE_OPT = {"revision"}
SOURCE_REQ = {"source_id", "kind", "semantic_type", "source_family", "format", "filename", "case_ids", "author",
              "clocks", "content"}
SOURCE_OPT = {"member_references", "afca_matter_ids", "association_note", "owner", "recipients", "cc",
              "business_reference", "subject", "attachments", "reports_events", "version", "supersedes_source_id",
              "copy_of_source_id", "derived_from", "logical_document_id", "future_dates_acknowledged", "held_by_complaints"}
DERIVED_RELATIONS = {"forwarded", "resent_with_changes", "excerpt", "representation", "translation", "annotated"}
CLOCK_KEYS = {"event_at", "created_at", "recorded_at", "available_at"}
NATIVE_CLOCK_KEYS = {"occurred_at", "recorded_at", "available_at"}
EVENT_REQ = {"event_id", "kind", "case_ids", "occurred"}
EVENT_OPT = {"member_reference", "afca_matter_ids", "prerequisite_event_ids", "prerequisite_unknown", "state",
             "obligation_id", "amount_minor", "category", "invoice_reference", "duration_seconds", "basis",
             "contributor_role", "phase", "actor", "task_reference", "episode_ref", "description", "label"}
JUDGEMENT_REQ = {"judgement_id", "case_ids", "cutoff", "candidate_driver", "judgement", "supporting", "contrary",
                 "missing_evidence", "review_status"}
JUDGEMENT_OPT = {"member_reference", "episode_ref", "world_intent_ref", "note"}


@dataclass
class Finding:
    code: str
    severity: str          # error | warning | info
    subject: str
    message: str
    fix: str = ""

    def as_dict(self):
        return {"code": self.code, "severity": self.severity, "subject": self.subject, "message": self.message,
                "fix": self.fix}


@dataclass
class Report:
    findings: list = field(default_factory=list)

    def add(self, code, subject, message, fix="", severity="error"):
        self.findings.append(Finding(code, severity, subject, message, fix))

    def warn(self, code, subject, message, fix=""):
        self.add(code, subject, message, fix, "warning")

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    def codes(self):
        return sorted({f.code for f in self.errors})

    def as_list(self):
        return [f.as_dict() for f in self.findings]


# ---------------------------------------------------------------- clocks
def melbourne_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=MELBOURNE)


@dataclass(frozen=True)
class Clock:
    precision: str
    value: str | None = None
    reason: str | None = None

    @property
    def known(self):
        return self.precision != "unknown"

    def start(self) -> datetime | None:
        if not self.known:
            return None
        if self.precision == "day":
            return melbourne_midnight(date.fromisoformat(self.value))
        return datetime.fromisoformat(self.value)

    def end(self) -> datetime | None:
        """Latest instant the clock can denote (day: the last microsecond of that Melbourne day)."""
        if not self.known:
            return None
        if self.precision == "day":
            return melbourne_midnight(date.fromisoformat(self.value) + timedelta(days=1)) - timedelta(microseconds=1)
        if self.precision == "minute":
            return datetime.fromisoformat(self.value) + timedelta(seconds=59)
        return datetime.fromisoformat(self.value)

    def local_day(self) -> date | None:
        if not self.known:
            return None
        return date.fromisoformat(self.value) if self.precision == "day" else self.start().astimezone(MELBOURNE).date()

    def available_from(self) -> datetime | None:
        """Instant from which an availability clock counts. A day-only availability is not before that day ends
        (the receiver's rule: available from the next Melbourne midnight)."""
        if not self.known:
            return None
        if self.precision == "day":
            return melbourne_midnight(date.fromisoformat(self.value) + timedelta(days=1))
        return self.start()

    def manifest(self):
        if not self.known:
            return {"value": None, "precision": "unknown", "reason": self.reason}
        if self.precision == "day":
            return {"value": self.value, "precision": "day", "tz": "Australia/Melbourne"}
        return {"value": self.value, "precision": self.precision, "tz": self.value[19:]}


def definitely_after(a: Clock | None, b: Clock | None) -> bool:
    """True only when every instant `a` can denote is later than every instant `b` can denote."""
    if a is None or b is None or not a.known or not b.known:
        return False
    return a.start() > b.end()


def held_by(c: Clock | None, cutoff_day: date) -> bool:
    """Is a clock within "end of `cutoff_day`" (Australia/Melbourne)? Half-open for exact instants: an instant counts
    only if it is strictly before the next local midnight, so something first held at 00:00:00 the next day is NOT in
    the earlier day. A day-precision clock is an interval, not an instant: a clock dated `cutoff_day` (or earlier)
    counts at that day's end. (Its documented envelope conversion to the next midnight is the same convention, never
    read back as an exact next-day instant.)"""
    if c is None or not c.known:
        return False
    if c.precision == "day":
        return date.fromisoformat(c.value) <= cutoff_day
    return c.start() < melbourne_midnight(cutoff_day + timedelta(days=1))


def parse_clock(raw, subject, key, report: Report) -> Clock | None:
    """Accepts {"value", "precision"} / {"precision": "unknown", "reason"} or a shorthand ISO string (date → day,
    date-time with offset → second). Offsets must be Melbourne's real offset at that instant (AEST/AEDT)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"value": raw, "precision": "day" if len(raw) == 10 else "second"}
    if not isinstance(raw, dict) or set(raw) - {"value", "precision", "reason"}:
        report.add("clock_shape", subject, f"{key} must be an ISO string or {{value, precision[, reason]}}",
                   "write e.g. \"2026-03-04T09:30:00+11:00\", \"2026-03-04\" or {\"precision\": \"unknown\", \"reason\": \"...\"}")
        return None
    prec = raw.get("precision")
    if prec not in PRECISIONS:
        report.add("clock_precision", subject, f"{key} precision {prec!r} is not one of {PRECISIONS}",
                   "use second, minute, day or unknown")
        return None
    if prec == "unknown":
        if raw.get("value") or not raw.get("reason"):
            report.add("clock_unknown", subject, f"{key} unknown must have no value and a reason",
                       "state why the time is unknown, e.g. \"export carries no time\"")
            return None
        return Clock("unknown", None, raw["reason"])
    v = raw.get("value")
    if not isinstance(v, str):
        report.add("clock_missing_value", subject, f"{key} has precision {prec} but no value", "supply the value")
        return None
    if prec == "day":
        try:
            date.fromisoformat(v)
            if len(v) != 10:
                raise ValueError
        except ValueError:
            report.add("clock_bad_day", subject, f"{key} {v!r} is not YYYY-MM-DD", "use a calendar date")
            return None
        return Clock("day", v)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        report.add("clock_bad_instant", subject, f"{key} {v!r} is not an ISO date-time", "use YYYY-MM-DDTHH:MM:SS+HH:MM")
        return None
    if dt.tzinfo is None:
        report.add("clock_no_offset", subject, f"{key} {v!r} has no UTC offset",
                   "add the Melbourne offset in force on that date (+10:00 AEST or +11:00 AEDT)")
        return None
    try:
        melbourne_abbr(v)
    except OffsetMismatch as exc:
        report.add("clock_wrong_offset", subject, f"{key}: {exc}",
                   "Melbourne uses +11:00 from the first Sunday in October to the first Sunday in April, else +10:00")
        return None
    if prec == "minute" and dt.second:
        report.add("clock_bad_instant", subject, f"{key} minute precision but seconds are {dt.second}", "drop seconds")
        return None
    return Clock(prec, v)


# ---------------------------------------------------------------- loading
@dataclass
class AuthoredInput:
    root: Path
    batch: dict
    participants: dict
    parties: dict
    cases: dict
    sources: dict          # source_id → record (input order)
    clocks: dict           # source_id → {key: Clock}
    native_clocks: dict    # ("party"|"case", id) → {key: Clock}
    events: dict           # event_id → record (private)
    event_clocks: dict     # event_id → Clock
    judgements: list
    leak_terms: list
    window: tuple = (None, None)


def read_jsonl(path: Path, subject: str, report: Report) -> list:
    if not path.is_file():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as exc:
            report.add("jsonl_parse", f"{subject}:{n}", f"not JSON: {exc}", "one complete JSON object per line")
            continue
        if not isinstance(rec, dict):
            report.add("jsonl_parse", f"{subject}:{n}", "line is not a JSON object", "one object per line")
            continue
        out.append(rec)
    return out


def check_keys(rec, req, opt, subject, report: Report) -> bool:
    keys = set(rec)
    missing, unknown = sorted(req - keys), sorted(keys - req - opt)
    if missing:
        report.add("missing_field", subject, f"missing required field(s) {missing}",
                   "the author must supply these; the tooling never invents them")
    claims = sorted(k for k in unknown if k in STATUS_CLAIM_KEYS)
    if claims:
        report.add("status_claim_in_input", subject, f"input claims review/processing status via {claims}",
                   "status is computed by the tooling from evidence; remove these fields")
    other = [k for k in unknown if k not in STATUS_CLAIM_KEYS]
    if other:
        report.add("unknown_field", subject, f"unknown field(s) {other}",
                   f"allowed: {sorted(req | opt)}; check spelling")
    return not missing


def _ident(v, subject, what, report):
    if not isinstance(v, str) or not IDENT.match(v):
        report.add("bad_identifier", subject, f"{what} {v!r} is not a stable identifier",
                   "letters/digits then . _ : - only, max 160 characters")
        return False
    return True


def _unique(records, key, kind, report):
    out = {}
    for r in records:
        k = r.get(key)
        if k in out:
            report.add("duplicate_id", f"{kind}:{k}", f"{key} {k!r} appears more than once",
                       "each identity is written once; versions use supersedes_source_id")
            continue
        out[k] = r
    return out


def load_input(root) -> tuple[AuthoredInput | None, Report]:
    root = Path(root).resolve()
    rep = Report()
    bpath = root / "BATCH.json"
    if not bpath.is_file():
        rep.add("missing_batch", str(root), "BATCH.json not found", "start from the canonical example")
        return None, rep
    batch = json.loads(bpath.read_text(encoding="utf-8"))
    check_keys(batch, BATCH_REQ, BATCH_OPT, "BATCH.json", rep)
    if batch.get("input_format") != INPUT_FORMAT or batch.get("input_version") != INPUT_VERSION:
        rep.add("input_version", "BATCH.json", f"expected {INPUT_FORMAT} {INPUT_VERSION}", "set input_format/input_version")
    if batch.get("timezone") != "Australia/Melbourne":
        rep.add("timezone", "BATCH.json", "timezone must be Australia/Melbourne", "set timezone")
    if batch.get("purpose") not in ("fixture", "corpus"):
        rep.add("purpose", "BATCH.json", "purpose must be fixture or corpus", "set purpose")
    if batch.get("purpose") == "fixture" and batch.get("excluded_from_corpus") is not True:
        rep.add("fixture_not_excluded", "BATCH.json", "fixture batches must declare excluded_from_corpus: true",
                "fixtures never enter the 100-member corpus")
    if not isinstance(batch.get("package_version"), int) or batch.get("package_version", 0) < 1:
        rep.add("package_version", "BATCH.json", "package_version must be a positive integer", "bump it for each rebuild")
    _ident(batch.get("batch_id"), "BATCH.json", "batch_id", rep)
    win = batch.get("observation_window") or {}
    try:
        window = (date.fromisoformat(win["start"]), date.fromisoformat(win["end_exclusive"]))
    except (KeyError, TypeError, ValueError):
        rep.add("window", "BATCH.json", "observation_window needs start and end_exclusive dates", "e.g. 2025-10-01 / 2026-10-01")
        window = (None, None)
    for c in batch.get("replay_checkpoints") or []:
        try:
            d = date.fromisoformat(c)
            if window[0] and not (window[0] <= d < window[1]):
                rep.add("checkpoint_window", "BATCH.json", f"checkpoint {c} outside the window", "use in-window dates")
        except (TypeError, ValueError):
            rep.add("checkpoint", "BATCH.json", f"checkpoint {c!r} is not a date", "end-of-day dates, YYYY-MM-DD")
    if batch.get("purpose") == "corpus" and not {"2026-09-28", "2026-09-30"} <= set(batch.get("replay_checkpoints") or []):
        rep.add("checkpoints_required", "BATCH.json", "corpus checkpoints must include 2026-09-28 and 2026-09-30",
                "these bracket the recent-emergence cluster")
    if len(batch.get("replay_checkpoints") or []) < 3:
        rep.add("checkpoints_count", "BATCH.json", "at least three replay checkpoints are required",
                "include 28 September and 30 September end-of-day for the corpus")

    rt = root / "runtime"
    parts = read_jsonl(rt / "participants.jsonl", "participants", rep)
    for p in parts:
        check_keys(p, PARTICIPANT_REQ, PARTICIPANT_OPT, f"participant:{p.get('actor_ref')}", rep)
        _ident(p.get("actor_ref"), f"participant:{p.get('actor_ref')}", "actor_ref", rep)
    participants = _unique(parts, "actor_ref", "participant", rep)

    native_clocks = {}
    parties_l = read_jsonl(rt / "parties.jsonl", "parties", rep)
    for p in parties_l:
        s = f"party:{p.get('party_id')}"
        if check_keys(p, PARTY_REQ, PARTY_OPT, s, rep):
            _ident(p["party_id"], s, "party_id", rep)
            native_clocks[("party", p["party_id"])] = _native_clocks(p, s, rep)
    parties = _unique(parties_l, "party_id", "party", rep)
    cases_l = read_jsonl(rt / "cases.jsonl", "cases", rep)
    for c in cases_l:
        s = f"case:{c.get('case_id')}"
        if check_keys(c, CASE_REQ, CASE_OPT, s, rep):
            _ident(c["case_id"], s, "case_id", rep)
            native_clocks[("case", c["case_id"])] = _native_clocks(c, s, rep)
    cases = _unique(cases_l, "case_id", "case", rep)

    srcs = read_jsonl(rt / "sources.jsonl", "sources", rep)
    clocks = {}
    for s in srcs:
        subj = f"source:{s.get('source_id')}"
        if not check_keys(s, SOURCE_REQ, SOURCE_OPT, subj, rep):
            continue
        _ident(s["source_id"], subj, "source_id", rep)
        cl = s.get("clocks") or {}
        if set(cl) - CLOCK_KEYS:
            rep.add("unknown_field", subj, f"unknown clock(s) {sorted(set(cl) - CLOCK_KEYS)}", f"clocks: {sorted(CLOCK_KEYS)}")
        clocks[s["source_id"]] = {k: parse_clock(cl.get(k), subj, k, rep) for k in CLOCK_KEYS if k in cl}
    sources = _unique([s for s in srcs if "source_id" in s], "source_id", "source", rep)

    pv = root / "private"
    evs = read_jsonl(pv / "events.jsonl", "events", rep)
    event_clocks = {}
    for e in evs:
        subj = f"event:{e.get('event_id')}"
        if check_keys(e, EVENT_REQ, EVENT_OPT, subj, rep):
            _ident(e["event_id"], subj, "event_id", rep)
            c = parse_clock(e["occurred"], subj, "occurred", rep)
            if c is not None:
                event_clocks[e["event_id"]] = c
    events = _unique([e for e in evs if "event_id" in e], "event_id", "event", rep)
    judgements = read_jsonl(pv / "expected-judgements.jsonl", "judgements", rep)
    for j in judgements:
        check_keys(j, JUDGEMENT_REQ, JUDGEMENT_OPT, f"judgement:{j.get('judgement_id')}", rep)
    leak_terms = []
    lt = pv / "LEAK-TERMS.json"
    if lt.is_file():
        leak_terms = [t for t in json.loads(lt.read_text(encoding="utf-8")).get("terms", []) if isinstance(t, str) and t.strip()]
    inp = AuthoredInput(root, batch, participants, parties, cases, sources, clocks, native_clocks, events,
                        event_clocks, judgements, leak_terms, window)
    return inp, rep


def _native_clocks(rec, subject, rep):
    cl = rec.get("clocks") or {}
    if set(cl) - NATIVE_CLOCK_KEYS:
        rep.add("unknown_field", subject, f"unknown clock(s) {sorted(set(cl) - NATIVE_CLOCK_KEYS)}",
                f"native clocks: {sorted(NATIVE_CLOCK_KEYS)}")
    for k in ("recorded_at", "available_at"):
        if k not in cl:
            rep.add("missing_clock", subject, f"native record needs clocks.{k}", "when the register recorded/released it")
    return {k: parse_clock(cl.get(k), subject, k, rep) for k in NATIVE_CLOCK_KEYS if k in cl}


def safe_input_path(root: Path, rel: str) -> Path | None:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "files":
        return None
    full = (root / "runtime" / rel).resolve()
    return full if (root / "runtime" / "files").resolve() in full.parents else None
