// Exact contributing populations. A chart mark opens a descriptor (URL `pop`)
// naming its true unit — cases, financial events, work entries, commitments,
// effort or response occurrences — and the published ID set behind it. The
// drawer shows a population header (origin, period/stock basis, knowledge,
// publication, count, unit) and compact cards for that unit, paged. A
// companion ID set is a UI selection bound to this publication; it is never
// presented as a Shared population reference.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { openDrawer } from '../ui/drawer.js';
import { loading, outcomeState, stateBox } from '../ui/states.js';
import { ids, intersect, reach, ageBands } from '../lib/calc.js';
import { arrivalsIndex } from '../lib/calc.js';
import { audMinor, secondsToHours, count } from '../lib/format.js';
import { fmtDate, knownThrough, bucketList, melbourneDate } from '../lib/periods.js';
import { label } from './model.js';

const PAGE = 12;

export function openPopulationFromUrl(app, loc, ctx) {
  const desc = loc.params.get('pop');
  if (!desc) return;
  const close = () => app.router.setParams({ pop: null, more: null, pg: null });
  const trigger = document.querySelector('main h1');
  const body = h('div', { class: 'stack' }, loading('Resolving the exact population'));
  openDrawer({ title: 'Contributing population', body, trigger, wide: true, onClose: close });
  resolve(app, ctx, desc).then((pop) => {
    if (!body.isConnected) return;
    if (pop.kind !== Outcome.OK) { mount(body, outcomeState(pop.out || pop)); return; }
    renderPopulation(app, ctx, body, pop, desc);
  });
}

// ---- descriptor resolution ----------------------------------------------

async function issueSet(app, ctx, id) {
  const iss = await app.sec('issues', {}, ctx.signal);
  if (iss.kind !== Outcome.OK) return { out: iss };
  const it = iss.items.find((x) => x.issue_id === id);
  if (!it) return { out: { kind: Outcome.UNAVAILABLE, error: { code: 'RESOURCE_UNAVAILABLE', message: 'Issue not in this publication', retryable: false, correlation_id: 'ui' } } };
  return { it };
}

async function arrivalsFor(app, grain, signal) {
  const a = await app.sec('arrivals', { grain }, signal);
  if (a.kind !== Outcome.OK) return { out: a };
  return { idx: arrivalsIndex(a.items), buckets: bucketList(a.items, grain) };
}

const MEASURE_LABEL = {
  remedies_paid: 'Paid compensation / remedies', complaint_expenses_paid: 'Paid complaint expenses',
  principal_or_entitlement_paid: 'Principal or entitlement paid (not complaint cost)', recorded_work: 'Recorded handling effort',
};

/** Basis for received-but-unassessed cases, split into holdout and pending where the publication says which. */
export function unassessedBasis(prefix, un, ho) {
  if (!ho) return `${prefix} but not assessed; not supported contributors and not negative findings`;
  const hold = un.filter((c) => ho.has(c)).length;
  return `${prefix} but not assessed (${hold} holdout, not eligible · ${un.length - hold} pending assessment); not supported contributors and not negative findings`;
}

/** Whole-publication unassessed cases for one issue: holdouts, eligible cases without a memory, unassessed pairs. */
export function unassessedForIssue(it, detail) {
  const out = new Set(detail?.assessment_scope?.not_eligible_cases_unassessed || []);
  for (const k of ['no_memory', 'unassessed']) for (const c of ids(it?.membership?.[k])) out.add(c);
  return [...out].sort();
}

