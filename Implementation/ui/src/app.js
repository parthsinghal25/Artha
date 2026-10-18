// Application bootstrap and shell for the complaints-intelligence dashboard.
// Owns runtime config, schema registry (frozen Shared 1.0.0 + backend
// companion 1.1.0), typed client, the publication-bound store, router, the
// reporting-period scope bar and the compact publication strip. Destinations
// live in ./views/. No view calls a model: every figure is read from the bound
// completed publication or composed from its fully paged published sets.

import { SchemaRegistry } from './lib/schema.js';
import { ApiClient, fetchTransport, RequestSlots, Outcome } from './api/client.js';
import { IntelStore } from './api/store.js';
import { Router, href } from './ui/router.js';
import { h, mount, announce } from './ui/dom.js';
import { loading, outcomeState, setSignInUrl, stateBox } from './ui/states.js';
import { openDrawer, closeAllDrawers } from './ui/drawer.js';
import { GRAINS, GRAIN_IDS, bucketList, selectedIndex, defaultTrendGrain, knownThrough, fmtDate, melbourneDate } from './lib/periods.js';
import { instant } from './lib/format.js';
import { icon } from './ui/icons.js';
import { renderOverview } from './views/overview.js';
import { renderIssues } from './views/issues.js';
import { renderIssue } from './views/issue.js';
import { renderExperience } from './views/experience.js';
import { renderImpact } from './views/impact.js';
import { renderAfca } from './views/afca.js';
import { renderCases } from './views/cases.js';
import { renderCase } from './views/case.js';
import { openPopulationFromUrl } from './views/population.js';
import { openOverlays } from './views/source.js';

export const DESTINATIONS = [
  { section: 'overview', label: 'Executive Overview', icon: 'overview' },
  { section: 'issues', label: 'Issues & Trends', icon: 'issues' },
  { section: 'experience', label: 'Member Experience & Backlog', icon: 'experience' },
  { section: 'impact', label: 'Business Impact', icon: 'impact', children: [{ path: '/impact/afca', label: 'AFCA', icon: 'afca' }] },
  { section: 'cases', label: 'Cases & Evidence', icon: 'cases' },
];

// Global scope parameters carried by every link.
export const GLOBAL_PARAMS = ['g', 'p', 'tg', 'n'];
// Selections that depend on the reporting period/grain; cleared when those change.
export const SCOPE_DEPENDENT = ['pop', 'pg', 'am', 'sel', 'src', 'cite', 'asset', 'parent', 'ip', 'case', 'ev'];

/** Coverage wording derived from the bound publication's own counts (never a fixed population claim). */
export function coverageSummary(cov) {
  const n = (v) => (v ?? '?');
  const head = `This publication covers ${n(cov.cases_in_view)} received cases: ${n(cov.memories)} assessed, ${n(cov.pending_memories)} eligible but pending assessment, ${n(cov.not_eligible_unassessed)} not eligible (holdout).`;
  const tail = cov.pending_memories > 0
    ? ' Pending and holdout cases are counted in arrivals and shown as unassessed — never as supported contributors or negative findings. Issue reach is a lower bound until the pending cases are assessed.'
    : ' Every eligible case is assessed; holdout cases are counted in arrivals and never diagnosed.';
  return head + tail;
}

function coverageTitle(cov) {
  return `${cov.memories ?? '?'} assessed · ${cov.pending_memories ?? '?'} pending · ${cov.not_eligible_unassessed ?? '?'} holdout · ${cov.eligible_in_view ?? '?'} eligible of ${cov.cases_in_view ?? '?'} received`;
}

export class App {
  constructor({ config, registry, transport }) {
    this.config = config;
    this.client = new ApiClient({ registry, transport });
    this.store = new IntelStore({ client: this.client });
    this.slots = new RequestSlots();
    this.router = new Router((loc, opts) => this.route(loc, opts));
    this.updateState = { state: 'idle' };
    this.store.addEventListener('authority-changed', () => {
      this.slots.abortAll();
      closeAllDrawers();
      announce('Your access changed. Views were reloaded without previously shown content.');
      setTimeout(() => this.reload(), 0);
    });
    this.store.addEventListener('publication-available', () => {
      this.updateState = { state: 'available', publication: this.store.pending?.publication };
      this.renderStrip();
      announce('A newer completed publication is available. Current results remain from the bound publication.');
    });
  }

