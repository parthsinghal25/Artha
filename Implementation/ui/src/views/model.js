// Shared view model: loads the bound publication's portfolio sections and
// composes period-specific issue reach exactly as bindings §4 defines it.
// All inputs are fully paged companion sections of one publication.

import { Outcome } from '../api/client.js';
import { arrivalsIndex, rankIssues, reach, reconcile, ids, compare } from '../lib/calc.js';
import { bucketList, trendWindow, overlaps, addDays } from '../lib/periods.js';
import { h } from '../ui/dom.js';
import { outcomeState } from '../ui/states.js';
import { pct, signed } from '../ui/viz.js';

/** First non-ok outcome among results, or null. */
export function firstFailure(...outs) {
  return outs.find((o) => !o || o.kind !== Outcome.OK) || null;
}

export function failed(out, opts) {
  return outcomeState(out, opts);
}

/**
 * Portfolio for the selected grain: overview, issue registry, alerts and every
 * issue's series at the selected grain (needed to reconcile composed reach and
 * to read published definition-quality notes).
 */
export async function loadPortfolio(app, ctx, { series = true } = {}) {
  const [ov, iss, al] = await Promise.all([app.sec('overview', {}, ctx.signal), app.sec('issues', {}, ctx.signal), app.sec('alerts', {}, ctx.signal)]);
  const bad = firstFailure(ov, iss, al);
  if (bad) return { kind: 'failed', out: bad };
  const arr = arrivalsIndex(ctx.arrivals.items);
  const issues = iss.items;
  let seriesMap = new Map();
  if (series) {
    const outs = await Promise.all(issues.map((i) => app.sec('issue', { issue_id: i.issue_id, grain: ctx.scope.grain }, ctx.signal)));
    const b = firstFailure(...outs);
    if (b) return { kind: 'failed', out: b };
    seriesMap = new Map(issues.map((i, k) => [i.issue_id, outs[k]]));
  }
  return {
    kind: Outcome.OK,
    ov: ov.items[0].overview,
    ovItem: ov.items[0],
    issues,
    byId: new Map(issues.map((i) => [i.issue_id, i])),
    alerts: al.items,
    alertById: new Map(al.items.map((a) => [a.issue_id, a])),
    arr,
    series: seriesMap,
  };
}

/** Series of other grains (trend window) for given issues. */
export async function loadTrend(app, ctx, issueIds) {
  const g = ctx.scope.trendGrain;
  const tb = g === ctx.scope.grain ? { kind: Outcome.OK, buckets: ctx.buckets, arrivals: ctx.arrivals } : await app.buckets(g, ctx.signal);
  if (tb.kind !== Outcome.OK) return { kind: 'failed', out: tb };
  const outs = await Promise.all(issueIds.map((id) => app.sec('issue', { issue_id: id, grain: g }, ctx.signal)));
  const b = firstFailure(...outs);
  if (b) return { kind: 'failed', out: b };
  const cols = trendWindow(tb.buckets, g, ctx.selected).map((c) => ({ ...c, highlight: overlaps(c, ctx.selected) }));
  return { kind: Outcome.OK, grain: g, cols, arr: arrivalsIndex(tb.arrivals.items), series: new Map(issueIds.map((id, k) => [id, outs[k]])) };
}

export const label = (iss) => iss?.definition?.label || iss?.label || iss?.issue_id;

/** Published definition-quality badges (never a validated-cause claim). */
export function issueBadges(iss, seriesOut) {
  const d = seriesOut?.detail?.issue;
  const out = [h('span', { class: 'badge badge-prov', title: 'Model-proposed definition; not independently validated' }, 'Provisional')];
  if (d?.discrimination?.state === 'non_discriminating') out.push(h('span', { class: 'badge badge-warn', title: d.discrimination.note || '' }, 'Broad'));
  if (d?.overlap_note) out.push(h('span', { class: 'badge', title: d.overlap_note }, 'Overlaps'));
  return out;
}

/**
 * Ranking rows for a bucket, each reconciled against the issue series'
 * published affected_new_cases. `matched` (from matchedPrevious) replaces the
 * previous bucket with a matched elapsed portion, or withholds the comparison.
 */