async function resolve(app, ctx, desc) {
  const [kind, a, b, c, d] = desc.split('~');
  const pub = app.store.binding.publication;
  const cutoff = fmtDate(knownThrough(pub.knowledge_end_exclusive));
  const base = { kind: Outcome.OK, desc, cutoff, publication: pub.publication_id };
  switch (kind) {
    case 'arr': {
      const r = await arrivalsFor(app, a, ctx.signal); if (r.out) return r;
      const bk = r.idx.get(b); const meta = r.buckets.find((x) => x.key === b);
      if (!bk) return { kind: 'missing' };
      const ho = await app.holdoutSet(ctx.signal);
      return { ...base, unit: 'cases', origin: 'Complaint Incidence', title: `Received cases · ${meta.label}`, basis: `Original receipt date in ${meta.label}`, ids: [...bk.R].sort(),
        groups: [{ id: 'all', label: 'Received', ids: [...bk.R].sort(), basis: `Original receipt date in ${meta.label}` },
          { id: 'unassessed', label: 'Received, unassessed', ids: [...bk.unassessed].sort(), basis: unassessedBasis(`Received in ${meta.label}`, [...bk.unassessed], ho) }],
        note: `${bk.assessed.size} assessed · ${bk.unassessed.size} unassessed · ${bk.members} distinct members` };
    }
    case 'reach': {
      const s = await issueSet(app, ctx, a); if (s.out) return s;
      const r = await arrivalsFor(app, b, ctx.signal); if (r.out) return r;
      const bk = r.idx.get(c); const meta = r.buckets.find((x) => x.key === c);
      if (!bk) return { kind: 'missing' };
      const rr = reach(ids(s.it.membership?.included), bk);
      const unc = [...intersect(ids(s.it.membership?.uncertain), bk.R)].sort();
      const un = [...bk.unassessed].sort();
      const ho = await app.holdoutSet(ctx.signal);
      return { ...base, unit: 'cases', issue: s.it, origin: 'Issue ranking / trend mark', title: `${label(s.it)} · ${meta.label}`,
        basis: `Supported (included) cases among cases received in ${meta.label}`, ids: rr.ids,
        groups: [{ id: 'included', label: 'Supported (included)', ids: rr.ids, basis: `Supported (included) cases among cases received in ${meta.label}` },
          { id: 'uncertain', label: 'Uncertain', ids: unc, basis: `Assessed with uncertain support, received in ${meta.label}; not supported contributors and not excluded` },
          { id: 'unassessed', label: 'Received, unassessed', ids: un, basis: unassessedBasis(`Received in ${meta.label}`, un, ho) }],
        note: `${rr.n} of ${rr.d} received cases${rr.share !== null ? ` · observed supported reach ${(rr.share * 100).toFixed(0)}%` : ''}. Uncertain and unassessed cases are not negative findings.` };
    }
    case 'fe': {
      const s = await issueSet(app, ctx, a); if (s.out) return s;
      const ser = await app.sec('issue', { issue_id: a, grain: b }, ctx.signal); if (ser.kind !== Outcome.OK) return { out: ser };
      const it = ser.items.find((x) => x.bucket === c);
      const r = await arrivalsFor(app, b, ctx.signal);
      const meta = r.buckets?.find((x) => x.key === c);
      if (!it?.first_evidenced) return { kind: 'missing' };
      const list = [...ids(it.first_evidenced)].sort();
      const complete = list.length === it.first_evidenced.value;
      return { ...base, unit: 'cases', issue: s.it, origin: 'First Evidenced', title: `${label(s.it)} · first evidenced in ${meta?.label || c}`,
        basis: `Supported cases whose support first appeared in ${meta?.label || c} (when the interpretation was learned, not receipt)`, ids: list,
        note: `${complete ? '' : `Published count ${it.first_evidenced.value}; contributor list incomplete. `}Not new arrivals and not incident onset.` };
    }
    case 'open': case 'openiss': {
      const ov = await app.sec('overview', {}, ctx.signal); if (ov.kind !== Outcome.OK) return { out: ov };
      const o = ov.items[0].overview;
      let openIds = ids(o.backlog.open);
      let iss = null;
      if (kind === 'openiss') {
        const s = await issueSet(app, ctx, a); if (s.out) return s;
        iss = s.it; openIds = intersect(openIds, ids(s.it.membership?.included));
      }
      const band = kind === 'open' ? a : b;
      const { bands, unknown } = ageBands(openIds, o.backlog.open_age_days?.by_case);
      const sel = !band ? [...openIds].sort() : band === 'unknown' ? unknown : (bands.find((x) => x.id === band)?.ids || []);
      const bl = !band ? 'all ages' : band === 'unknown' ? 'unknown age' : bands.find((x) => x.id === band)?.label;
      return { ...base, unit: 'cases', issue: iss, origin: 'Backlog Age Distribution', title: `Open at cutoff${iss ? ` · ${label(iss)}` : ''} · ${bl}`,
        basis: `Open at evidence cutoff ${cutoff} (stock, not arrivals)`, ids: sel, ages: o.backlog.open_age_days?.by_case,
        note: 'Complaint age from published per-case ages; not time waiting on a team. Representative case = oldest known age (deterministic, not statistically typical).' };
    }
    case 'mem': {
      const s = await issueSet(app, ctx, a); if (s.out) return s;
      let list;
      let ho = null;
      if (b === 'unassessed') {
        const ser = await app.sec('issue', { issue_id: a, grain: 'month' }, ctx.signal);
        list = ser.kind === Outcome.OK ? unassessedForIssue(s.it, ser.detail?.issue) : [];
        ho = new Set(ser.detail?.issue?.assessment_scope?.not_eligible_cases_unassessed || []);
      } else list = [...ids(s.it.membership?.[b])].sort();
      return { ...base, unit: 'cases', issue: s.it, origin: 'Issue population', title: `${label(s.it)} · ${b}`, basis: 'Whole publication membership (all received to date)', ids: list,
        note: b === 'excluded' ? 'Excluded = assessed with evidence against inclusion. Never confused with unassessed.' : b === 'unassessed' ? `${unassessedBasis('Received', list, ho)}.` : null, membershipState: b };
    }
    case 'alert': {
      const al = await app.sec('alerts', {}, ctx.signal); if (al.kind !== Outcome.OK) return { out: al };
      const x = al.items.find((y) => y.issue_id === a);
      const s = await issueSet(app, ctx, a); if (s.out) return s;
      const w = b === 'baseline' ? x.baseline_window : x.current_window;
      const v = b === 'persistent' ? x.persistent_open_over_90_days : w.affected;
      const span = b === 'persistent' ? `open more than 90 days at cutoff ${cutoff}` : b === 'baseline' ? `received ${fmtDate(w.start)} – before ${fmtDate(w.end_exclusive)}` : `received ${fmtDate(w.start)} – ${fmtDate(w.end_inclusive)}`;
      return { ...base, unit: 'cases', issue: s.it, origin: 'Emerging Issue Signal', title: `${label(s.it)} · ${b === 'persistent' ? 'persistent open' : `${b} window`}`, basis: `Published signal window: ${span}`, ids: [...ids(v)].sort(),
        note: `${x.rule.note}. Newly discovered older cases are not new arrivals.` };
    }
    case 'fin': case 'work': {
      const measure = kind === 'work' ? 'recorded_work' : a;
      const grain = kind === 'work' ? a : b; const key = kind === 'work' ? b : c;
      const bu = await app.sec('burden', { grain }, ctx.signal); if (bu.kind !== Outcome.OK) return { out: bu };
      const it = bu.items.find((x) => x.bucket === key);
      const r = await arrivalsFor(app, grain, ctx.signal);
      const meta = r.buckets?.find((x) => x.key === key);
      if (!it) return { kind: 'missing' };
      return { ...base, unit: measure === 'recorded_work' ? 'work' : 'financial', origin: MEASURE_LABEL[measure], title: `${MEASURE_LABEL[measure]} · ${meta?.label || key}`,
        basis: `${measure === 'recorded_work' ? 'Work dated' : 'Paid'} in ${meta?.label || key} (event date) · event union, shared records once`, ids: [...ids(it[measure])].sort(),
        published: it[measure], note: bu.detail?.total?.note };
    }
    case 'iburden': case 'pburden': case 'unalloc': case 'afcab': {
      let v; let title; let basis; let iss = null; const unit = kind === 'pburden' || kind === 'unalloc' || kind === 'afcab' ? a : b;
      if (kind === 'iburden') {
        const ser = await app.sec('issue', { issue_id: a, grain: 'month' }, ctx.signal); if (ser.kind !== Outcome.OK) return { out: ser };
        const s = await issueSet(app, ctx, a); iss = s.it;
        v = ser.detail?.issue?.associated_burden?.[unit];
        title = `${label(iss)} · ${MEASURE_LABEL[unit]}`; basis = 'Associated with included cases over the whole publication window (1 Oct 2025 – 30 Sep 2026); not causal, overlaps other issues';
      } else if (kind === 'afcab') {
        const af = await app.sec('afca', {}, ctx.signal); if (af.kind !== Outcome.OK) return { out: af };
        v = af.detail?.burden_of_cases_with_external_matter?.[unit];
        title = `Recorded burden of cases associated with AFCA · ${MEASURE_LABEL[unit]}`; basis = 'All recorded events of cases linked to an AFCA matter — not matter-only fees';
      } else {
        const ov = await app.sec('overview', {}, ctx.signal); if (ov.kind !== Outcome.OK) return { out: ov };
        const o = ov.items[0].overview;
        v = kind === 'pburden' ? o.burden_total?.[unit] : o.burden_unallocated_to_any_issue?.[unit];
        title = `${kind === 'pburden' ? 'Portfolio union' : 'Not associated with any issue'} · ${MEASURE_LABEL[unit]}`; basis = 'Whole publication window · event union';
      }
      if (!v) return { kind: 'missing' };
      return { ...base, unit: unit === 'recorded_work' ? 'work' : 'financial', issue: iss, origin: 'Recorded Burden', title, basis, ids: [...ids(v)].sort(), published: v };
    }
    case 'sq': {
      // Shared analysis.query cell contributors (exact selected-period issue burden).
      const [measure, issueId, start, end] = [a, b, c, d];
      const out = await sharedCell(app, measure, issueId === '-' ? [] : [issueId], start, end, ctx.signal);
      if (out.kind !== Outcome.OK) return { out };
      const s = issueId !== '-' ? await issueSet(app, ctx, issueId) : null;
      return { ...base, unit: measure === 'work.recorded_hours' ? 'work' : 'financial', issue: s?.it, origin: 'Shared analysis result', title: `${s?.it ? `${label(s.it)} · ` : ''}${measure}`,
        basis: `Activity ${fmtDate(melbourneDate(start))} – ${fmtDate(knownThrough(end))} · result ${out.payload.result_id} cell ${out.cell.key} (${out.cell.point.state}${out.cell.point.reason ? `: ${out.cell.point.reason}` : ''})`,
        ids: out.items.map((i) => i.entity_id), contributorItems: new Map(out.items.map((i) => [i.entity_id, i])), sharedRef: { result_id: out.payload.result_id, cell_key: out.cell.key, population_id: out.cell.population_id } };
    }
    case 'sqm': {
      // Shared analysis.query filtered to one AFCA matter (matter-scoped records only).
      const [measure, matterId, start, end] = [a, b, c, d];
      const out = await sharedCell(app, measure, [], start, end, ctx.signal, { afca_matter_ids: [matterId] });
      if (out.kind !== Outcome.OK) return { out };
      return { ...base, unit: measure === 'work.recorded_hours' ? 'work' : 'financial', origin: 'AFCA matter-scoped records', title: `AFCA matter ${short(matterId)} · ${measure}`,
        basis: `Records linked to the matter itself, ${fmtDate(melbourneDate(start))} – ${fmtDate(knownThrough(end))} · result ${out.payload.result_id} (${out.cell.point.state})`,
        ids: out.items.map((i) => i.entity_id), contributorItems: new Map(out.items.map((i) => [i.entity_id, i])), sharedRef: { result_id: out.payload.result_id, cell_key: out.cell.key, population_id: out.cell.population_id } };
    }
    case 'sqc': {
      // Shared analysis.query filtered to one case (that case's recorded records).
      const [measure, caseId, start, end] = [a, b, c, d];
      const out = await sharedCell(app, measure, [], start, end, ctx.signal, { case_ids: [caseId] });
      if (out.kind !== Outcome.OK) return { out };
      return { ...base, unit: measure === 'work.recorded_hours' ? 'work' : 'financial', origin: 'Case recorded impact', title: `Case ${short(caseId)} · ${measure}`,
        basis: `Records of this case, ${fmtDate(melbourneDate(start))} – ${fmtDate(knownThrough(end))} · result ${out.payload.result_id} (${out.cell.point.state})`,
        ids: out.items.map((i) => i.entity_id), contributorItems: new Map(out.items.map((i) => [i.entity_id, i])), sharedRef: { result_id: out.payload.result_id, cell_key: out.cell.key, population_id: out.cell.population_id } };
    }
    case 'eff': case 'cmt': case 'cmtdue': case 'rsp': {
      const ex = await app.sec('experience', {}, ctx.signal); if (ex.kind !== Outcome.OK) return { out: ex };
      const e = ex.items[0];
      const v = kind === 'eff' ? e.member_effort?.[a] : kind === 'cmt' ? e.commitments?.by_status?.[a] : kind === 'cmtdue' ? e.commitments?.due_passed_without_confirmation : e.responses?.[a];
      const occ = await occurrences(app, ctx, kind, a);
      if (occ.out) return occ;
      const t = kind === 'eff' ? `Member effort · ${a}` : kind === 'rsp' ? `Expressed position · ${a.replace(':', ' — ')}` : kind === 'cmtdue' ? 'Commitments due without recorded confirmation' : `Commitments · ${a}`;
      return { ...base, unit: 'occurrence', origin: 'Member Experience', title: t, basis: 'Publication scope (all held evidence to cutoff), not a selected-period count',
        ids: occ.list.map((x) => x.id), occurrences: occ.list, published: v,
        note: `${v?.value ?? '?'} published ${v?.unit || 'occurrences'}; ${occ.list.length} resolved to case memories${occ.list.length !== v?.value ? ' — mismatch shown, not hidden' : ''}. ${kind.startsWith('cmt') ? 'Missing confirmation is unknown, not breach.' : ''}` };
    }
    case 'rep': {
      const ex = await app.sec('experience', {}, ctx.signal); if (ex.kind !== Outcome.OK) return { out: ex };
      const v = ex.items[0].contacts?.cases_with_repeat_inbound_contact;
      return { ...base, unit: 'cases', origin: 'Member Experience', title: 'Cases with repeat inbound contact', basis: 'Publication scope', ids: [...ids(v)].sort() };
    }
    case 'ivl': {
      const co = await app.sec('continuity', {}, ctx.signal); if (co.kind !== Outcome.OK) return { out: co };
      const list = co.detail?.summary?.[a] || [];
      return { ...base, unit: 'cases', origin: 'Completion intervals', title: a.replaceAll('_', ' '), basis: 'Closed intervals in the publication; administrative closure is not service completion', ids: [...list].sort() };
    }
    case 'afcam': {
      const af = await app.sec('afca', {}, ctx.signal); if (af.kind !== Outcome.OK) return { out: af };
      const m = af.items.find((x) => x.afca_matter_id === a);
      if (!m) return { kind: 'missing' };
      return { ...base, unit: 'cases', origin: 'AFCA matter', title: `Cases linked to AFCA matter ${a.slice(0, 8)}…`, basis: 'Held factual associations', ids: [...m.case_ids].sort() };
    }
    default:
      return { kind: 'missing' };
  }
}

