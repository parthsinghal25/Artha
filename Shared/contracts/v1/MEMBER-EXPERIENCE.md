# Essential member-experience adoption

14 October 2026. Companion 1.0.0. Normative with `DECISIONS.md` and `shared.schema.json`; owner review is recorded separately.

Adopt the Data Engineer's [essential review](../../../Data/MEMBER-EXPERIENCE-ESSENTIAL-CONTRACT-REVIEW-2026-10-14.md), specifically C-01–C-05, E-01–E-04 and R-01–R-03, with the clarifications below. Its five-shape/twelve-measure original catalogue remains an unchanged proposal. The three accepted shape boundaries are commitment, member-effort event and scoped outcome response. A minimal communicated-outcome target is supporting reference data for the third shape, not a new ingestion feed or operational workflow.

Seven companion measure IDs explicitly crosswalk `proposal.mx.X` → `mx.X`, definition version `1.0.0`, registry `shared-measures-1.0.0`. Data's thirty-two baseline IDs remain version `0.2.0`. No percentage, satisfaction/effort composite, retention/exit, handling headline, pending-commitment stock or closed-case satisfaction rollup is adopted.

| Companion ID | Unit and time basis | First UI scope |
|---|---|---|
| `mx.commitments.recorded` | Unique commitment occurrences by promise activity | Case detail and cohort context; internal promises a separately selected scope |
| `mx.commitments.follow_through` | Original-due cohort; six mutually exclusive outcome counts at evidence cutoff | Essential V12 |
| `mx.effort.repeated_explanation_contacts` | Unique supported repeated-explanation contacts by contact activity | Case detail first; portfolio view later |
| `mx.effort.repeated_submissions` | Unique supported participant repeat-submission attempts by submission activity | Case detail first; portfolio view later |
| `mx.effort.repeat_valid_evidence_requests` | Unique eligible later institution request occasions by request activity | Essential V12 |
| `mx.effort.members_repeat_valid_evidence` | Known represented-member union from eligible request occasions | Coverage/reach support; richer portfolio view later |
| `mx.responses.expressed_positions` | Response-occasion × communicated target × dimension relationships by response activity | Essential V12; explicit dimension, default service if supported |

All shapes preserve participant role, represented-member authority/identity/life state, source bindings, positive/contrary citations, precision, contextual linkage, evidence state and actual computation/publication separately. Report-only allegation is retained but does not count as a supported captured undertaking. Material conflicts remain visible; human review does not turn an uncertain proposition into a supported one. Compatible reconstruction/as-reported rules apply to every record.

## Deadline and fulfilment rules

Require an evidenced undertaking for a precise action/target, not a diary entry, request, generic intention or approval. Institution commitments in an AFCA settlement are institution undertakings. Default scope is member-facing; an explicit internal filter is separately labelled.

Store original wording and citation. An exact-time due is inclusive at that instant. A day-only due is an exclusive boundary at the next local day's start. Source-explicit timezone prevails; otherwise the declared fictional institution uses Melbourne. Vague wording remains unknown. No invented due instant or inferred service/legal deadline.

Comparison boundary and **due-cohort anchor** are separate typed fields. A day-only “by 31 May” carries `cohort_anchor={precision: day, date: 2026-05-31}` and exclusive comparison boundary 1 June at local midnight; it belongs to the May named-date cohort. An exact promise due at 1 June midnight carries an instant anchor equal to its inclusive boundary; it belongs to June. Day cohorts compare the promised local date to the query's local half-open dates; instant cohorts compare the actual instant to its half-open window. Preserve a source-explicit timezone in the inspector; never subtract 24 hours to infer the preceding calendar date across DST. Precision/boundary-kind/anchor must agree, and semantic validation checks next-day/equal-instant coherence. Unknown due has null comparison boundary/cohort anchor and is outside dated cohorts.

For explicit relative **business-day** wording the first build supports only dates interpreted against a supplied, frozen, versioned institution working calendar. It must enumerate working/nonworking dates (including any holidays), timezone and counting rule: exclude anchor day, count the next eligible local dates, due by end of the nth date unless the source specifies a time. No inferred default holiday calendar is adopted. If no accepted calendar or reliable anchor exists, due remains uninterpreted. Absolute day/instant promises work without a calendar. This avoids blocking source writing while preventing guessed holidays.

