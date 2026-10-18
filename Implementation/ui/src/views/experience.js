// Member Experience & Backlog: incoming incidence kept separate from the
// at-cutoff stock; ordered open-age bands and an issue × age heatmap; member
// effort, commitment follow-through and expressed positions as occurrence
// units; completion intervals and repeat-case associations kept distinct from
// post-closure returns (not published as such).

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { columns, bandBars, heatmap, partition } from '../ui/viz.js';
import { ids, intersect, ageBands, AGE_BANDS } from '../lib/calc.js';
import { count } from '../lib/format.js';
import { fmtDate, knownThrough } from '../lib/periods.js';
import { loadPortfolio, loadTrend, label, popHref, failed } from './model.js';
import { short, caseHref } from './population.js';
import { info } from '../ui/labels.js';

export async function renderExperience(app, loc, ctx) {
  const model = await loadPortfolio(app, ctx, { series: false });
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Member Experience & Backlog'), failed(model.out)); return; }
  const [ex, co, trend] = await Promise.all([app.sec('experience', {}, ctx.signal), app.sec('continuity', {}, ctx.signal), loadTrend(app, ctx, [])]);
  if (!ctx.isCurrent()) return;
  const ov = model.ov;
  const cutoff = fmtDate(knownThrough(app.store.binding.publication.knowledge_end_exclusive));
  const b = model.arr.get(ctx.selected.key);
  const e = ex.kind === Outcome.OK ? ex.items[0] : null;
  const openIds = ids(ov.backlog.open);
  const { bands, unknown } = ageBands(openIds, ov.backlog.open_age_days?.by_case);

  mount(app.main,
    h('div', { class: 'page-head' }, h('h1', {}, 'Member Experience & Backlog'),
      h('p', { class: 'page-sub' }, `${ctx.selected.label} · backlog at ${cutoff}`, info('Incoming complaints are a flow for the selected period; the backlog is a stock at the evidence cutoff. Fewer arrivals do not mean an older problem has ended.', 'Flow vs stock'))),
    h('div', { class: 'facts' },
      fact('Received', b ? count(b.R.size) : '—', ctx.selected.label, () => app.router.go(popHref(app, `arr~${ctx.scope.grain}~${ctx.selected.key}`))),
      fact('Open now', count(ov.backlog.open.value), `${ov.backlog.closed.value} closed`, () => app.router.go(popHref(app, 'open'))),
      fact('Members chasing again', e ? count(e.contacts.cases_with_repeat_inbound_contact.value) : '—', 'Cases with repeat inbound contact', e ? () => app.router.go(popHref(app, 'rep')) : null)),
    h('div', { class: 'grid g-7-5' },
      h('section', { class: 'panel', 'aria-labelledby': 'h-inc' }, h('h2', { id: 'h-inc' }, 'Arrivals', info(`Cases received per ${trend.kind === Outcome.OK ? trend.grain : 'bucket'}. The highlighted bar overlaps ${ctx.selected.label}. Historical backlog is not drawn: lifecycle data covers assessed cases only, so a whole-portfolio line would be incomplete.`, 'About arrivals')),
        trend.kind === Outcome.OK ? [
          columns({ points: trend.cols.map((c) => { const a = trend.arr.get(c.key); return { key: c.key, short: c.short, year: c.year, label: c.label, highlight: c.highlight, value: a?.R.size ?? null, display: String(a?.R.size ?? '—'), state: a ? 'value' : 'unavailable' }; }),
            unitWord: 'received cases', onSelect: (p) => app.router.go(popHref(app, `arr~${trend.grain}~${p.key}`)) }),
        ] : failed(trend.out, { compact: true })),
      h('section', { class: 'panel', 'aria-labelledby': 'h-age2' }, h('h2', { id: 'h-age2' }, 'Backlog by age'),
        h('p', { class: 'panel-sub' }, `Median ${ov.backlog.open_age_days?.median ?? '—'} days · oldest ${ov.backlog.open_age_days?.max ?? '—'} days`),
        bandBars({ rows: [...bands.map((x) => ({ id: x.id, label: x.label, value: x.ids.length, display: String(x.ids.length) })), { id: 'unknown', label: 'Unknown age', value: unknown.length, display: String(unknown.length), muted: true }],
          onSelect: (r) => app.router.go(popHref(app, `open~${r.id}`)) }))),
    issueAgePanel(app, model, openIds),
    e ? h('div', { class: 'grid g-6-6' }, effortPanel(app, e), commitmentPanel(app, e)) : failed(ex, { compact: true }),
    e ? responsesPanel(app, e) : null,
    co.kind === Outcome.OK ? continuityPanels(app, ctx, co, model) : h('section', { class: 'panel' }, h('h2', {}, 'Continuity'), failed(co, { compact: true })));
}

function fact(title, value, basis, onOpen) {
  return h('section', { class: 'fact' }, h('p', { class: 'fact-title' }, title), h('p', { class: 'fact-fig tnum' }, value), h('p', { class: 'fact-basis' }, basis),
    onOpen ? h('button', { type: 'button', class: 'link-button', onclick: onOpen }, 'See cases') : null);
}