/** Exact Shared analysis filters: one activity window, optional issue/case/matter scope, nothing dropped. */
export function analysisFilters(issueIds, start, end, extra = {}) {
  return {
    activity_start: start, activity_end_exclusive: end, receipt_cohort_start: null, receipt_cohort_end_exclusive: null,
    receipt_basis: 'original_institution', granularity: 'none', issue_ids: issueIds, selection_operator: issueIds.length ? 'any' : 'none',
    issue_population_mode: 'all_linked_to_date', case_ids: extra.case_ids || [], afca_matter_ids: extra.afca_matter_ids || [], case_statuses: [], age_bands: [],
    taxonomy_level: null, combination_projection: 'not_applicable', parameters: {},
  };
}

/** One Shared analysis.query with exact filters, plus its cell contributors (all pages). */
export async function sharedCell(app, measureId, issueIds, start, end, signal, extra = {}) {
  const def = app.store.binding.context.supported_definitions.find((x) => x.measure_id === measureId);
  if (!def) return { kind: 'unsupported', measure: measureId };
  const filters = analysisFilters(issueIds, start, end, extra);
  const q = await app.store.shared((c) => ({ operation: 'analysis.query', context_id: c.context_id, query_id: `UI-${measureId.replace(/[^a-z]/g, '')}`, definition: { registry_id: def.registry_id, measure_id: def.measure_id, definition_version: def.definition_version }, filters, population_reference: null }), { signal });
  if (q.kind !== Outcome.OK) return q;
  const cell = q.payload.cells.find((x) => !x.population.selectors.length) || q.payload.cells[0];
  const items = [];
  if (cell?.contributor_set_id) {
    let cursor = null;
    do {
      const pg = await app.store.shared((c) => ({ operation: 'contributors.list', context_id: c.context_id, result_id: q.payload.result_id, cell_key: cell.key, cursor, page_size: 100 }), { signal });
      if (pg.kind !== Outcome.OK) return pg;
      items.push(...pg.payload.items);
      cursor = pg.payload.next_cursor;
    } while (cursor);
  }
  return { kind: Outcome.OK, payload: q.payload, cell, items };
}

