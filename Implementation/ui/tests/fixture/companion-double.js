// TEST FIXTURE ONLY — never served to the app, never real data.
// A tiny deterministic stand-in for the intelligence companion 1.1.0 and the
// few Shared reads the analytical views touch, used to exercise states that
// the real pilot does not show on demand: a month with no received cases, a
// clipped final week, a holdout case in the denominator, ranking ties, a
// changed publication, a changed permission revision and a malformed
// response. Every label below says "Fixture".

const PUB = (id, n = 1, pending = 0) => ({
  publication_id: id, publication_policy: 'synthetic-demo-v1', temporal_policy: 'demo-held-day-v1', view_date: '2026-09-30',
  knowledge_end_exclusive: '2026-10-01T00:00:00+10:00',
  watermarks: { input_watermark: '2026-09-30T16:00:00+10:00', knowledge_end_exclusive: '2026-10-01T00:00:00+10:00', last_memory_assessment: '2026-10-17T10:00:00Z', last_registry: '2026-10-17T10:00:00Z', published_at: `2026-10-17T1${n}:00:00Z` },
  coverage: { cases_in_view: 7, eligible_in_view: 6, extraction_partial_or_failed: 0, memories: 6 - pending, not_eligible_unassessed: 1, pending_memories: pending, unassessed_pairs: 0, detail: 'section=coverage' },
  model_assessed: true, human_review: 'not_performed', analysis_set_version: `ASET-FIX${n}`, stale: false,
});

// Receipt dates (Melbourne calendar). h1 is a holdout: received, never assessed.
export const CASES = {
  c1: '2026-07-10', c2: '2026-07-20',
  c3: '2026-09-02', c4: '2026-09-22', c5: '2026-09-29', h1: '2026-09-30', c6: '2026-09-25',
};
export const ISSUES = {
  'ISS-A': { label: 'Fixture issue A', included: ['c1', 'c3', 'c4'], uncertain: ['c5'] },
  'ISS-B': { label: 'Fixture issue B', included: ['c2', 'c3', 'c4', 'c5'], uncertain: [] },
  'ISS-C': { label: 'Fixture issue C', included: ['c5', 'c6'], uncertain: ['c4'] },
};

// Fixture AFCA matters: one per month, one shared by two cases, one closed (withdrawn).
const MATTERS = [
  { afca_matter_id: 'M-FIX-1', current_stage: 'registration_referral', closure_route: null, received_date: '2026-07-15', closed_date: null, case_ids: ['c1', 'c3'], multi_case: true, association_refs: [], stage_evidence: 'cited', stage_history: [] },
  { afca_matter_id: 'M-FIX-2', current_stage: 'closed', closure_route: 'withdrawn', received_date: '2026-08-10', closed_date: '2026-09-05', case_ids: ['c2'], multi_case: false, association_refs: [], stage_evidence: 'cited', stage_history: [] },
  { afca_matter_id: 'M-FIX-3', current_stage: 'decision', closure_route: null, received_date: '2026-09-12', closed_date: null, case_ids: ['c4'], multi_case: false, association_refs: [], stage_evidence: 'cited', stage_history: [] },
];
// Fixture-only negative net association (a reversal larger than the payments): ISS-C −A$45.00 from F-NEG.
const ASSOC = { 'ISS-C': { remedies_paid: { contributors: ['F-NEG'], unit: 'AUD_cents', value: -4500 } }, 'ISS-A': { remedies_paid: { contributors: ['F1'], unit: 'AUD_cents', value: 10000 } } };

const MONTHS = [['2026-07', '2026-07-01', '2026-08-01'], ['2026-08', '2026-08-01', '2026-09-01'], ['2026-09', '2026-09-01', '2026-10-01']];
const WEEKS = [['2026-09-21/W', '2026-09-21', '2026-09-28'], ['2026-09-28/W', '2026-09-28', '2026-10-01']];
const DAYS = Array.from({ length: 10 }, (_, i) => { const d = `2026-09-${String(21 + i).padStart(2, '0')}`; const n = new Date(Date.UTC(2026, 8, 22 + i)).toISOString().slice(0, 10); return [d, d, n]; });
const QUARTERS = [['2026-Q3', '2026-07-01', '2026-10-01']];
const OY = [['OY2025-26', '2025-10-01', '2026-10-01']];
const GRAINS = { month: MONTHS, week: WEEKS, day: DAYS, quarter: QUARTERS, observation_year: OY };

