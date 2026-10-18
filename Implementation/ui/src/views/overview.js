// Executive Overview: a compact operational KPI strip, then the Intelligence
// Brief (one featured finding and up to two investigation entry points, see
// brief.js), then the portfolio charts: leading issues beside the selected-issue
// count/share dynamics, backlog age and recorded burden previews. The full
// signal list, ranking, heatmap and comparisons live in Issues & Trends.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { kpi, rankBars, trendPanels, bandBars, spark, pct, signed } from '../ui/viz.js';
import { ids, ageBands, median, reach } from '../lib/calc.js';
import { audMinor, secondsToHours, count } from '../lib/format.js';
import { fmtDate, knownThrough, lastDay } from '../lib/periods.js';
import { loadPortfolio, loadTrend, rankedFor, label, issueBadges, shareText, deltaText, previousBucket, partialComparison, popHref, failed, matchedPrevious } from './model.js';
import { icon } from '../ui/icons.js';
import { intelligenceBrief } from './brief.js';
import { info } from '../ui/labels.js';

export async function renderOverview(app, loc, ctx) {
  const model = await loadPortfolio(app, ctx);
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Executive Overview'), failed(model.out)); return; }
  const burden = await app.sec('burden', { grain: ctx.scope.grain }, ctx.signal);
  if (!ctx.isCurrent()) return;
  const prev = previousBucket(ctx);
  const prevMatched = await matchedPrevious(app, ctx, prev);
  if (!ctx.isCurrent()) return;
  const rows = rankedFor(model, ctx.selected.key, prev?.key, prevMatched);
  const top = rows.slice(0, ctx.scope.topN);
  const chosen = ctx.scope.issues.length ? ctx.scope.issues : top.slice(0, 1).map((r) => r.id);
  const trend = await loadTrend(app, ctx, chosen);
  if (!ctx.isCurrent()) return;
  const pub = app.store.binding.publication;
  const through = fmtDate(knownThrough(pub.knowledge_end_exclusive));

  const cmpBucket = prevMatched?.unavailable ? null : prevMatched || prev;
  mount(app.main,
    h('div', { class: 'page-head' },
      h('h1', {}, 'Executive Overview'),
      h('p', { class: 'page-sub' }, `${ctx.selected.label} · evidence to ${through}`)),
    kpiRow(app, ctx, model, burden, prev, prevMatched),
    intelligenceBrief(app, ctx, model, rows, cmpBucket?.label),
    h('h2', { class: 'section-title' }, 'Portfolio view'),
    h('div', { class: 'grid g-7-5' },
      leadingIssues(app, ctx, model, rows, top, cmpBucket, chosen),
      dynamicsPanel(app, ctx, model, trend, chosen)),
    h('div', { class: 'grid g-7-5' },
      backlogPanel(app, ctx, model),
      burdenPreview(app, ctx, model)));
}