export function rankedFor(model, bucketKey, prevKey, matched = null) {
  const bucket = model.arr.get(bucketKey);
  const prev = matched?.unavailable ? null : matched || (prevKey ? model.arr.get(prevKey) : null);
  const rows = rankIssues(model.issues, bucket);
  for (const r of rows) {
    const s = model.series.get(r.id);
    r.reconcile = s ? reconcile(r, s.items.find((x) => x.bucket === bucketKey)) : { state: 'not_loaded' };
    r.prev = prev ? reach(ids(r.issue.membership?.included), prev) : null;
    r.cmp = compare(r, r.prev);
  }
  return rows;
}

export function shareText(r) {
  if (r.state === 'no_arrivals') return 'share n/a (no received cases)';
  if (r.state === 'denominator_unavailable' || r.share === null) return 'share unavailable';
  return `${pct(r.share)} of ${r.d}`;
}

export function deltaText(cmp, prevLabel) {
  if (!cmp || cmp.state !== 'value') return `No comparable ${prevLabel ? prevLabel : 'previous period'}`;
  const pts = cmp.sharePts === null ? 'share change n/a' : `${signed(cmp.sharePts, ' pts')}`;
  return `${signed(cmp.dCount)} vs ${prevLabel} (${cmp.prevN} → ${cmp.prevN + cmp.dCount}) · ${pts}`;
}

/** Previous comparable bucket (same grain, same publication), or null at the first bucket / for the observation year. */
export function previousBucket(ctx) {
  if (ctx.scope.grain === 'observation_year') return null;
  const i = ctx.buckets.findIndex((b) => b.key === ctx.selected.key);
  return i > 0 ? ctx.buckets[i - 1] : null;
}

/** Whether the selected/previous comparison spans unequal partial weeks. */
export function partialComparison(ctx, prev) {
  return ctx.scope.grain === 'week' && prev && (ctx.selected.partial || prev.partial) && ctx.selected.days !== prev.days;
}

/**
 * Matched elapsed comparison for unequal partial weeks (design §4.3): when the
 * selected week is clipped at the observation end, compare it with the same
 * weekdays of the previous week, composed from the published day buckets.
 * When only the previous week is partial, the comparison is withheld.
 * Returns null when the ordinary previous bucket is comparable.
 */
export async function matchedPrevious(app, ctx, prev) {
  if (!partialComparison(ctx, prev)) return null;
  if (!ctx.selected.partial) return { unavailable: true, reason: `${prev.label} is a partial week (${prev.days} days)` };
  const days = await app.sec('arrivals', { grain: 'day' }, ctx.signal);
  if (days.kind !== Outcome.OK) return { unavailable: true, reason: 'day buckets unavailable' };
  const idx = arrivalsIndex(days.items);
  const R = new Set(); const assessed = new Set(); const unassessed = new Set(); const memberIds = new Set();
  let complete = true;
  for (let i = 0; i < ctx.selected.days; i += 1) {
    const d = idx.get(addDays(prev.start, i));
    if (!d) { complete = false; continue; }
    for (const x of d.R) R.add(x);
    for (const x of d.assessed) assessed.add(x);
    for (const x of d.unassessed) unassessed.add(x);
    for (const x of d.memberIds) memberIds.add(x);
    complete = complete && d.complete;
  }
  return { key: `${prev.key}:first${ctx.selected.days}d`, R, assessed, unassessed, memberIds, members: memberIds.size, complete, days: ctx.selected.days, label: `first ${ctx.selected.days} days of ${prev.label}` };
}

/**
 * The one comparable previous period used by Overview, Issues, previous leaders
 * and the issue profile: the ordinary previous bucket, or its matched elapsed
 * portion when the selected week is clipped, or withheld (bucket null).
 */
export async function comparablePeriod(app, ctx, model) {
  const prev = previousBucket(ctx);
  const matched = await matchedPrevious(app, ctx, prev);
  if (!prev) return { prev: null, matched, bucket: null, label: null };
  if (matched?.unavailable) return { prev, matched, bucket: null, label: null, reason: matched.reason };
  return { prev, matched, bucket: matched || model.arr.get(prev.key) || null, label: matched ? matched.label : prev.label };
}

/** Ranking of the comparable previous period (same matched bounds as the displayed comparison). */
export function previousRanking(model, cmp) {
  return cmp.bucket ? rankIssues(model.issues, cmp.bucket) : [];
}

/** Build the URL of the current page with a population descriptor opened. */
export function popHref(app, desc, extra = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set('pop', desc);
  for (const [k, v] of Object.entries(extra)) { if (v === null) url.searchParams.delete(k); else url.searchParams.set(k, v); }
  return url.pathname + url.search;
}

export function openPop(app, desc) {
  app.router.go(popHref(app, desc));
}

export { bucketList };
