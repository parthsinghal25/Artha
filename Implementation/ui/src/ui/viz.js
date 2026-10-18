// Chart primitives for the executive presentation. Every selectable mark is a
// real <button> with a complete accessible label (value, unit, denominator,
// period), so keyboard and screen-reader users reach the same contributors
// without an analytical table. Geometry only: values arrive computed.

import { h } from './dom.js';
import { info } from './labels.js';

const SERIES = ['s0', 's1', 's2'];
/** "1 record" / "3 records" (regular English plural only). */
export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
export const pct = (v, digits = 0) => (v === null || v === undefined ? null : `${(v * 100).toFixed(digits)}%`);
export const signed = (v, unit = '', digits = 0) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v).toFixed(digits)}${unit}`);

/**
 * Horizontal ranking bars. rows: [{id, label, value, display, note, prev, badges, selected, ariaLabel}]
 * Bars start at zero; `prev` draws a comparison tick.
 */
export function rankBars({ rows, onSelect, onLabel, max, unitLabel = 'cases', caption = null, labelHref = null }) {
  // Signed geometry: a negative value (e.g. a net reversal) extends left of the zero line, never clamped to zero.
  const hiV = Math.max(0, max ?? Math.max(0, ...rows.map((r) => Math.max(r.value || 0, r.prev || 0))));
  const loV = Math.min(0, ...rows.map((r) => Math.min(r.value || 0, r.prev || 0)));
  const span = loV < 0 ? hiV - loV || 1 : Math.max(1, hiV);
  const zero = loV < 0 ? (-loV / span) * 100 : 0;
  const pos = (v) => zero + (v / span) * 100;
  return h('div', { class: `rank${loV < 0 ? ' is-signed' : ''}` },
    rows.map((r) => h('div', { class: `rank-row${r.selected ? ' is-selected' : ''}`, dataset: { issue: r.id } },
      h('div', { class: 'rank-label' },
        labelHref ? h('a', { href: labelHref(r), 'data-nav': '', class: 'rank-name', dataset: { focusKey: `lbl:${r.id}` } }, r.label)
          : h('span', { class: 'rank-name' }, r.label),
        r.badges?.length ? h('span', { class: 'rank-badges' }, r.badges) : null),
      h('button', {
        type: 'button', class: 'rank-track', 'aria-pressed': r.selected ? 'true' : 'false', 'aria-label': r.ariaLabel,
        dataset: { focusKey: `bar:${r.id}` }, onclick: (e) => onSelect && onSelect(r, e.currentTarget),
      },
      loV < 0 ? h('span', { class: 'rank-zero', style: { left: `${zero}%` } }) : null,
      h('span', { class: `rank-bar${r.value === 0 ? ' is-zero' : ''}${r.value < 0 ? ' is-neg' : ''}`, style: { left: `${Math.min(pos(r.value || 0), zero)}%`, width: `${Math.abs(((r.value || 0) / span) * 100)}%` } }),
      Number.isFinite(r.prev) ? h('span', { class: 'rank-prev', style: { left: `${pos(r.prev)}%` }, title: 'Previous period' }) : null),
      h('div', { class: 'rank-value tnum' }, h('strong', {}, r.display), r.note ? h('span', { class: 'rank-note' }, r.note) : null))),
    caption ? h('p', { class: 'chart-caption' }, caption) : null);
}

/** X-axis tick: short label, with the year on the first tick and wherever the year changes. */
export function tick(cols, i) {
  const c = cols[i];
  const showYear = c.year && (i === 0 || cols[i - 1].year !== c.year);
  return [c.short, showYear ? h('em', { class: 'tick-year' }, String(c.year)) : null];
}

/** Sequential indigo scale 0..1 → background colour (light → dark), readable text colour. */
export function seq(t) {
  const c0 = [238, 240, 253]; const c1 = [55, 48, 163];
  const k = Math.max(0, Math.min(1, t));
  const mix = c0.map((a, i) => Math.round(a + (c1[i] - a) * k));
  return { bg: `rgb(${mix.join(',')})`, fg: k > 0.5 ? '#fff' : '#172033' };
}

/**
 * Issue × time heatmap. rows: [{id,label,href}] (fixed order), cols: [{key, short, label, partial, highlight}],
 * cell(row, col) → {state:'value'|'na'|'unavailable', t (0..1), display, ariaLabel, selected}.
 */
export function heatmap({ rows, cols, cell, onSelect, caption }) {
  const grid = h('div', { class: 'heat', role: 'group', 'aria-label': caption || 'Issue by period heatmap', style: { '--cols': String(cols.length) } },
    h('div', { class: 'heat-corner' }),
    cols.map((c, i) => h('div', { class: `heat-col${c.highlight ? ' is-hl' : ''}`, title: c.label }, tick(cols, i), c.partial ? h('span', { class: 'partial-mark', title: 'Partial period' }, '◐') : null)),
    rows.map((r) => [
      h('div', { class: 'heat-row', title: r.label }, r.href ? h('a', { href: r.href, 'data-nav': '', class: 'heat-name' }, r.label) : h('span', { class: 'heat-name' }, r.label),
        r.count !== undefined && r.count !== null ? h('span', { class: 'heat-n tnum' }, String(r.count)) : null),
      cols.map((c) => {
        const v = cell(r, c);
        if (v.state !== 'value') {
          return h('button', { type: 'button', class: `heat-cell is-${v.state}${c.highlight ? ' is-hl' : ''}${v.selected ? ' is-selected' : ''}`, 'aria-label': v.ariaLabel, dataset: { focusKey: `cell:${r.id}:${c.key}` }, onclick: (e) => onSelect && onSelect(r, c, e.currentTarget) }, v.display);
        }
        const s = seq(v.t);
        return h('button', {
          type: 'button', class: `heat-cell${c.highlight ? ' is-hl' : ''}${v.selected ? ' is-selected' : ''}${v.zero ? ' is-zero' : ''}`, style: { background: s.bg, color: s.fg },
          'aria-pressed': v.selected ? 'true' : 'false', 'aria-label': v.ariaLabel, dataset: { focusKey: `cell:${r.id}:${c.key}` }, onclick: (e) => onSelect && onSelect(r, c, e.currentTarget),
        }, v.display);
      }),
    ]));
  return h('div', { class: 'heat-scroll', tabindex: '0', role: 'region', 'aria-label': `${caption || 'Heatmap'} (scrolls horizontally when narrow)` }, grid);
}

/**
 * Aligned count and share panels on one time axis for up to three issues.
 * series: [{id, label, slot, points: [{key, n, share, state}]}], cols: [{key, short, label, highlight}].
 */
export function trendPanels({ series, cols, onSelect, selectedKey, denominators }) {
  const maxN = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.n || 0)));
  const W = 100; const H = 100;
  const x = (i) => ((i + 0.5) / cols.length) * W;
  const shareMax = Math.max(0.0001, ...series.flatMap((s) => s.points.map((p) => (p.share ?? 0))));
  const sTop = Math.min(1, Math.max(0.25, Math.ceil(shareMax * 4) / 4));
  const countPanel = h('div', { class: 'tp-panel' },
    h('div', { class: 'tp-axis' }, h('span', {}, String(maxN)), h('span', {}, '0')),
    h('div', { class: 'tp-plot tp-count', style: { '--cols': String(cols.length) } },
      // Each bucket holds one button per series, so a selection always names its own issue and bucket.
      cols.map((c, ci) => h('div', { class: `tp-bucket${c.highlight ? ' is-hl' : ''}`, role: 'group', 'aria-label': c.label }, series.map((s) => {
        const p = s.points[ci];
        const na = p.n === null || p.n === undefined;
        const share = p.share !== null && p.share !== undefined ? `, ${pct(p.share)} of ${denominators?.[ci] ?? '?'} received` : '';
        const bar = na ? h('span', { class: `tp-col ${SERIES[s.slot]} is-na`, style: { height: '100%' } })
          : h('span', { class: `tp-col ${SERIES[s.slot]}${p.n === 0 ? ' is-zero' : ''}`, style: { height: `${(p.n / maxN) * 100}%` } });
        if (na || !onSelect) return h('span', { class: 'tp-hit is-static', role: 'img', 'aria-label': `${s.label} · ${c.label}: ${na ? 'unavailable' : `${p.n} cases${share}`}` }, bar);
        return h('button', {
          type: 'button', class: `tp-hit${selectedKey === `${s.id}:${c.key}` ? ' is-selected' : ''}`,
          'aria-label': `${s.label} · ${c.label}: ${plural(p.n, 'case')}${share}. Inspect this issue's cases.`,
          dataset: { focusKey: `tb:${s.id}:${c.key}`, issue: s.id, bucket: c.key }, onclick: (e) => onSelect(c, s, e.currentTarget),
        }, bar);
      }))))) ;
  const lines = series.map((s) => {
    const segs = []; let cur = [];
    s.points.forEach((p, i) => {
      if (p.share === null || p.share === undefined) { if (cur.length) segs.push(cur); cur = []; return; }
      cur.push([x(i), H - (p.share / sTop) * H]);
    });
    if (cur.length) segs.push(cur);
    return h('g', { class: `tp-line ${SERIES[s.slot]}` },
      segs.map((seg) => (seg.length === 1
        ? h('circle', { cx: seg[0][0], cy: seg[0][1], r: 1.6 })
        : [h('polyline', { points: seg.map((pt) => pt.join(',')).join(' '), fill: 'none' }), seg.map((pt) => h('circle', { cx: pt[0], cy: pt[1], r: 1.1 }))])));
  });
  const sharePanel = h('div', { class: 'tp-panel' },
    h('div', { class: 'tp-axis' }, h('span', {}, pct(sTop)), h('span', {}, '0%')),
    h('div', { class: 'tp-plot tp-share' },
      h('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
        h('line', { x1: 0, x2: W, y1: H, y2: H, class: 'tp-base' }), lines),
      h('div', { class: 'tp-na-row', style: { '--cols': String(cols.length) } }, cols.map((c, ci) => h('span', {}, series.every((s) => s.points[ci].state === 'no_arrivals') ? 'n/a' : '')))));
  return h('div', { class: 'tp' },
    h('div', { class: 'tp-legend' }, series.map((s) => h('span', { class: `tp-key ${SERIES[s.slot]}` }, h('i', { 'aria-hidden': 'true' }), s.label))),
    h('p', { class: 'tp-title' }, 'Supported cases (count)'), countPanel,
    h('p', { class: 'tp-title' }, 'Observed supported reach (share of received cases)'), sharePanel,
    h('div', { class: 'tp-x', style: { '--cols': String(cols.length) } }, cols.map((c, i) => h('span', { class: c.highlight ? 'is-hl' : '' }, tick(cols, i)))),
    denominators ? h('div', { class: 'tp-den', style: { '--cols': String(cols.length) }, title: 'Received cases in each bucket (denominator)' },
      denominators.map((d) => h('span', { class: d === 0 ? 'is-zero' : '' }, d === null ? '?' : String(d)))) : null);
}

