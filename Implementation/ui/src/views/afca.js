// AFCA subsection of Business Impact: matters exist only through held factual
// associations; the current-stage partition, received-matter series and
// closure routes are separate charts; a selected matter shows its cited stage
// timeline, linked cases and matter-scoped recorded records. The burden of
// AFCA-linked cases is labelled as such and never relabelled as AFCA fees.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { partition, columns, timeline, plural } from '../ui/viz.js';
import { ids } from '../lib/calc.js';
import { audMinor, secondsToHours } from '../lib/format.js';
import { fmtDate, melbourneMidnightUtc } from '../lib/periods.js';
import { loadTrend, popHref, failed } from './model.js';
import { sharedCell, evidenceChip, caseCard, short } from './population.js';
import { info } from '../ui/labels.js';
import { loading, outcomeState } from '../ui/states.js';

const STAGE_TONE = { registration_referral: 'b', investigation: 'c', decision: 'a', closed: 'n' };
const word = (s) => String(s || 'unknown').replaceAll('_', ' ');

export async function renderAfca(app, loc, ctx) {
  const af = await app.sec('afca', {}, ctx.signal);
  if (!ctx.isCurrent()) return;
  const head = h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, h('a', { href: app.link('/impact'), 'data-nav': '' }, 'Business Impact'), h('span', { 'aria-hidden': 'true' }, ' / '), h('span', {}, 'AFCA'));
  if (af.kind !== Outcome.OK) {
    mount(app.main, head, h('h1', {}, 'AFCA'), af.kind === Outcome.UNAVAILABLE && af.error.code === 'NOT_CALCULATED'
      ? outcomeState(af) : failed(af));
    return;
  }
  const matterId = loc.parts[2];
  if (matterId) return renderMatter(app, loc, ctx, af, matterId, head);
  const trend = await loadTrend(app, ctx, []);
  if (!ctx.isCurrent()) return;
  const d = af.detail || {};
  const st = d.matters_by_current_stage || {};
  const closed = af.items.filter((m) => m.closed_date || m.current_stage === 'closed');
  const routes = {};
  for (const m of closed) routes[m.closure_route] = (routes[m.closure_route] || 0) + 1;
  const noDate = af.items.filter((m) => !m.received_date).length;
  const bu = d.burden_of_cases_with_external_matter || {};
  const sel = matterSelection(app.loc.params.get('am'), af, st, closed, trend);
  const pick = (am) => app.setLocal({ am: am === app.loc.params.get('am') ? null : am });
  const shown = sel ? af.items.filter((m) => sel.ids.has(m.afca_matter_id)) : af.items;

  mount(app.main, head,
    h('div', { class: 'page-head' }, h('h1', {}, 'AFCA'),
      h('p', { class: 'page-sub' }, `${af.total} external-dispute matters · ${d.cases_with_external_matter?.value ?? '—'} cases linked`, info(`${d.not_assessed_matters ?? 0} further ${(d.not_assessed_matters ?? 0) === 1 ? 'matter is' : 'matters are'} linked only to unassessed cases and not assessed.${d.note ? ` ${d.note[0].toUpperCase()}${d.note.slice(1)}${/[.!?]$/.test(d.note) ? '' : '.'}` : ''} AFCA is calculated for this September publication only.`, 'About AFCA'))),
    h('div', { class: 'grid g-6-6' },
      h('section', { class: 'panel' }, h('h2', {}, 'Where each matter is', info('One stage per matter at the evidence cutoff, taken from cited source wording.', 'About stages')),
        partition({ unitWord: 'matters', parts: Object.entries(st).map(([k, v]) => ({ id: k, label: word(k), value: v.value, tone: STAGE_TONE[k] || 'a' })),
          selectedId: sel?.kind === 'stage' ? sel.key : null, onSelect: (p) => pick(`stage~${p.id}`) }),
        h('h3', {}, 'How closed matters ended'),
        closed.length ? partition({ unitWord: 'closed matters', parts: Object.entries(routes).map(([k, v]) => ({ id: k, label: word(k), value: v, tone: 'n' })),
          selectedId: sel?.kind === 'route' ? sel.key : null, onSelect: (p) => pick(`route~${p.id}`) }) : h('p', { class: 'muted' }, 'No concluded matters.')),
      h('section', { class: 'panel' }, h('h2', {}, 'Matters received'),
        trend.kind === Outcome.OK ? [
          noDate ? h('p', { class: 'panel-sub' }, `${noDate} with no receipt date`) : null,
          columns({ points: trend.cols.map((c) => { const n = af.items.filter((m) => m.received_date && m.received_date >= c.start && m.received_date < c.end).length; return { key: c.key, short: c.short, year: c.year, label: c.label, highlight: c.highlight, value: n, display: String(n), state: 'value' }; }),
            unitWord: 'matters received', height: 110, selectedKey: sel?.kind === 'recv' ? sel.key : null, onSelect: (p) => pick(`recv~${trend.grain}~${p.key}`) }),
        ] : failed(trend.out, { compact: true }))),
    h('section', { class: 'panel', 'aria-labelledby': 'h-matters' }, h('h2', { id: 'h-matters' }, 'Matters'),
      sel ? h('div', { class: 'selection-bar', role: 'status', dataset: { matterSelection: sel.kind } },
        h('span', {}, h('strong', { class: 'tnum' }, String(shown.length)), ` of ${af.items.length} matters · ${sel.label}`),
        shown.length ? null : h('span', { class: 'muted' }, ' — none in this selection (measured zero)'),
        h('button', { type: 'button', class: 'link-button', onclick: () => app.setLocal({ am: null }) }, 'Show all matters'))
        : h('p', { class: 'panel-sub' }, 'Click a stage, route or month to filter'),
      h('div', { class: 'card-grid' }, shown.map((m) => h('article', { class: 'card', dataset: { matter: m.afca_matter_id } },
        h('p', { class: 'card-eyebrow mono' }, `Matter ${short(m.afca_matter_id)}`),
        h('p', {}, h('span', { class: `badge tone-${STAGE_TONE[m.current_stage] || 'a'}`, dataset: { stage: m.current_stage }, tabindex: '-1' }, word(m.current_stage)), m.multi_case ? h('span', { class: 'badge' }, `Shared by ${m.case_ids.length} cases`) : null),
        h('p', { class: 'muted' }, `Received ${m.received_date ? fmtDate(m.received_date) : 'unknown'} · ${m.closed_date ? `closed ${fmtDate(m.closed_date)} (${word(m.closure_route)})` : 'not closed'}`),
                h('a', { href: app.link(`/impact/afca/${m.afca_matter_id}`), 'data-nav': '', class: 'card-link', dataset: { focusKey: `matter:${m.afca_matter_id}` } }, 'Matter journey →'))))),
    h('section', { class: 'panel' }, h('h2', {}, 'Cost of AFCA-linked cases', info('Every recorded event of the linked cases over the publication window — not AFCA fees or matter-only cost. Shared records count once. Matter-only records are on each matter page.', 'About this cost')),
      h('div', { class: 'kpis' }, [['remedies_paid', 'Compensation'], ['complaint_expenses_paid', 'Expenses'], ['recorded_work', 'Staff hours'], ['principal_or_entitlement_paid', 'Principal / entitlement']].map(([k, l]) => h('div', { class: 'mini' },
        h('p', { class: 'mini-title' }, l), h('p', { class: 'mini-fig tnum' }, bu[k] ? (k === 'recorded_work' ? secondsToHours(bu[k].value) : audMinor(bu[k].value)) : 'Not recorded'),
        bu[k] ? h('a', { href: popHref(app, `afcab~${k}`), 'data-nav': '', class: 'card-link' }, `${plural(ids(bu[k]).size, 'record')} →`) : null)))),
  );
}