  dispose() {
    this.router.dispose();
    this.slots.abortAll();
    closeAllDrawers();
  }

  // ---- scope (URL) -------------------------------------------------------
  scope(params = this.loc?.params || new URLSearchParams()) {
    const grain = GRAIN_IDS.includes(params.get('g')) ? params.get('g') : 'month';
    const tg = GRAIN_IDS.includes(params.get('tg')) && params.get('tg') !== 'observation_year' ? params.get('tg') : defaultTrendGrain(grain);
    const topN = params.get('n') === '10' ? 10 : 5;
    const issues = (params.get('iss') || '').split(',').filter((x) => /^ISS-[A-Za-z0-9]+$/.test(x)).slice(0, 3);
    return { grain, period: params.get('p') || null, trendGrain: tg, topN, issues };
  }

  globals(extra = {}) {
    const p = {};
    for (const k of GLOBAL_PARAMS) if (this.loc?.params.get(k)) p[k] = this.loc.params.get(k);
    return { ...p, ...extra };
  }

  link(path, extra = {}) {
    return href(path, this.globals(extra));
  }

  /** Replace scope params; reporting-period changes clear dependent selections (never relabel them). */
  changeScope(updates, what) {
    const url = new URL(window.location.href);
    const cleared = SCOPE_DEPENDENT.filter((k) => url.searchParams.get(k));
    for (const k of SCOPE_DEPENDENT) url.searchParams.delete(k);
    for (const [k, v] of Object.entries(updates)) { if (v === null || v === undefined) url.searchParams.delete(k); else url.searchParams.set(k, v); }
    if (cleared.length) this.pendingNotice = `Selection cleared because the ${what} changed; it is not relabelled with the new period.`;
    this.router.go(url.pathname + (url.search || ''));
  }

  /** Update view-local params (tab, chosen issues, unit) without touching scope. */
  setLocal(updates, { push = false } = {}) {
    this.router.setParams(updates, { push });
  }

  reload() {
    if (this.loc) this.route(this.loc, { fresh: true });
  }

  // ---- data helpers used by views ---------------------------------------
  /** Bound context (source inspector and viewers read capabilities from it). */
  get rootContext() {
    return this.store.binding?.context || null;
  }

  /** Shared operation in the binding context; `ref` is unused (no child population contexts in this build). */
  op(ref, build, opts = {}) {
    return this.store.shared(build, opts);
  }

  sec(section, opts = {}, signal) {
    return this.store.section(section, opts, { signal });
  }

  /** Published not-eligible (holdout) case IDs; unassessed cases outside it are eligible but pending. */
  async holdoutSet(signal) {
    const c = await this.sec('coverage', {}, signal);
    const list = c.kind === Outcome.OK ? c.items[0]?.not_eligible_unassessed : null;
    return Array.isArray(list) ? new Set(list) : null;
  }

  /** Buckets of a grain from the published arrivals of that grain. */
  async buckets(grain, signal) {
    const out = await this.sec('arrivals', { grain }, signal);
    if (out.kind !== Outcome.OK) return out;
    return { kind: Outcome.OK, buckets: bucketList(out.items, grain), arrivals: out };
  }