// Fixture case memories (c3 plain support; c4 support + contrary + a contradiction + open questions).
const claim = (id, src, quote, extra = {}) => ({ claim_id: id, quote, note: `Fixture note ${id}`, ref: { source_id: src, content_sha256: 'a'.repeat(64), anchor: { kind: 'text', start_codepoint: 0, end_codepoint: quote.length } }, modality: 'recorded', kind: 'event', actor: 'business', author_role: 'case_handler', business_date: '2026-09-10', semantic_type: 'fixture', ...extra });
const memDetail = (cid, ms, more = {}) => ({ memory_id: `MEM-${cid}`, revision: 1, memberships: ms, factors: [], narrative: [], contradictions: [], unresolved_questions: [], responses: [], producer: { provider: 'fixture', model: 'fixture-model' }, coverage: { state: 'complete' }, critique: {}, human_review: 'not_performed', model_assessed: true, ...more });
const MEMORIES = {
  c3: { items: [claim('CL-3a', 'SRC-F3', 'Fixture: the status was shown as complete before the work was done.')],
    detail: memDetail('c3', [{ issue_id: 'ISS-A', label: 'Fixture issue A', state: 'included', support_claim_ids: ['CL-3a'], contrary_claim_ids: [], reason: 'Fixture reason: status shown before completion.', evidence_basis: 'direct', claims_shown: 1, claims_total: 1 }]) },
  c4: { items: [claim('CL-4a', 'SRC-F4', 'Fixture: member told the change was done on 1 September.'), claim('CL-4b', 'SRC-F4', 'Fixture: the system log shows activation on 6 September.', { modality: 'denied' }), claim('CL-4c', 'SRC-F4', 'Fixture: handler notes the record was corrected.')],
    detail: memDetail('c4', [{ issue_id: 'ISS-A', label: 'Fixture issue A', state: 'included', support_claim_ids: ['CL-4a'], contrary_claim_ids: ['CL-4b'], reason: 'Fixture reason: told done before activation.', evidence_basis: 'direct', claims_shown: 3, claims_total: 3 }], {
      contradictions: [{ note: 'Fixture contradiction: CL-4a and CL-4b give different dates.', claim_ids: ['CL-4a', 'CL-4b'] }],
      unresolved_questions: ['Fixture question: was the member informed of the later date?'],
      narrative: [{ text: 'Fixture summary sentence.', claim_ids: ['CL-4c'] }],
    }) },
};

const v = (list, unit = 'cases') => ({ contributors: [...list].sort(), unit, value: list.length });
const inB = (d, [, s, e]) => d >= s && d < e;

export class CompanionDouble {
  constructor() {
    this.publication = 'IPUB-FIXTURE-1';
    this.pubN = 1;
    this.revision = 'PERM-fixture-1';
    this.ops = [];
    this.breakSection = null; // section whose response is made malformed
    this.wrongSeries = null; // issue whose published series disagrees with its membership
    this.ctxN = 0;
    this.noBurdenGrains = []; // grains whose burden series is not published (as the pilot has no day series)
    this.pending = []; // eligible cases without a case memory yet (a partial publication): unassessed, not holdout
  }

  context() {
    this.ctxN += 1;
    return {
      context_id: `CTX-fixture-${this.ctxN}`, contract_version: '1.0.0', release_id: 'REL-fixture', scope_token: 'SCOPE-fixture', view_id: 'complaints-leadership',
      permission_revision: this.revision, capabilities: ['aggregate', 'cases', 'contributors', 'sources'], timezone: 'Australia/Melbourne',
      evidence_as_of: '2026-10-01T00:00:00+10:00', evidence_boundary: 'exclusive_stock_endpoint', origin_population_reference: null,
      analysis_mode: 'reconstructed', analysis_as_of: null, source_set_version: 'SSV-fixture', analysis_set_version: `ASET-FIX${this.pubN}`,
      taxonomy_version: 'TAXV-fixture', supported_definitions: [], supported_response_dimensions: [],
      resolved_at: new Date().toISOString(), expires_at: new Date(Date.now() + 300000).toISOString(), synthetic_demo: true,
    };
  }