/**
 * Exact matter selection from published fields: stage cohort (published IDs when
 * complete), closure route among concluded matters, or receipt date in a bucket.
 * Stage and route cohorts stay separate selections.
 */
export function matterSelection(am, af, st, closed, trend) {
  if (!am) return null;
  const [kind, a, b] = am.split('~');
  if (kind === 'stage') {
    const pubIds = ids(st[a]);
    const list = pubIds.size === st[a]?.value ? [...pubIds] : af.items.filter((m) => m.current_stage === a).map((m) => m.afca_matter_id);
    return { kind, key: a, label: `current stage ${word(a)}`, ids: new Set(list) };
  }
  if (kind === 'route') return { kind, key: a, label: `concluded by ${word(a)}`, ids: new Set(closed.filter((m) => m.closure_route === a).map((m) => m.afca_matter_id)) };
  if (kind === 'recv') {
    const c = trend.kind === Outcome.OK && trend.grain === a ? trend.cols.find((x) => x.key === b) : null;
    if (!c) return { kind, key: b, label: 'receipt bucket not in the current trend window', ids: new Set() };
    return { kind, key: b, label: `received in ${c.label}`, ids: new Set(af.items.filter((m) => m.received_date && m.received_date >= c.start && m.received_date < c.end).map((m) => m.afca_matter_id)) };
  }
  return null;
}