  // ---- shell -------------------------------------------------------------
  renderShell() {
    const root = document.getElementById('app');
    this.nav = h('nav', { class: 'nav', 'aria-label': 'Destinations' },
      h('ul', {}, DESTINATIONS.map((d) => h('li', {},
        h('a', { href: `/${d.section}`, 'data-nav': '', dataset: { section: d.section } }, icon(d.icon), h('span', {}, d.label)),
        d.children ? h('ul', { class: 'nav-sub' }, d.children.map((c) => h('li', {}, h('a', { href: c.path, 'data-nav': '', dataset: { path: c.path } }, icon(c.icon), h('span', {}, c.label))))) : null))));
    this.menuButton = h('button', { type: 'button', class: 'menu-button', 'aria-expanded': 'false', 'aria-controls': 'rail', onclick: () => this.toggleMenu() }, icon('menu'), h('span', {}, 'Menu'));
    this.scopeBar = h('div', { class: 'scope', role: 'region', 'aria-label': 'Reporting period' });
    this.strip = h('div', { class: 'strip', role: 'region', 'aria-label': 'Publication and coverage' });
    this.main = h('main', { id: 'main', tabindex: '-1' });
    // Remember, per page, the control that opened a drawer so focus can return to it after the page re-renders on close.
    this.openers = new Map();
    this.main.addEventListener('click', (e) => {
      const el = e.target.closest('a[href], button');
      if (!el || !this.loc) return;
      const href = el.getAttribute('href');
      if (el.dataset.focusKey || /[?&](pop|src|asset)=/.test(href || '')) this.openers.set(this.loc.parts.join('/'), { href, key: el.dataset.focusKey || null });
    }, true);
    mount(root,
      h('a', { class: 'skip', href: '#main' }, 'Skip to content'),
      h('aside', { class: 'rail', id: 'rail' },
        h('div', { class: 'brand' }, h('span', { class: 'brandmark', 'aria-hidden': 'true' }, 'A'),
          h('div', {}, h('strong', {}, 'Artha'), h('small', {}, 'Complaints Intelligence'))),
        this.nav,
        h('div', { class: 'rail-foot' }, h('span', { class: 'synthetic' }, 'Synthetic demonstration data'), h('small', {}, 'Model-assessed · not independently validated'))),
      h('div', { class: 'workspace' },
        h('header', { class: 'top' }, this.menuButton, this.scopeBar, this.strip),
        this.main),
      h('div', { id: 'live-region', class: 'sr-only', 'aria-live': 'polite' }));
  }

  toggleMenu(force) {
    const open = force ?? !document.body.classList.contains('menu-open');
    document.body.classList.toggle('menu-open', open);
    this.menuButton?.setAttribute('aria-expanded', String(open));
  }

