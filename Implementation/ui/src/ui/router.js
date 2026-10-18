// History-API router. URLs carry only opaque permitted identifiers and display
// preferences (never evidence text or personal data, never authority). Scroll
// position and the focused trigger are stored per history entry and restored
// on back/forward.

const SEP = '~';

export function encodeRef(ref) {
  return ref ? [ref.result_id, ref.cell_key, ref.population_id, ref.predicate_sha256].join(SEP) : null;
}

export function decodeRef(s) {
  if (!s) return null;
  const parts = s.split(SEP);
  if (parts.length !== 4 || !/^[0-9a-f]{64}$/.test(parts[3])) return null;
  return { result_id: parts[0], cell_key: parts[1], population_id: parts[2], predicate_sha256: parts[3] };
}

export function encodeCitation(anchor) {
  if (!anchor) return null;
  if (anchor.kind === 'text') return `t${SEP}${anchor.start_codepoint}${SEP}${anchor.end_codepoint}`;
  if (anchor.kind === 'field') return `f${SEP}${anchor.json_pointer}`;
  return null;
}

export function decodeCitation(s) {
  if (!s) return null;
  if (s.startsWith(`t${SEP}`)) {
    const [, a, b] = s.split(SEP);
    const start = Number(a); const end = Number(b);
    return Number.isInteger(start) && Number.isInteger(end) ? { kind: 'text', start_codepoint: start, end_codepoint: end } : null;
  }
  if (s.startsWith(`f${SEP}`)) return { kind: 'field', json_pointer: s.slice(2) };
  return null;
}

/** Parse location into {section, id, params}. */
export function parseLocation(loc = window.location) {
  const parts = loc.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(loc.search);
  const section = parts[0] || 'overview';
  return { section, sub: parts[1] || null, id: parts[2] || parts[1] || null, parts, params };
}

export class Router {
  constructor(onRoute) {
    this.onRoute = onRoute;
    this._pop = (e) => this._render(e.state);
    this._click = (e) => {
      const a = e.target.closest('a[data-nav]');
      if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      this.go(a.getAttribute('href'));
    };
    window.addEventListener('popstate', this._pop);
    document.addEventListener('click', this._click);
  }

  dispose() {
    window.removeEventListener('popstate', this._pop);
    document.removeEventListener('click', this._click);
  }

  start() {
    history.replaceState({ scrollY: 0, focusId: null, depth: history.state?.depth || 0 }, '');
    this._render(history.state);
  }

  /** Navigate, recording where the user was so Back returns there. */
  go(href, { replace = false, fresh = null } = {}) {
    const focusId = document.activeElement?.id || document.activeElement?.dataset?.focusKey || null;
    const depth = history.state?.depth || 0; // entries pushed in this app session, so a view can step back to where it was entered
    const prev = new URL(window.location.href);
    history.replaceState({ ...(history.state || {}), scrollY: window.scrollY, focusId }, '');
    if (replace) history.replaceState({ scrollY: 0, focusId: null, depth }, '', href);
    else history.pushState({ scrollY: 0, focusId: null, depth: depth + 1 }, '', href);
    // Opening or closing a drawer keeps the page in place; any other change re-renders from the top.
    const overlayOnly = fresh !== true && sameExceptOverlays(prev, new URL(href, window.location.href));
    this._render(history.state, !overlayOnly, overlayOnly);
  }

  /** Update preference params in place (no new history entry for each toggle). */
  setParams(updates, { push = false } = {}) {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === undefined || v === '') url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    if (push) this.go(url.pathname + url.search);
    else {
      history.replaceState({ ...(history.state || {}), scrollY: window.scrollY }, '', url.pathname + url.search);
      this._render(history.state, false, true);
    }
  }

  /** Record an in-place selection in the URL without re-rendering the view. */
  replaceParams(updates) {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === undefined || v === '') url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    history.replaceState({ ...(history.state || {}), scrollY: window.scrollY }, '', url.pathname + url.search);
  }

  async _render(state, fresh = false, keepScroll = false) {
    const keptY = window.scrollY;
    const keptFocus = document.activeElement?.closest?.('main') ? document.activeElement.dataset?.focusKey || null : null;
    await this.onRoute(parseLocation(), { fresh });
    if (keepScroll) {
      if (window.scrollY !== keptY) window.scrollTo(0, keptY); // a re-render may briefly shorten the page
      // An in-place selection re-renders the view; return focus to the same control.
      if (keptFocus && !document.querySelector('dialog[open]')) document.querySelector(`[data-focus-key="${CSS.escape(keptFocus)}"]`)?.focus({ preventScroll: true });
      return;
    }
    const y = state?.scrollY || 0;
    window.scrollTo(0, fresh ? 0 : y);
    if (!fresh && state?.focusId) {
      const el = document.getElementById(state.focusId) || document.querySelector(`[data-focus-key="${CSS.escape(state.focusId)}"]`);
      if (el) el.focus({ preventScroll: true });
    } else if (fresh) {
      const heading = document.querySelector('main h1');
      if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
    }
  }
}

const OVERLAY_PARAMS = ['pop', 'more', 'pg', 'src', 'cite', 'asset', 'parent'];
function sameExceptOverlays(a, b) {
  if (a.pathname !== b.pathname) return false;
  const rest = (u) => [...u.searchParams].filter(([k]) => !OVERLAY_PARAMS.includes(k)).sort().join('&');
  return rest(a) === rest(b);
}

export function href(path, params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') q.set(k, v);
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
