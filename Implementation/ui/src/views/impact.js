// Business Impact: paid compensation, paid complaint expenses, outstanding
// obligations (stock) and recorded handling hours, always separate. Issue
// comparisons use exact Shared selected-period results or the clearly labelled
// publication-window association; time series by event date with reversals
// below zero; financial-event timeline from resolved records. AFCA is a
// directly accessible subsection.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { rankBars, columns, timeline, plural } from '../ui/viz.js';
import { ids, union } from '../lib/calc.js';
import { audMinor, secondsToHours, count } from '../lib/format.js';
import { fmtDate, knownThrough, melbourneMidnightUtc } from '../lib/periods.js';
import { loadPortfolio, loadTrend, label, popHref, failed } from './model.js';
import { sharedCell, evidenceChip, short, caseHref } from './population.js';
import { loading, outcomeState } from '../ui/states.js';
import { info } from '../ui/labels.js';
import { icon } from '../ui/icons.js';

// Exact selected-period results cost one Shared calculation per issue and measure, so only a bounded,
// disclosed candidate set is requested (never all issues at once), a few at a time.
export const PERIOD_ISSUE_LIMIT = 10;
const PERIOD_CONCURRENCY = 2;

/** Candidates for exact period queries: largest publication-window association first (ties by issue ID). */
export function periodCandidates(issues, assoc, limit = PERIOD_ISSUE_LIMIT) {
  return [...issues].sort((a, b) => (assoc(b) ?? -Infinity) - (assoc(a) ?? -Infinity) || (a.issue_id < b.issue_id ? -1 : 1)).slice(0, limit);
}

/** Runs fn over items with at most n in flight; results keep item order. */
export async function inPool(items, n, fn) {
  const out = new Array(items.length); let k = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => { while (k < items.length) { const i = k++; out[i] = await fn(items[i]); } }));
  return out;
}

const CASH = [['remedies_paid', 'Paid compensation / remedies', 'cash.remedies_paid'], ['complaint_expenses_paid', 'Paid complaint expenses', 'cash.expenses_paid']];