  transport() {
    return async (body) => {
      this.ops.push(body.operation === 'intelligence.get' ? `${body.operation}:${body.selection.section}${body.selection.grain ? `@${body.selection.grain}` : ''}` : body.operation);
      if (body.operation === 'intelligence.get') return { httpStatus: 200, json: this.intel(body) };
      if (body.operation === 'case.memory.get') return { httpStatus: 200, json: MEMORIES[body.case_id] && !this.pending.includes(body.case_id) ? this.memory(body) : this.err(body, 'RESOURCE_UNAVAILABLE') };
      return { httpStatus: 200, json: this.err(body, 'RESOURCE_UNAVAILABLE') };
    };
  }

  memory(body) {
    const m = MEMORIES[body.case_id];
    const ctx = { ...this.context(), context_id: body.context_id };
    return { operation: body.operation, request_id: 'REQ-fixture', status: 'ok', context: ctx, payload: { publication: PUB(this.publication, this.pubN, this.pending.length), section: 'memory', items: m.items, next_cursor: null, total_items: m.items.length, detail: m.detail } };
  }

  err(body, code) {
    return { operation: body.operation, request_id: 'REQ-fixture', status: 'unavailable', context: null, error: { code, message: 'Fixture: not available', retryable: false, correlation_id: 'fixture' } };
  }

  ok(body, section, items, detail) {
    const ctx = body.context_id ? { ...this.context(), context_id: body.context_id } : this.context();
    const payload = { publication: PUB(this.publication, this.pubN, this.pending.length), section, items, next_cursor: null, total_items: items.length };
    if (detail) payload.detail = detail;
    if (this.breakSection === section) payload.section = 'something-else';
    return { operation: body.operation, request_id: 'REQ-fixture', status: 'ok', context: ctx, payload };
  }

