// Read-only original/preview viewers. Bytes are fetched only by redeeming the
// opaque, short-lived action the backend issued (asset.get / source.get).
// Locations are applied only from a *verified* locator; otherwise the viewer
// opens the whole file and says the location is unmapped.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { stateBox, loading, outcomeState } from '../ui/states.js';
import { parseCsv, readXlsx, parseA1, colLetters, sandboxedSrcdoc, parseEml } from './parsers.js';
import { bytes, shortHash, humanise } from '../lib/format.js';

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export function viewerKind(mediaType) {
  const mt = (mediaType || '').split(';')[0].trim().toLowerCase();
  if (mt === 'application/pdf') return 'pdf';
  if (mt === 'image/png' || mt === 'image/jpeg') return 'image';
  if (mt === 'text/csv') return 'csv';
  if (mt === XLSX) return 'xlsx';
  if (mt === 'text/html') return 'html';
  if (mt === 'text/plain') return 'text';
  if (mt === 'message/rfc822') return 'eml';
  return 'unsupported';
}

const FORMAT_LABEL = { pdf: 'PDF', image: 'image', csv: 'spreadsheet (CSV)', xlsx: 'spreadsheet (Excel)', html: 'web page', text: 'text', eml: 'email file', unsupported: 'file' };
const REPRESENTATION_LABEL = { original: 'Original file', preview: 'Readable version', derived_redacted: 'Redacted copy' };

/** Plain-language name for a permitted representation, e.g. "Original file (PDF)". */
export function describeAction(action) {
  return `${REPRESENTATION_LABEL[action.representation] || humanise(action.representation)} (${FORMAT_LABEL[viewerKind(action.media_type)]})`;
}

/** True when this browser can show the representation in-page. */
export function canDisplay(action) {
  const k = viewerKind(action.media_type);
  return k !== 'unsupported';
}

/**
 * Pick the one representation to show by default, so users never choose
 * formats: a displayable original first; otherwise the service's readable
 * version (e.g. for an email or Word file); otherwise a redacted copy;
 * otherwise whatever is permitted (the viewer then says it cannot be shown).
 * Only actions the service already permitted are ever considered.
 */
export function chooseAction(actions) {
  const rank = (a) => {
    const shown = canDisplay(a);
    if (a.representation === 'original' && shown) return 0;
    if (a.representation === 'preview' && shown) return 1;
    if (a.representation === 'derived_redacted' && shown) return 2;
    return 3;
  };
  return [...actions].sort((x, y) => rank(x) - rank(y))[0] || null;
}

/** Redeem an action into bytes; re-requests the action once if it expired. */
export async function redeem(app, action, refetchAction) {
  const tmpl = app.config.asset_redeem_path;
  if (!tmpl) return { kind: 'undocumented' };
  let act = action;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (Date.parse(act.expires_at) - Date.now() < 5000) {
      const fresh = await refetchAction();
      if (fresh.kind !== Outcome.OK) return fresh;
      act = fresh.payload;
    }
    let res;
    try {
      res = await fetch(tmpl.replace('{token}', encodeURIComponent(act.retrieval_token)), { credentials: 'same-origin' });
    } catch {
      return { kind: Outcome.TRANSPORT, reason: 'unreachable' };
    }
    if ((res.status === 401 || res.status === 403 || res.status === 410) && attempt === 0) {
      const fresh = await refetchAction();
      if (fresh.kind !== Outcome.OK) return fresh;
      act = fresh.payload;
      continue;
    }
    if (!res.ok) return { kind: Outcome.UNAVAILABLE, error: { code: 'RESOURCE_UNAVAILABLE', message: '', retryable: false, correlation_id: `http-${res.status}` } };
    const buf = await res.arrayBuffer();
    const digest = await sha256Hex(buf);
    if (digest !== act.bytes_sha256) {
      return { kind: Outcome.UNAVAILABLE, error: { code: 'INTEGRITY_FAILURE', message: '', retryable: false, correlation_id: 'client-digest' } };
    }
    return { kind: 'bytes', buffer: buf, action: act };
  }
  return { kind: Outcome.UNAVAILABLE, error: { code: 'RESOURCE_UNAVAILABLE', message: '', retryable: false, correlation_id: 'client-redeem' } };
}