/** Resolve experience occurrence IDs to the case memories that hold them (deterministic scan of published memories). */
async function occurrences(app, ctx, kind, key) {
  const ov = await app.sec('overview', {}, ctx.signal); if (ov.kind !== Outcome.OK) return { out: ov };
  const cases = ov.items[0].overview.assessed_cases.contributors;
  const mems = await Promise.all(cases.map((c) => app.store.memory(c, { signal: ctx.signal })));
  const bad = mems.find((m) => m.kind !== Outcome.OK);
  if (bad) return { out: bad };
  const list = [];
  mems.forEach((m, i) => {
    const d = m.detail; const caseId = cases[i];
    const claims = new Map(m.items.map((c) => [c.claim_id, c]));
    const cite = (cids) => (cids || []).map((x) => claims.get(x)).filter(Boolean);
    if (kind === 'eff') for (const e of d.member_effort || []) if (e.kind === key) list.push({ id: e.effort_id, caseId, title: e.kind, claims: cite(e.claim_ids) });
    if (kind === 'cmt' || kind === 'cmtdue') {
      for (const c of d.commitments || []) {
        if (kind === 'cmt' && c.status !== key) continue;
        if (kind === 'cmtdue' && !(c.due_date && c.due_date <= knownThrough(app.store.binding.publication.knowledge_end_exclusive) && !(c.evidence_claim_ids || []).length)) continue;
        list.push({ id: c.commitment_id, caseId, title: c.action, sub: `Due ${c.due_date ? fmtDate(c.due_date) : 'not stated'} · status ${c.status}`, claims: cite([c.promise_claim_id, ...(c.evidence_claim_ids || [])]) });
      }
    }
    if (kind === 'rsp') {
      const [dim, pos] = key.split(':');
      (d.responses || []).forEach((r, k) => { if (r.dimension === dim && r.position === pos) list.push({ id: `${caseId}#r${k}`, caseId, title: r.target, sub: `${r.dimension} · ${r.position}`, claims: cite(r.claim_ids) }); });
    }
  });
  return { list };
}