  intel(body) {
    const s = body.selection; const g = s.grain;
    const all = Object.keys(CASES);
    const unassessedIds = ['h1', ...this.pending];
    const assessed = all.filter((c) => !unassessedIds.includes(c));
    const keep = (list) => list.filter((c) => !this.pending.includes(c));
    const open = ['c4', 'c5', 'c6', 'h1'];
    switch (s.section) {
      case 'coverage': return this.ok(body, 'coverage', [{ cases_in_view: 7, not_eligible_unassessed: ['h1'], checks: { fixture: true } }]);
      case 'publications': return this.ok(body, 'publications', [PUB(this.publication, this.pubN, this.pending.length)]);
      case 'overview': return this.ok(body, 'overview', [{
        overview: {
          cases_received_to_date: v(all), assessed_cases: v(assessed), members: v(['m1', 'm2', 'm3'], 'members'), not_eligible_unassessed_cases: ['h1'],
          backlog: { open: v(open), closed: v(['c1', 'c2', 'c3']), lifecycle_unknown: v([]), lifecycle_conflicting: v([]), open_age_days: { by_case: { c4: 8, c5: 1, c6: 200 }, median: 8, max: 200 } },
          burden_total: { remedies_paid: v(['F1'], 'AUD_cents'), complaint_expenses_paid: v([], 'AUD_cents'), recorded_work: { ...v(['W1'], 'seconds'), value: 3600 } },
          burden_unallocated_to_any_issue: { remedies_paid: v([], 'AUD_cents'), complaint_expenses_paid: v([], 'AUD_cents'), recorded_work: v([], 'seconds') },
        },
        issue_count: 3,
      }]);
      case 'issues': return this.ok(body, 'issues', Object.entries(ISSUES).map(([id, x]) => ({
        issue_id: id, definition: { issue_id: id, label: x.label, definition: 'Fixture definition', definition_version: 1, level: 'mechanism', dimension: 'service', lineage: [], status: 'active' },
        membership: { included: v(keep(x.included)), uncertain: v(keep(x.uncertain)), ...(this.pending.length ? { no_memory: v(this.pending) } : {}) }, affected_members: v(['m1'], 'members'), open_included: v(keep(x.included).filter((c) => open.includes(c))),
      })));
      case 'alerts': return this.ok(body, 'alerts', Object.keys(ISSUES).map((id) => ({
        issue_id: id, state: id === 'ISS-C' ? 'insufficient_baseline' : 'no_alert', absolute_change: 0,
        current_window: { affected: v([]), assessed_arrivals: 0, start: '2026-09-03', end_inclusive: '2026-09-30' },
        baseline_window: { affected: v([]), affected_per_28_days: 0, assessed_arrivals: 0, start: '2026-06-11', end_exclusive: '2026-09-03' },
        persistent_open_over_90_days: v([]), rule: { baseline_days: 84, current_days: 28, min_baseline_assessed_arrivals: 3, min_current_new_cases: 3, note: 'Fixture heuristic', rule_version: 'fixture-rule' },
        note: 'Fixture',
      })));
      case 'arrivals': return this.ok(body, 'arrivals', GRAINS[g].map((b) => {
        const R = all.filter((c) => inB(CASES[c], b));
        return { bucket: b[0], start: b[1], end_exclusive: b[2], cases: v(R), members: v(R.map((c) => `m-${c}`), 'members'), assessed_cases: v(R.filter((c) => !unassessedIds.includes(c))), unassessed_cases: R.filter((c) => unassessedIds.includes(c)) };
      }));
      case 'issue': {
        const x = ISSUES[s.issue_id];
        return this.ok(body, 'issue', GRAINS[g].map((b) => {
          let hit = keep(x.included).filter((c) => inB(CASES[c], b));
          if (this.wrongSeries === s.issue_id && b[0] === '2026-09') hit = hit.slice(1);
          const R = all.filter((c) => inB(CASES[c], b) && !unassessedIds.includes(c));
          return { bucket: b[0], affected_new_cases: v(hit), first_evidenced: v(hit), share_of_assessed_arrivals: { contributors: hit, numerator: hit.length, denominator: R.length, unassessed: [], uncertain: [], value: R.length ? hit.length / R.length : null } };
        }), { issue: { issue_id: s.issue_id, label: x.label, assessment_scope: { not_eligible_cases_unassessed: ['h1'] }, associated_burden: ASSOC[s.issue_id] || {}, discrimination: { state: 'ok' } }, definition: { label: x.label, definition: 'Fixture definition', definition_version: 1, level: 'mechanism', dimension: 'service', lineage: [] } });
      }
      case 'afca': return this.ok(body, 'afca', MATTERS, {
        matters_by_current_stage: Object.fromEntries(['registration_referral', 'decision', 'closed'].map((st) => [st, v(MATTERS.filter((m) => m.current_stage === st).map((m) => m.afca_matter_id), 'matters')])),
        not_assessed_matters: 0, cases_with_external_matter: v(['c1', 'c2', 'c3', 'c4']), burden_of_cases_with_external_matter: {}, note: 'Fixture matters',
      });
      case 'burden': if (this.noBurdenGrains.includes(g)) return this.ok(body, 'burden', [], { total: {} });
        return this.ok(body, 'burden', GRAINS[g].map((b) => ({ bucket: b[0], remedies_paid: v(b[0] === '2026-09' ? ['F1'] : [], 'AUD_cents'), complaint_expenses_paid: v([], 'AUD_cents'), recorded_work: { ...v(b[0] === '2026-09' ? ['W1'] : [], 'seconds'), value: b[0] === '2026-09' ? 3600 : 0 } })).map((x) => (x.remedies_paid.value ? { ...x, remedies_paid: { ...x.remedies_paid, value: 10000 } } : x)), { total: {} });
      default: return this.err(body, 'NOT_CALCULATED');
    }
  }
}