async function sha256Hex(buf) {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Render an asset action in its format viewer.
 * @param {object} app
 * @param {object} action asset_action
 * @param {{locator?: object|null, refetch: () => Promise<object>, filename?: string}} opts
 */
export function assetViewer(app, action, { locator = null, refetch, filename = '' }) {
  const kind = viewerKind(action.media_type);
  const wrap = h('div', { class: 'viewer', dataset: { viewer: kind } },
    h('div', { class: 'viewer-head' },
      h('span', {}, h('strong', {}, describeAction(action)), filename ? h('span', { class: 'muted' }, ` · ${filename}`) : null),
      h('span', { class: 'muted', title: `File fingerprint (SHA-256) ${action.bytes_sha256}; checked before display` }, `Integrity checked · ${shortHash(action.bytes_sha256)}`)),
    locatorNote(locator),
    h('div', { class: 'viewer-body' }, loading('Retrieving permitted file')));
  const body = wrap.querySelector('.viewer-body');
  if (kind === 'unsupported') {
    mount(body, stateBox('unsupported-viewer', 'This file type cannot be shown in the browser',
      'The file is held and you may access it, but it cannot be displayed here. Its full text is shown above.'));
    return wrap;
  }
  (async () => {
    const got = await redeem(app, action, refetch);
    if (got.kind === 'undocumented') {
      mount(body, stateBox('undocumented', 'Original-file delivery not yet available',
        'The backend has not yet documented how retrieval actions are redeemed. The extracted source remains readable.'));
      return;
    }
    if (got.kind !== 'bytes') { mount(body, outcomeState(got)); return; }
    try {
      mount(body, await renderBytes(kind, got.buffer, got.action, locator));
    } catch (e) {
      mount(body, stateBox('render-failed', 'This file could not be displayed', 'The browser could not render the permitted bytes. The extracted source is unaffected.'));
    }
  })();
  return wrap;
}

function locatorNote(loc) {
  if (!loc) return h('p', { class: 'locator-note muted' }, 'Showing the whole document.');
  if (loc.state === 'verified') {
    const where = loc.location?.kind === 'pdf' ? `page ${loc.location.page}` : humanise(loc.precision);
    return h('p', { class: 'locator-note ok', title: `Verified mapping ${loc.mapping_id} (${humanise(loc.precision)} precision)` }, `Opened at the quoted passage (${where}).`);
  }
  return h('p', { class: `locator-note ${loc.state === 'unmapped' ? 'warn' : 'err'}`, title: loc.reason },
    'The quoted passage could not be pinpointed in this file, so the whole document is shown. The quote above is still valid.');
}

async function renderBytes(kind, buf, action, loc) {
  const verified = loc && loc.state === 'verified' ? loc.location : null;
  switch (kind) {
    case 'pdf': return pdfView(buf, verified);
    case 'image': return imageView(buf, action.media_type, verified);
    case 'csv': return csvView(buf, verified);
    case 'xlsx': return xlsxView(buf, verified);
    case 'html': return htmlPreview(buf);
    case 'text': return textView(buf, verified);
    case 'eml': return emlView(buf);
    default: return stateBox('unsupported-viewer', 'No viewer', null);
  }
}

function pdfView(buf, loc) {
  const url = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
  const page = loc && loc.kind === 'pdf' ? loc.page : null;
  // Plain #page=N only: adding view=FitH makes Chrome's viewer ignore the page jump.
  const src = page ? `${url}#page=${page}` : url;
  return h('div', { class: 'pdf' },
    page && loc.region ? h('p', { class: 'muted' }, 'The verified region is listed below; the built-in PDF view shows page precision only.') : null,
    loc?.region ? h('p', { class: 'mono muted' }, `Region (normalised, top-left): ${fmtRect(loc.region)}`) : null,
    h('iframe', { src, title: 'PDF document (read-only)', class: 'pdf-frame' }),
    h('p', { class: 'muted pdf-fallback' }, 'If the document area stays blank, your browser is not showing PDFs inside the page. ',
      h('button', { type: 'button', class: 'link-button', onclick: () => window.open(src, '_blank', 'noopener') }, 'Open PDF in a new tab'),
      ' (the same verified file, read-only).'));
}

function fmtRect(r) {
  return `x ${r.x0.toFixed(3)}–${r.x1.toFixed(3)}, y ${r.y0.toFixed(3)}–${r.y1.toFixed(3)}`;
}

function imageView(buf, mediaType, loc) {
  const url = URL.createObjectURL(new Blob([buf], { type: mediaType }));
  let scale = 1; let tx = 0; let ty = 0;
  const img = h('img', { src: url, alt: 'Supplied image (read-only)', draggable: 'false' });
  const overlay = loc && loc.kind === 'image' && loc.region
    ? h('div', { class: 'img-region', style: {
      left: `${loc.region.x0 * 100}%`, top: `${loc.region.y0 * 100}%`,
      width: `${(loc.region.x1 - loc.region.x0) * 100}%`, height: `${(loc.region.y1 - loc.region.y0) * 100}%`,
    }, 'aria-hidden': 'true' }) : null;
  const stage = h('div', { class: 'img-stage' }, img, overlay);
  const frame = h('div', { class: 'img-frame', tabindex: '0', 'aria-label': 'Image. Use plus, minus, arrow keys and 0 to reset.' }, stage);
  const apply = () => { stage.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`; };
  const zoom = (f) => { scale = Math.min(8, Math.max(0.25, scale * f)); apply(); };
  const reset = () => { scale = 1; tx = 0; ty = 0; apply(); };
  frame.addEventListener('keydown', (e) => {
    const step = 40;
    if (e.key === '+' || e.key === '=') zoom(1.25);
    else if (e.key === '-') zoom(0.8);
    else if (e.key === '0') reset();
    else if (e.key === 'ArrowLeft') tx += step;
    else if (e.key === 'ArrowRight') tx -= step;
    else if (e.key === 'ArrowUp') ty += step;
    else if (e.key === 'ArrowDown') ty -= step;
    else return;
    e.preventDefault(); apply();
  });
  let drag = null;
  frame.addEventListener('pointerdown', (e) => { drag = { x: e.clientX - tx, y: e.clientY - ty }; frame.setPointerCapture(e.pointerId); });
  frame.addEventListener('pointermove', (e) => { if (drag) { tx = e.clientX - drag.x; ty = e.clientY - drag.y; apply(); } });
  frame.addEventListener('pointerup', () => { drag = null; });
  return h('div', { class: 'image-viewer' },
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'button-secondary', onclick: () => zoom(1.25) }, 'Zoom in'),
      h('button', { type: 'button', class: 'button-secondary', onclick: () => zoom(0.8) }, 'Zoom out'),
      h('button', { type: 'button', class: 'button-secondary', onclick: reset }, 'Reset')),
    overlay ? h('p', { class: 'muted' }, 'Outlined area is the verified mapped region (EXIF-normalised image).') : null,
    frame);
}

function csvView(buf, loc) {
  const text = new TextDecoder('utf-8', { fatal: false }).decode(buf);
  const rows = parseCsv(text);
  if (!rows.length) return stateBox('empty', 'Empty file', null);
  const [header, ...records] = rows;
  // Original record index is 1-based excluding the header (Shared §4) and never changes with sorting.
  let view = records.map((r, i) => ({ index: i + 1, r }));
  const target = loc && loc.kind === 'csv' ? loc : null;
  const table = h('table', { class: 'data-table sheet' });
  let sortCol = null; let asc = true;
  const draw = () => {
    mount(table,
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Record'), header.map((c, ci) => h('th', { scope: 'col' },
        h('button', { type: 'button', class: 'th-sort', 'aria-sort': sortCol === ci ? (asc ? 'ascending' : 'descending') : null,
          onclick: () => { asc = sortCol === ci ? !asc : true; sortCol = ci; view = [...view].sort((a, b) => cmp(a.r[ci], b.r[ci]) * (asc ? 1 : -1)); draw(); } }, c))))),
      h('tbody', {}, view.map(({ index, r }) => h('tr', { class: target && target.data_record_index === index ? 'hit-row' : null, id: target && target.data_record_index === index ? 'csv-hit' : null },
        h('th', { scope: 'row', class: 'mono' }, String(index)),
        header.map((_, ci) => h('td', { class: target && target.data_record_index === index && target.column_index === ci + 1 ? 'hit-cell' : null }, r[ci] ?? ''))))));
  };
  draw();
  const wrap = h('div', {},
    h('p', { class: 'muted' }, `${records.length} records. Values are shown exactly as stored (including leading zeros). Sorting is display-only; record numbers stay original.`),
    h('div', { class: 'table-scroll tall' }, table));
  if (target) setTimeout(() => document.getElementById('csv-hit')?.scrollIntoView({ block: 'center' }), 50);
  return wrap;
}

function cmp(a = '', b = '') {
  return String(a).localeCompare(String(b), 'en-AU', { numeric: true });
}

async function xlsxView(buf, loc) {
  const wb = await readXlsx(buf);
  const target = loc && loc.kind === 'xlsx' ? loc : null;
  let active = target ? Math.max(0, wb.sheets.findIndex((s) => s.sheetId === target.sheet_id || s.name === target.sheet_name)) : 0;
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Sheets' });
  const grid = h('div', { class: 'table-scroll tall' });
  const draw = () => {
    mount(tabs, wb.sheets.map((s, i) => h('button', { type: 'button', role: 'tab', 'aria-selected': String(i === active), onclick: () => { active = i; draw(); } }, s.name)));
    const s = wb.sheets[active];
    const hit = target && (s.sheetId === target.sheet_id || s.name === target.sheet_name) ? parseA1(target.a1_range) : null;
    const maxCol = Math.min(s.maxCol, 60); const maxRow = Math.min(s.maxRow, 2000);
    mount(grid, h('table', { class: 'data-table sheet' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), Array.from({ length: maxCol }, (_, i) => h('th', { scope: 'col' }, colLetters(i + 1))))),
      h('tbody', {}, Array.from({ length: maxRow }, (_, ri) => h('tr', {}, h('th', { scope: 'row' }, String(ri + 1)),
        Array.from({ length: maxCol }, (_, ci) => {
          const ref = `${colLetters(ci + 1)}${ri + 1}`;
          const cell = s.cells.get(ref);
          const inHit = hit && ri + 1 >= hit.r1 && ri + 1 <= hit.r2 && ci + 1 >= hit.c1 && ci + 1 <= hit.c2;
          return h('td', { class: `${inHit ? 'hit-cell' : ''}${cell?.formula ? ' has-formula' : ''}`, title: cell?.formula ? `Formula (not evaluated): =${cell.formula}` : ref, id: inHit && ri + 1 === hit.r1 && ci + 1 === hit.c1 ? 'xlsx-hit' : null },
            cell?.value ?? '');
        }))))));
    if (hit) setTimeout(() => document.getElementById('xlsx-hit')?.scrollIntoView({ block: 'center', inline: 'center' }), 50);
  };
  draw();
  return h('div', {},
    h('p', { class: 'muted' }, 'Read-only. Stored cell values are shown; formulas are never recalculated and macros never run. Cells with a formula are marked.'),
    tabs, grid);
}

function htmlPreview(buf) {
  const html = new TextDecoder('utf-8').decode(buf);
  return h('iframe', { class: 'html-preview', title: 'Sanitised preview (scripts, forms and remote content disabled)', sandbox: '', referrerpolicy: 'no-referrer', srcdoc: sandboxedSrcdoc(html) });
}

/** Decoded view of the verified email bytes: headers, text body and attachment names (never opened). */
function emlView(buf) {
  const m = parseEml(buf);
  const row = (k, v) => (v ? [h('dt', {}, k), h('dd', {}, v)] : []);
  return h('div', { class: 'eml' },
    h('dl', { class: 'kv eml-head' }, row('From', m.from), row('To', m.to), row('Cc', m.cc), row('Date', m.date), row('Subject', m.subject)),
    h('pre', { class: 'source-text' }, m.text || '(no text body)'),
    m.attachments.length ? h('p', { class: 'muted' }, `Attachments in this email: ${m.attachments.join(', ')} (listed only; permitted attachments appear below).`) : null,
    h('p', { class: 'muted small' }, 'Decoded in the browser from the integrity-checked original. The cited passage is highlighted in the source text above.'));
}

async function textView(buf, loc) {
  const text = new TextDecoder('utf-8').decode(buf);
  const target = loc && loc.kind === 'text' ? loc : null;
  if (target) {
    // Spans refer to the digest-bound decoded representation; highlight only if it matches.
    const decodedDigest = await sha256Hex(new TextEncoder().encode(text).buffer);
    const cps = Array.from(text);
    if (decodedDigest === target.decoded_representation_sha256 && target.end_codepoint <= cps.length && target.end_codepoint > target.start_codepoint) {
      return h('pre', { class: 'source-text' }, cps.slice(0, target.start_codepoint).join(''),
        h('mark', { id: 'text-hit' }, cps.slice(target.start_codepoint, target.end_codepoint).join('')), cps.slice(target.end_codepoint).join(''));
    }
    return h('div', {}, h('p', { class: 'locator-note warn' }, 'The decoded text differs from the mapped representation, so no highlight is shown.'),
      h('pre', { class: 'source-text' }, text));
  }
  return h('pre', { class: 'source-text' }, text);
}

export { bytes };
