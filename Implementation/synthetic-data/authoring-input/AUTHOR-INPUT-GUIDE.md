# Author input guide: fresh replacement pilot

Audience: the cloud author creating the replacement corpus. The tooling in this folder turns what you write into the
existing receiving format and checks it mechanically. **You write the histories and the source content. The code
never writes the story.** Independent source review is a separate person's job; nothing you or the tooling produce
counts as review or acceptance.

The worked example `examples/fx-five-members/` contains 5 fictional members and 6 cases. It uses every input shape
described below and passes every check. Its `FX-` identities are a test fixture and must **never** appear in the corpus.

---

## 1. What you are creating

A Google hackathon proof of concept for complaints leadership at the fictional fund **Example Super**. A later AI will
read realistic, noisy records and infer:

- recurring operational mechanisms;
- explanations that change over time;
- trends from daily to annual;
- recorded company cash, work and AFCA burden.

Your job is believable evidence plus private expected answers that can test that reasoning. Do not build the AI or
the dashboard.

Roadmap: validated 100 members → 500 (AI intelligence) → 5,000 → final UI. This brief is the 100.

All people, records, procedures and incidents are fictional:

- no real customer details;
- no AustralianSuper (or any real fund) branding;
- no invented claims about a real organisation.

Ordinary source bodies read like business records. They contain no planted-answer commentary. Do not incur paid
API/service charges without the participant's explicit approval.

### Exact population

| Member group | Members | Cases each | Episodes each | Cases | Episodes |
|---|---:|---:|---:|---:|---:|
| Single complaint | 75 | 1 | 1 | 75 | 75 |
| Repeat complaint about the same problem | 15 | 2 | 1 | 30 | 15 |
| Independent second problem | 5 | 2 | 2 | 10 | 10 |
| Original, repeat, then independent problem | 5 | 3 | 2 | 15 | 10 |
| **Total** | **100** | | | **130** | **110** |

Population rules:

- A reopening keeps its case ID. It is a `case_reopened` event, not a new case.
- Contacts, documents, representatives and AFCA matters are not extra members or cases.
- In a death-benefit journey the deceased person remains the member. Living correspondents are separate parties
  (claimant `other` or `representative`), and claimant status does not imply entitlement.
- Mint new opaque IDs. They must not encode product, mechanism, severity, split or answer, in an ID, filename or
  folder.
- Never reuse Case 11 identities, `C-TRIAL-*` or `MEM-*` IDs. The checker refuses them, and refuses `FX-` IDs in a
  corpus batch.

### Private primary episode groups

These are private design groups. They are **not** runtime tags.

| Group | Episodes |
|---|---:|
| Accepted instruction not activated or fulfilled | 10 |
| Previously valid evidence requested again | 10 |
| Approval request follows an incorrect or obsolete route | 10 |
| Ordinary complaints handled or resolved appropriately | 40 |
| Legitimate complexity, dependencies or necessary waits | 20 |
| Similar symptoms with a different explanation (lookalikes) | 10 |
| Insufficient or genuinely conflicting evidence | 10 |

Coverage rules:

- **Product areas:** each mechanism spans at least two product or journey areas, and the areas overlap in calendar
  time. Do not use one product per quarter. Suitable areas are pension payments and bank changes, insurance and TPD,
  death benefits, contributions, fees and refunds, and transfers.
- **Multiple problems and positive handling:**
  - At least 12 cases have two supported contributing problems.
  - Include explicit good handling.
  - Include legitimate dissatisfaction with a valid decision.
  - Not every complaint is an institutional failure.
- **AFCA:**
  - 15 distinct AFCA matters, spread across agreement, determination, withdrawal and still open.
  - At least two matters link more than one internal case. One matter is never counted twice.
  - Include some threats to go to AFCA that never become a lodged matter.
  - Show only material the fund holds, never AFCA's private deliberations.

### Time

- **Window:** 1 October 2025 to 30 September 2026, Australia/Melbourne. Distribute arrivals irregularly across all
  12 months. Vary the lags between events; do not use fixed +1/+3/+7-day patterns. Cases near 30 September may stay
  open.
- **Daylight saving and offsets:** the window opens on AEST.

  | Period | Offset |
  |---|---|
  | 1–4 Oct 2025 | +10:00 |
  | 5 Oct 2025 – 5 Apr 2026 03:00 | +11:00 (AEDT) |
  | After 5 Apr 2026 | +10:00 (AEST) |

  The checker refuses a wrong offset.