/**
 * Simple columns (one unit), signed values extend below zero.
 * points: [{key, short, label, value, display, state, highlight}]
 */
export function columns({ points, onSelect, selectedKey, unitWord, height = 140, showValues = points.length <= 16 }) {
  const vals = points.map((p) => (p.state === 'value' ? p.value : 0));
  const maxV = Math.max(0, ...vals); const minV = Math.min(0, ...vals);
  const span = Math.max(1e-9, maxV - minV);
  const zero = (maxV / span) * 100;
  return h('div', { class: 'cols', style: { '--h': `${height}px`, '--cols': String(points.length) } },
    h('div', { class: 'cols-plot' },
      h('span', { class: 'cols-zero', style: { top: `${zero}%` } }),
      points.map((p) => {
        const v = p.state === 'value' ? p.value : null;
        const hgt = v === null ? 0 : (Math.abs(v) / span) * 100;
        const top = v === null ? 0 : v >= 0 ? zero - hgt : zero;
        const aria = `${p.label}: ${p.state === 'value' ? p.display : 'not available'}${unitWord ? ` ${unitWord}` : ''}`;
        const live = onSelect && p.state === 'value'; // no inert buttons: unavailable or non-drillable marks are static
        return h(live ? 'button' : 'span', live ? {
          type: 'button', class: `cols-b${p.highlight ? ' is-hl' : ''}${selectedKey === p.key ? ' is-selected' : ''}`,
          'aria-label': aria, 'aria-pressed': selectedKey === p.key ? 'true' : 'false', dataset: { focusKey: `col:${p.key}` },
          onclick: (e) => onSelect(p, e.currentTarget),
        } : { class: `cols-b is-static${p.highlight ? ' is-hl' : ''}`, role: 'img', 'aria-label': aria }, v === null ? h('span', { class: 'cols-na' }, '—')
          : [h('span', { class: `cols-bar${v < 0 ? ' is-neg' : ''}${v === 0 ? ' is-zero' : ''}`, style: { top: `${top}%`, height: `${Math.max(hgt, v === 0 ? 0 : 1.5)}%` } }),
            showValues === true || (showValues === 'highlight' && p.highlight) ? h('span', { class: 'cols-val', style: { top: `calc(${v >= 0 ? top : top + hgt}% - ${v >= 0 ? 15 : -2}px)` } }, p.display) : null]);
      })),
    h('div', { class: 'cols-x' }, points.map((p, i) => h('span', { class: p.highlight ? 'is-hl' : '' }, tick(points, i)))));
}

