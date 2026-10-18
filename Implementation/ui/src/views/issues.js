// Issues & Trends: selected-period Period Comparison bars, a fixed-identity
// Issue Incidence Heatmap over the trend window, Issue Dynamics for chosen
// issues and every published signal (including those outside Top N).

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { rankBars, heatmap, pct, signed } from '../ui/viz.js';
import { ids, reach } from '../lib/calc.js';
import { count } from '../lib/format.js';
import { loadPortfolio, loadTrend, rankedFor, label, issueBadges, shareText, deltaText, comparablePeriod, previousRanking, popHref, failed } from './model.js';
import { info } from '../ui/labels.js';
import { dynamicsPanel, signalCard } from './overview.js';

export async function renderIssues(app, loc, ctx) {
  const model = await loadPortfolio(app, ctx);
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Issues & Trends'), failed(model.out)); return; }
  const cmpP = await comparablePeriod(app, ctx, model);
  if (!ctx.isCurrent()) return;
  const { prev, matched } = cmpP;
  const cmpBucket = cmpP.bucket ? { label: cmpP.label } : null;
  const rows = rankedFor(model, ctx.selected.key, prev?.key, matched);
  const top = rows.slice(0, ctx.scope.topN);
  const pinned = ctx.scope.issues;
  const chosen = pinned.length ? pinned : top.slice(0, 1).map((r) => r.id);
  const heatRows = [...top.map((r) => r.id), ...pinned.filter((id) => !top.some((r) => r.id === id))];
  const trend = await loadTrend(app, ctx, [...new Set([...heatRows, ...chosen])]);
  if (!ctx.isCurrent()) return;
  const prevRows = previousRanking(model, cmpP); // same matched bounds as the comparison above
  // Only issues that actually led the comparable period (a zero count is not a leader).
  const displaced = prevRows.slice(0, ctx.scope.topN).filter((r) => r.n > 0 && !top.some((t) => t.id === r.id));
  const q = (loc.params.get('q') || '').trim().toLowerCase();

  mount(app.main,
    h('div', { class: 'page-head' },
      h('h1', {}, 'Issues & Trends'),
      h('p', { class: 'page-sub' }, `${model.issues.length} AI-found issues · ${ctx.selected.label}`, info(`Provisional, model-assessed issues. Top ${ctx.scope.topN} limits what is displayed only — every share uses all received cases.`, 'About these issues'))),
    issueFinder(app, model, rows, q),
    h('div', { class: 'grid g-4-8' },
      h('section', { class: 'panel', 'aria-labelledby': 'h-cmp' },
        h('div', { class: 'panel-head' }, h('h2', { id: 'h-cmp' }, 'What changed', info('Supported cases, their share of all received cases, and the change in cases and percentage points.', 'About this comparison'))),
        h('p', { class: 'panel-sub' }, `${ctx.selected.label} vs ${cmpBucket ? cmpBucket.label : 'no comparable period'}`),
        rankBars({
          rows: top.map((r) => ({
            id: r.id, label: label(r.issue), value: r.n, prev: r.prev?.n, display: count(r.n),
            note: r.cmp.state === 'value' ? `${shareText(r)} · ${signed(r.cmp.dCount)} ${Math.abs(r.cmp.dCount) === 1 ? 'case' : 'cases'} · ${r.cmp.sharePts === null ? 'share change n/a' : signed(r.cmp.sharePts, ' pts')}` : shareText(r),
            badges: issueBadges(r.issue, model.series.get(r.id)), selected: chosen.includes(r.id),
            ariaLabel: `${label(r.issue)}: ${r.n} ${r.n === 1 ? 'case' : 'cases'}, ${shareText(r)}, ${deltaText(r.cmp, cmpBucket?.label)}. Inspect contributors.`,
          })),
          onSelect: (r) => app.router.go(popHref(app, `reach~${r.id}~${ctx.scope.grain}~${ctx.selected.key}`)),
          labelHref: (r) => app.link(`/issues/${r.id}`),
        }),
        displaced.length ? h('div', { class: 'displaced' }, h('p', { class: 'eyebrow' }, `Previous Top ${ctx.scope.topN} leaders now outside`),
          displaced.map((r) => h('a', { href: app.link(`/issues/${r.id}`), 'data-nav': '', class: 'chip' }, `${label(r.issue)} · ${r.n} in ${cmpP.label}`))) : null),
      heatPanel(app, ctx, model, trend, heatRows, pinned)),
    dynamicsPanel(app, ctx, model, trend, chosen),
    pinRow(app, model, chosen),
    signalsPanel(app, ctx, model, top));
  if (loc.params.get('tab') === 'signals') document.getElementById('h-allsig')?.scrollIntoView({ block: 'start' });
}

function issueFinder(app, model, rows, q) {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const list = model.issues.filter((i) => !q || label(i).toLowerCase().includes(q));
  return h('details', { class: 'finder', open: q ? true : null },
    h('summary', {}, `All issues (${model.issues.length})`),
    h('form', { class: 'finder-form', role: 'search', onsubmit: (e) => { e.preventDefault(); app.setLocal({ q: e.currentTarget.querySelector('input').value || null }); } },
      h('label', { for: 'issue-q', class: 'sr-only' }, 'Search issues in this publication'),
      h('input', { id: 'issue-q', type: 'search', placeholder: 'Search issue names in this publication', value: q || '' }),
      h('button', { type: 'submit', class: 'button-secondary small' }, 'Search')),
    h('ul', { class: 'finder-list' }, list.map((i) => h('li', {},
      h('a', { href: app.link(`/issues/${i.issue_id}`), 'data-nav': '' }, label(i)),
      h('span', { class: 'muted tnum' }, ` · ${byId.get(i.issue_id)?.n ?? 0} in period · ${i.membership?.included?.value ?? 0} included to date`)))));
}