- **Demonstrations:**
  - *Recent emergence:* ten accepted-instruction episodes concentrate on 29–30 September among background activity.
    Their wording varies. Logs or internal mail may later make a shared condition supportable; early complaints alone
    need not prove it.
  - *Persistence:* repeated valid-evidence requests across months. Some cases resolve, some recur and some stay open
    with little contact. Keep the earlier receipt, the validity assessment against the right requirement and version,
    and the later repeat request. Receipt alone is not validity.
  - *Mitigation:* obsolete approval routing occurs before a dated, fictional mid-year procedure change. Afterwards
    there are fewer new cases but a residual backlog. Other background factors still continue.
- **Replay checkpoints:** at least three in `BATCH.json`, including `2026-09-28` and `2026-09-30` (end of day).
- **Staged later sources:** add two, inside the 130 cases. One weakens an earlier explanation; one resolves an earlier
  uncertainty. Each is dated by its own availability.

---

## 2. Folder layout

Create one folder per batch. A whole corpus can be one batch, or several batches of complete members.

```text
<batch>/
  BATCH.json
  runtime/
    participants.jsonl   parties.jsonl   cases.jsonl   sources.jsonl
    files/                       # finished originals you supply (EML, PDF, PNG/JPG, XLSX, CSV, TXT) and long texts
  private/
    events.jsonl                 # the world: dated events, including every money/work/lifecycle event
    expected-judgements.jsonl    # evidence-conditioned expectations with exact anchors
    LEAK-TERMS.json              # private labels that must never appear in runtime
    member-briefs/<member>.md    # your connected-member briefs (free form)
    SPLIT-MANIFEST.json, INCIDENT-CATALOGUE.json, ...   # anything else private; copied, never shipped to AI
```

Only `runtime/` can reach the delivery. Unknown or misspelt fields are errors; nothing is silently defaulted.

### Record shapes

Each JSONL line is one JSON object.

- **BATCH.json** requires:
  - `input_format: "sdgen-authored-input"` and `input_version: "1.0.0"`;
  - `batch_id` and `package_version` (bump it on every rebuild);
  - `purpose: "corpus" | "fixture"`;
  - `timezone: "Australia/Melbourne"`;
  - `observation_window {start, end_exclusive}`;
  - `replay_checkpoints`.

  It may also carry `statement`, `deliberate_gaps` and `population_note`. Never put a status claim here (`accepted`,
  `independent_review`, `ai_processed` and so on). Status is computed.
- **participants.jsonl:** `actor_ref`, `display_name`, `role` (a factual job or relationship) and, for anyone on an
  email, `email`. Members' `actor_ref` is their `party_id`.
- **parties.jsonl:** `party_id`, `member_reference` (or `null`), `party_role`
  (member | beneficiary | representative | other), `support_preferences` (`[]` means none recorded) and
  `clocks {recorded_at, available_at[, occurred_at]}`.
- **cases.jsonl:** `case_id`, `party_ids`, `broad_tags`, `original_receipt_date`, `complaints_receipt_date`,
  `manager_reference`, `product_or_journey`, `channel` (phone | email | portal | letter | afca | other | unknown) and
  `clocks`. These become v0.2.0 native `case` and `party` input records.
- **sources.jsonl:** one line per original. Required fields:

  | Field | Meaning |
  |---|---|
  | `source_id` | Stable and opaque |
  | `kind` | email, call_summary, case_note, letter, document, attachment_original, image, financial_export, work_export, lifecycle_export, register_export, procedure, afca_record or other |
  | `semantic_type` | Plain business type, e.g. `complaint`, `acknowledgement` |
  | `source_family` | S01 register/intake · S02 vendor call summary · S03 correspondence · S04 receipt/validity/identity · S05 internal exchange/case note · S06–S08 task/approval/lifecycle extracts · S09 procedure · S10 work, invoice or remedy extract · S12 external dispute record |
  | `format` | EML, TXT, PDF, CSV, XLSX, PNG or JPG |
  | `filename` | Unique; extension matches the format; no private meaning |
  | `case_ids` | All the cases it belongs to. If none is known, give an `association_note` instead. |
  | `author` | A participant |
  | `clocks` | See below |
  | `content` | See section 3 |

  Optional fields:

  - `member_references`, `afca_matter_ids`;
  - `recipients`, `cc` (required for email), `owner`;
  - `business_reference`, `subject` (required for a rendered email);
  - `attachments`;
  - `reports_events`: the event IDs this source states have happened;
  - `version`, `supersedes_source_id` (a new version), `logical_document_id`;
  - `copy_of_source_id`: an **exact** copy. Its final bytes must equal the original's, or the build refuses it
    (`false_copy`);
  - `derived_from: {"source_id", "relation"}`: a truthful link for different bytes. The relation is one of
    `forwarded`, `resent_with_changes`, `excerpt`, `representation`, `translation` or `annotated`. A forwarded email
    or a revised or retyped document uses this, never `copy_of_source_id`;
  - `future_dates_acknowledged`, `held_by_complaints`.