export async function renderImpact(app, loc, ctx) {
  const model = await loadPortfolio(app, ctx);
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Business Impact'), failed(model.out)); return; }
  const [bu, trendBurden, afca] = await Promise.all([
    app.sec('burden', { grain: ctx.scope.grain }, ctx.signal),
    app.sec('burden', { grain: ctx.scope.trendGrain }, ctx.signal),
    app.sec('afca', {}, ctx.signal)]);
  const trend = await loadTrend(app, ctx, []);
  if (!ctx.isCurrent()) return;
  const pub = app.store.binding.publication;
  const cutoff = fmtDate(knownThrough(pub.knowledge_end_exclusive));
  const bi = bu.kind === Outcome.OK ? bu.items.find((x) => x.bucket === ctx.selected.key) : null;
  const scopeSel = app.loc.params.get('bs') === 'pub' ? 'pub' : 'period';
  const unit = CASH.some(([k]) => k === app.loc.params.get('bm')) ? app.loc.params.get('bm') : 'complaint_expenses_paid';
  const obSlot = h('div', { class: 'kpi-main' }, h('span', { class: 'muted' }, 'Requesting…'));
  const issueSlot = h('div', {}, loading('Requesting issue values'));
  const hoursSlot = h('div', {}, loading('Requesting issue values'));
  const tlSlot = h('div', {});

  mount(app.main,
    h('div', { class: 'page-head' }, h('h1', {}, 'Business Impact'),
      h('p', { class: 'page-sub' }, `What complaints cost in ${ctx.selected.label}`, info('Money paid in the period and the outstanding-obligation stock are never added together. Held records are partial; a missing record is not zero.', 'About these figures'))),
    h('div', { class: 'kpis' },
      moneyCard(app, ctx, 'Compensation paid', bi?.remedies_paid, `fin~remedies_paid~${ctx.scope.grain}~${ctx.selected.key}`, `Paid in ${ctx.selected.label} by payment date.`),
      moneyCard(app, ctx, 'Complaint expenses', bi?.complaint_expenses_paid, `fin~complaint_expenses_paid~${ctx.scope.grain}~${ctx.selected.key}`, `Paid in ${ctx.selected.label}; excludes member principal or entitlement.`),
      h('section', { class: 'kpi' }, h('div', { class: 'kpi-head' }, h('h2', { class: 'kpi-title' }, 'Still owed'), info(`Outstanding obligations at the evidence cutoff (${cutoff}) — a stock, not approved minus paid.`, 'About still owed')), obSlot),
      h('section', { class: 'kpi' }, h('div', { class: 'kpi-head' }, h('h2', { class: 'kpi-title' }, 'Staff hours'), info(bi ? `Work dated in ${ctx.selected.label} (${count(bi.recorded_work.value)} seconds). Hours only — no salary rates.` : `No ${ctx.scope.grain} work output is published for this bucket — unavailable, not zero.`, 'About staff hours')),
        h('div', { class: 'kpi-main' }, bi ? h('span', { class: 'kpi-num tnum' }, secondsToHours(bi.recorded_work.value)) : h('span', { class: 'kpi-na' }, 'Unavailable')),
        bi ? h('div', { class: 'kpi-foot' }, h('a', { href: popHref(app, `work~${ctx.scope.grain}~${ctx.selected.key}`), 'data-nav': '', class: 'link-button' }, `${ids(bi.recorded_work).size} work ${ids(bi.recorded_work).size === 1 ? 'entry' : 'entries'} →`)) : null)),
    h('section', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, 'Cost by issue'),
        h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Time scope' },
          [['period', ctx.selected.label], ['pub', 'Full year']].map(([v, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': v === scopeSel ? 'true' : 'false', onclick: () => app.setLocal({ bs: v === 'period' ? null : v }) }, l)))),
      h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Monetary measure' }, CASH.map(([k, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': k === unit ? 'true' : 'false', onclick: () => app.setLocal({ bm: k === 'complaint_expenses_paid' ? null : k }) }, l))),
      h('div', { class: 'grid g-6-6 flat' },
        h('div', {}, h('h3', {}, CASH.find(([k]) => k === unit)[1]), issueSlot),
        h('div', {}, h('h3', {}, 'Staff hours by issue'), hoursSlot))),
    h('div', { class: 'grid g-4-4-4' },
      seriesPanel(app, ctx, trend, trendBurden, 'remedies_paid', 'Compensation'),
      seriesPanel(app, ctx, trend, trendBurden, 'complaint_expenses_paid', 'Expenses'),
      seriesPanel(app, ctx, trend, trendBurden, 'recorded_work', 'Staff hours')),
    h('section', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, 'Every payment', info('Each held payment, reversal and principal record in the publication, by effective date, with its evidence. Reversals are shown signed.', 'About payments')), h('span', { class: 'panel-meta' }, 'Oct 2025 – Sep 2026')),
      tlSlot),
    afcaShortcut(app, afca));

  // Outstanding obligations (stock) and the issue comparisons load after first paint.
  sharedCell(app, 'obligations.outstanding', [], melbourneMidnightUtc('2025-10-01'), pub.knowledge_end_exclusive, ctx.signal).then((ob) => {
    if (!ctx.isCurrent()) return;
    if (ob.kind !== Outcome.OK) { mount(obSlot, outcomeState(ob, { compact: true })); return; }
    const p = ob.cell.point;
    mount(obSlot, h('span', { class: 'kpi-num tnum' }, p.value === null ? 'Not available' : audMinor(p.value)), h('small', { class: 'muted' }, ` ${p.state} · ${ob.items.length} obligations`));
  });
  issueBars(app, ctx, model, scopeSel, unit, issueSlot, hoursSlot);
  financialTimeline(app, ctx, model, tlSlot);
}

function moneyCard(app, ctx, title, v, desc, basis) {
  return h('section', { class: 'kpi' }, h('div', { class: 'kpi-head' }, h('h2', { class: 'kpi-title' }, title), info(v ? basis : `${basis} No ${ctx.scope.grain} output is published for this bucket — unavailable, not zero.`, `About ${title}`)),
    h('div', { class: 'kpi-main' }, v ? h('span', { class: 'kpi-num tnum' }, audMinor(v.value)) : h('span', { class: 'kpi-na' }, 'Unavailable')),
    v ? h('div', { class: 'kpi-foot' }, h('a', { href: popHref(app, desc), 'data-nav': '', class: 'link-button' }, `${plural(ids(v).size, 'financial event')} →`)) : null);
}