function issueAgePanel(app, model, openIds) {
  const ages = model.ov.backlog.open_age_days?.by_case;
  const cols = [...AGE_BANDS.map((b) => ({ key: b.id, short: b.label.replace(' days', 'd'), label: b.label })), { key: 'unknown', short: 'Unknown', label: 'Unknown age' }];
  const rowsData = model.issues.map((iss) => {
    const set = intersect(openIds, ids(iss.membership?.included));
    const { bands, unknown } = ageBands(set, ages);
    const m = new Map(bands.map((b) => [b.id, b.ids.length])); m.set('unknown', unknown.length);
    return { id: iss.issue_id, label: label(iss), total: set.size, m };
  }).sort((a, b) => b.total - a.total || (a.id < b.id ? -1 : 1));
  const port = ageBands(openIds, ages);
  const pm = new Map(port.bands.map((b) => [b.id, b.ids.length])); pm.set('unknown', port.unknown.length);
  const all = [{ id: 'portfolio', label: 'All open cases', total: openIds.size, m: pm }, ...rowsData];
  const TOP = 8;
  const max = Math.max(1, ...rowsData.flatMap((r) => [...r.m.values()]));
  const slot = h('div', {});
  const draw = (n) => { const rows = all.slice(0, n + 1); mount(slot, heatmap({
      caption: 'Open cases by issue and age band', rows: rows.map((r) => ({ id: r.id, label: r.label, count: r.total, href: r.id === 'portfolio' ? null : app.link(`/issues/${r.id}`, { tab: 'experience' }) })), cols,
      cell: (r, c) => {
        const row = rows.find((x) => x.id === r.id); const v = row.m.get(c.key) || 0;
        return { state: 'value', t: r.id === 'portfolio' ? Math.min(1, v / Math.max(1, ...pm.values())) : v / max, zero: v === 0, display: String(v), ariaLabel: `${r.label}, ${c.label}: ${v} open cases. Inspect.` };
      },
      onSelect: (r, c) => app.router.go(popHref(app, r.id === 'portfolio' ? `open~${c.key}` : `openiss~${r.id}~${c.key}`)),
    }), all.length - 1 > n ? h('button', { type: 'button', class: 'link-button show-more', onclick: () => draw(all.length) }, `Show all ${all.length - 1} issues`) : null); };
  draw(TOP);
  return h('section', { class: 'panel', 'aria-labelledby': 'h-iage' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-iage' }, 'Where the old cases sit', info('Open supported cases at the evidence cutoff by complaint age. A case can sit in several issue rows; the first row counts each open case once. Busiest issues first.', 'About this table'))),
    slot);
}

function effortPanel(app, e) {
  const me = e.member_effort || {};
  return h('section', { class: 'panel', 'aria-labelledby': 'h-eff' }, h('h2', { id: 'h-eff' }, 'Member effort', info('Occurrences found in correspondence and call summaries across the whole publication — counted as occurrences, not complaints.', 'About member effort')),
    bandBars({ rows: ['chase', 'resubmission', 'channel_change'].map((k) => ({ id: k, label: k === 'chase' ? 'Chasing' : k === 'resubmission' ? 'Resubmission' : 'Channel change', value: me[k]?.value ?? 0, display: `${me[k]?.value ?? 0}` })),
      onSelect: (r) => app.router.go(popHref(app, `eff~${r.id}`)) }),
    h('details', { class: 'disclose' }, h('summary', {}, 'Contacts by channel'), h('p', { class: 'muted small' }, `${Object.entries(e.contacts?.by_channel || {}).map(([k, v]) => `${v.value} ${k}`).join(' · ')} · direction ${Object.entries(e.contacts?.by_direction || {}).map(([k, v]) => `${v.value} ${k}`).join(' · ')}`)));
}

function commitmentPanel(app, e) {
  const c = e.commitments || {};
  const parts = Object.entries(c.by_status || {}).map(([k, v], i) => ({ id: k, label: k.replaceAll('_', ' '), value: v.value, tone: k === 'unknown' ? 'u' : ['a', 'b', 'c'][i % 3] }));
  return h('section', { class: 'panel', 'aria-labelledby': 'h-cmt' }, h('h2', { id: 'h-cmt' }, 'Promises kept?', c.note ? info(c.note, 'About commitments') : null),
    h('p', { class: 'panel-sub' }, `${parts.reduce((a, p) => a + p.value, 0)} commitments by status`),
    partition({ parts, unitWord: 'commitments', onSelect: (p) => app.router.go(popHref(app, `cmt~${p.id}`)) }),
    h('p', {}, h('a', { href: popHref(app, 'cmtdue'), 'data-nav': '' }, `${c.due_passed_without_confirmation?.value ?? 0} past due, unconfirmed →`), info('Due date passed with no recorded confirmation. Unknown — not evidence of a breach.', 'About past-due commitments')));
}

function responsesPanel(app, e) {
  const r = e.responses || {};
  const byDim = {};
  for (const [k, v] of Object.entries(r)) { const [dim, pos] = k.split(':'); (byDim[dim] = byDim[dim] || []).push({ k, pos, v: v.value }); }
  return h('section', { class: 'panel', 'aria-labelledby': 'h-rsp' }, h('h2', { id: 'h-rsp' }, 'How members responded', info('Positions members expressed about a specific outcome, grouped by dimension. Occurrences — not a satisfaction score.', 'About member responses')),
    h('div', { class: 'dim-rows' }, Object.entries(byDim).map(([dim, list]) => h('div', { class: 'dim-row' }, h('span', { class: 'dim-name' }, dim),
      h('div', { class: 'chips' }, list.map((x) => h('a', { class: `chip pos-${x.pos}`, href: popHref(app, `rsp~${x.k}`), 'data-nav': '' }, `${x.pos.replaceAll('_', ' ')} · ${x.v}`)))))));
}

/**
 * Cases with each kind of closed interval, and reopened cases, as separate counts.
 * The published lists are per case and can overlap (a case can close without
 * completion, reopen and close with completion), so they are never shown as
 * slices of one partition; the distinct-case union is stated once.
 */
export function completionMeasures(app, s) {
  const w = s.closed_intervals_with_recorded_completion || [];
  const wo = s.closed_intervals_without_completion_evidence || [];
  const re = s.reopened_cases || [];
  const closedUnion = new Set([...w, ...wo]);
  const overlap = [...closedUnion].filter((c) => w.includes(c) && wo.includes(c)).length;
  const rows = [
    { id: 'closed_intervals_with_recorded_completion', label: 'Recorded completion', value: w.length },
    { id: 'closed_intervals_without_completion_evidence', label: 'No completion evidence', value: wo.length },
  ].map((r) => ({ ...r, display: String(r.value), ariaLabel: `${r.label}: ${r.value} cases with at least one such closed interval. Inspect cases.` }));
  return h('div', { class: 'stack', dataset: { completion: 'separate' } },
    bandBars({ rows, onSelect: (r) => app.router.go(popHref(app, `ivl~${r.id}`)) }),
    h('p', { class: 'chart-caption tnum' }, `${closedUnion.size} distinct cases${overlap ? ` · ${overlap} in both rows` : ''}`, info('Cases with at least one closed interval of each kind. A case can appear in both rows, so they do not add up.', 'About these rows')),
    h('h3', {}, 'Reopened'),
    h('p', {}, re.length ? h('a', { href: popHref(app, 'ivl~reopened_cases'), 'data-nav': '' }, `${re.length} reopened ${re.length === 1 ? 'case' : 'cases'} →`) : h('span', { class: 'muted' }, 'None published')));
}

function continuityPanels(app, ctx, co, model) {
  const s = co.detail?.summary || {};
  const linked = co.items.filter((x) => x.state !== 'uncertain');
  const unsure = co.items.filter((x) => x.state === 'uncertain');
  const pair = (x) => h('article', { class: 'card', dataset: { continuity: x.continuity_id } },
    h('p', { class: 'card-eyebrow' }, x.state === 'uncertain' ? 'Uncertain association' : 'Evidence-linked association'),
    h('p', {}, `Member ${short(x.member_reference)} · ${x.case_ids.length} cases`),
    h('p', { class: 'muted small' }, x.basis.length ? x.basis.slice(0, 3).map((b) => b.basis.replaceAll('_', ' ')).join(' · ') + (x.basis.length > 3 ? ` · +${x.basis.length - 3}` : '') : 'Same member only; no shared evidence'),
    h('p', {}, x.case_ids.map((c) => h('a', { href: caseHref(app, c, null, 'Repeat-case association'), 'data-nav': '', class: 'card-link' }, `Case ${short(c)} →`))));
  return [
    h('section', { class: 'panel', 'aria-labelledby': 'h-ivl' }, h('h2', { id: 'h-ivl' }, 'Closed — but was the work done?', info('Administrative closure is not proof the service was completed, and completing one named action is not full resolution.', 'About closure')),
      completionMeasures(app, s)),
    h('section', { class: 'panel', 'aria-labelledby': 'h-rep' }, h('h2', { id: 'h-rep' }, 'Members with repeat cases', info('A repeat case is not automatically the same problem or a return after closure.', 'About repeat cases')),
      h('p', { class: 'panel-sub' }, `${s.members_with_repeat_cases ?? '—'} members · ${s.repeat_case_pairs_evidence_linked ?? 0} evidence-linked · ${s.repeat_case_pairs_uncertain ?? 0} uncertain`),
      h('details', { class: 'disclose' }, h('summary', {}, `Show ${linked.length + unsure.length} associations`), h('div', { class: 'card-grid' }, [...linked, ...unsure].map(pair)))),
    h('p', { class: 'muted small note-line' }, 'Post-closure returns: not published', info('Return contacts after closure, returning members and new cases after a same-problem return are not separate measures in this publication, and are not inferred from repeat-case pairs. Reopenings published: ' + (s.reopened_cases?.length ?? 0) + '.', 'About post-closure returns')),
  ];
}