// ---- rendering ------------------------------------------------------------

function renderPopulation(app, ctx, body, pop, desc) {
  const groups = pop.groups || [{ id: 'all', label: 'Contributors', ids: pop.ids }];
  // The chosen state is part of the selection (URL `pg`), so case/source navigation and Back restore it.
  let group = groups.find((g) => g.id === new URL(window.location.href).searchParams.get('pg')) || groups[0];
  const listSlot = h('div', { class: 'stack' });
  const activeSlot = h('div', { class: 'pop-active' });
  const unitWords = { cases: ['case', 'cases'], financial: ['financial event', 'financial events'], work: ['work entry', 'work entries'], occurrence: ['occurrence', 'occurrences'] }[pop.unit];
  const units = (n) => `${n} ${unitWords[n === 1 ? 0 : 1]}`;
  const pickers = groups.length > 1 ? h('div', { class: 'seg', role: 'group', 'aria-label': 'Population state' }, groups.map((g) => h('button', {
    type: 'button', class: 'seg-b', 'aria-pressed': g === group ? 'true' : 'false', dataset: { group: g.id },
    onclick: (e) => {
      group = g;
      for (const b of e.currentTarget.parentElement.children) b.setAttribute('aria-pressed', String(b === e.currentTarget));
      app.router.replaceParams({ pg: g === groups[0] ? null : g.id });
      renderActive(); renderList();
    },
  }, `${g.label} · ${g.ids.length}`))) : null;
  const renderActive = () => mount(activeSlot, groups.length > 1 ? h('dl', { class: 'pop-facts pop-facts-active' },
    h('div', {}, h('dt', {}, 'Showing'), h('dd', { class: 'tnum', dataset: { activeCount: String(group.ids.length) } }, `${group.label} · ${units(group.ids.length)}`)),
    h('div', { class: 'span-3' }, h('dt', {}, 'State basis'), h('dd', {}, group.basis || pop.basis))) : null);
  const renderList = () => mount(listSlot, cardList(app, ctx, { ...pop, title: groups.length > 1 && group !== groups[0] ? `${pop.title} · ${group.label}` : pop.title }, group.ids, desc));
  mount(body,
    h('header', { class: 'pop-head' },
      h('p', { class: 'eyebrow' }, pop.origin),
      h('h3', { class: 'pop-title' }, pop.title),
      h('dl', { class: 'pop-facts' },
        h('div', {}, h('dt', {}, groups.length > 1 ? 'Chart mark' : 'Count'), h('dd', { class: 'tnum' }, units(pop.ids.length))),
        h('div', {}, h('dt', {}, groups.length > 1 ? 'Mark basis' : 'Basis'), h('dd', {}, pop.basis)),
        h('div', {}, h('dt', {}, 'Knowledge'), h('dd', {}, `Evidence to ${pop.cutoff}`)),
        h('div', {}, h('dt', {}, 'Publication'), h('dd', { class: 'mono' }, pop.publication)),
        pop.published && Number.isInteger(pop.published.value) && pop.unit !== 'occurrence'
          ? h('div', {}, h('dt', {}, 'Published total'), h('dd', { class: 'tnum' }, pop.unit === 'work' ? `${secondsToHours(pop.published.value)} (${count(pop.published.value)} s)` : audMinor(pop.published.value))) : null),
      pop.note ? h('p', { class: 'muted' }, pop.note) : null,
      pop.sharedRef ? h('p', { class: 'muted mono' }, `Shared result ${pop.sharedRef.result_id} · population ${pop.sharedRef.population_id}`) : h('p', { class: 'muted' }, 'Companion ID set bound to this publication (not a Shared population reference).')),
    pickers, activeSlot, listSlot);
  renderActive(); renderList();
}