### Clocks

Each clock is an ISO string, or `{value, precision}`, or `{"precision": "unknown", "reason": ...}`.

| Clock | Meaning |
|---|---|
| `event_at` | When the recorded thing happened or was sent (an email's Date) |
| `created_at` | When this original was produced (an export's own date; a letter's issue) |
| `recorded_at` | When it entered the case file |
| `available_at` | **Required.** When the complaints team held it. Must fall inside the window. |

- **Precision:** `second`, `minute`, `day` or `unknown`. Day-only is fine for a date with no known time. Never invent
  seconds; unknown stays unknown, with a reason.
- **Native records:** a day-only native clock becomes the next Melbourne midnight in the record envelope. This is the
  receiver's not-before rule. Each conversion is listed in the manifest (`day_precision_clocks`) and the build log,
  and is read back as that day, never as an exact next-day instant.
- **"By the end of day D":** this is how every checkpoint, metric and judgement cutoff decides whether something was
  held.
  - An exact time counts only if it is strictly before the next local midnight. Evidence first held at 00:00:00 the
    next day is *not* in day D.
  - A day-precision clock dated D counts in D.
  - Avoid exact-midnight availability unless you mean it; the build warns (`midnight_boundary`) because the current
    backend compares inclusively there.
- **Rendering time:** the real time you render never enters the bytes. A rendered email needs a minute- or
  second-precision `event_at`. If the time is genuinely unknown, supply the finished `.eml` instead.

---

## 3. Source content: write it, or hand over the finished file

| `content.mode` | Produces | You supply |
|---|---|---|
| `supplied_file` | any format | `path: "files/<name>"`. Copied **byte for byte**. A finished EML must agree with its record: sender, Date, Subject, and every attachment's bytes. |
| `text` | TXT | `text` (or `path`). Written exactly; nothing is added. |
| `email` | EML | `body_text` (or `body_path`) and optional `message_id`, `in_reply_to_source_id`, `references_source_ids`. The text/plain body is your text exactly. Headers come from the record; attachments are the real bytes of the sources you list. |
| `pdf_document` | PDF | `title` and optional `issuer`, `footer`, and `blocks`: `{"h"}`, `{"p"}`, `{"ul": [...]}`, `{"kv": [[label, value], ...]}`, `{"table": {"columns", "rows"}}`. Neutral layout. |
| `table` | CSV/XLSX | `columns`, `rows` and optional `sheet`. Values are written as given. |
| `snapshot` | CSV/XLSX | Nothing else. The rows are your private events of the table's kind for the source's cases or matters that had occurred by its `created_at`. Use it for "an export run on date X". |

### Attachments

Every attachment is a held original of its own (`kind: attachment_original`). Emails list it under `attachments` with
one of these dispositions:

- `supplied`: the first carrier;
- `reused`: forwarded or attached again; one original, several occurrences;
- `superseded`: an older version.

A file that is mentioned but not held is `{"filename": ..., "disposition": "not_held" | "mentioned"}`, with a single
plain sentence in the body such as "I could not attach…". **An accidental missing attachment is refused.**

### Writing quality

These requirements come from the brief.

- **Evidence types:** a practical mix of call summaries (TXT), emails with real threads and attachments, case notes,
  CSV registers and ledgers, and XLSX work or transaction extracts. Add selected genuine PDFs and a few meaningful
  PNG/JPG scans. Do not vary format by whether a case is normal or problematic.
- **Depth:** routine journeys usually need 6–10 substantive records; complex ones need 12–20 or more. Do not pad.
- **Holdings:** the corpus contains the records the complaints team holds:
  - intake and acknowledgement;
  - internal exchanges;
  - clarification and evidence requests;
  - receipt and validity assessments;
  - approval routing and decisions;
  - actions and progress updates;
  - outcome and closure;
  - returns.
- **Call summaries:** a vendor summary, never a transcript. It can preserve an attributed request, dissatisfaction,
  or an explicit promise and deadline.
- **Noise:** benign and varied. Quoted chains, signatures, abbreviations, small typos, inconsistent terms, irrelevant
  small talk, an outdated contact detail, late uploads. No garbage, fake redaction blocks or model instructions.
- **Keep these distinct:**
  - member says sent / recorded receipt / accepted validity;
  - requested / conditional / granted approval versus attempted / completed action;
  - administrative closure / completed service / merits decision / member satisfaction;
  - "thanks for the update" versus accepting the outcome;
  - AFCA threat / lodged matter / outcome / remedy / payment;
  - a handler's hypothesis versus a confirmed condition.
- **Knowledge at the time:** a source knows only what its author could know at its own date.
  - Dates later than the source's date are flagged. List the genuine ones (promises and deadlines) in
    `future_dates_acknowledged`.
  - Naming a case ID before that complaint was received is refused.
  - Shared incident or procedure facts are consistent across members, and become available only when staff knew them.

---

## 4. Money, work and lifecycle: private events plus source tables

Every dated event goes in `private/events.jsonl`:

```json
{"event_id", "kind", "case_ids", "occurred", ...}
```

Event kinds:

| Group | Kinds |
|---|---|
| Lifecycle | `case_received`, `case_closed`, `case_reopened` |
| Service and process | `instruction_received`, `evidence_received`, `evidence_requested`, `validity_assessed`, `approval_requested`, `approval_granted`, `approval_declined`, `routed`, `service_attempted`, `service_completed`, `member_contact`, `promise_made` |
| AFCA | `afca_threatened`, `afca_lodged`, `afca_outcome`, `afca_closed` |
| Other | `incident`, `procedure_change`, `other` |
| Money | `financial` (with `state`, `obligation_id`, `amount_minor`, `category`, optional `invoice_reference`) |
| Work | `work` (with `duration_seconds`, `basis`, `contributor_role`, `phase`, optional `actor`, `task_reference`) |

Optional on any event: `prerequisite_event_ids`, `prerequisite_unknown` (a reason) and `episode_ref`.

Payments, approvals, routing, requests, assessments, execution, closure and reopening each have their **own** event ID
and date.

Money and work reach the runtime only as source tables, and every row records an authored event exactly.

| Table (`kind`) | Required columns | Optional |
|---|---|---|
| `financial_export` | `event_id,obligation_id,case_id,date,state,amount_minor,category` | `event_time,linked_case_ids,invoice_reference,afca_matter_id,currency,note` |
| `work_export` | `work_id,case_id,date,duration_seconds,basis,contributor_role,phase` | `event_time,linked_case_ids,actor,task_reference,afca_matter_id,note` |
| `lifecycle_export` | `lifecycle_event_id,case_id,date,status` (received/closed/reopened) | `event_time,linked_case_ids,note` |

`event_time` is the event's exact time (ISO with the Melbourne offset) on the row's `date`. It carries known intraday
order into the delivered evidence. A `snapshot` adds it automatically whenever the event has a known time. In an
authored `table`, add it yourself whenever several events of one obligation or case share a day; otherwise the build
warns `runtime_order_lost`. The backend's ledger and work profiles ignore the column, and keep reading `date`.

### Frozen values

These are the backend's canonical values; aliases are refused.

- **state:** `requested`, `offered`, `approved`, `agreed`, `awarded`, `invoiced`, `payment`, `payment_reversal`,
  `refund`, `amount_revision`.
- **category:** `afca_fee`, `external_assistance`, `other_complaint_expense`, `compensation`, `settlement`,
  `benefit_principal`, `refund_entitlement`.

  A lifecycle state is not a category: never write `remedy_payment`.
- **work phase:** `member_contact`, `internal_coordination`, `evidence_gathering`, `approval`,
  `submission_preparation`, `conciliation_preparation`, `conciliation_attendance`, `determination_response`,
  `follow_up`, `other`.
- **basis:** `recorded` for actual work. `planned`/`estimate` rows are kept as evidence but never counted.

### Rules the checker enforces

- **Amounts:** integer AUD cents. Only `payment_reversal` and `refund` are negative.
- **Order:** known times order events; two date-only events on the same day stay unordered and are never given an
  invented intraday order or ordered by ID.
  - A sequence that is impossible under every order the clocks allow is refused. Examples: a payment at 09:00 before
    an approval at 16:00, a reversal before its payment, or a closure before the receipt.
  - A same-day date-only pair that could be valid is accepted with an `ordering_ambiguous` warning. Give times if
    you know them.
- **Payments and reversals:**
  - A payment needs an earlier `approved`/`agreed`/`awarded`/`invoiced`/`amount_revision` on the **same obligation**.
    If that record genuinely isn't held, say so with `prerequisite_unknown`.
  - Cumulative net payments (payments minus reversals) at each payment cannot exceed the **latest approval or
    `amount_revision` in effect at that moment**, by known time. A downward revision lowers the ceiling for later
    payments only; a revision after a payment does not change what that payment was checked against. If the
    approval and revision share a date with no times, either may be in effect: a payment above every candidate is
    rejected, and one between them is accepted with an `ordering_ambiguous` warning. Give times if you know them.
  - A reversal needs an earlier payment on the same obligation, and cannot exceed it.
  - Links are explicit IDs; nothing is matched on equal amounts.
- **Shared charges:** a shared charge is **one** event. List all of its cases in `linked_case_ids` (`;`-separated)
  and in the event's `case_ids`. An invoice is invoiced once.
- **Dates:** a dated export cannot contain a row dated after its own `created_at`. For later events, issue a later
  export or a `snapshot`.
- **Window:** events are never clamped. An event after 30 September is refused, not moved.
- **Duplicates:** the same event ID repeated in another export must carry identical values. Copies are deduplicated.
- **Lifecycle:** the order is received → closed → reopened → closed. Case status comes **only** from visible
  lifecycle rows:
  - *confirmed open*: the last row is received or reopened;
  - *confirmed closed*;
  - *unknown*: there are no rows, or same-day rows whose order decides the state.

  Unknown is never counted as open. An outcome or closure *letter* is not a lifecycle row and closes nothing, so give
  closures and reopenings their own lifecycle events and an export.
- **Coverage:** state what is not covered in `deliberate_gaps`. A missing work log is not zero work, and zero
  recorded cash or work in incomplete holdings is not zero burden. The metrics list the arrived cases with no work or
  financial rows.

---

## 5. Private expectations

`private/expected-judgements.jsonl` holds one line per expected judgement, with these fields:

- `judgement_id`, `case_ids`, `member_reference`, `cutoff` (YYYY-MM-DD) and `candidate_driver` (a private label);
- `judgement`: one of `supported`, `uncertain`, `contradicted` or `not_assessed`;
- `supporting` and `contrary`: each a list of `{source_id, quote}`;
- `missing_evidence`: a list of text;
- optional `episode_ref`, `world_intent_ref` and `note`;
- `review_status: "author_proposed"`, which is the only value allowed.

What the tooling checks: each quote is found in the final bytes of the cited original, and that original was
available by the cutoff. It does **not** judge whether the reasoning is right, and nothing you write becomes
supported gold until a separate source-first reviewer agrees.

Other private material:

- **World truth** (events, briefs, the incident catalogue) may be stronger than the evidence-conditioned
  expectations, and the two are separate.
- **Unknown is not negative:** include ordinary cases with no established target mechanism.
- **Split:** choose 80 development / 20 holdout members **before** inference, and keep a member's cases together.
  The split is private only.
- **LEAK-TERMS.json:** list every private label (driver keys, episode IDs, split names). The build refuses any that
  appear in a runtime file, filename, cell, PDF text or attachment.

Mechanical metrics are **computed for you** from the final bytes. You do not hand-write them. The tooling records:

- arrivals and unique members;
- case status stocks: confirmed open, confirmed closed, unknown, and "received without evidenced closure" (open plus
  unknown; never presented as confirmed backlog), with lifecycle coverage;
- gross, reversed and net paid cash, kept separate from approvals, invoices and requests;
- recorded work seconds, with the cases that have no recorded rows;
- AFCA matters referenced.

Each is computed at every checkpoint and at daily, weekly, monthly, quarterly and annual grain, with exact
contributor IDs and reconciliation (`private/EXPECTED-METRICS.json`).

**Annual** means the observation year (the whole window, 1 Oct 2025 to 30 Sep 2026), not calendar years. Calendar
year grouping is not produced or tested.

---

## 6. Commands

Run these from `Implementation/synthetic-data`. Nothing calls a model or a paid API.

```bash
.venv/bin/python -m sdgen authored-check <batch>
```

Full check, including in-memory rendering and checks on the final bytes. Publishes nothing. Exit 0 means clean. Each
finding has a `code`, `subject`, `message` and `fix`. Add `--member FX-C03` to filter.

```bash
.venv/bin/python -m sdgen authored-build <batch> --out-root <new candidates folder>
```

Builds a **new** immutable candidate `<batch_id>-p<package_version>`. It refuses on any error, refuses an existing
candidate, and refuses output inside preserved datasets (Case 11, replacement v1/v2, releases, receiving exports,
reviews, contracts). The default root is `Data/synthesis/authored-candidates/`.

```bash
.venv/bin/python -m sdgen authored-verify <candidate>
```

Read-only re-verification: bytes, parsers, schema, clocks, tables, leakage, preservation, expectations, status
honesty and ZIP parity. It also confirms it changed nothing.

```bash
../ai-backend/.venv/bin/python tools/authored_receiver_check.py <candidate>/runtime/intake-bundle <out.json>
```

Runs the unmodified backend receiver, extractors and ledger/work profiles; everything ends `staged_pending_review`.

It exits 0 only when every original and native row is staged, no extraction failed, and every declared
financial/work table is recognised and projects exactly the row IDs it holds. Two outcomes are allowed but listed
separately:

- disclosed partial extraction, which is image OCR only;
- known F13 losses, which are rows lost to today's single-case/no-AFCA mapping.

Any other failure exits 1, with the original ID, table and reason.

`--ingest-scratch` also runs a disposable ingest. It needs `SDB_DATA_DIR` set to an existing **empty** directory
under the system temp area, away from the backend's default store. Anything else is refused (exit 2) before a runtime
is created, and the validated path is bound explicitly into the runtime settings.

```bash
.venv/bin/python -m sdgen authored-diff <old candidate> <new candidate>
```

Compares two candidates original by original.

---

## 7. Resume or repair one member

Rendering is deterministic, and the manifest holds no wall-clock time, so unchanged sources give byte-identical
originals.

1. Edit only that member's lines in `runtime/*.jsonl`, `runtime/files/` and `private/events.jsonl` /
   `expected-judgements.jsonl`. Do not regenerate other members.
2. Run `authored-check <batch> --member <id>` until it is clean, then a plain `authored-check`.
3. Bump `package_version` and run `authored-build` into the candidates folder.
4. Run `authored-diff <previous> <new>`. Only that member's originals should be listed as changed. Anything else
   changing is a red flag.

To continue an unfinished corpus, add complete members (all their parties, cases, sources and events) and rebuild.
Never add half a member. Earlier candidates are never overwritten.

---

## 8. What is and is not established

**Established mechanically:** structure, identities, clocks, offsets and chronology. Also:

- event, ledger and work coherence;
- exact bytes, attachments and occurrences;
- receiver compatibility;
- leakage of declared private terms;
- reconciled checkpoint and grain metrics from final bytes.

**Not established by any check:**

- the realism or quality of the prose;
- causal correctness of private judgements;
- independent review;
- AI behaviour;
- text in image pixels.

The candidate's `review/REVIEW-STATUS.json` says `independent_source_review: not_performed` and
`ai_processing: not_performed` until those actually happen. A separate reviewer fills
`review/INDEPENDENT-REVIEW-FORM.json` after reading the runtime sources first.

**Known backend limit.** Today's ingest maps one case per original (`references.case`) and gives sources no AFCA
links, so financial and work rows for a second case, or rows carrying `afca_matter_id`, are left unresolved by its
projection. The receiver check lists exactly which rows. Keep the full links anyway; never duplicate a shared charge
to fit. Shared-matter projection is later AI integration work.

**Lineage the backend does not consume yet.** Authored `logical_document_id`, `supersedes_source_id`,
`copy_of_source_id` and `derived_from` are preserved in the manifest (`lineage`) and SOURCE-INDEX. The current
backend derives its own document and version IDs, and does not read these links or `event_time`. Do not assume
version-aware or order-aware AI behaviour until that is integrated.

**Hand back** the candidate folder (both ZIPs inside) with counts and the commands' outputs. The status is
`ready_for_review`, with separate statements of what was authored, rendered and internally checked, and that it has
not been AI-processed.