Compare fulfilment to the promised action and criteria. Update, explanation, approval, processing, settlement and receipt are distinct. Date-only completion covers the entire local day; on-time requires every possible completion instant to be on time, late requires every possible completion instant to be late. An interval straddling the due is unknown. `time_value.interval` bounds are inclusive possible instants; day precision uses the local-day half-open interval, not an invented final millisecond.

Evaluate **original opportunity** separately from current state. Positive cancellation before the opportunity or a proven unactivated condition can justify not-applicable; a post-deadline cancellation/extension cannot erase the original opportunity. Uncertain activation/cancellation or material fulfilment conflict yields unknown reasoning. Known eligible due plus unknown fulfilment stays inside the cohort's unknown bucket. Unknown due stays outside the dated cohort and is separately disclosed as missing eligibility.

Six original-due states: supported action fulfilled on-time; supported fulfilled late; explicit nonfulfilment after an elapsed applicable due; applicable due not yet elapsed; positive inapplicability at the original opportunity; otherwise unknown. A later supported fulfilment updates the current original-due result to late, preserving earlier versions. Missing confirmation alone never proves failure. Revised-due performance is optional and separately labelled; never overwrite original due history.

## Repeated effort

Contact, submission attempt and institution request are distinct canonical occasions. A multi-file submission or multi-item request counts once if at least one child qualifies. Repeated source representations/forwards/receipts do not create occasions. Canonicalisation requires supported occurrence references; uncertain duplicate identity cannot silently collapse or multiply confirmed contributors.

Count **repeat submission occasions**, including a supported participant attempt even if delivery is unknown/not received. Keep receipt state separate. Prior participant submission, same item/version, same requirement and later order require evidence. Exclude staff forwarding and changed/corrected versions. This is an explicit adoption of Data E-02's attempt rule, not a document-receipt count.

Previously valid repeat request requires recognised prior receipt/validity for that item/version/applicable requirement **before** the later request. Later assessment, a certificate's own wording, upload receipt or unsupported member claim cannot substitute. Continuing validity and order need evidence; missing assessment or ambiguous same-day order is uncertain. Changed/expired/incomplete requirements remain legitimate exclusions. Children retain their own reason/state even when another child qualifies the request.

A repeated explanation requires a continuing material concern, prior participant contact and supported problem continuity. Same member/topic alone cannot merge unrelated problems. Legitimate repetition is still observed repetition and never automatically avoidable. Member reach recomputes known represented-member union; beneficiaries/representatives do not become extra members or living retention evidence.

## Responses and communicated targets

Each target identifies a communicated explanation, decision, handling action or service outcome, its communication occurrence, recipient and evidence. It is not an inferred case-wide outcome. A response occasion binds one respondent communication action; different participants in an email thread are separate occasions. Count a canonical occasion/target/dimension relationship once. Confirm target communication/order and source-backed recipient/authority; unresolved target is an uncertain candidate outside the supported relationship total.

An unidentified response target is represented by null target ID/version, `target_identity_state=unidentified`, uncertain/excluded linkage, a reason and an unknown dimension when necessary. Its analysis/candidate ID identifies the observed response candidate, **not** a invented communicated target. A known candidate target may have real IDs with uncertain linkage. Only supported resolvable target/version/dimension relationships enter confirmed totals; unidentified candidates are disclosed in candidate-relationship coverage, not duplicated into the confirmed outcome-position unknown bucket. Likewise unidentified commitment action targets/item/version/requirement comparisons can remain uncertain with null identity; `eligible_repeat` children and supported commitment targets require actual resolvable references. Never manufacture IDs to satisfy a schema.

Position values are accept, disagree, unresolved, update-only, unknown and **conflicting**. Conflicting clauses on the same relationship count once with both citations. Payment acceptance and compensation disagreement are separate targets. Silence creates no response. “Thanks for the update” is update-only for that communication, not acceptance of service fulfilment. An actual known-target response with unclassifiable position can populate unknown. A later same-respondent/target expression can contextualise the current state but does not erase a genuine earlier activity occurrence.

The UI must label original-due, contact/submission/request and response activity periods accurately. No-response coverage, missing identity/time/target, uncertain linkage and outcome uncertainty are distinct; they are not all a single inferred satisfaction bucket.

## Acceptance boundaries

Implement explicit negative examples from the Data review: vague due; date-only straddle; later cancellation/extension; valid-plus-new-item request; absent prior validity; repeated submission plus staff forward; unrelated same-member problem; split target positions; same-target conflict; silent closure; denied/future proof. Isolated contract examples are not rendered customer records or evaluation gold. Independent evidence-conditioned gold is prepared from accepted sources; model predictions never create their own key.
