// Canonical issue profile, reached from any ranking, heatmap, signal, backlog
// or burden mark and from "Investigate finding". Header states the published
// definition and population, with provenance behind "How this finding was
// produced"; five local tabs: Investigate (default; connected population →
// assessment → evidence, see investigate.js), Dynamics, Population,
// Experience & Backlog, Recorded Impact.
// The originating period stays explicit; lifetime membership is labelled as
// such and never substituted for a clicked period's population.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { columns, bandBars, pct, signed, plural } from '../ui/viz.js';
import { ids, intersect, ageBands, reach, compare } from '../lib/calc.js';
import { audMinor, secondsToHours, count, instant } from '../lib/format.js';
import { fmtDate, knownThrough, melbourneMidnightUtc } from '../lib/periods.js';
import { loadPortfolio, loadTrend, label, issueBadges, shareText, deltaText, comparablePeriod, popHref, failed } from './model.js';
import { dynamicsPanel, signalCard } from './overview.js';
import { caseCard, sharedCell, evidenceChip, short, caseHref, unassessedBasis, unassessedForIssue } from './population.js';
import { loading, outcomeState } from '../ui/states.js';
import { kindTag, info, MODEL_NOTE } from '../ui/labels.js';
import { tabInvestigate } from './investigate.js';

const TABS = [['investigate', 'Investigate'], ['dynamics', 'Dynamics'], ['population', 'Population'], ['experience', 'Experience & Backlog'], ['impact', 'Recorded Impact']];

export async function renderIssue(app, loc, ctx) {
  const id = loc.parts[1];
  const model = await loadPortfolio(app, ctx);
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Issue profile'), failed(model.out)); return; }
  const iss = model.byId.get(id);
  if (!iss) { mount(app.main, h('h1', {}, 'Issue not in this publication'), h('p', {}, h('a', { href: app.link('/issues'), 'data-nav': '' }, 'Back to Issues & Trends'))); return; }
  const ser = model.series.get(id);
  const d = ser.detail?.issue || {};
  const def = ser.detail?.definition || iss.definition;
  const tab = TABS.some(([t]) => t === loc.params.get('tab')) ? loc.params.get('tab') : 'investigate';
  const bucket = model.arr.get(ctx.selected.key);
  const r = reach(ids(iss.membership?.included), bucket);
  const body = h('div', { class: 'tab-body' }, loading());

  mount(app.main,
    h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, h('a', { href: app.link('/issues'), 'data-nav': '' }, 'Issues & Trends'), h('span', { 'aria-hidden': 'true' }, ' / '), h('span', {}, 'Issue profile')),
    h('div', { class: 'page-head issue-head' },
      h('p', { class: 'eyebrow' }, kindTag('ai', 'AI finding'), info(`${MODEL_NOTE}. Found and assessed by the model; not yet reviewed by a person.`, 'About AI findings')),
      h('h1', {}, label(iss)),
      h('div', { class: 'chips' }, issueBadges(iss, ser),
        h('span', { class: 'chip chip-origin' }, `Origin period: ${ctx.selected.label} · ${r.n ?? '—'} supported of ${r.d ?? '—'} received (${shareText(r)})`)),
      h('p', { class: 'definition' }, def.definition),
      h('div', { class: 'head-details' }, h('details', { class: 'def-more' }, h('summary', {}, 'Inclusion rule'),
        h('dl', { class: 'kv' }, h('dt', {}, 'Include if'), h('dd', {}, def.include_if || '—'), h('dt', {}, 'Exclude if'), h('dd', {}, def.exclude_if || '—'),
          h('dt', {}, 'Alternatives'), h('dd', {}, def.alternatives || '—'))),
        producedDetails(app, def, d)),
      h('div', { class: 'pop-strip' },
        [['included', 'supported'], ['uncertain', 'uncertain'], ['excluded', 'excluded']].map(([st, w]) => h('span', {}, h('strong', { class: 'tnum' }, String(iss.membership?.[st]?.value ?? 0)), ` ${w}`)),
        h('span', { title: 'Holdout (not eligible), eligible cases pending assessment, and unassessed pairs' }, h('strong', { class: 'tnum' }, String(unassessedForIssue(iss, d).length)), ' not assessed'),
        h('span', {}, h('strong', { class: 'tnum' }, String(iss.affected_members?.value ?? '—')), ' members'),
        h('span', {}, h('strong', { class: 'tnum' }, String(iss.open_included?.value ?? '—')), ' open at cutoff'),
        info('Whole publication, all cases received to date. Not assessed = holdout, pending assessment or an unassessed pair.', 'About these counts'))),
    h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Issue profile sections' },
      TABS.map(([t, l]) => h('a', { role: 'tab', href: app.link(`/issues/${id}`, { tab: t === 'investigate' ? null : t, iss: loc.params.get('iss') }), 'data-nav': '', 'aria-selected': t === tab ? 'true' : 'false', class: 'tab', dataset: { tab: t } }, l))),
    body);

  const render = { investigate: tabInvestigate, dynamics: tabDynamics, population: tabPopulation, experience: tabExperience, impact: tabImpact }[tab];
  await render(app, loc, ctx, model, iss, ser, body);
}