async function issueBars(app, ctx, model, scopeSel, unit, slot, hoursSlot) {
  const measure = CASH.find(([k]) => k === unit)[2];
  let money; let hours; let limited = null;
  const ab = (id) => model.series.get(id)?.detail?.issue?.associated_burden || {};
  if (scopeSel === 'pub') {
    money = model.issues.map((i) => ({ iss: i, v: ab(i.issue_id)[unit]?.value ?? null, desc: `iburden~${i.issue_id}~${unit}`, state: 'association' }));
    hours = model.issues.map((i) => ({ iss: i, v: ab(i.issue_id).recorded_work?.value ?? null, desc: `iburden~${i.issue_id}~recorded_work`, state: 'association', seconds: true }));
  } else {
    const start = melbourneMidnightUtc(ctx.selected.start); const end = melbourneMidnightUtc(ctx.selected.end);
    const mi = periodCandidates(model.issues, (i) => ab(i.issue_id)[unit]?.value);
    const wi = periodCandidates(model.issues, (i) => ab(i.issue_id).recorded_work?.value);
    const [m, w] = await Promise.all([
      inPool(mi, PERIOD_CONCURRENCY, (i) => sharedCell(app, measure, [i.issue_id], start, end, ctx.signal)),
      inPool(wi, PERIOD_CONCURRENCY, (i) => sharedCell(app, 'work.recorded_hours', [i.issue_id], start, end, ctx.signal))]);
    if (!ctx.isCurrent()) return;
    const row = (i, o, meas) => (o.kind === Outcome.OK ? { iss: i, v: o.cell.point.value, state: o.cell.point.state, n: o.items.length, desc: `sq~${meas}~${i.issue_id}~${start}~${end}` } : { iss: i, v: null, state: 'unavailable', out: o });
    money = mi.map((i, k) => row(i, m[k], measure));
    hours = wi.map((i, k) => row(i, w[k], 'work.recorded_hours'));
    if (model.issues.length > mi.length) limited = { queried: mi.length, of: model.issues.length };
  }
  const sort = (rows) => rows.filter((r) => r.v !== null).sort((a, b) => b.v - a.v || (a.iss.issue_id < b.iss.issue_id ? -1 : 1));
  const fmtM = (v) => audMinor(v);
  const fmtH = (r) => (r.seconds ? secondsToHours(r.v) : `${Number(r.v).toFixed(2)} h`);
  const total = scopeSel === 'pub' ? model.ov.burden_total : null;
  const scopeText = scopeSel === 'pub' ? h('span', {}, 'Full year', info('Records associated with each issue’s supported cases over the publication window. Issues overlap; association, not cause.', 'About this scope')) : h('span', {}, ctx.selected.label, info(`Exact results for ${ctx.selected.label} by event date. “partial” means issue discovery is incomplete.`, 'About this scope'));
  const unavailable = [...money, ...hours].filter((r) => r.state === 'unavailable');
  const limitNote = (what) => (limited ? h('p', { class: 'warn-text', dataset: { burdenLimit: `${limited.queried}/${limited.of}` } },
    `Top ${limited.queried} of ${limited.of} issues shown`, info(`Only the ${limited.queried} issues with the largest full-year ${what} were queried for ${ctx.selected.label}, so this is not a complete ranking. “Full year” shows every issue.`, 'Why only some issues')) : null);
  const bars = (rows, fmt, unitName) => {
    const s = sort(rows);
    return [
      zeroNote(rows, s, unitName),
      // Signed values: a negative net amount (reversal larger than payments) keeps its sign and length.
      s.length ? rankBars({ rows: s.map((r) => ({ id: r.iss.issue_id, label: label(r.iss), value: r.v, display: fmt(r), note: r.state === 'association' ? null : r.state, ariaLabel: `${label(r.iss)}: ${fmt(r)} (${r.state}). Inspect records.` })),
        onSelect: (r) => app.router.go(popHref(app, s.find((x) => x.iss.issue_id === r.id).desc)), labelHref: (r) => app.link(`/issues/${r.id}`, { tab: 'impact' }) }) : null,
    ];
  };
  mount(slot, h('p', { class: 'panel-sub' }, scopeText), limitNote('amount'), bars(money, (r) => fmtM(r.v), 'amounts'),
    total ? h('p', { class: 'chart-caption' }, `Total ${audMinor(total[unit]?.value)} · `, h('a', { href: popHref(app, `pburden~${unit}`), 'data-nav': '' }, 'records'), ' · no issue: ', h('a', { href: popHref(app, `unalloc~${unit}`), 'data-nav': '' }, audMinor(model.ov.burden_unallocated_to_any_issue?.[unit]?.value))) : null,
    unavailable.length ? h('p', { class: 'warn-text' }, `${unavailable.length} issue requests unavailable.`) : null);
  mount(hoursSlot, h('p', { class: 'panel-sub' }, scopeText), limitNote('hours'), bars(hours, fmtH, 'hours'),
    total ? h('p', { class: 'chart-caption' }, `Total ${secondsToHours(total.recorded_work?.value)} · `, h('a', { href: popHref(app, 'pburden~recorded_work'), 'data-nav': '' }, 'work entries')) : null);
}

