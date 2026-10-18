// Intelligence Brief: the opening of Executive Overview. One featured finding
// and up to two secondary investigation entry points, selected deterministically
// from published results (never a severity or priority score, never invented):
//
//   1. lead       — the leading supported pattern in the selected period
//                   (most supported received cases, ties by issue ID; all pages ranked);
//   2. signal     — a published emerging signal, preferring one outside Top N;
//   3. persistent — a supported pattern with persistent open workload
//                   (most supported cases open > 90 days at cutoff, published by the alert).
//
// An issue appears at most once; a role with no qualifying published issue is
// omitted rather than filled.

import { h } from '../ui/dom.js';
import { spark, pct } from '../ui/viz.js';
import { ids, intersect } from '../lib/calc.js';
import { count } from '../lib/format.js';
import { fmtDate } from '../lib/periods.js';
import { kindTag, info, MODEL_NOTE } from '../ui/labels.js';
import { icon } from '../ui/icons.js';
import { label, issueBadges, shareText, deltaText, popHref } from './model.js';

const byId = (a, b) => (a.issue_id < b.issue_id ? -1 : a.issue_id > b.issue_id ? 1 : 0);

/**
 * Deterministic brief selection. `rows` are the selected period's ranking rows
 * over ALL issues (rankedFor); `alerts` the fully paged published alerts.
 */
export function selectBrief(rows, alerts, topN) {
  const out = [];
  const used = new Set();
  const topIds = new Set(rows.slice(0, topN).map((r) => r.id));
  const lead = rows.find((r) => (r.n ?? 0) > 0);
  if (lead) { out.push({ role: 'lead', id: lead.id }); used.add(lead.id); }
  const emerging = alerts.filter((a) => a.state === 'emerging_concentration' && !used.has(a.issue_id))
    .sort((a, b) => b.absolute_change - a.absolute_change || byId(a, b));
  const sig = emerging.find((a) => !topIds.has(a.issue_id)) || emerging[0];
  if (sig) { out.push({ role: 'signal', id: sig.issue_id, alert: sig, outsideTop: !topIds.has(sig.issue_id) }); used.add(sig.issue_id); }
  const persistent = alerts.filter((a) => (a.persistent_open_over_90_days?.value ?? 0) > 0 && !used.has(a.issue_id))
    .sort((a, b) => b.persistent_open_over_90_days.value - a.persistent_open_over_90_days.value || byId(a, b));
  if (persistent[0]) out.push({ role: 'persistent', id: persistent[0].issue_id, alert: persistent[0] });
  return out;
}

/** Selected-period assessment states for one issue: uncertain and not assessed among the received cases. */
export function periodStates(iss, bucket) {
  if (!bucket) return null;
  const R = bucket.R;
  const noMem = ids(iss.membership?.no_memory); const unPair = ids(iss.membership?.unassessed);
  const unassessed = new Set([...R].filter((c) => bucket.unassessed.has(c) || noMem.has(c) || unPair.has(c)));
  return { received: R.size, uncertain: intersect(ids(iss.membership?.uncertain), R), unassessed };
}

const ROLE = {
  lead: () => 'Top pattern',
  signal: () => 'Emerging',
  persistent: () => 'Stuck open',
};

export function intelligenceBrief(app, ctx, model, rows, prevLabel) {
  const picks = selectBrief(rows, model.alerts, ctx.scope.topN);
  const rowOf = new Map(rows.map((r) => [r.id, r]));
  const bucket = model.arr.get(ctx.selected.key);
  if (!picks.length) {
    return h('section', { class: 'brief brief-empty', 'aria-labelledby': 'h-brief' },
      h('h2', { id: 'h-brief', class: 'brief-title' }, 'Intelligence Brief'),
      h('p', { class: 'muted' }, `No supported complaint pattern among cases received in ${ctx.selected.label}, and no published signal or persistent workload pattern. This is a published result, not missing data.`));
  }
  const [first, ...rest] = picks;
  return h('section', { class: 'brief', 'aria-labelledby': 'h-brief' },
    h('div', { class: 'brief-head' },
      h('h2', { id: 'h-brief', class: 'brief-title' }, 'Intelligence Brief'),
      h('p', { class: 'brief-sub' }, `What the AI found in ${ctx.selected.label}`, info(`Up to three entry points picked by fixed rules from the published results — the leading pattern, an emerging signal and persistent open work. Not a severity or priority score. ${MODEL_NOTE}.`, 'How these were chosen'))),
    h('div', { class: `brief-grid${rest.length ? '' : ' single'}` },
      findingCard(app, ctx, model, first, rowOf.get(first.id), bucket, prevLabel, true),
      rest.length ? h('div', { class: 'brief-side' }, rest.map((p) => findingCard(app, ctx, model, p, rowOf.get(p.id), bucket, prevLabel, false))) : null));
}