  renderScopeBar(buckets, selected) {
    const s = this.scope();
    const showTopN = ['overview', 'issues'].includes(this.loc?.section) && !this.loc?.parts[1];
    const seg = (label, items, value, onPick, name) => h('div', { class: 'seg', role: 'group', 'aria-label': label },
      items.map(([v, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': v === value ? 'true' : 'false', dataset: { [name]: v }, onclick: () => v !== value && onPick(v) }, l)));
    const idx = buckets ? buckets.findIndex((b) => b.key === selected?.key) : -1;
    const pick = (key) => this.changeScope({ p: key }, 'reporting period');
    const trendOpts = [['day', 'Daily'], ['week', 'Weekly'], ['month', 'Monthly'], ['quarter', 'Quarterly']];
    mount(this.scopeBar,
      h('div', { class: 'scope-group' }, h('span', { class: 'scope-label' }, 'Reporting period'),
        seg('Reporting period grain', GRAINS.map((g) => [g.id, g.id === 'observation_year' ? 'Obs. year' : g.label]), s.grain,
          (v) => this.changeScope({ g: v === 'month' ? null : v, p: null, tg: null }, 'reporting grain'), 'grain')),
      buckets && selected ? h('div', { class: 'scope-group picker' },
        h('button', { type: 'button', class: 'icon-button', 'aria-label': 'Previous period', disabled: idx <= 0 ? true : null, onclick: () => pick(buckets[idx - 1].key) }, icon('prev')),
        h('label', { class: 'sr-only', for: 'ctl-period' }, 'Selected period'),
        h('select', { id: 'ctl-period', onchange: (e) => pick(e.target.value) },
          [...buckets].reverse().map((b) => h('option', { value: b.key, selected: b.key === selected.key ? true : null }, `${b.label}${b.partial ? ' (partial)' : ''}`))),
        h('button', { type: 'button', class: 'icon-button', 'aria-label': 'Next period', disabled: idx >= buckets.length - 1 ? true : null, onclick: () => pick(buckets[idx + 1].key) }, icon('next'))) : null,
      h('div', { class: 'scope-group' }, h('label', { class: 'scope-label', for: 'ctl-trend' }, 'Trend window'),
        h('select', { id: 'ctl-trend', onchange: (e) => this.changeScope({ tg: e.target.value === defaultTrendGrain(s.grain) ? null : e.target.value }, 'trend grain') },
          trendOpts.map(([v, l]) => h('option', { value: v, selected: v === s.trendGrain ? true : null }, l)))),
      showTopN ? h('div', { class: 'scope-group' }, h('span', { class: 'scope-label' }, 'Leaders'),
        seg('Leading issues shown', [['5', 'Top 5'], ['10', 'Top 10']], String(s.topN), (v) => this.setLocal({ n: v === '5' ? null : v }), 'topn')) : null);
  }

  renderStrip() {
    const b = this.store.binding;
    if (!b) { mount(this.strip, h('span', { class: 'muted' }, 'No completed publication bound')); return; }
    const pub = b.publication; const cov = pub.coverage || {};
    const sel = this.selectedBucket;
    const u = this.updateState;
    mount(this.strip,
      h('span', { class: 'strip-tag', title: 'Synthetic demonstration data · model-assessed, not independently validated' }, 'Synthetic demo'),
      h('span', {}, h('span', { class: 'muted' }, 'Evidence to '), h('strong', {}, fmtDate(knownThrough(pub.knowledge_end_exclusive)))),
      // Assessed = cases with a sealed case memory in this publication (not merely eligible for one).
      h('span', { dataset: { coverage: 'assessed' }, title: coverageTitle(cov) }, h('strong', { class: 'tnum' }, `${cov.memories ?? '?'}/${cov.cases_in_view ?? '?'}`), h('span', { class: 'muted' }, ' cases assessed by AI')),
      h('span', { class: 'muted', title: `Publication completed ${instant(pub.watermarks?.published_at)}` }, `Updated ${pub.watermarks?.published_at ? fmtDate(melbourneDate(pub.watermarks.published_at)) : '—'}`),
      h('span', { class: 'strip-actions' },
        h('button', { type: 'button', class: 'link-button', dataset: { action: 'coverage' }, onclick: (e) => this.openCoverage(e.currentTarget) }, 'Coverage & versions'),
        u.state === 'available'
          ? h('button', { type: 'button', class: 'button-primary small', dataset: { action: 'adopt' }, onclick: () => this.adopt() }, 'Load completed publication')
          : h('button', { type: 'button', class: 'button-secondary small', dataset: { action: 'check-updates' }, disabled: u.state === 'checking' ? true : null, onclick: () => this.checkUpdates() },
            u.state === 'checking' ? 'Checking for a completed update…' : 'Check updates'),
        u.state === 'same' ? h('span', { class: 'muted', role: 'status' }, `Latest completed analysis retained · checked ${instant(u.at)}`) : null,
        u.state === 'failed' ? h('span', { class: 'warn-text', role: 'status' }, 'Update check failed — current results retained') : null,
        u.state === 'available' ? h('span', { class: 'signal-text', role: 'status' }, `Completed publication available (${instant(u.publication?.watermarks?.published_at)})`) : null));
  }

  async checkUpdates() {
    this.updateState = { state: 'checking' };
    this.renderStrip();
    const r = await this.store.checkUpdates();
    if (r.kind === 'new') this.updateState = { state: 'available', publication: r.served };
    else if (r.kind === 'same') this.updateState = { state: 'same', at: new Date().toISOString() };
    else this.updateState = { state: 'failed' };
    this.renderStrip();
  }

  adopt() {
    if (!this.store.adoptPending()) return;
    this.updateState = { state: 'idle' };
    announce('Loaded the completed publication. Every view now uses it.');
    const url = new URL(window.location.href);
    for (const k of SCOPE_DEPENDENT) url.searchParams.delete(k);
    this.router.go(url.pathname + url.search, { replace: true, fresh: true });
  }

  async openCoverage(trigger) {
    const b = this.store.binding;
    const pub = b.publication; const ctx = b.context; const cov = pub.coverage || {};
    const body = h('div', { class: 'stack' }, loading('Loading coverage'));
    openDrawer({ title: 'Coverage & versions', body, trigger });
    const [c, pl] = await Promise.all([this.sec('coverage'), this.sec('publications')]);
    const ci = c.kind === Outcome.OK ? c.items[0] : null;
    const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
    mount(body,
      h('p', { dataset: { coverageSummary: '' } }, coverageSummary(cov)),
      h('h3', {}, 'Processed coverage'),
      h('dl', { class: 'kv' },
        row('Received cases in view', cov.cases_in_view),
        row('Eligible for assessment', `${cov.eligible_in_view} cases`),
        row('Assessed', `${cov.memories} cases (sealed case memory, assessed against the published issues)`),
        row('Eligible, pending assessment', `${cov.pending_memories} cases (no case memory yet; counted in arrivals, shown as unassessed)`),
        row('Not eligible · unassessed', `${cov.not_eligible_unassessed} cases (holdout; counted in arrivals, never diagnosed)`),
        row('Unassessed case–issue pairs', String(cov.unassessed_pairs ?? '—')),
        row('Partial or failed extraction', `${cov.extraction_partial_or_failed} source(s)`),
        row('Assessment', `${pub.model_assessed ? 'Model-assessed' : 'Not model-assessed'} · human review ${String(pub.human_review).replace('_', ' ')}`),
        ci ? row('Publication checks', Object.entries(ci.checks || {}).map(([k, v]) => `${k.replaceAll('_', ' ')}: ${v ? 'pass' : 'fail'}`).join(' · ')) : null),
      h('h3', {}, 'Dates'),
      h('dl', { class: 'kv' },
        row('Knowledge view', pub.view_date),
        row('Evidence end (exclusive)', `${instant(pub.knowledge_end_exclusive)} — evidence through ${fmtDate(knownThrough(pub.knowledge_end_exclusive))}`),
        row('Input watermark', instant(pub.watermarks?.input_watermark)),
        row('Last memory assessment', instant(pub.watermarks?.last_memory_assessment)),
        row('Publication completed', instant(pub.watermarks?.published_at))),
      h('h3', {}, 'Versions'),
      h('dl', { class: 'kv' },
        row('Publication', h('span', { class: 'mono' }, pub.publication_id)),
        row('Release', h('span', { class: 'mono' }, ctx.release_id)),
        row('Analysis set', h('span', { class: 'mono' }, pub.analysis_set_version)),
        row('Taxonomy', h('span', { class: 'mono' }, ctx.taxonomy_version)),
        row('Source set', h('span', { class: 'mono' }, ctx.source_set_version)),
        row('Policies', `${pub.publication_policy} · ${pub.temporal_policy}`),
        row('Contracts', `Shared ${this.config.contract_version} · intelligence companion ${this.config.intelligence_schema || 'unavailable'}`),
        row('Requests this session', String(this.store.requests))),
      h('h3', {}, 'Knowledge publications served'),
      pl.kind === Outcome.OK ? h('ul', { class: 'pub-list' }, pl.items.map((p) => h('li', {},
        h('span', { class: 'mono' }, p.publication_id), ` · view ${p.view_date} · completed ${instant(p.watermarks?.published_at)} `,
        p.publication_id === pub.publication_id ? h('span', { class: 'badge badge-ok' }, 'Bound') : p.stale ? h('span', { class: 'badge' }, 'Superseded') : null))) : outcomeState(pl, { compact: true }),
      pl.kind === Outcome.OK && pl.items.some((p) => p.view_date !== pub.view_date)
        ? h('p', { class: 'muted' }, 'Earlier knowledge views are listed for transparency only. Their issue drill-through has a known membership-lookup defect (AI owner) and their AFCA and continuity sections are not calculated, so this dashboard does not switch to them. Reconstructions of earlier months in the bound view are not alerts issued at those dates.')
        : null);
  }

  async route(loc, { fresh }) {
    if (loc.section === 'afca') { this.router.go(this.link(`/impact/afca${loc.parts[1] ? `/${loc.parts[1]}` : ''}`), { replace: true }); return; }
    const prevLoc = this.loc;
    this.loc = loc;
    // History depth at which the current case page was entered; its Back control returns past any source/file steps.
    const casePath = loc.section === 'cases' && loc.parts[1] ? loc.parts.join('/') : null;
    if (!casePath) this.caseEntryDepth = null;
    else if (!prevLoc || prevLoc.parts.join('/') !== casePath) this.caseEntryDepth = history.state?.depth ?? null;
    this.toggleMenu(false);
    const notice = this.pendingNotice; this.pendingNotice = null;
    const afca = loc.section === 'impact' && loc.parts[1] === 'afca';
    for (const a of this.nav.querySelectorAll('a[data-section]')) {
      if (a.dataset.section === loc.section && !afca) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.setAttribute('href', this.link(`/${a.dataset.section}`));
    }
    for (const a of this.nav.querySelectorAll('a[data-path]')) {
      if (afca) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.setAttribute('href', this.link(a.dataset.path));
    }
    this.slots.abortAll();
    closeAllDrawers();
    const guard = this.slots.begin('route');
    if (!this.main.firstChild || fresh) mount(this.main, loading('Loading published results'));
    const b = await this.store.ensure(guard.signal);
    if (!guard.isCurrent()) return;
    if (b.kind !== Outcome.OK) {
      this.selectedBucket = null;
      this.renderScopeBar(null, null);
      this.renderStrip();
      const dest = DESTINATIONS.find((d) => d.section === loc.section) || DESTINATIONS[0];
      mount(this.main, h('h1', {}, dest.label), outcomeState(b), connectionNote(this.config));
      return;
    }
    const s = this.scope(loc.params);
    const bk = await this.buckets(s.grain, guard.signal);
    if (!guard.isCurrent()) return;
    if (bk.kind !== Outcome.OK) {
      this.renderScopeBar(null, null); this.renderStrip();
      mount(this.main, h('h1', {}, 'Published results unavailable'), outcomeState(bk));
      return;
    }
    const selected = bk.buckets[selectedIndex(bk.buckets, s.period)];
    this.selectedBucket = selected;
    this.renderScopeBar(bk.buckets, selected);
    this.renderStrip();
    const views = {
      overview: renderOverview,
      issues: loc.parts[1] ? renderIssue : renderIssues,
      experience: renderExperience,
      impact: afca ? renderAfca : renderImpact,
      cases: loc.parts[1] ? renderCase : renderCases,
    };
    const view = views[loc.section];
    if (!view) {
      mount(this.main, h('h1', {}, 'Not found'), h('p', {}, h('a', { href: this.link('/overview'), 'data-nav': '' }, 'Go to Executive Overview')));
      return;
    }
    const ctx = { scope: s, buckets: bk.buckets, selected, arrivals: bk.arrivals, signal: guard.signal, isCurrent: guard.isCurrent };
    try {
      await view(this, loc, ctx);
    } catch (e) {
      if (e?.name === 'AbortError') return;
      if (guard.isCurrent()) mount(this.main, stateBox('internal', 'This view could not be rendered', String(e?.message || e)));
      throw e;
    }
    if (this.loc !== loc || !guard.isCurrent()) return;
    if (notice) this.main.prepend(h('div', { class: 'notice', role: 'status' }, notice));
    openPopulationFromUrl(this, loc, ctx);
    openOverlays(this, loc);
    const overlay = (l) => !!l && ['pop', 'src', 'asset'].some((k) => l.params.get(k));
    if (overlay(prevLoc) && !overlay(loc) && prevLoc.parts.join('/') === loc.parts.join('/')) this.restoreActivation();
  }

  /** Focus the control that opened the drawer just closed (matched by focus key, else by link target). */
  restoreActivation() {
    const a = this.openers.get(this.loc.parts.join('/'));
    if (!a) return;
    const el = (a.key && this.main.querySelector(`[data-focus-key="${CSS.escape(a.key)}"]`))
      || (a.href && [...this.main.querySelectorAll('a[href]')].find((x) => x.getAttribute('href') === a.href));
    if (el) el.focus({ preventScroll: false });
  }
}

function connectionNote(config) {
  return h('section', { class: 'panel' },
    h('h2', {}, 'Connection'),
    h('ul', {},
      h('li', {}, config.transport_configured ? 'Gateway: connected to the configured analysis service.' : 'Gateway: no analysis service is configured; nothing is shown in its place.'),
      h('li', {}, config.intelligence_schema ? `Intelligence companion ${config.intelligence_schema} schema verified.` : 'Intelligence companion schema unavailable to this UI.'),
      h('li', {}, 'Sign-in uses the existing demonstration session of the analysis service. This UI holds no credentials.')));
}

// ---- boot -----------------------------------------------------------------
export async function boot() {
  const config = await (await fetch('/config.json', { credentials: 'same-origin' })).json();
  const registry = new SchemaRegistry();
  for (const name of ['runtime.schema.json', 'shared.schema.json', 'intelligence-v1.schema.json']) {
    const res = await fetch(`/contracts/${name}`);
    if (res.ok) registry.add(await res.json());
  }
  setSignInUrl(config.signin_url);
  const app = new App({ config, registry, transport: fetchTransport(config.operation_path) });
  app.renderShell();
  app.router.start();
  window.__app = app; // inspection handle; holds no credentials
  return app;
}
