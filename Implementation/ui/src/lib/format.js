// Presentation-only formatting. Values arrive authoritative from the backend;
// nothing here aggregates, rounds a published total for calculation, or infers
// a missing value. Integer cents/seconds are converted with BigInt so display
// never drifts through floating point.

export const TZ = 'Australia/Melbourne';

const dateFmt = new Intl.DateTimeFormat('en-AU', { timeZone: TZ, day: 'numeric', month: 'short', year: 'numeric' });
const dateTimeFmt = new Intl.DateTimeFormat('en-AU', {
  timeZone: TZ, day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
});
const monthFmt = new Intl.DateTimeFormat('en-AU', { timeZone: TZ, month: 'short', year: 'numeric' });
const intFmt = new Intl.NumberFormat('en-AU', { maximumFractionDigits: 0 });

/** Integer AUD minor units → "A$1,234.56" exactly. Negative values keep their sign. */
export function audMinor(minor) {
  if (!Number.isInteger(minor)) return null;
  let v = BigInt(minor);
  const neg = v < 0n;
  if (neg) v = -v;
  const dollars = v / 100n;
  const cents = (v % 100n).toString().padStart(2, '0');
  const grouped = dollars.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${neg ? '−' : ''}A$${grouped}.${cents}`;
}

/** Integer seconds → hours, half-up to 2dp (Data demo-config display_hours rule). */
export function secondsToHours(seconds) {
  if (!Number.isInteger(seconds) || seconds < 0) return null;
  const hundredths = (BigInt(seconds) * 100n * 2n + 3600n) / 7200n; // half-up
  const whole = hundredths / 100n;
  const frac = (hundredths % 100n).toString().padStart(2, '0');
  return `${whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${frac} h`;
}

export function count(n) {
  return Number.isFinite(n) ? intFmt.format(n) : '—';
}

/**
 * Display a metric point value with its declared unit. Unknown units are shown
 * verbatim with the raw number; we never guess a conversion.
 */
export function pointValue(point) {
  if (!point || point.value === null || point.value === undefined) return null;
  const { value, unit } = point;
  switch (unit) {
    case 'AUD_minor': return audMinor(value);
    case 'seconds': return secondsToHours(value);
    case 'hours': return `${raw(value)} h`;
    case 'days': return `${raw(value)} days`;
    case 'member_days': return `${raw(value)} member-days`;
    default: return Number.isInteger(value) ? count(value) : raw(value);
  }
}

function raw(v) {
  // Faithful: no rounding beyond what the service published.
  return Number.isInteger(v) ? count(v) : String(v);
}

const UNIT_LABEL = {
  cases: 'cases', members: 'members', contacts: 'contacts', reopenings: 'reopenings', hours: 'hours',
  AUD_minor: 'AUD', days: 'days', seconds: 'hours', matters: 'AFCA matters', episodes: 'episodes',
  member_days: 'member-days', commitments: 'commitments', closure_transitions: 'closure transitions',
};
export function unitLabel(unit) {
  return UNIT_LABEL[unit] || unit;
}

export function instant(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${dateTimeFmt.format(d)} Melbourne`;
}

export function dateOnly(iso) {
  if (!iso) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number);
    // A date-only value is a calendar date; format without converting through a timezone.
    return new Intl.DateTimeFormat('en-AU', { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric' })
      .format(new Date(Date.UTC(y, m - 1, d)));
  }
  return dateFmt.format(new Date(iso));
}

const melbClock = new Intl.DateTimeFormat('en-GB', { timeZone: 'Australia/Melbourne', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
/** True when an instant is exactly a Melbourne local midnight (a period's exclusive end). */
export function isLocalMidnight(iso) {
  const d = new Date(iso);
  return !Number.isNaN(d.getTime()) && melbClock.format(d) === '00:00:00';
}

/** An inclusive evidence cutoff in words: a local-midnight cutoff reads as the end of the previous day. */
export function cutoffWords(iso) {
  if (!iso || !isLocalMidnight(iso)) return instant(iso);
  return `end of ${dateFmt.format(new Date(Date.parse(iso) - 1))} (Melbourne)`;
}

/** Date-precision form of cutoffWords. */
export function cutoffDate(iso) {
  if (!iso || /^\d{4}-\d{2}-\d{2}$/.test(iso) || !isLocalMidnight(iso)) return dateOnly(iso);
  return `end of ${dateFmt.format(new Date(Date.parse(iso) - 1))}`;
}

export function monthLabel(iso) {
  return iso ? monthFmt.format(new Date(iso)) : '—';
}

/** Interval label for a half-open bucket, shown as inclusive local dates. */
export function intervalLabel(startIso, endExclusiveIso) {
  if (!startIso || !endExclusiveIso) return '—';
  const lastInstant = new Date(new Date(endExclusiveIso).getTime() - 1);
  return `${dateFmt.format(new Date(startIso))} – ${dateFmt.format(lastInstant)}`;
}

/** Shared `time_value` → readable text that never invents precision. */
export function timeValue(tv) {
  if (!tv) return 'Unknown time';
  switch (tv.precision) {
    case 'instant': return instant(tv.instant);
    case 'day': return `${dateOnly(tv.date)} (date only)`;
    case 'interval': return `Between ${instant(tv.earliest)} and ${instant(tv.latest)}`;
    case 'unknown': return `Unknown time — ${tv.reason}`;
    default: return 'Unknown time';
  }
}

/** Sort key for time_value: unknowns last, stable by ID at the caller. */
export function timeSortKey(tv) {
  if (!tv) return Number.POSITIVE_INFINITY;
  if (tv.precision === 'instant') return Date.parse(tv.instant);
  if (tv.precision === 'day') return Date.parse(`${tv.date}T00:00:00Z`);
  if (tv.precision === 'interval') return Date.parse(tv.earliest);
  return Number.POSITIVE_INFINITY;
}

/** Slice text by Unicode code points, start inclusive / end exclusive (v0.2 anchors). */
export function codepointSlice(text, start, end) {
  const cps = Array.from(text);
  if (!(Number.isInteger(start) && Number.isInteger(end)) || start < 0 || end <= start || end > cps.length) return null;
  return { before: cps.slice(0, start).join(''), match: cps.slice(start, end).join(''), after: cps.slice(end).join('') };
}

/** Resolve an RFC 6901 JSON pointer; returns undefined when unresolved (never guesses). */
export function jsonPointer(obj, pointer) {
  if (pointer === '') return obj;
  if (!pointer.startsWith('/')) return undefined;
  let node = obj;
  for (const raw of pointer.slice(1).split('/')) {
    const key = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (node === null || typeof node !== 'object' || !(key in node)) return undefined;
    node = node[key];
  }
  return node;
}

export function shortHash(h) {
  return h ? `${h.slice(0, 10)}…` : '—';
}

export function humanise(s) {
  return String(s ?? '').replace(/_/g, ' ');
}

export function bytes(n) {
  if (!Number.isInteger(n)) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const ENTITY_LABEL = {
  case_id: 'cases', member_reference: 'members', contact_id: 'contacts', episode_id: 'episodes', work_id: 'work entries',
  financial_event_id: 'payment records', obligation_id: 'obligations', afca_matter_id: 'AFCA matters', commitment_id: 'commitments',
  submission_id: 'submission occasions', request_event_id: 'request occasions', response_relationship_id: 'response relationships',
  closure_transition_id: 'closure transitions', relationship_id: 'relationships', candidate_relationship_id: 'candidate relationships',
};
/** Plain-language counting unit for an entity_unit enum. */
export function entityLabel(unit) {
  return ENTITY_LABEL[unit] || humanise(unit);
}
