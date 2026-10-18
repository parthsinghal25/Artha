// Publication-bound read store. One coherent binding (context + publication)
// serves a whole navigation journey: companion sections and Shared drill-through
// operations all run in that binding's context. Every section is fully paged
// before use (a missing page means incomplete, never a smaller population).
// Results are cached by publication, permission revision, section, issue,
// grain and request; a response from a different publication is withheld, and
// a newly completed publication is offered as one whole-dashboard swap — never
// mixed with results already on screen. Nothing here calls a model.

import { Outcome } from './client.js';

export const VIEW_DATE = '2026-09-30';
const TEMPORAL_POLICY = 'demo-held-day-v1';
const EXPIRY_MARGIN_MS = 15_000;
const PAGE = 100;
const MAX_PAGES = 40;

export class IntelStore extends EventTarget {
  constructor({ client, viewDate = VIEW_DATE, now = () => Date.now() }) {
    super();
    this.client = client;
    this.viewDate = viewDate;
    this.now = now;
    this.binding = null; // { context, publication }
    this.pending = null; // a different completed publication seen while this binding is in use
    this.cache = new Map();
    this.inflight = new Map();
    this.requests = 0; // operations sent (inspection aid; companion reads never run inference)
    client.onAuthorityChange = () => this.invalidateAuthority();
  }

  invalidateAuthority() {
    this.binding = null;
    this.pending = null;
    this.cache.clear();
    this.inflight.clear();
    this.dispatchEvent(new Event('authority-changed'));
  }

  selection(section, extra = {}) {
    const s = { temporal_policy: TEMPORAL_POLICY, view_date: this.viewDate, section };
    if (extra.issue_id) s.issue_id = extra.issue_id;
    if (extra.grain) s.grain = extra.grain;
    return s;
  }

  async send(request, signal) {
    this.requests += 1;
    return this.client.call(request, { signal });
  }

  /** Establish the binding from the served current publication for the knowledge view. */
  async ensure(signal) {
    if (this.binding && Date.parse(this.binding.context.expires_at) - this.now() > EXPIRY_MARGIN_MS) return { kind: Outcome.OK, binding: this.binding };
    const out = await this.send({ operation: 'intelligence.get', context_id: null, selection: this.selection('coverage'), cursor: null, page_size: 1 }, signal);
    if (out.kind !== Outcome.OK) return out;
    const pub = out.payload.publication;
    if (this.binding && pub.publication_id !== this.binding.publication.publication_id) {
      // The served publication changed under us: keep the bound results, offer the new one whole.
      this.pending = { context: out.context, publication: pub };
      this.dispatchEvent(new Event('publication-available'));
      return { kind: 'publication_changed', publication: pub, bound: this.binding.publication };
    }
    if (this.binding && out.context.permission_revision !== this.binding.context.permission_revision) {
      this.invalidateAuthority();
      return { kind: 'authority_changed' };
    }
    this.binding = { context: out.context, publication: pub };
    return { kind: Outcome.OK, binding: this.binding };
  }

  /** Adopt a pending publication: everything is reloaded under it together. */
  adoptPending() {
    if (!this.pending) return false;
    this.binding = this.pending;
    this.pending = null;
    this.cache.clear();
    this.inflight.clear();
    this.dispatchEvent(new Event('binding-changed'));
    return true;
  }

  key(parts) {
    const b = this.binding;
    return `${b.publication.publication_id}|${b.context.permission_revision}|${JSON.stringify(parts)}`;
  }

  async cached(parts, load, signal) {
    const r = await this.ensure(signal);
    if (r.kind !== Outcome.OK) return r;
    const k = this.key(parts);
    if (this.cache.has(k)) return this.cache.get(k);
    if (this.inflight.has(k)) return this.inflight.get(k);
    const p = (async () => {
      const out = await load();
      if (out.kind === Outcome.OK) this.cache.set(k, out);
      return out;
    })();
    this.inflight.set(k, p);
    try { return await p; } finally { this.inflight.delete(k); }
  }