/**
 * Zero/unavailable wording for per-issue results. Only a complete set of known
 * results that are all zero is a measured zero; an empty or partly unavailable
 * set never is, and partial (issue discovery incomplete) zeros are a known subtotal.
 */
export function zeroNote(rows, available, unitName) {
  const missing = rows.length - available.length;
  if (!available.length) return h('p', { class: 'warn-text', dataset: { burdenState: 'unavailable' } }, `No issue result is available for ${unitName} in this scope — unavailable, not zero.`);
  if (!available.every((r) => r.v === 0)) return missing ? h('p', { class: 'warn-text', dataset: { burdenState: 'partial' } }, `${missing} of ${rows.length} issue results unavailable; shown values are a known subset.`) : null;
  if (missing) return h('p', { class: 'warn-text', dataset: { burdenState: 'partial' } }, `Zero in the ${available.length} available issue results; ${missing} unavailable — not a measured zero for every issue.`);
  if (available.some((r) => r.state === 'partial')) return h('p', { class: 'muted', dataset: { burdenState: 'partial-zero' } }, `No recorded ${unitName} in the known results; issue discovery is incomplete, so this is a known zero subtotal.`);
  return h('p', { class: 'muted', dataset: { burdenState: 'zero' } }, `No recorded ${unitName} for any issue in this scope (measured zero).`);
}

/** Window total only over complete bucket coverage; otherwise a labelled known subtotal or unavailable. */
export function windowTotal(points, fmt) {
  const known = points.filter((p) => p.state === 'value');
  const missing = points.length - known.length;
  const sum = known.reduce((a, p) => a + p.value, 0);
  if (!known.length) return { state: 'unavailable', text: 'Window total unavailable — no published value for any bucket in this window (not zero).' };
  if (missing) return { state: 'partial', text: `Known subtotal ${fmt(sum)} · ${missing} of ${points.length} buckets unavailable (not zero).` };
  return { state: 'complete', text: `Window total ${fmt(sum)}` };
}

function seriesPanel(app, ctx, trend, bu, key, title) {
  if (trend.kind !== Outcome.OK || bu.kind !== Outcome.OK) return h('section', { class: 'panel' }, h('h2', {}, title), failed(trend.kind !== Outcome.OK ? trend.out : bu, { compact: true }));
  const fmt = (v) => (key === 'recorded_work' ? secondsToHours(v) : audMinor(v));
  const pts = trend.cols.map((c) => {
    const it = bu.items.find((x) => x.bucket === c.key);
    return { key: c.key, short: c.short, year: c.year, label: c.label, highlight: c.highlight, value: it ? it[key].value : null, display: it ? fmt(it[key].value) : '—', state: it ? 'value' : 'unavailable' };
  });
  const kind = key === 'recorded_work' ? 'work' : 'fin';
  const tot = windowTotal(pts, fmt);
  return h('section', { class: 'panel' }, h('h2', {}, title),
    h('p', { class: 'panel-sub' }, `By ${key === 'recorded_work' ? 'work' : 'payment'} date`),
    bu.items.length ? null : h('p', { class: 'warn-text' }, `This publication has no ${trend.grain} burden series; values are unavailable, not zero.`),
    columns({ points: pts, unitWord: '', showValues: 'highlight', onSelect: (p) => app.router.go(popHref(app, kind === 'work' ? `work~${trend.grain}~${p.key}` : `fin~${key}~${trend.grain}~${p.key}`)) }),
    h('p', { class: `chart-caption tnum${tot.state === 'complete' ? '' : ' warn-text'}`, dataset: { windowTotal: tot.state } }, tot.text));
}