/** Provenance kept out of the way but one click from every finding. */
function producedDetails(app, def, d) {
  const b = app.store.binding; const pub = b.publication; const c = b.context || {};
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
  return h('details', { class: 'produced' }, h('summary', {}, 'How this finding was produced'),
    h('p', { class: 'muted small' }, 'Found by a model over sealed case memories and published as a fixed snapshot. No model runs while you browse.'),
    h('dl', { class: 'kv compact' },
      row('Assessment', `${pub.model_assessed ? 'Model-assessed' : 'Not model-assessed'} · human review ${String(pub.human_review || 'not stated').replaceAll('_', ' ')}`),
      row('Definition', `${def.definition_id ? `${def.definition_id} · ` : ''}v${def.definition_version} · ${def.level} · ${def.dimension}`),
      row('Lineage', (def.lineage || []).map((l) => `${l.event} at view ${l.at_view}`).join(' · ') || '—'),
      row('Discrimination', d.discrimination ? `${d.discrimination.state.replaceAll('_', ' ')} · included in ${pct(d.discrimination.included_share)} of ${d.discrimination.assessed} assessed${d.discrimination.note ? ` — ${d.discrimination.note}` : ''}` : '—'),
      row('Knowledge view', `${pub.view_date} · evidence through ${fmtDate(knownThrough(pub.knowledge_end_exclusive))} · completed ${instant(pub.watermarks?.published_at)}`),
      row('Publication', h('span', { class: 'mono' }, pub.publication_id)),
      row('Release', h('span', { class: 'mono' }, c.release_id)),
      row('Analysis set · taxonomy', h('span', { class: 'mono' }, `${pub.analysis_set_version} · ${c.taxonomy_version}`)),
      row('Contracts', `Shared ${app.config?.contract_version || '—'} · intelligence companion ${app.config?.intelligence_schema || '—'}`)));
}

async function tabDynamics(app, loc, ctx, model, iss, ser, body) {
  const trend = await loadTrend(app, ctx, [iss.issue_id]);
  if (!ctx.isCurrent()) return;
  const d = ser.detail?.issue || {};
  // Same comparable period as Overview/Issues (matched elapsed days for a clipped week).
  const cmpP = await comparablePeriod(app, ctx, model);
  if (!ctx.isCurrent()) return;
  const cur = reach(ids(iss.membership?.included), model.arr.get(ctx.selected.key));
  const pr = cmpP.bucket ? reach(ids(iss.membership?.included), cmpP.bucket) : null;
  const cmp = compare(cur, pr);
  const alert = model.alertById.get(iss.issue_id);
  const fe = trend.kind === Outcome.OK ? trend.series.get(iss.issue_id).items : [];
  const feCols = trend.kind === Outcome.OK ? trend.cols.map((c) => {
    const it = fe.find((x) => x.bucket === c.key);
    return { key: c.key, short: c.short, year: c.year, label: c.label, highlight: c.highlight, value: it?.first_evidenced?.value ?? null, display: String(it?.first_evidenced?.value ?? '—'), state: it ? 'value' : 'unavailable' };
  }) : [];
  mount(body,
    h('div', { class: 'grid g-8-4' },
      dynamicsPanel(app, ctx, model, trend, [iss.issue_id], { title: 'Supported Reach Over Time' }),
      h('div', { class: 'stack' },
        h('section', { class: 'panel' }, h('h2', {}, 'Period Comparison'),
          h('p', { class: 'big-fig tnum' }, `${cur.n ?? '—'}`, h('small', {}, ` cases in ${ctx.selected.label}`)),
          h('p', {}, shareText(cur)),
          h('p', { class: 'muted' }, deltaText(cmp, cmpP.label || cmpP.reason)),
          h('a', { href: popHref(app, `reach~${iss.issue_id}~${ctx.scope.grain}~${ctx.selected.key}`), 'data-nav': '', class: 'card-link' }, 'Inspect selected-period cases →')),
        alert ? signalCard(app, ctx, model, alert, true, ser) : null)),
    h('section', { class: 'panel' }, h('h2', {}, 'When the evidence appeared', info('Cases whose support first appeared in each bucket — when the pattern was learned, not when the complaint was received. Not new arrivals and not incident onset.', 'About first evidenced')),
      feCols.length ? columns({ points: feCols, unitWord: 'cases first evidenced', onSelect: (p) => app.router.go(popHref(app, `fe~${iss.issue_id}~${trend.grain}~${p.key}`)) }) : h('p', { class: 'muted' }, 'Unavailable')),
    d.overlapping_issues?.length ? h('section', { class: 'panel' }, h('h2', {}, 'Overlapping issues', info(d.overlap_note || 'Published overlap with other issues (Jaccard of supported cases).', 'About overlap')),
      h('div', { class: 'chips' }, d.overlapping_issues.map((o) => h('a', { class: 'chip', href: app.link(`/issues/${o.issue_id}`), 'data-nav': '' }, `${label(model.byId.get(o.issue_id))} · ${o.shared_cases} shared · J=${o.jaccard}`)))) : null);
}