function findingCard(app, ctx, model, pick, row, bucket, prevLabel, featured) {
  const iss = model.byId.get(pick.id);
  const ser = model.series.get(pick.id);
  const def = ser?.detail?.definition || iss.definition || {};
  const st = periodStates(iss, bucket);
  const vals = ser ? ser.items.map((x) => x.affected_new_cases?.value ?? null) : [];
  const titleId = `brief-${pick.role}`;
  const investigate = app.link(`/issues/${pick.id}`);
  const defBody = h('p', { class: `finding-def${featured ? '' : ' is-clamped'}`, id: `${titleId}-def` }, def.definition || 'No published definition text.');
  const defToggle = featured ? null : h('button', {
    type: 'button', class: 'link-button small', 'aria-expanded': 'false', 'aria-controls': `${titleId}-def`,
    onclick: (e) => { const open = defBody.classList.toggle('is-clamped') === false; e.currentTarget.setAttribute('aria-expanded', String(open)); e.currentTarget.textContent = open ? 'Show less' : 'Show full definition'; },
  }, 'Show full definition');
  return h('article', { class: `finding${featured ? ' is-featured' : ''}`, 'aria-labelledby': titleId, dataset: { role: pick.role, issue: pick.id } },
    h('div', { class: 'finding-eyebrow' },
      h('span', { class: 'finding-role' }, icon(pick.role === 'signal' ? 'signal' : pick.role === 'persistent' ? 'experience' : 'lens', { size: 15 }), ROLE[pick.role](ctx)),
      pick.outsideTop ? h('span', { class: 'badge' }, `Outside Top ${ctx.scope.topN}`) : null,
      issueBadges(iss, ser)),
    h('h3', { class: 'finding-title', id: titleId }, h('a', { href: investigate, 'data-nav': '' }, label(iss))),
    h('div', { class: 'finding-def-wrap' }, defBody, defToggle),
    h('div', { class: 'finding-metrics' },
      h('div', { class: 'finding-metric' },
        h('p', { class: 'finding-num tnum' }, row?.n === null || row?.n === undefined ? '—' : count(row.n), h('small', {}, ` complaint${row?.n === 1 ? '' : 's'}`)),
        h('p', { class: 'finding-share' }, `${row ? shareText(row) : 'share unavailable'} received`),
        h('p', { class: 'finding-delta muted' }, row ? deltaText(row.cmp, prevLabel) : '')),
      vals.length ? h('div', { class: 'finding-trend', title: `Supported cases per ${ctx.scope.grain === 'observation_year' ? 'year' : ctx.scope.grain} (receipt date)` }, spark(vals)) : null),
    roleFact(app, ctx, pick, iss),
    st ? h('p', { class: 'finding-coverage' },
      kindTag('uncertain', `${count(st.uncertain.size)} uncertain`), ' ',
      kindTag('na', `${count(st.unassessed.size)} not assessed`),
      info(`Of ${count(st.received)} cases received in ${ctx.selected.label}. Uncertain = the AI could not decide; not assessed = no assessment yet. Issues overlap, so shares do not add to 100%.`, 'About coverage')) : null,
    h('div', { class: 'finding-actions' },
      h('a', { href: investigate, 'data-nav': '', class: 'button-primary', dataset: { action: 'investigate', focusKey: `brief:${pick.id}` } }, icon('lens', { size: 16 }), 'Investigate finding'),
      row?.n ? h('a', { href: popHref(app, `reach~${pick.id}~${ctx.scope.grain}~${ctx.selected.key}`), 'data-nav': '', class: 'link-button' }, `See ${count(row.n)} cases`) : null));
}

function roleFact(app, ctx, pick, iss) {
  const a = pick.alert;
  if (pick.role === 'signal') {
    const cw = a.current_window; const bw = a.baseline_window;
    return h('p', { class: 'finding-fact' }, h('strong', { class: 'tnum' }, count(cw.affected.value)), ` in the last 28 days vs ${Number.isFinite(bw.affected_per_28_days) ? +bw.affected_per_28_days.toFixed(1) : 'n/a'} before`,
      info(`${count(cw.affected.value)} supported cases received ${fmtDate(cw.start, { year: false })}–${fmtDate(cw.end_inclusive)}, against ${Number.isFinite(bw.affected_per_28_days) ? +bw.affected_per_28_days.toFixed(1) : 'n/a'} per 28 days in ${fmtDate(bw.start, { year: false })}–${fmtDate(bw.end_exclusive)} (exclusive). A demo heuristic, not statistical significance.`, 'About this signal'));
  }
  if (pick.role === 'persistent') {
    return h('p', { class: 'finding-fact' }, h('strong', { class: 'tnum' }, count(a.persistent_open_over_90_days.value)), ` open over 90 days · ${count(iss.open_included?.value ?? 0)} open in total · `,
      h('a', { href: popHref(app, `alert~${pick.id}~persistent`), 'data-nav': '' }, 'See them'));
  }
  return null;
}

export { pct };