function cardList(app, ctx, pop, list, desc) {
  if (!list.length) return stateBox('empty', `No ${pop.unit === 'cases' ? 'cases' : 'records'} in this selection`, 'Measured zero for this exact selection.', { compact: true });
  let shown = 0;
  const wrap = h('div', { class: 'card-grid' });
  const more = h('button', { type: 'button', class: 'button-secondary', onclick: () => page() }, 'Load more');
  const foot = h('div', { class: 'list-foot' });
  const page = () => {
    const next = list.slice(shown, shown + PAGE);
    shown += next.length;
    for (const id of next) wrap.appendChild(card(app, ctx, pop, id, desc));
    mount(foot, h('span', { class: 'muted' }, `Showing ${shown} of ${list.length}`), shown < list.length ? more : null);
  };
  // Representative case for stock selections: oldest known age first (labelled).
  if (pop.ages) list = [...list].sort((x, y) => (pop.ages[y] ?? -1) - (pop.ages[x] ?? -1) || (x < y ? -1 : 1));
  page();
  return h('div', {}, pop.ages && list.length ? h('p', { class: 'muted' }, 'Ordered by known age, oldest first; the first card is the labelled representative (oldest known open case).') : null, wrap, foot);
}

function card(app, ctx, pop, id, desc) {
  if (pop.unit === 'cases') return caseCard(app, ctx, id, { desc, age: pop.ages?.[id], from: pop.title });
  if (pop.unit === 'financial') return recordCard(app, ctx, 'financial_event', id, pop.contributorItems?.get(id), desc);
  if (pop.unit === 'work') return recordCard(app, ctx, 'work_entry', id, pop.contributorItems?.get(id), desc);
  const o = pop.occurrences.find((x) => x.id === id);
  return h('article', { class: 'card' },
    h('p', { class: 'card-eyebrow mono' }, o.id.startsWith(o.caseId) ? 'Response occurrence' : o.id),
    h('h4', { class: 'card-title' }, o.title),
    o.sub ? h('p', { class: 'muted' }, o.sub) : null,
    o.claims.slice(0, 2).map((c) => evidenceChip(app, c.ref, c.quote, o.caseId, desc)),
    h('a', { href: caseHref(app, o.caseId, desc, pop.title), 'data-nav': '', class: 'card-link' }, `Case ${short(o.caseId)} →`));
}