async function tabPopulation(app, loc, ctx, model, iss, ser, body) {
  const d = ser.detail?.issue || {};
  const bucket = model.arr.get(ctx.selected.key);
  const states = [
    ['period', `In ${ctx.selected.label}`, reach(ids(iss.membership?.included), bucket).ids],
    ['included', 'Included', [...ids(iss.membership?.included)].sort()],
    ['uncertain', 'Uncertain', [...ids(iss.membership?.uncertain)].sort()],
    ['excluded', 'Excluded', [...ids(iss.membership?.excluded)].sort()],
    ['unassessed', 'Unassessed', unassessedForIssue(iss, d)],
  ].filter(([s, , l]) => s === 'period' || s === 'included' || l.length || iss.membership?.[s]);
  const pick = states.find(([s]) => s === loc.params.get('ms')) || states[0];
  const grid = h('div', { class: 'card-grid' });
  const foot = h('div', { class: 'list-foot' });
  let shown = 0;
  const page = () => {
    for (const cid of pick[2].slice(shown, shown + 12)) grid.appendChild(membershipCard(app, ctx, cid, iss, pick[0], pick[1]));
    shown = Math.min(pick[2].length, shown + 12);
    mount(foot, h('span', { class: 'muted' }, `Showing ${shown} of ${pick[2].length}`), shown < pick[2].length ? h('button', { type: 'button', class: 'button-secondary', onclick: page }, 'Load more') : null);
  };
  mount(body,
    h('section', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, 'Cases')),
      h('div', { class: 'seg', role: 'group', 'aria-label': 'Membership state' }, states.map(([s, l, list]) => h('button', {
        type: 'button', class: 'seg-b', 'aria-pressed': s === pick[0] ? 'true' : 'false', dataset: { ms: s }, onclick: () => app.setLocal({ ms: s === 'period' ? null : s }),
      }, `${l} · ${list.length}`))),
      h('p', { class: 'panel-sub' }, pick[0] === 'period' ? `Received in ${ctx.selected.label}`
        : pick[0] === 'unassessed' ? h('span', {}, 'Not assessed', info(`${unassessedBasis('Received', pick[2], new Set(d.assessment_scope?.not_eligible_cases_unassessed || []))}. Missing assessment is not exclusion.`, 'About not assessed'))
          : pick[0] === 'excluded' ? h('span', {}, 'Excluded after assessment', info('Assessed, with the published reason for exclusion. Not the same as not assessed.', 'About excluded'))
            : 'All received to date'),
      grid, foot));
  page();
}