function kpiRow(app, ctx, model, burden, prev, prevMatched) {
  const b = model.arr.get(ctx.selected.key);
  const p = prevMatched?.unavailable ? null : prevMatched || (prev ? model.arr.get(prev.key) : null);
  const ov = model.ov;
  const openIds = ids(ov.backlog.open);
  const ages = ov.backlog.open_age_days?.by_case || {};
  const known = [...openIds].filter((id) => Number.isFinite(ages[id]));
  const med = median(known.map((id) => ages[id]));
  const bi = burden.kind === Outcome.OK ? burden.items.find((x) => x.bucket === ctx.selected.key) : null;
  const diff = b && p ? b.R.size - p.R.size : null;
  const pLabel = prevMatched && !prevMatched.unavailable ? prevMatched.label : prev?.label;
  return h('div', { class: 'kpis kpi-strip', role: 'group', 'aria-label': 'Operational measures' },
    kpi({
      id: 'k-incidence', title: 'Complaints received',
      value: b ? h('span', { class: 'kpi-num tnum' }, count(b.R.size), h('small', {}, ' received cases')) : h('span', { class: 'kpi-na' }, 'Unavailable'),
      sub: b ? h('span', {}, `${count(b.members)} members · ${b.unassessed.size} unassessed`) : null,
      more: `Cases received ${ctx.scope.grain === 'day' ? 'on' : 'in'} ${ctx.selected.label} by original receipt date. ${b ? `${count(b.assessed.size)} assessed by the model, ${b.unassessed.size} not assessed (holdout or pending).` : ''}`,
      foot: p ? h('span', { class: 'kpi-delta tnum' }, `${signed(diff)} vs ${pLabel} (${p.R.size})`) : h('span', { class: 'muted' }, prevMatched?.unavailable ? `No matched comparison: ${prevMatched.reason}` : 'No comparable previous period'),
      onOpen: b ? (t) => app.router.go(popHref(app, `arr~${ctx.scope.grain}~${ctx.selected.key}`)) : null,
    }),
    kpi({
      id: 'k-backlog', title: 'Open now',
      value: h('span', { class: 'kpi-num tnum' }, count(ov.backlog.open.value), h('small', {}, ' open cases')),
      sub: h('span', {}, `Median age ${med === null ? '—' : `${Math.round(med)} days`}`),
      more: `Open at the evidence cutoff (${fmtDate(knownThrough(app.store.binding.publication.knowledge_end_exclusive))}) — a stock, not this period's arrivals. ${openIds.size - known.length} with unknown age.`,
      foot: ov.backlog.lifecycle_unknown?.value ? h('span', { class: 'muted' }, `${ov.backlog.lifecycle_unknown.value} lifecycle unknown`) : null,
      onOpen: () => app.router.go(popHref(app, 'open')),
    }),
    kpi({
      id: 'k-money', title: 'Paid out',
      state: bi ? 'value' : 'na',
      value: bi ? h('div', { class: 'kpi-pair' },
        h('button', { type: 'button', class: 'kpi-pair-item', dataset: { focusKey: 'kpi:remedies' }, onclick: () => app.router.go(popHref(app, `fin~remedies_paid~${ctx.scope.grain}~${ctx.selected.key}`)) },
          h('span', { class: 'kpi-num sm tnum' }, audMinor(bi.remedies_paid.value)), h('small', {}, 'Compensation')),
        h('button', { type: 'button', class: 'kpi-pair-item', dataset: { focusKey: 'kpi:expenses' }, onclick: () => app.router.go(popHref(app, `fin~complaint_expenses_paid~${ctx.scope.grain}~${ctx.selected.key}`)) },
          h('span', { class: 'kpi-num sm tnum' }, audMinor(bi.complaint_expenses_paid.value)), h('small', {}, 'Complaint expenses')))
        : h('span', { class: 'kpi-na' }, 'Unavailable for this period'),
      more: `Paid in ${ctx.selected.label} by payment date, from held records only — a missing record is not zero. Outstanding obligations are on Business Impact.`,
    }),
    kpi({
      id: 'k-hours', title: 'Staff hours',
      state: bi ? 'value' : 'na',
      value: bi ? h('span', { class: 'kpi-num tnum' }, secondsToHours(bi.recorded_work.value).replace(' h', ''), h('small', {}, ' h')) : h('span', { class: 'kpi-na' }, 'Unavailable'),
      sub: bi ? h('span', {}, `${count(ids(bi.recorded_work).size)} work entries`) : null,
      more: `Recorded handling work dated in ${ctx.selected.label} (${bi ? count(bi.recorded_work.value) : '—'} seconds). Hours only — no salary conversion.`,
      onOpen: bi ? () => app.router.go(popHref(app, `work~${ctx.scope.grain}~${ctx.selected.key}`)) : null,
    }));
}

function leadingIssues(app, ctx, model, rows, top, prev, chosen) {
  const nRec = model.arr.get(ctx.selected.key);
  const mismatch = rows.filter((r) => r.reconcile.state === 'mismatch');
  return h('section', { class: 'panel', 'aria-labelledby': 'h-leading' },
    h('div', { class: 'panel-head' },
      h('h2', { id: 'h-leading' }, 'Top issues', info(`Top ${ctx.scope.topN} of ${model.issues.length} by supported cases received in ${ctx.selected.label}. Share is of all ${nRec ? nRec.R.size : '?'} received cases (${nRec ? nRec.unassessed.size : '?'} not assessed). The tick marks ${prev ? prev.label : 'no previous period'}. Model-assessed; issues overlap, so shares do not add to 100%. Click a bar for its cases, a name for the issue.`, 'About top issues')),
      h('a', { href: app.link('/issues'), 'data-nav': '', class: 'panel-link' }, `All ${model.issues.length} issues`)),
    h('p', { class: 'panel-sub' }, `${ctx.selected.label} · share of received · tick = ${prev ? prev.label : 'no previous'}`),
    rankBars({
      rows: top.map((r) => ({
        id: r.id, label: label(r.issue), value: r.n, prev: r.prev?.n, display: count(r.n), note: shareText(r),
        badges: issueBadges(r.issue, model.series.get(r.id)), selected: chosen.includes(r.id),
        ariaLabel: `${label(r.issue)}: ${r.n} supported cases, ${shareText(r)}, ${deltaText(r.cmp, prev?.label)}. Inspect contributors.`,
      })),
      onSelect: (r) => app.router.go(popHref(app, `reach~${r.id}~${ctx.scope.grain}~${ctx.selected.key}`)),
      labelHref: (r) => app.link(`/issues/${r.id}`, { iss: null }),
    }),
    mismatch.length ? h('p', { class: 'warn-text' }, `Reconciliation failed for ${mismatch.length} issue(s): composed reach differs from the published series. Treat as a binding failure.`) : null,
    h('details', { class: 'compare' }, h('summary', {}, `Compare trends (${chosen.length}/3)`), h('div', { class: 'pick-row' },
      top.map((r) => h('button', {
        type: 'button', class: 'chip-toggle', 'aria-pressed': chosen.includes(r.id) ? 'true' : 'false',
        onclick: () => {
          const next = chosen.includes(r.id) ? chosen.filter((x) => x !== r.id) : [...chosen, r.id].slice(-3);
          app.setLocal({ iss: next.join(',') || null });
        },
      }, label(r.issue))))));
}