/** Per-action service states summarised by count, e.g. "2 attempted · 1 confirmed complete". */
export function serviceSummary(service) {
  const n = new Map();
  for (const x of service || []) n.set(x.value, (n.get(x.value) || 0) + 1);
  if (!n.size) return 'unknown';
  return [...n].map(([k, v]) => `${v} ${String(k).replaceAll('_', ' ')}`).join(' · ');
}

export const short = (id) => (id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id);

export function caseHref(app, caseId, desc, fromTitle) {
  return app.link(`/cases/${encodeURIComponent(caseId)}`, { o: desc || null, ot: fromTitle ? (fromTitle.length > 140 ? `${fromTitle.slice(0, 139)}…` : fromTitle) : null, ob: returnPath() });
}

/** The page (and open selection) a case was opened from, for its Back control. Overlays and nested returns are dropped. */
function returnPath() {
  const u = new URL(window.location.href);
  for (const k of ['src', 'cite', 'asset', 'parent', 'ob']) u.searchParams.delete(k);
  return u.pathname + u.search;
}

/** Compact case card; facts load lazily from case.get (and membership from the bound issue registry). */
export function caseCard(app, ctx, caseId, { desc = null, age = null, from = null } = {}) {
  const facts = h('div', { class: 'card-facts muted' }, 'Loading case facts…');
  const issuesSlot = h('div', { class: 'chips' });
  const el = h('article', { class: 'card', dataset: { case: caseId } },
    h('p', { class: 'card-eyebrow mono' }, short(caseId)),
    facts, issuesSlot,
    h('a', { href: caseHref(app, caseId, desc, from), 'data-nav': '', class: 'card-link', dataset: { focusKey: `case:${caseId}` } }, 'Open chronology →'));
  (async () => {
    const [cg, iss] = await Promise.all([
      app.store.shared((c) => ({ operation: 'case.get', context_id: c.context_id, case_id: caseId, cursor: null, page_size: 1 })),
      app.sec('issues'),
    ]);
    if (cg.kind !== Outcome.OK) { mount(facts, h('span', {}, cg.kind === Outcome.UNAVAILABLE ? 'Case not available in this access scope' : 'Case facts unavailable')); return; }
    const d = cg.payload.case_record.data; const sp = cg.payload.state_projection;
    mount(facts,
      h('span', {}, `Received ${fmtDate(d.original_receipt_date)}`),
      h('span', {}, String(d.product_or_journey || '').replaceAll('_', ' ') || 'Journey not stated'),
      h('span', { class: `badge badge-admin-${sp.administrative?.value}` }, `Admin: ${sp.administrative?.value || 'unknown'}`),
      h('span', { class: 'badge' }, `Service: ${serviceSummary(sp.service)}`),
      Number.isFinite(age) ? h('span', { class: 'tnum' }, `${age} days open`) : null);
    if (iss.kind === Outcome.OK) {
      const inc = iss.items.filter((x) => ids(x.membership?.included).has(caseId));
      mount(issuesSlot, inc.length ? [inc.slice(0, 2).map((x) => h('span', { class: 'chip' }, label(x))), inc.length > 2 ? h('span', { class: 'chip muted' }, `+${inc.length - 2} issues`) : null]
        : h('span', { class: 'chip muted' }, 'No supported issue membership'));
    }
  })();
  return el;
}