/** Horizontal labelled bars for ordered categories (age bands, effort kinds). rows: [{id,label,value,display,muted,selected,ariaLabel}] */
export function bandBars({ rows, onSelect }) {
  const top = Math.max(1, ...rows.map((r) => r.value || 0));
  return h('div', { class: 'bands' }, rows.map((r) => h(onSelect ? 'button' : 'div', onSelect ? {
    type: 'button', class: `band${r.muted ? ' is-muted' : ''}${r.selected ? ' is-selected' : ''}`, 'aria-pressed': r.selected ? 'true' : 'false',
    'aria-label': r.ariaLabel || `${r.label}: ${r.display}`, dataset: { focusKey: `band:${r.id}` }, onclick: (e) => onSelect(r, e.currentTarget),
  } : { class: `band is-static${r.muted ? ' is-muted' : ''}`, role: 'img', 'aria-label': r.ariaLabel || `${r.label}: ${r.display}` },
  h('span', { class: 'band-label' }, r.label),
  h('span', { class: 'band-track' }, h('span', { class: `band-bar${r.value === 0 ? ' is-zero' : ''}`, style: { width: `${((r.value || 0) / top) * 100}%` } })),
  h('span', { class: 'band-value tnum' }, r.display))));
}

/** One mutually exclusive partition as a segmented bar + legend buttons. parts: [{id,label,value,tone}] */
export function partition({ parts, onSelect, unitWord, selectedId = null }) {
  const total = parts.reduce((a, p) => a + (p.value || 0), 0);
  return h('div', { class: 'part' },
    h('div', { class: 'part-bar', 'aria-hidden': 'true' }, parts.filter((p) => p.value > 0).map((p) => h('span', { class: `part-seg tone-${p.tone || 'a'}`, style: { flexGrow: String(p.value) } }))),
    h('div', { class: 'part-legend' }, parts.map((p) => {
      const kids = [h('i', { class: `tone-${p.tone || 'a'}`, 'aria-hidden': 'true' }), h('span', {}, p.label), h('strong', { class: 'tnum' }, String(p.value))];
      const aria = `${p.label}: ${p.value} ${unitWord} of ${total}`;
      return onSelect ? h('button', {
        type: 'button', class: `part-item${selectedId === p.id ? ' is-selected' : ''}`, 'aria-label': aria, 'aria-pressed': selectedId === p.id ? 'true' : 'false',
        dataset: { focusKey: `part:${p.id}` }, onclick: (e) => onSelect(p, e.currentTarget),
      }, kids) : h('span', { class: 'part-item is-static', 'aria-label': aria }, kids);
    })));
}