export function dynamicsPanel(app, ctx, model, trend, chosen, { title = 'Trends', compact = false } = {}) {
  if (trend.kind !== Outcome.OK) return h('section', { class: 'panel' }, h('h2', {}, title), failed(trend.out, { compact: true }));
  const series = chosen.map((id, slot) => {
    const s = trend.series.get(id);
    const inc = ids(model.byId.get(id)?.membership?.included);
    return {
      id, slot, label: label(model.byId.get(id)),
      points: trend.cols.map((c) => {
        const r = reach(inc, trend.arr.get(c.key));
        return { key: c.key, n: r.n, share: r.share, state: r.state };
      }),
      published: s,
    };
  });
  const dens = trend.cols.map((c) => trend.arr.get(c.key)?.R.size ?? null);
  return h('section', { class: 'panel', 'aria-labelledby': 'h-dyn' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-dyn' }, title, info(`Bottom row: cases received per bucket (the denominator). The highlighted bucket overlaps ${ctx.selected.label}. Earlier buckets are reconstructed from September evidence, not alerts issued at the time.`, 'About this chart')),
      h('span', { class: 'panel-meta' }, `${trend.grain === 'day' ? 'Daily' : trend.grain === 'week' ? 'Weekly' : trend.grain === 'quarter' ? 'Quarterly' : 'Monthly'} · ${trend.cols.length} buckets`)),
    series.length ? trendPanels({
      series, cols: trend.cols, denominators: dens,
      // The clicked series names its own issue; never substitute the first plotted issue.
      onSelect: (c, s) => app.router.go(popHref(app, `reach~${s.id}~${trend.grain}~${c.key}`)),
    }) : h('p', { class: 'muted' }, 'Select up to three issues to compare.'),
  );
}

export function signalCard(app, ctx, model, a, inTop, seriesOut) {
  const iss = model.byId.get(a.issue_id);
  const cw = a.current_window; const bw = a.baseline_window;
  const vals = seriesOut ? seriesOut.items.map((x) => x.affected_new_cases?.value ?? null) : [];
  return h('article', { class: 'signal', dataset: { issue: a.issue_id } },
    h('div', { class: 'signal-head' }, h('span', { class: 'signal-state' }, icon('signal', { size: 14 }), a.state === 'emerging_concentration' ? 'Emerging concentration' : a.state.replaceAll('_', ' ')),
      inTop ? null : h('span', { class: 'badge' }, `Outside Top ${ctx.scope.topN}`)),
    h('a', { class: 'signal-title', href: app.link(`/issues/${a.issue_id}`, { tab: 'dynamics' }), 'data-nav': '' }, label(iss)),
    h('p', { class: 'signal-fig tnum' }, h('strong', {}, String(cw.affected.value)), ` cases ${fmtDate(cw.start, { year: false })}–${fmtDate(cw.end_inclusive)} vs ${Number.isFinite(bw.affected_per_28_days) ? +bw.affected_per_28_days.toFixed(2) : 'n/a'} per 28 days ${fmtDate(bw.start, { year: false })}–${fmtDate(bw.end_exclusive)} (excl.)`),
    vals.length ? h('div', { class: 'signal-spark' }, spark(vals), h('span', { class: 'muted' }, `${ctx.scope.grain === 'month' ? 'Monthly' : 'Per bucket'} supported cases`)) : null,
    h('div', { class: 'signal-foot' },
      h('a', { href: popHref(app, `alert~${a.issue_id}~current`), 'data-nav': '', dataset: { focusKey: `sig:${a.issue_id}` } }, 'Current-window cases'),
      a.persistent_open_over_90_days?.value ? h('a', { href: popHref(app, `alert~${a.issue_id}~persistent`), 'data-nav': '' }, `${a.persistent_open_over_90_days.value} open > 90 days`) : null));
}

