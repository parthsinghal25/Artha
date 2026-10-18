// Reporting periods and trend windows over the buckets the companion actually
// serves. Bucket keys, starts and exclusive ends come from the published
// `arrivals` section of the selected grain; nothing here invents a period the
// service did not return. Melbourne calendar arithmetic is used only to label
// bounds and to translate a served local date into the instant a Shared filter
// needs.

export const TZ = 'Australia/Melbourne';

export const GRAINS = [
  { id: 'day', label: 'Day', trend: 30, unitWord: 'day' },
  { id: 'week', label: 'Week', trend: 12, unitWord: 'week' },
  { id: 'month', label: 'Month', trend: 12, unitWord: 'month' },
  { id: 'quarter', label: 'Quarter', trend: 4, unitWord: 'quarter' },
  { id: 'observation_year', label: 'Observation year', trend: 1, unitWord: 'observation year' },
];
export const GRAIN_IDS = GRAINS.map((g) => g.id);
export const grainInfo = (id) => GRAINS.find((g) => g.id === id) || GRAINS[2];

/** Default trend grain for a selected-period grain (design §4.1). */
export function defaultTrendGrain(grain) {
  return grain === 'observation_year' ? 'month' : grain;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-09-30" → {y, m, d}. */
export function parseDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
  return m ? { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) } : null;
}

export function fmtDate(s, { year = true } = {}) {
  const p = parseDate(s);
  if (!p) return s || '—';
  return `${p.d} ${MONTHS[p.m - 1]}${year ? ` ${p.y}` : ''}`;
}

/** Shift a calendar date by whole days (UTC arithmetic on the date only; no time zone). */
export function addDays(s, n) {
  const p = parseDate(s);
  const t = new Date(Date.UTC(p.y, p.m - 1, p.d + n));
  return t.toISOString().slice(0, 10);
}

export function daysBetween(a, b) {
  const pa = parseDate(a); const pb = parseDate(b);
  return Math.round((Date.UTC(pb.y, pb.m - 1, pb.d) - Date.UTC(pa.y, pa.m - 1, pa.d)) / 86400000);
}

/** Last calendar day included in [start, endExclusive). */
export function lastDay(endExclusive) {
  return addDays(endExclusive, -1);
}

function offsetMinutes(utcMs) {
  const parts = new Intl.DateTimeFormat('en-AU', { timeZone: TZ, timeZoneName: 'shortOffset' }).formatToParts(new Date(utcMs));
  const name = parts.find((p) => p.type === 'timeZoneName')?.value || 'GMT+10';
  const m = /GMT([+-])(\d{1,2})(?::(\d{2}))?/.exec(name);
  if (!m) return 600;
  return (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0));
}

/** Melbourne local midnight of a calendar date as a UTC ISO instant (handles AEST/AEDT). */
export function melbourneMidnightUtc(date) {
  const p = parseDate(date);
  const base = Date.UTC(p.y, p.m - 1, p.d);
  let t = base - offsetMinutes(base - 600 * 60000) * 60000;
  t = base - offsetMinutes(t) * 60000;
  return new Date(t).toISOString().replace('.000Z', 'Z');
}

/** Calendar date in Melbourne of an instant. */
export function melbourneDate(iso) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso));
}

/** Last day fully known for an exclusive knowledge endpoint (e.g. 2026-10-01T00:00+10:00 → 2026-09-30). */
export function knownThrough(knowledgeEndExclusive) {
  return melbourneDate(new Date(Date.parse(knowledgeEndExclusive) - 1).toISOString());
}

/**
 * Normalise served buckets: [{key, start, end, days, partial, label, short}].
 * A week is partial when the service clipped it at an observation boundary
 * (fewer than seven days); other grains are complete calendar units.
 */
export function bucketList(arrivalItems, grain) {
  return arrivalItems.map((it) => {
    const days = daysBetween(it.start, it.end_exclusive);
    const partial = grain === 'week' ? days < 7 : false;
    return { key: it.bucket, start: it.start, end: it.end_exclusive, days, partial, ...bucketLabels(it.bucket, it.start, it.end_exclusive, grain) };
  });
}

export function bucketLabels(key, start, end, grain) {
  const last = lastDay(end);
  switch (grain) {
    case 'day': return { label: fmtDate(start), short: fmtDate(start, { year: false }), year: parseDate(start).y };
    case 'week': return { label: `Week ${fmtDate(start)} – ${fmtDate(last)}`, short: fmtDate(start, { year: false }), year: parseDate(start).y };
    case 'month': {
      const p = parseDate(start);
      return { label: `${MONTHS[p.m - 1]} ${p.y}`, short: MONTHS[p.m - 1], year: p.y };
    }
    case 'quarter': {
      const a = parseDate(start); const b = parseDate(last);
      const q = /Q(\d)/.exec(key)?.[1];
      return { label: `Q${q} ${a.y} (${MONTHS[a.m - 1]}–${MONTHS[b.m - 1]})`, short: `Q${q}`, year: a.y };
    }
    case 'observation_year': return { label: `Observation year ${fmtDate(start)} – ${fmtDate(last)}`, short: 'Obs. year' };
    default: return { label: key, short: key };
  }
}

/** Index of the selected bucket; defaults to the latest served bucket (the publication's period, never the laptop date). */
export function selectedIndex(buckets, key) {
  const i = buckets.findIndex((b) => b.key === key);
  return i >= 0 ? i : buckets.length - 1;
}

/**
 * Trend window: the last N buckets of the trend grain ending at the bucket
 * that contains the selected period's last day.
 */
export function trendWindow(trendBuckets, trendGrain, selected) {
  const n = grainInfo(trendGrain).trend;
  const endDay = selected ? lastDay(selected.end) : null;
  let endIdx = trendBuckets.length - 1;
  if (endDay) {
    const i = trendBuckets.findIndex((b) => b.start <= endDay && endDay < b.end);
    if (i >= 0) endIdx = i;
  }
  return trendBuckets.slice(Math.max(0, endIdx - n + 1), endIdx + 1);
}

/** Whether a trend bucket overlaps the selected period (highlighted, not relabelled). */
export function overlaps(b, selected) {
  return selected && b.start < selected.end && selected.start < b.end;
}