async function renderMatter(app, loc, ctx, af, matterId, head) {
  const m = af.items.find((x) => x.afca_matter_id === matterId);
  if (!m) { mount(app.main, head, h('h1', {}, 'Matter not in this publication'), h('p', {}, h('a', { href: app.link('/impact/afca'), 'data-nav': '' }, 'Back to AFCA'))); return; }
  const recSlot = h('div', { class: 'kpis kpis-3' }, loading('Requesting matter-scoped records'));
  const cases = h('div', { class: 'card-grid' }, m.case_ids.map((c) => caseCard(app, ctx, c, { desc: `afcam~${matterId}`, from: `AFCA matter ${short(matterId)}` })));
  mount(app.main, head,
    h('div', { class: 'page-head' }, h('h1', {}, `AFCA matter ${short(matterId)}`),
      h('p', { class: 'page-sub' }, `${word(m.current_stage)} · received ${m.received_date ? fmtDate(m.received_date) : 'unknown'} · ${m.closed_date ? `closed ${fmtDate(m.closed_date)} via ${word(m.closure_route)}` : 'not closed'} · ${m.multi_case ? `one matter shared by ${m.case_ids.length} cases` : 'one linked case'}`)),
    h('div', { class: 'grid g-7-5' },
      h('section', { class: 'panel' }, h('h2', {}, 'Stage history'),
        timeline(m.stage_history.map((s) => ({ date: fmtDate(s.date), kind: 'Stage', title: word(s.stage), body: `“${s.quote}”`, chips: [evidenceChip(app, s.ref, null, m.case_ids[0], `afcam~${matterId}`)] })))),
      h('section', { class: 'panel' }, h('h2', {}, 'Evidence of the link', info('Held source records that tie the matter to its cases. A threat or draft notice is not a lodged matter.', 'About the link')),
        h('div', { class: 'chips' }, m.association_refs.map((r) => evidenceChip(app, { source_id: r.source_id, content_sha256: null, anchor: null }, `Source held ${fmtDate(r.day)}`, m.case_ids[0], `afcam~${matterId}`))))),
    h('section', { class: 'panel' }, h('h2', {}, 'Cost of this matter', info('Only records linked to the matter itself over the publication window — not all costs of its cases.', 'About this cost')),
      recSlot),
    h('section', { class: 'panel' }, h('h2', {}, 'Linked cases'), cases));
  const start = melbourneMidnightUtc('2025-10-01'); const end = app.store.binding.publication.knowledge_end_exclusive;
  const ms = [['cash.remedies_paid', 'Compensation / remedies'], ['cash.expenses_paid', 'Complaint expenses (incl. AFCA fees)'], ['work.recorded_hours', 'Recorded handling hours']];
  const outs = await Promise.all(ms.map(([mm]) => sharedCell(app, mm, [], start, end, ctx.signal, { afca_matter_ids: [matterId] })));
  if (!ctx.isCurrent()) return;
  mount(recSlot, ms.map(([mm, l], i) => {
    const o = outs[i];
    if (o.kind !== Outcome.OK) return h('div', { class: 'mini' }, h('p', { class: 'mini-title' }, l), outcomeState(o, { compact: true }));
    const p = o.cell.point;
    return h('div', { class: 'mini', dataset: { measure: mm } }, h('p', { class: 'mini-title' }, l),
      h('p', { class: 'mini-fig tnum' }, p.value === null ? 'Not available' : p.unit === 'AUD_minor' ? audMinor(p.value) : `${Number(p.value).toFixed(2)} h`),
      h('p', { class: 'muted small' }, `${p.state}${p.reason ? ` — ${p.reason}` : ''}`),
      o.items.length ? h('div', {}, o.items.slice(0, 3).map((it) => h('p', { class: 'muted small' }, `${short(it.entity_id)} · ${it.inclusion_reason}`))) : null,
      o.items.length ? h('a', { href: popHref(app, `sqm~${mm}~${matterId}~${start}~${end}`), 'data-nav': '', class: 'card-link' }, `${plural(o.items.length, 'record')} →`) : null);
  }));
}
