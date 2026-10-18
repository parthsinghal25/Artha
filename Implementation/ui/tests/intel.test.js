// Calculation and time-model tests for the analytical compositions (bindings §4).
import { test, ok, eq } from './harness.js';
import { arrivalsIndex, reach, rankIssues, compare, ageBands, reconcile, ids, union } from '../src/lib/calc.js';
import { bucketList, trendWindow, melbourneMidnightUtc, knownThrough, defaultTrendGrain, addDays } from '../src/lib/periods.js';

const counted = (list) => ({ contributors: list, unit: 'cases', value: list.length });
const arr = (bucket, start, end, list, unassessed = []) => ({ bucket, start, end_exclusive: end, cases: counted(list), members: counted(list), assessed_cases: counted(list.filter((x) => !unassessed.includes(x))), unassessed_cases: unassessed });

test('reach: numerator = included ∩ received; denominator = all received incl. unassessed; not the assessed-only ratio', () => {
  const b = arrivalsIndex([arr('2026-09', '2026-09-01', '2026-10-01', ['a', 'b', 'c', 'h'], ['h'])]).get('2026-09');
  const r = reach(new Set(['a', 'b', 'x']), b);
  eq([r.n, r.d, r.share], [2, 4, 0.5]);
  eq(r.ids, ['a', 'b']);
  ok(r.share !== 2 / 3, 'assessed-only share (2/3) is not substituted');
});

test('reach: no received cases → share not applicable; incomplete contributor list → share unavailable', () => {
  const idx = arrivalsIndex([arr('2026-08', '2026-08-01', '2026-09-01', []),
    { ...arr('2026-07', '2026-07-01', '2026-08-01', ['a']), cases: { contributors: ['a'], unit: 'cases', value: 3 } }]);
  const z = reach(new Set(['a']), idx.get('2026-08'));
  eq([z.n, z.d, z.share, z.state], [0, 0, null, 'no_arrivals']);
  const u = reach(new Set(['a']), idx.get('2026-07'));
  eq([u.share, u.state], [null, 'denominator_unavailable']);
  eq(reach(new Set(['a']), undefined).state, 'unavailable');
});

test('ranking: counts descending, ties by issue ID; Top N slices display only and never changes the denominator', () => {
  const b = arrivalsIndex([arr('m', '2026-09-01', '2026-10-01', ['a', 'b', 'c', 'd', 'e'])]).get('m');
  const iss = (id, inc) => ({ issue_id: id, membership: { included: counted(inc) } });
  const rows = rankIssues([iss('ISS-C', ['a', 'b']), iss('ISS-A', ['a', 'b']), iss('ISS-B', ['a', 'b', 'c']), iss('ISS-D', [])], b);
  eq(rows.map((r) => r.id), ['ISS-B', 'ISS-A', 'ISS-C', 'ISS-D']);
  eq(rows.map((r) => r.n), [3, 2, 2, 0]);
  const top2 = rows.slice(0, 2);
  ok(top2.every((r) => r.d === 5) && rows.every((r) => r.d === 5), 'denominator identical for all issues and independent of Top N');
  // a different period re-ranks the same identities
  const b2 = arrivalsIndex([arr('n', '2026-08-01', '2026-09-01', ['z'])]).get('n');
  const rows2 = rankIssues([iss('ISS-C', ['z']), iss('ISS-A', ['a']), iss('ISS-B', [])], b2);
  eq(rows2[0].id, 'ISS-C', 'an issue moves into first place in another period');
});

test('overlap: a case in two issues counts once in each issue and once in the portfolio union', () => {
  const A = new Set(['a', 'b']); const B = new Set(['b', 'c']);
  const b = arrivalsIndex([arr('m', '2026-09-01', '2026-10-01', ['a', 'b', 'c'])]).get('m');
  eq([reach(A, b).n, reach(B, b).n, union(A, B).size], [2, 2, 3]);
  ok(reach(A, b).share + reach(B, b).share > 1, 'overlapping shares may exceed 100%');
});