/** Case card with this issue's published membership reason and support/contrary citations (from case memory). */
function membershipCard(app, ctx, caseId, iss, state, stateLabel) {
  // The selected-period tab keeps its exact receipt-period descriptor; whole-publication tabs keep `mem`.
  const desc = state === 'period' ? `reach~${iss.issue_id}~${ctx.scope.grain}~${ctx.selected.key}` : `mem~${iss.issue_id}~${state}`;
  const el = caseCard(app, ctx, caseId, { desc, from: `${label(iss)} · ${stateLabel}` });
  if (state === 'unassessed') { el.appendChild(h('p', { class: 'muted' }, 'Not assessed for this issue in this publication (holdout, pending assessment or unassessed pair).')); return el; }
  const slot = h('div', { class: 'card-reason' }, h('span', { class: 'muted' }, 'Loading membership reason…'));
  el.insertBefore(slot, el.lastChild);
  app.store.memory(caseId).then((m) => {
    if (m.kind !== Outcome.OK) { mount(slot, outcomeState(m, { compact: true })); return; }
    const ms = (m.detail.memberships || []).find((x) => x.issue_id === iss.issue_id);
    if (!ms) { mount(slot, h('p', { class: 'muted' }, 'No membership record for this issue in the case memory.')); return; }
    const claims = new Map(m.items.map((c) => [c.claim_id, c]));
    const chip = (cid, tone) => { const c = claims.get(cid); return c ? h('span', { class: `cite-${tone}` }, evidenceChip(app, c.ref, c.quote, caseId, desc)) : null; };
    mount(slot,
      h('p', {}, h('span', { class: `badge badge-ms-${ms.state}` }, ms.state), ' ', ms.reason || 'No reason recorded'),
      ms.support_claim_ids?.length ? h('div', { class: 'chips' }, h('span', { class: 'muted' }, 'Support:'), ms.support_claim_ids.slice(0, 2).map((c) => chip(c, 'support'))) : null,
      ms.contrary_claim_ids?.length ? h('div', { class: 'chips' }, h('span', { class: 'muted' }, 'Contrary:'), ms.contrary_claim_ids.slice(0, 2).map((c) => chip(c, 'contrary'))) : null,
      h('p', { class: 'muted small' }, `Evidence basis ${String(ms.evidence_basis || '').replaceAll('_', ' ')} · ${ms.claims_shown ?? '?'} of ${ms.claims_total ?? '?'} claims shown to the assessor`));
  });
  return el;
}

async function tabExperience(app, loc, ctx, model, iss, ser, body) {
  const ov = model.ov;
  const openIds = intersect(ids(ov.backlog.open), ids(iss.membership?.included));
  const { bands, unknown } = ageBands(openIds, ov.backlog.open_age_days?.by_case);
  const alert = model.alertById.get(iss.issue_id);
  const d = ser.detail?.issue || {};
  const included = [...ids(iss.membership?.included)];
  const effSlot = h('div', {});
  mount(body,
    h('div', { class: 'grid g-6-6' },
      h('section', { class: 'panel' }, h('h2', {}, 'Still open', info('A historical link is not proof of a currently active fault.', 'About open cases')),
        h('p', { class: 'panel-sub' }, `${openIds.size} open at ${fmtDate(knownThrough(app.store.binding.publication.knowledge_end_exclusive))} · median ${d.open_included_age_days?.median ?? '—'} days · oldest ${d.open_included_age_days?.max ?? '—'}`),
        bandBars({ rows: [...bands.map((b) => ({ id: b.id, label: b.label, value: b.ids.length, display: String(b.ids.length) })), { id: 'unknown', label: 'Unknown age', value: unknown.length, display: String(unknown.length), muted: true }],
          onSelect: (r) => app.router.go(popHref(app, `openiss~${iss.issue_id}~${r.id}`)) }),
        alert?.persistent_open_over_90_days?.value ? h('p', {}, h('a', { href: popHref(app, `alert~${iss.issue_id}~persistent`), 'data-nav': '' }, `${alert.persistent_open_over_90_days.value} persistent open more than 90 days →`)) : null),
      h('section', { class: 'panel' }, h('h2', {}, 'Member effort', info('Occurrences recorded in the case memories of this issue’s supported cases, whole publication. Counted as occurrences or commitments, not complaints.', 'About member effort')), effSlot)));
  // On demand only: summarising effort reads every included case memory.
  const go = await new Promise((resolve) => mount(effSlot,
    included.length ? h('button', { type: 'button', class: 'button-secondary', onclick: () => resolve(true) }, `Summarise ${count(included.length)} cases`) : h('p', { class: 'muted' }, 'No supported cases.')));
  if (!go || !ctx.isCurrent()) return;
  mount(effSlot, loading(`Reading ${count(included.length)} case memories`));
  const mems = await Promise.all(included.map((c) => app.store.memory(c, { signal: ctx.signal })));
  if (!ctx.isCurrent()) return;
  const ok = mems.filter((m) => m.kind === Outcome.OK);
  const effort = {}; const cmt = {}; let contradictions = 0;
  for (const m of ok) {
    for (const e of m.detail.member_effort || []) effort[e.kind] = (effort[e.kind] || 0) + 1;
    for (const c of m.detail.commitments || []) cmt[c.status] = (cmt[c.status] || 0) + 1;
    contradictions += (m.detail.contradictions || []).length;
  }
  mount(effSlot,
    ok.length < included.length ? h('p', { class: 'warn-text' }, `${included.length - ok.length} case memories unavailable; counts are partial.`) : null,
    bandBars({ rows: ['chase', 'resubmission', 'channel_change'].map((k) => ({ id: k, label: k.replace('_', ' '), value: effort[k] || 0, display: plural(effort[k] || 0, 'occurrence') })) }),
    h('p', { class: 'muted' }, `Commitments: ${Object.entries(cmt).map(([k, v]) => `${v} ${k}`).join(' · ') || 'none recorded'} · ${contradictions} contradictions`));
}