function backlogPanel(app, ctx, model) {
  const ov = model.ov;
  const openIds = ids(ov.backlog.open);
  const { bands, unknown } = ageBands(openIds, ov.backlog.open_age_days?.by_case);
  const over90 = bands.filter((b) => b.lo > 90).reduce((n, b) => n + b.ids.length, 0);
  const rows = [...bands.map((b) => ({ id: b.id, label: b.label, value: b.ids.length, display: String(b.ids.length) })),
    { id: 'unknown', label: 'Unknown age', value: unknown.length, display: String(unknown.length), muted: true }];
  return h('section', { class: 'panel', 'aria-labelledby': 'h-age' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-age' }, 'Backlog by age', info('Complaint age at the evidence cutoff — not time waiting on a team.', 'About backlog age')),
      h('a', { href: app.link('/experience'), 'data-nav': '', class: 'panel-link' }, 'Age by issue')),
    h('p', { class: 'panel-sub' }, h('strong', { class: 'tnum' }, count(openIds.size)), ' open · ', h('strong', { class: 'tnum' }, count(over90)), ' over 90 days'),
    bandBars({ rows: rows.map((r) => ({ ...r, ariaLabel: `${r.label}: ${r.value} open cases. Inspect.` })), onSelect: (r) => app.router.go(popHref(app, `open~${r.id}`)) }));
}

const BURDEN_UNITS = [
  ['remedies_paid', 'Paid compensation'],
  ['complaint_expenses_paid', 'Complaint expenses'],
  ['recorded_work', 'Staff hours'],
];

function burdenPreview(app, ctx, model) {
  const unit = BURDEN_UNITS.find(([u]) => u === app.loc.params.get('bu'))?.[0] || 'complaint_expenses_paid';
  const fmt = (v) => (unit === 'recorded_work' ? secondsToHours(v) : audMinor(v));
  const rows = model.issues.map((iss) => {
    const ab = model.series.get(iss.issue_id)?.detail?.issue?.associated_burden;
    return { iss, v: ab?.[unit]?.value ?? null };
  }).filter((r) => r.v !== null).sort((a, b) => b.v - a.v || (a.iss.issue_id < b.iss.issue_id ? -1 : 1)).slice(0, 5);
  const total = model.ov.burden_total?.[unit]?.value;
  return h('section', { class: 'panel', 'aria-labelledby': 'h-burden' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-burden' }, 'Cost by issue', info(`Whole publication window (1 Oct 2025 – 30 Sep 2026), not ${ctx.selected.label}. Records associated with each issue's supported cases — association, not cause. Issues share cases, so bars overlap and do not add up. Portfolio total: ${total === undefined ? '—' : fmt(total)}. AFCA-associated cost has its own view.`, 'About cost by issue')),
      h('a', { href: app.link('/impact'), 'data-nav': '', class: 'panel-link' }, 'Business Impact')),
    h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Burden unit' }, BURDEN_UNITS.map(([u, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': u === unit ? 'true' : 'false', onclick: () => app.setLocal({ bu: u === 'complaint_expenses_paid' ? null : u }) }, l))),
    h('p', { class: 'panel-sub' }, `Oct 2025 – Sep 2026 · total ${total === undefined ? '—' : fmt(total)}`),
    rankBars({
      rows: rows.map((r) => ({ id: r.iss.issue_id, label: label(r.iss), value: r.v, display: fmt(r.v), ariaLabel: `${label(r.iss)}: ${fmt(r.v)} associated over the publication window. Inspect records.` })),
      onSelect: (r) => app.router.go(popHref(app, `iburden~${r.id}~${unit}`)),
      labelHref: (r) => app.link(`/issues/${r.id}`, { tab: 'impact' }),
    }),
    h('p', { class: 'chart-caption' }, h('a', { href: app.link('/impact/afca'), 'data-nav': '' }, 'AFCA matters →')));
}

/** Trend window as calendar dates and bucket count, e.g. "13 Jul – 30 Sep 2026 (12 weeks)". */
function trendRange(trend) {
  const a = trend.cols[0]; const b = trend.cols[trend.cols.length - 1]; const n = trend.cols.length;
  if (n === 1) return a.label;
  const from = fmtDate(a.start, { year: a.start.slice(0, 4) !== b.start.slice(0, 4) });
  return `${from} – ${fmtDate(lastDay(b.end))} (${n} ${trend.grain === 'observation_year' ? 'years' : `${trend.grain}s`})`;
}