function heatPanel(app, ctx, model, trend, heatRows, pinned) {
  if (trend.kind !== Outcome.OK) return h('section', { class: 'panel' }, h('h2', {}, 'Issue Incidence Heatmap'), failed(trend.out, { compact: true }));
  const mode = app.loc.params.get('hm') === 'share' ? 'share' : 'count';
  const cells = new Map();
  let maxN = 0;
  for (const id of heatRows) {
    const inc = ids(model.byId.get(id).membership?.included);
    for (const c of trend.cols) {
      const r = reach(inc, trend.arr.get(c.key));
      cells.set(`${id}|${c.key}`, r);
      if (r.n > maxN) maxN = r.n;
    }
  }
  const sel = app.loc.params.get('pop') || '';
  return h('section', { class: 'panel', 'aria-labelledby': 'h-heat' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-heat' }, 'Heatmap', info(`Rows: ${ctx.selected.label} Top ${ctx.scope.topN}${pinned.length ? ' plus pinned issues' : ''}. Columns: surrounding ${trend.grain}s. ${mode === 'share' ? 'Share of all received cases in each bucket.' : `Supported received cases (0–${maxN}).`} Hatched = nothing received.`, 'About the heatmap')),
      h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Heatmap scale' },
        [['count', 'Count'], ['share', 'Share']].map(([v, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': v === mode ? 'true' : 'false', onclick: () => app.setLocal({ hm: v === 'count' ? null : v }) }, l)))),
    heatmap({
      caption: 'Issue incidence by period',
      rows: heatRows.map((id) => ({ id, label: label(model.byId.get(id)), href: app.link(`/issues/${id}`) })),
      cols: trend.cols,
      cell: (r, c) => {
        const v = cells.get(`${r.id}|${c.key}`);
        const selected = sel === `reach~${r.id}~${trend.grain}~${c.key}`;
        if (v.state === 'no_arrivals') return { state: 'na', display: '', ariaLabel: `${r.label}, ${c.label}: no received cases; share not applicable`, selected };
        if (v.state === 'unavailable') return { state: 'unavailable', display: '?', ariaLabel: `${r.label}, ${c.label}: unavailable`, selected };
        const t = mode === 'share' ? (v.share ?? 0) : maxN ? v.n / maxN : 0;
        return { state: 'value', t, zero: v.n === 0, display: mode === 'share' ? (v.share === null ? '–' : pct(v.share)) : String(v.n), selected,
          ariaLabel: `${r.label}, ${c.label}: ${v.n} of ${v.d} received cases${v.share !== null ? ` (${pct(v.share)})` : ''}. Inspect contributors.` };
      },
      onSelect: (r, c) => app.router.go(popHref(app, `reach~${r.id}~${trend.grain}~${c.key}`)),
    }),
    h('div', { class: 'heat-den' }, h('span', { class: 'muted' }, 'Received per bucket: '), trend.cols.map((c) => h('span', { class: 'tnum' }, `${c.short} ${trend.arr.get(c.key)?.R.size ?? '?'}`)).reduce((a, x) => [...a, a.length ? ' · ' : '', x], [])));
}

function pinRow(app, model, chosen) {
  return h('section', { class: 'panel pin-panel', 'aria-label': 'Choose issues for dynamics' },
    h('p', { class: 'eyebrow' }, `Pin up to 3 issues to compare (${chosen.length}/3)`),
    h('div', { class: 'pick-row' }, model.issues.map((i) => h('button', {
      type: 'button', class: 'chip-toggle', 'aria-pressed': chosen.includes(i.issue_id) ? 'true' : 'false', dataset: { pin: i.issue_id },
      onclick: () => {
        const next = chosen.includes(i.issue_id) ? chosen.filter((x) => x !== i.issue_id) : [...chosen, i.issue_id].slice(-3);
        app.setLocal({ iss: next.join(',') || null });
      },
    }, label(i)))));
}

function signalsPanel(app, ctx, model, top) {
  const topIds = new Set(top.map((r) => r.id));
  const flagged = model.alerts.filter((a) => a.state !== 'no_alert').sort((a, b) => b.absolute_change - a.absolute_change || (a.issue_id < b.issue_id ? -1 : 1));
  const quiet = model.alerts.filter((a) => a.state === 'no_alert');
  return h('section', { class: 'panel', 'aria-labelledby': 'h-allsig' },
    h('div', { class: 'panel-head' }, h('h2', { id: 'h-allsig' }, 'Emerging signals', info(`Published ${flagged[0]?.rule?.rule_version || ''} rule: at least ${flagged[0]?.rule?.min_current_new_cases ?? 3} cases in the current window and ${flagged[0]?.rule?.min_baseline_assessed_arrivals ?? 3} assessed arrivals in the baseline. Windows are fixed by the publication, not the period selector.`, 'About signals'))),
    h('p', { class: 'panel-sub' }, h('strong', { class: 'tnum' }, String(flagged.length)), ` of ${model.alerts.length} issues are rising`),
    h('div', { class: 'signal-cards wrap' }, flagged.map((a) => signalCard(app, ctx, model, a, topIds.has(a.issue_id), model.series.get(a.issue_id)))),
    quiet.length ? h('details', { class: 'disclose' }, h('summary', {}, `${quiet.length} with no signal`), h('p', { class: 'muted small' }, quiet.map((a) => label(model.byId.get(a.issue_id))).join(' · '))) : null);
}
