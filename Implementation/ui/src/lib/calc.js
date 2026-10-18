// Deterministic compositions of fully paged published sets (bindings §4).
// The browser only intersects, unions, ranks and formats IDs the companion
// returned. It never infers membership, prorates money or fills a gap.

/** Set of contributor IDs of a published counted value ({value, unit, contributors}). */
export function ids(v) {
  return new Set(Array.isArray(v?.contributors) ? v.contributors : []);
}

export function intersect(a, b) {
  const out = new Set();
  for (const x of a) if (b.has(x)) out.add(x);
  return out;
}

export function union(...sets) {
  const out = new Set();
  for (const s of sets) for (const x of s) out.add(x);
  return out;
}

/**
 * Index the published arrivals of one grain by bucket key. `R` is every
 * permitted received case in the bucket, including disclosed unassessed cases.
 * A bucket whose published count disagrees with its contributor list is marked
 * so its share is withheld rather than computed from a partial list.
 */
export function arrivalsIndex(items) {
  const m = new Map();
  for (const it of items) {
    const R = ids(it.cases);
    const unassessed = new Set(it.unassessed_cases || []);
    m.set(it.bucket, {
      key: it.bucket,
      start: it.start,
      end: it.end_exclusive,
      R,
      count: it.cases?.value ?? null,
      members: it.members?.value ?? null,
      memberIds: ids(it.members),
      assessed: ids(it.assessed_cases),
      assessedCount: it.assessed_cases?.value ?? null,
      unassessed,
      complete: Number.isInteger(it.cases?.value) && it.cases.value === R.size,
    });
  }
  return m;
}

/**
 * Observed supported reach of one issue in one received bucket:
 * n = |included ∩ received|, d = |received|, share = n/d.
 * d = 0 → share not applicable; an incomplete denominator → share unavailable.
 */
export function reach(included, bucket) {
  if (!bucket) return { n: null, d: null, share: null, state: 'unavailable', ids: [] };
  const hit = intersect(included, bucket.R);
  const d = bucket.R.size;
  if (!bucket.complete) return { n: hit.size, d, share: null, state: 'denominator_unavailable', ids: [...hit].sort() };
  if (d === 0) return { n: 0, d: 0, share: null, state: 'no_arrivals', ids: [] };
  return { n: hit.size, d, share: hit.size / d, state: 'value', ids: [...hit].sort() };
}

/**
 * Reconcile a composed reach with the issue series' published
 * `affected_new_cases` for the same bucket. Mismatch is a binding failure.
 */
export function reconcile(r, seriesItem) {
  if (!seriesItem) return { state: 'not_loaded' };
  const pub = ids(seriesItem.affected_new_cases);
  const same = pub.size === r.ids.length && r.ids.every((x) => pub.has(x));
  return { state: same ? 'match' : 'mismatch', published: [...pub].sort() };
}

/**
 * Rank every returned issue on its selected-bucket reach count, descending,
 * ties by issue ID ascending. Top N is applied by the caller for display only.
 */
export function rankIssues(issues, bucket) {
  const rows = issues.map((iss) => ({ issue: iss, id: iss.issue_id, ...reach(ids(iss.membership?.included), bucket) }));
  rows.sort((a, b) => (b.n ?? -1) - (a.n ?? -1) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  rows.forEach((r, i) => { r.rank = i + 1; });
  return rows;
}

/**
 * Comparable previous-period change on the same publication and definitions:
 * signed case difference and share change in percentage points. Relative
 * growth only when the previous count is positive.
 */
export function compare(cur, prev) {
  if (!prev || prev.n === null || cur.n === null) return { state: 'unavailable' };
  const dCount = cur.n - prev.n;
  const sharePts = cur.share !== null && prev.share !== null ? (cur.share - prev.share) * 100 : null;
  const pct = prev.n > 0 ? (dCount / prev.n) * 100 : null;
  return { state: 'value', dCount, sharePts, pct, prevN: prev.n, prevShare: prev.share };
}

export const AGE_BANDS = [
  { id: '0-7', label: '0–7 days', lo: 0, hi: 7 },
  { id: '8-30', label: '8–30 days', lo: 8, hi: 30 },
  { id: '31-60', label: '31–60 days', lo: 31, hi: 60 },
  { id: '61-90', label: '61–90 days', lo: 61, hi: 90 },
  { id: '91-180', label: '91–180 days', lo: 91, hi: 180 },
  { id: '181+', label: '181+ days', lo: 181, hi: Infinity },
];

/**
 * Band the open case set by its published per-case ages. Cases without a
 * published age fall in a separate unknown band; nothing is estimated.
 */
export function ageBands(openIds, ageByCase) {
  const bands = AGE_BANDS.map((b) => ({ ...b, ids: [] }));
  const unknown = [];
  for (const id of [...openIds].sort()) {
    const a = ageByCase?.[id];
    if (!Number.isFinite(a)) { unknown.push(id); continue; }
    bands.find((b) => a >= b.lo && a <= b.hi).ids.push(id);
  }
  return { bands, unknown };
}

export function median(values) {
  const v = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

/** Sum of integer minor units / seconds over unique records (signed reversals preserved). */
export function sumUnique(records, field) {
  const seen = new Set();
  let total = 0;
  for (const r of records) {
    if (seen.has(r.id)) continue;
    seen.add(r.id);
    if (!Number.isInteger(r[field])) return null;
    total += r[field];
  }
  return total;
}

/** Stable display colour slot (0..2) for up to three selected issues, by selection order. */
export function seriesSlot(selected, id) {
  const i = selected.indexOf(id);
  return i >= 0 ? i : null;
}