/** Compact sparkline for signal cards (count per bucket), gaps for unavailable. */
export function spark(values) {
  const W = 120; const H = 28;
  const max = Math.max(1, ...values.filter(Number.isFinite));
  const pts = values.map((v, i) => (Number.isFinite(v) ? [(i / Math.max(1, values.length - 1)) * W, H - (v / max) * (H - 4) - 2] : null));
  const bars = pts.map((p, i) => (p ? h('rect', { x: p[0] - 2, y: p[1], width: 4, height: H - p[1], class: values[i] === 0 ? 'sp-zero' : 'sp-bar' }) : null));
  return h('svg', { class: 'spark', viewBox: `-3 0 ${W + 6} ${H}`, 'aria-hidden': 'true' }, bars);
}

/** KPI card. */
export function kpi({ title, value, sub, basis, more = null, foot, onOpen, state = 'value', id }) {
  return h('section', { class: `kpi${state !== 'value' ? ` is-${state}` : ''}`, 'aria-labelledby': `${id}-t` },
    h('div', { class: 'kpi-head' }, h('h2', { class: 'kpi-title', id: `${id}-t` }, title), more ? info(more, `About ${title}`) : null),
    h('div', { class: 'kpi-main' }, value),
    sub ? h('div', { class: 'kpi-sub' }, sub) : null,
    basis ? h('p', { class: 'kpi-basis' }, basis) : null,
    foot || onOpen ? h('div', { class: 'kpi-foot' }, foot, onOpen ? h('button', { type: 'button', class: 'link-button', dataset: { focusKey: `kpi:${id}` }, onclick: (e) => onOpen(e.currentTarget) }, 'See cases') : null) : null);
}

/** Vertical timeline. items: [{date, title, body, tone, chips, kind}] */
export function timeline(items, { empty = 'No dated events.' } = {}) {
  if (!items.length) return h('p', { class: 'muted' }, empty);
  return h('ol', { class: 'tl' }, items.map((it) => h('li', { class: `tl-item tone-${it.tone || 'a'}`, dataset: it.data || {} },
    h('div', { class: 'tl-date tnum' }, it.date),
    h('div', { class: 'tl-body' },
      h('div', { class: 'tl-head' }, it.kind ? h('span', { class: 'tl-kind' }, it.kind) : null, h('strong', {}, it.title)),
      it.body ? h('p', {}, it.body) : null,
      it.chips?.length ? h('div', { class: 'chips' }, it.chips) : null))));
}