const MONEY = [['remedies_paid', 'Paid compensation / remedies', 'cash.remedies_paid'], ['complaint_expenses_paid', 'Paid complaint expenses', 'cash.expenses_paid'], ['recorded_work', 'Recorded handling effort', 'work.recorded_hours']];

async function tabImpact(app, loc, ctx, model, iss, ser, body) {
  const ab = ser.detail?.issue?.associated_burden || {};
  const fmt = (k, v) => (k === 'recorded_work' ? secondsToHours(v) : audMinor(v));
  const start = melbourneMidnightUtc(ctx.selected.start); const end = melbourneMidnightUtc(ctx.selected.end);
  const periodSlot = h('div', { class: 'kpis kpis-3' }, loading('Requesting exact selected-period values'));
  mount(body,
    h('section', { class: 'panel' }, h('h2', {}, ctx.selected.label, info('Exact results for this issue by event date. “Partial” means issue discovery is incomplete — not an exhaustive affected population.', 'About these values')),
      periodSlot),
    h('section', { class: 'panel' }, h('h2', {}, 'Full year', info(`${ab.note || ''} Associated with supported cases — not causal or avoidable cost. ${ser.detail?.issue?.burden_shared_with_other_issues_cases?.length ?? 0} of its cases also belong to other issues.`, 'About full-year cost')),
      h('div', { class: 'kpis' }, [...MONEY, ['principal_or_entitlement_paid', 'Principal or entitlement (not complaint cost)']].map(([k, l]) => h('div', { class: 'mini' },
        h('p', { class: 'mini-title' }, l),
        h('p', { class: 'mini-fig tnum' }, ab[k] ? fmt(k, ab[k].value) : 'Not recorded'),
        ab[k] ? h('a', { href: popHref(app, `iburden~${iss.issue_id}~${k}`), 'data-nav': '', class: 'card-link' }, `${plural(ids(ab[k]).size, 'record')} →`) : null)))));
  const outs = await Promise.all(MONEY.map(([, , m]) => sharedCell(app, m, [iss.issue_id], start, end, ctx.signal)));
  const ob = await sharedCell(app, 'obligations.outstanding', [iss.issue_id], melbourneMidnightUtc('2025-10-01'), app.store.binding.publication.knowledge_end_exclusive, ctx.signal);
  if (!ctx.isCurrent()) return;
  mount(periodSlot,
    MONEY.map(([k, l, m], i) => periodMini(app, l, outs[i], m, iss.issue_id, start, end)),
    periodMini(app, 'Outstanding obligations at cutoff', ob, 'obligations.outstanding', iss.issue_id, melbourneMidnightUtc('2025-10-01'), app.store.binding.publication.knowledge_end_exclusive));
}

function periodMini(app, title, out, measure, issueId, start, end) {
  if (out.kind !== Outcome.OK) return h('div', { class: 'mini' }, h('p', { class: 'mini-title' }, title), outcomeState(out, { compact: true }));
  const p = out.cell.point;
  const v = p.value === null ? 'Not available' : p.unit === 'AUD_minor' ? audMinor(p.value) : p.unit === 'hours' ? `${Number(p.value).toFixed(2)} h` : count(p.value);
  return h('div', { class: 'mini', dataset: { measure } },
    h('p', { class: 'mini-title' }, title),
    h('p', { class: 'mini-fig tnum' }, v),
    h('p', { class: 'muted small' }, `${p.state}${p.reason ? ` — ${p.reason}` : ''} · ${out.items.length} contributing records`),
    out.items.length ? h('a', { href: popHref(app, `sq~${measure}~${issueId}~${start}~${end}`), 'data-nav': '', class: 'card-link' }, 'Inspect records →') : null);
}

export { caseHref, short };