  /** Run one request in the binding context, re-resolving once if the context expired. */
  async inContext(build, signal) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const r = await this.ensure(signal);
      if (r.kind !== Outcome.OK) return r;
      const out = await this.send(build(this.binding.context), signal);
      if (out.kind === Outcome.UNAVAILABLE && out.error?.code === 'CONTEXT_EXPIRED' && attempt === 0) {
        this.binding = { ...this.binding, context: { ...this.binding.context, expires_at: new Date(0).toISOString() } };
        continue;
      }
      return out;
    }
    return { kind: Outcome.UNAVAILABLE, error: { code: 'CONTEXT_EXPIRED', message: 'Context expired twice', retryable: true, correlation_id: 'client' } };
  }

  /** A companion section, fully paged and checked to belong to the bound publication. */
  section(section, { issue_id = null, grain = null } = {}, { signal } = {}) {
    return this.cached(['intel', section, issue_id, grain], async () => {
      const items = [];
      let cursor = null; let first = null; let pages = 0;
      do {
        const out = await this.inContext((ctx) => ({ operation: 'intelligence.get', context_id: ctx.context_id, selection: this.selection(section, { issue_id, grain }), cursor, page_size: PAGE }), signal);
        if (out.kind !== Outcome.OK) return out;
        const pub = out.payload.publication;
        if (pub.publication_id !== this.binding.publication.publication_id) {
          this.pending = { context: out.context, publication: pub };
          this.dispatchEvent(new Event('publication-available'));
          return { kind: 'publication_changed', publication: pub, bound: this.binding.publication };
        }
        first = first || out;
        items.push(...out.payload.items);
        cursor = out.payload.next_cursor;
        pages += 1;
        if (pages > MAX_PAGES) return { kind: 'incomplete', reason: 'page limit reached', section };
      } while (cursor);
      if (items.length !== first.payload.total_items) return { kind: 'incomplete', reason: `received ${items.length} of ${first.payload.total_items} items`, section };
      return { kind: Outcome.OK, publication: first.payload.publication, context: first.context, items, detail: first.payload.detail || null, total: first.payload.total_items, pages };
    }, signal);
  }

  /** case.memory.get for the bound publication (all claim pages). */
  memory(caseId, { signal } = {}) {
    return this.cached(['memory', caseId], async () => {
      const items = [];
      let cursor = null; let first = null; let pages = 0;
      do {
        const out = await this.inContext((ctx) => ({ operation: 'case.memory.get', context_id: ctx.context_id, case_id: caseId, memory_revision_id: null, cursor, page_size: PAGE }), signal);
        if (out.kind !== Outcome.OK) return out;
        if (out.payload.publication.publication_id !== this.binding.publication.publication_id) return { kind: 'publication_changed', publication: out.payload.publication, bound: this.binding.publication };
        first = first || out;
        items.push(...out.payload.items);
        cursor = out.payload.next_cursor;
        pages += 1;
        if (pages > MAX_PAGES) return { kind: 'incomplete', reason: 'page limit reached', section: 'memory' };
      } while (cursor);
      return { kind: Outcome.OK, publication: first.payload.publication, items, detail: first.payload.detail || {}, total: first.payload.total_items };
    }, signal);
  }

  /** A frozen Shared operation in the binding context (cached when ok unless `cache: false`). */
  shared(build, { signal, cache = true } = {}) {
    if (!cache) return this.inContext(build, signal);
    const probe = build({ context_id: '§' });
    return this.cached(['shared', probe], () => this.inContext(build, signal), signal);
  }

  /**
   * Check updates: read publication metadata only (no ingestion, no inference).
   * Returns the served publication and whether it differs from the bound one.
   */
  async checkUpdates(signal) {
    const out = await this.send({ operation: 'intelligence.get', context_id: null, selection: this.selection('publications'), cursor: null, page_size: 100 }, signal);
    if (out.kind !== Outcome.OK) return out;
    const served = out.payload.publication;
    const bound = this.binding?.publication;
    if (bound && served.publication_id !== bound.publication_id) {
      this.pending = { context: out.context, publication: served };
      this.dispatchEvent(new Event('publication-available'));
      return { kind: 'new', served, bound, list: out.payload.items };
    }
    return { kind: 'same', served, list: out.payload.items };
  }
}