test('comparison: signed case change and percentage points; no relative growth from zero; missing prior unavailable', () => {
  const c = compare({ n: 3, share: 0.6 }, { n: 0, share: 0 });
  eq([c.dCount, Math.round(c.sharePts), c.pct], [3, 60, null]);
  const d = compare({ n: 2, share: 0.4 }, { n: 4, share: 0.8 });
  eq([d.dCount, Math.round(d.sharePts), d.pct], [-2, -40, -50]);
  eq(compare({ n: 1, share: null }, { n: 1, share: null }).sharePts, null, 'n/a shares give no point change');
  eq(compare({ n: 1, share: 1 }, null).state, 'unavailable');
});

test('age bands: ordered bands from published per-case ages; missing ages in their own band', () => {
  const { bands, unknown } = ageBands(new Set(['a', 'b', 'c', 'd', 'e']), { a: 0, b: 7, c: 8, d: 400 });
  eq(bands.map((b) => b.ids.length), [2, 1, 0, 0, 0, 1]);
  eq(unknown, ['e']);
});

test('reconciliation: composed reach must match the published series IDs exactly', () => {
  const r = { ids: ['a', 'b'] };
  eq(reconcile(r, { affected_new_cases: counted(['b', 'a']) }).state, 'match');
  eq(reconcile(r, { affected_new_cases: counted(['a']) }).state, 'mismatch');
  eq(reconcile(r, undefined).state, 'not_loaded');
  eq([...ids(undefined)], []);
});

test('Melbourne local midnight → UTC across AEST/AEDT and both DST transitions', () => {
  eq(melbourneMidnightUtc('2026-09-01'), '2026-08-31T14:00:00Z'); // AEST
  eq(melbourneMidnightUtc('2026-01-01'), '2025-12-31T13:00:00Z'); // AEDT
  eq(melbourneMidnightUtc('2026-04-05'), '2026-04-04T13:00:00Z'); // DST ends 03:00 that day; midnight still AEDT
  eq(melbourneMidnightUtc('2026-04-06'), '2026-04-05T14:00:00Z');
  eq(melbourneMidnightUtc('2025-10-05'), '2025-10-04T14:00:00Z'); // DST starts 02:00 that day; midnight still AEST
  eq(melbourneMidnightUtc('2025-10-06'), '2025-10-05T13:00:00Z');
  eq(knownThrough('2026-10-01T00:00:00+10:00'), '2026-09-30');
  eq(addDays('2026-02-28', 1), '2026-03-01');
});

test('served buckets: clipped boundary weeks are partial; trend windows end at the selected period', () => {
  const weeks = bucketList([
    { bucket: '2025-09-29/W', start: '2025-10-01', end_exclusive: '2025-10-06' },
    { bucket: '2025-10-06/W', start: '2025-10-06', end_exclusive: '2025-10-13' },
    { bucket: '2026-09-28/W', start: '2026-09-28', end_exclusive: '2026-10-01' }], 'week');
  eq(weeks.map((w) => [w.days, w.partial]), [[5, true], [7, false], [3, true]]);
  const months = bucketList(Array.from({ length: 12 }, (_, i) => {
    const s = new Date(Date.UTC(2025, 9 + i, 1)).toISOString().slice(0, 10); const e = new Date(Date.UTC(2025, 10 + i, 1)).toISOString().slice(0, 10);
    return { bucket: s.slice(0, 7), start: s, end_exclusive: e };
  }), 'month');
  eq(months[0].label, 'Oct 2025');
  const win = trendWindow(months, 'month', months[5]);
  eq([win.length, win[win.length - 1].key], [6, '2026-03'], 'window ends at the selected bucket; no later buckets shown');
  eq(trendWindow(months, 'month', months[11]).length, 12);
  eq(defaultTrendGrain('observation_year'), 'month');
  eq(bucketList([{ bucket: '2026-Q3', start: '2026-07-01', end_exclusive: '2026-10-01' }], 'quarter')[0].label, 'Q3 2026 (Jul–Sep)');
});