async function financialTimeline(app, ctx, model, slot) {
  const bt = model.ov.burden_total || {};
  const kinds = [['remedies_paid', 'Compensation / remedy'], ['complaint_expenses_paid', 'Complaint expense'], ['principal_or_entitlement_paid', 'Principal / entitlement (not complaint cost)']];
  const all = [];
  for (const [k, l] of kinds) for (const id of ids(bt[k])) all.push({ id, cls: l });
  const btn = h('button', { type: 'button', class: 'button-secondary', onclick: async () => {
    mount(slot, loading(`Resolving ${all.length} financial records`));
    const recs = await Promise.all(all.map(async (r) => ({ ...r, out: await app.store.shared((c) => ({ operation: 'entity.get', context_id: c.context_id, entity_id: r.id, entity_kind: 'financial_event', immutable_record_id: null, cursor: null, page_size: 20 })) })));
    const ok = recs.filter((r) => r.out.kind === Outcome.OK).map((r) => ({ ...r, d: r.out.payload.record.data, refs: r.out.payload.source_refs || [] }));
    ok.sort((a, b) => (a.d.effective_date < b.d.effective_date ? -1 : a.d.effective_date > b.d.effective_date ? 1 : 0));
    const obCases = new Map();
    await Promise.all([...new Set(ok.map((r) => r.d.obligation_id).filter(Boolean))].map(async (oid) => {
      const ob = await app.store.shared((c) => ({ operation: 'entity.get', context_id: c.context_id, entity_id: oid, entity_kind: 'obligation', immutable_record_id: null, cursor: null, page_size: 20 }));
      if (ob.kind === Outcome.OK) obCases.set(oid, ob.payload.record.data);
    }));
    mount(slot,
      recs.length > ok.length ? h('p', { class: 'warn-text' }, `${recs.length - ok.length} records unavailable in this access scope.`) : null,
      timeline(ok.map((r) => {
        const ob = obCases.get(r.d.obligation_id); const c = ob?.case_ids?.[0];
        return {
          date: fmtDate(r.d.effective_date), kind: r.cls, tone: r.d.amount_minor < 0 ? 'w' : 'a', data: { record: r.id },
          title: `${audMinor(r.d.amount_minor)} · ${String(r.d.kind).replaceAll('_', ' ')}`,
          body: `${ob ? `${ob.category} obligation · ` : ''}record ${short(r.id)}${r.d.adjusts_event_id ? ` · adjusts ${short(r.d.adjusts_event_id)}` : ''}`,
          chips: [evidenceChip(app, r.refs[0], null, c, null), c ? h('a', { href: caseHref(app, c, null, 'Financial event'), 'data-nav': '', class: 'chip' }, `Case ${short(c)}`) : null].filter(Boolean),
        };
      })));
  } }, `Show ${all.length} financial events`);
  mount(slot, btn);
}

function afcaShortcut(app, afca) {
  if (afca.kind !== Outcome.OK) return h('section', { class: 'panel' }, h('h2', {}, 'AFCA'), outcomeState(afca, { compact: true }));
  const st = afca.detail?.matters_by_current_stage || {};
  return h('section', { class: 'panel afca-short' },
    h('div', { class: 'panel-head' }, h('h2', {}, icon('afca'), ' AFCA'), h('a', { href: app.link('/impact/afca'), 'data-nav': '', class: 'button-primary small' }, 'Open AFCA')),
    h('p', {}, h('strong', { class: 'tnum' }, String(afca.total)), ` external-dispute matters · ${Object.entries(st).map(([k, v]) => `${v.value} ${k.replaceAll('_', ' ')}`).join(' · ')}`, info(`${afca.detail?.not_assessed_matters ?? 0} further matters are linked only to unassessed cases and are not assessed.`, 'About AFCA matters')));
}