/** Financial event / work entry card resolved through Shared entity.get. */
export function recordCard(app, ctx, kind, id, contributorItem, desc) {
  const el = h('article', { class: 'card record', dataset: { record: id } }, h('p', { class: 'card-eyebrow mono' }, `${kind === 'work_entry' ? 'Work entry' : 'Financial event'} ${short(id)}`), h('p', { class: 'muted' }, 'Loading record…'));
  (async () => {
    const out = await app.store.shared((c) => ({ operation: 'entity.get', context_id: c.context_id, entity_id: id, entity_kind: kind, immutable_record_id: null, cursor: null, page_size: 20 }));
    if (out.kind !== Outcome.OK) { mount(el, h('p', { class: 'card-eyebrow mono' }, short(id)), outcomeState(out, { compact: true })); return; }
    const d = out.payload.record.data;
    let caseIds = contributorItem?.case_ids || d.case_ids || null;
    if (!caseIds && d.obligation_id) {
      const ob = await app.store.shared((c) => ({ operation: 'entity.get', context_id: c.context_id, entity_id: d.obligation_id, entity_kind: 'obligation', immutable_record_id: null, cursor: null, page_size: 20 }));
      if (ob.kind === Outcome.OK) caseIds = ob.payload.record.data.case_ids;
    }
    const all = out.payload.source_refs?.length ? out.payload.source_refs : (d.evidence_refs || []);
    const refs = all.filter((r, i) => all.findIndex((x) => x.source_id === r.source_id) === i); // one chip per source
    const firstCase = caseIds?.[0] || null;
    if (kind === 'work_entry') {
      mount(el,
        h('p', { class: 'card-eyebrow mono' }, `Work entry ${short(id)}`),
        h('p', { class: 'card-amount tnum' }, secondsToHours(d.duration_seconds), h('small', {}, ` · ${count(d.duration_seconds)} s ${d.duration_basis}`)),
        h('p', {}, `${String(d.phase || 'phase not stated').replaceAll('_', ' ')} · ${String(d.contributor_role || '').replaceAll('_', ' ')}`),
        h('p', { class: 'muted' }, `Dated ${fmtDate(d.event_date)}${d.afca_matter_ids?.length ? ` · AFCA matter ${short(d.afca_matter_ids[0])}` : ''}${d.duplicate_of_work_id ? ` · duplicate of ${short(d.duplicate_of_work_id)}` : ''}`),
        h('div', { class: 'chips' }, refs.slice(0, 2).map((r) => evidenceChip(app, r, null, firstCase, desc))),
        caseIds?.length ? h('p', {}, caseIds.map((c) => h('a', { href: caseHref(app, c, desc, 'Work entry'), 'data-nav': '', class: 'card-link' }, `Case ${short(c)} →`))) : h('p', { class: 'muted' }, 'No linked case returned'));
    } else {
      const neg = d.amount_minor < 0;
      mount(el,
        h('p', { class: 'card-eyebrow mono' }, `Financial event ${short(id)}`),
        h('p', { class: `card-amount tnum${neg ? ' is-neg' : ''}` }, audMinor(d.amount_minor), h('small', {}, ` ${d.currency}`)),
        h('p', {}, `${String(d.kind).replaceAll('_', ' ')}${d.adjusts_event_id ? ` · adjusts ${short(d.adjusts_event_id)}` : ''}`),
        h('p', { class: 'muted' }, `Effective ${fmtDate(d.effective_date)} · value ${String(d.value_basis || '').replaceAll('_', ' ')}${d.obligation_id ? ` · obligation ${short(d.obligation_id)}` : ''}`),
        h('div', { class: 'chips' }, refs.slice(0, 2).map((r) => evidenceChip(app, r, null, firstCase, desc))),
        caseIds?.length ? h('p', {}, caseIds.map((c) => h('a', { href: caseHref(app, c, desc, 'Financial event'), 'data-nav': '', class: 'card-link' }, `Case ${short(c)} →`))) : h('p', { class: 'muted' }, 'No linked case returned'));
    }
  })();
  return el;
}

/** Evidence chip: opens the cited source on the case page (or here when no case is known). */
export function evidenceChip(app, ref, quote, caseId, desc) {
  if (!ref) return null;
  const cite = ref.anchor?.kind === 'text' ? `t~${ref.anchor.start_codepoint}~${ref.anchor.end_codepoint}` : null;
  const src = ref.content_sha256 ? `${ref.source_id}~${ref.content_sha256}` : ref.source_id;
  // On the case's own page the link keeps the page's return context; elsewhere it opens the case with the source.
  const here = !caseId || window.location.pathname === `/cases/${encodeURIComponent(caseId)}`;
  const target = !here ? app.link(`/cases/${encodeURIComponent(caseId)}`, { src, cite, o: desc || null, ob: desc ? returnPath() : null }) : (() => {
    const u = new URL(window.location.href);
    u.searchParams.set('src', src);
    if (cite) u.searchParams.set('cite', cite); else u.searchParams.delete('cite');
    u.searchParams.delete('asset'); u.searchParams.delete('parent');
    return u.pathname + u.search;
  })();
  return h('a', { href: target, 'data-nav': '', class: 'chip chip-evidence', dataset: { focusKey: `src:${ref.source_id}` }, title: quote || `Source ${ref.source_id}` },
    quote ? `“${quote.length > 70 ? `${quote.slice(0, 68)}…` : quote}”` : `Source ${short(ref.source_id)}`);
}
