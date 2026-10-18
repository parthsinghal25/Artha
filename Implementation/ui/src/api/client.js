// Typed client for the twelve frozen Shared 1.0.0 operations and the two
// backend-owned additive intelligence companion 1.1.0 read operations
// (`intelligence.get`, `case.memory.get`). Each family is validated against its
// own schema; the frozen Shared validator is never widened to accept companion
// payloads.
//
// Every request is validated against `<operation>.request` before sending and
// every response against `<operation>.response` before the UI may render it. A
// non-conforming payload becomes an explicit `contract_violation` outcome and is
// never displayed. The client holds no credentials: the backend owns the
// session (HttpOnly cookie) and the double-submit CSRF token, which the client
// only echoes from the readable `sdb_csrf` cookie as `X-CSRF-Token` on each
// operations POST (backend INTEGRATION-ENVIRONMENT §2). It never invents,
// stores or logs a token, and never retries a refused request without one.

import { SHARED_ID, INTEL_ID } from '../lib/schema.js';
import { verifyBinding } from '../lib/digest.js';

export const OPERATIONS = [
  'context.resolve', 'analysis.query', 'findings.list', 'contributors.list', 'taxonomy.get', 'case.get',
  'case.sources.list', 'case.files.list', 'entity.get', 'source.get', 'asset.get', 'location.resolve',
];
export const COMPANION_OPERATIONS = ['intelligence.get', 'case.memory.get'];

/** Outcome kinds a caller must handle. */
export const Outcome = Object.freeze({
  OK: 'ok',
  UNAVAILABLE: 'unavailable', // contract `status: unavailable` with typed error
  TRANSPORT: 'transport', // no backend / unreachable / unauthenticated
  CONTRACT_VIOLATION: 'contract_violation',
  ABORTED: 'aborted',
});

export const CSRF_COOKIE = 'sdb_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

/** Value of one readable cookie, or null. */
export function readCookie(cookieString, name) {
  for (const part of (cookieString || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === name) {
      try { return decodeURIComponent(part.slice(i + 1).trim()) || null; } catch { return null; }
    }
  }
  return null;
}

/**
 * Bounded parallelism for operations. Each read waits on per-request state lookups, and the hosted edge
 * (Firebase → Cloud Run) ends any request after 60 s; firing every per-issue read at once would queue the last
 * ones past that limit. Requests wait their turn in the browser instead (order kept; aborts honoured).
 */
export function limiter(max) {
  let active = 0; const queue = [];
  const next = () => { while (active < max && queue.length) { const job = queue.shift(); if (!job.cancelled) { active += 1; job.start(); } } };
  return (fn, signal) => new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const job = { cancelled: false, start: () => Promise.resolve().then(fn).then(resolve, reject).finally(() => { active -= 1; next(); }) };
    signal?.addEventListener('abort', () => { if (!job.cancelled && queue.includes(job)) { job.cancelled = true; queue.splice(queue.indexOf(job), 1); reject(new DOMException('Aborted', 'AbortError')); } }, { once: true });
    queue.push(job); next();
  });
}
export const MAX_IN_FLIGHT = 16;

/**
 * Same-origin operations transport. `fetchImpl` and `cookies` are seams for
 * tests; in the app they are the browser's fetch and document.cookie.
 */
export function fetchTransport(operationPath, { fetchImpl = (...a) => fetch(...a), cookies = () => document.cookie, maxInFlight = MAX_IN_FLIGHT } = {}) {
  const gate = limiter(maxInFlight);
  return (body, signal) => gate(() => send(body, signal), signal);
  async function send(body, signal) {
    let res;
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
    const token = readCookie(cookies(), CSRF_COOKIE);
    if (token) headers[CSRF_HEADER] = token; // absent token: the backend answers 401/403 and the UI says so
    try {
      res = await fetchImpl(operationPath, {
        method: 'POST',
        credentials: 'same-origin',
        headers,
        body: JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      return { httpStatus: 0, json: null };
    }
    let json = null;
    try { json = await res.json(); } catch { json = null; }
    return { httpStatus: res.status, json };
  }
}

export class ApiClient {
  /**
   * @param {{registry: import('../lib/schema.js').SchemaRegistry, transport: Function, onAuthorityChange?: Function}} opts
   */
  constructor({ registry, transport, onAuthorityChange }) {
    this.registry = registry;
    this.transport = transport;
    this.onAuthorityChange = onAuthorityChange || (() => {});
    this.lastRevision = null;
  }

  async call(request, { signal } = {}) {
    const op = request.operation;
    if (COMPANION_OPERATIONS.includes(op)) return this.callCompanion(request, { signal });
    if (!OPERATIONS.includes(op)) throw new Error(`unknown operation ${op}`);
    const reqErrors = this.registry.validateDef(SHARED_ID, `${op}.request`, request);
    if (reqErrors.length) {
      // A malformed request is a UI defect; surface it rather than sending.
      return { kind: Outcome.CONTRACT_VIOLATION, side: 'request', errors: reqErrors };
    }
    let raw;
    try {
      raw = await this.transport(request, signal);
    } catch (e) {
      if (e.name === 'AbortError') return { kind: Outcome.ABORTED };
      return { kind: Outcome.TRANSPORT, reason: 'unreachable' };
    }
    if (signal?.aborted) return { kind: Outcome.ABORTED };
    const { httpStatus, json } = raw;
    if (json && json.transport === 'not_configured') return { kind: Outcome.TRANSPORT, reason: 'not_configured' };
    // 401 = no or expired session; 403 = missing/incorrect CSRF token or refused origin. Transport, not contract.
    if (httpStatus === 401 && (!json || json.operation !== op)) return { kind: Outcome.TRANSPORT, reason: 'unauthenticated' };
    if (httpStatus === 403 && (!json || json.operation !== op)) return { kind: Outcome.TRANSPORT, reason: 'forbidden' };
    if (!json || typeof json !== 'object' || json.operation === undefined) {
      return { kind: Outcome.TRANSPORT, reason: httpStatus === 0 ? 'unreachable' : 'http_error', httpStatus };
    }
    const resErrors = this.registry.validateDef(SHARED_ID, `${op}.response`, json);
    if (resErrors.length || json.operation !== op) {
      return { kind: Outcome.CONTRACT_VIOLATION, side: 'response', errors: resErrors.length ? resErrors : ['operation mismatch'] };
    }
    const semantic = [...semanticChecks(request, json), ...(await bindingChecks(request, json))];
    if (semantic.length) return { kind: Outcome.CONTRACT_VIOLATION, side: 'response', errors: semantic };
    if (json.context && json.context.permission_revision) {
      if (this.lastRevision && this.lastRevision !== json.context.permission_revision) {
        this.onAuthorityChange(this.lastRevision, json.context.permission_revision);
      }
      this.lastRevision = json.context.permission_revision;
    }
    if (json.status === 'ok') return { kind: Outcome.OK, context: json.context, payload: json.payload, requestId: json.request_id };
    return { kind: Outcome.UNAVAILABLE, context: json.context, error: json.error, requestId: json.request_id };
  }

  /** Companion 1.1.0 read: request and response validated against the companion schema only. */
  async callCompanion(request, { signal } = {}) {
    const op = request.operation;
    if (!this.registry.docs.has(INTEL_ID)) return { kind: Outcome.CONTRACT_VIOLATION, side: 'request', errors: ['intelligence companion schema 1.1.0 is not available to this UI'] };
    const reqErrors = this.registry.validateDef(INTEL_ID, `${op}.request`, request);
    if (reqErrors.length) return { kind: Outcome.CONTRACT_VIOLATION, side: 'request', errors: reqErrors };
    let raw;
    try {
      raw = await this.transport(request, signal);
    } catch (e) {
      if (e.name === 'AbortError') return { kind: Outcome.ABORTED };
      return { kind: Outcome.TRANSPORT, reason: 'unreachable' };
    }
    if (signal?.aborted) return { kind: Outcome.ABORTED };
    const { httpStatus, json } = raw;
    if (json && json.transport === 'not_configured') return { kind: Outcome.TRANSPORT, reason: 'not_configured' };
    if (httpStatus === 401 && (!json || json.operation !== op)) return { kind: Outcome.TRANSPORT, reason: 'unauthenticated' };
    if (httpStatus === 403 && (!json || json.operation !== op)) return { kind: Outcome.TRANSPORT, reason: 'forbidden' };
    if (!json || typeof json !== 'object' || json.operation === undefined) {
      return { kind: Outcome.TRANSPORT, reason: httpStatus === 0 ? 'unreachable' : 'http_error', httpStatus };
    }
    const shape = json.status === 'ok' ? 'ok_response' : 'error_response';
    const resErrors = this.registry.validateDef(INTEL_ID, shape, json);
    if (json.operation !== op) resErrors.push('operation mismatch');
    if (json.status === 'ok') {
      if (request.context_id && json.context?.context_id !== request.context_id) resErrors.push('response context differs from requested context');
      if (!json.context?.context_id || !json.context?.release_id) resErrors.push('companion response carries no usable context');
      if (json.payload.publication.analysis_set_version !== json.context?.analysis_set_version) resErrors.push('publication analysis set differs from its context');
      if (request.selection?.view_date && json.payload.publication.view_date !== request.selection.view_date) resErrors.push(`publication for ${json.payload.publication.view_date} returned for view ${request.selection.view_date}`);
      if (request.selection?.section && json.payload.section !== request.selection.section) resErrors.push('section mismatch');
    }
    if (resErrors.length) return { kind: Outcome.CONTRACT_VIOLATION, side: 'response', errors: resErrors };
    if (json.context && json.context.permission_revision) {
      if (this.lastRevision && this.lastRevision !== json.context.permission_revision) this.onAuthorityChange(this.lastRevision, json.context.permission_revision);
      this.lastRevision = json.context.permission_revision;
    }
    if (json.status === 'ok') return { kind: Outcome.OK, context: json.context, payload: json.payload, requestId: json.request_id };
    return { kind: Outcome.UNAVAILABLE, context: json.context, error: json.error, requestId: json.request_id };
  }
}

/**
 * Cross-field checks that the schema shape alone cannot express but the UI
 * depends on to keep populations exact. These verify safe consumption; they
 * are not a reimplementation of backend acceptance.
 */
export function semanticChecks(request, response) {
  const errs = [];
  if (response.status !== 'ok') return errs;
  const p = response.payload;
  if (request.context_id && response.context && response.context.context_id !== request.context_id) {
    // A refreshed context id is acceptable only if the server says so via a
    // new resolve; operations must answer in the context they were asked.
    errs.push('response context differs from requested context');
  }
  errs.push(...knowledgeChecks(request, response));
  switch (request.operation) {
    case 'taxonomy.get':
      if (p.taxonomy_version !== request.taxonomy_version) errs.push(`taxonomy ${p.taxonomy_version} returned for requested ${request.taxonomy_version}`);
      if (response.context?.taxonomy_version && p.taxonomy_version !== response.context.taxonomy_version) errs.push('taxonomy differs from the context taxonomy version');
      break;
    case 'analysis.query': {
      const tv = response.context?.taxonomy_version ?? null;
      const used = [p.result_context.versions.taxonomy_version, p.result_context.predicate.taxonomy_version, ...p.cells.map((c) => c.population.versions.taxonomy_version)].filter(Boolean);
      if (used.some((v) => v !== tv)) errs.push(`result uses taxonomy ${used.find((v) => v !== tv)}, context declares ${tv ?? 'none'}`);
      for (const cell of p.cells) {
        if (cell.population_id !== cell.population.population_id) errs.push(`cell ${cell.key}: population id mismatch`);
        if (cell.population.predicate.population_sha256 !== cell.population.predicate_sha256) errs.push(`cell ${cell.key}: predicate digest mismatch`);
        const ref = cell.point.contributor_set_reference;
        if ((cell.contributor_set_id === null) !== (ref === null) || (ref !== null && ref !== cell.contributor_set_id)) {
          errs.push(`cell ${cell.key}: contributor set reference mismatch`);
        }
      }
      const keys = new Set(p.cells.map((c) => c.key));
      if (keys.size !== p.cells.length) errs.push('duplicate cell keys');
      if (request.definition && p.result_context.predicate.definition.measure_id !== request.definition.measure_id) {
        errs.push('result definition differs from requested definition');
      }
      break;
    }
    case 'contributors.list': {
      if (p.result_id !== request.result_id || p.cell_key !== request.cell_key) errs.push('contributor page for a different result/cell');
      if (p.population.predicate_sha256 !== p.predicate_sha256) errs.push('contributor page digest mismatch');
      const ids = new Set(p.items.map((i) => i.entity_id));
      if (ids.size !== p.items.length) errs.push('duplicate contributors on page');
      if (p.items.length > 100) errs.push('page exceeds 100 items');
      break;
    }
    case 'case.get':
      if (p.case_record.kind !== 'case' || p.case_record.data.case_id !== request.case_id) errs.push('case payload is not the requested case');
      if (p.state_projection.case_id !== request.case_id) errs.push('state projection for a different case');
      break;
    case 'source.get':
      if (p.record.kind !== 'source' || p.record.data.source_id !== request.source_id) errs.push('source payload is not the requested source');
      if (request.content_sha256 && p.source_binding.content_sha256 !== request.content_sha256) errs.push('source version differs from requested hash');
      break;
    case 'case.sources.list':
    case 'case.files.list':
      if (p.case_id !== request.case_id) errs.push('inventory for a different case');
      break;
    case 'entity.get':
      if (p.entity_kind !== request.entity_kind) errs.push('entity kind mismatch');
      if (request.immutable_record_id && p.immutable_record_id !== request.immutable_record_id) errs.push('immutable record mismatch');
      break;
    case 'asset.get':
      if (p.asset_version_id !== request.asset_version_id) errs.push('asset action for a different asset');
      break;
    default:
      break;
  }
  return errs;
}

// ---- knowledge-boundary consistency (CV-01) ------------------------------------
// A consumer check, not reconstruction: anything a response says became known
// after its own context's evidence cutoff (respecting the boundary) means later
// evidence leaked into an earlier view, so the response is withheld.

const dayFmt = new Map();
/** Calendar date of an instant in a time zone (YYYY-MM-DD). */
export function zonedDate(iso, timeZone = 'Australia/Melbourne') {
  if (!dayFmt.has(timeZone)) dayFmt.set(timeZone, new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }));
  return dayFmt.get(timeZone).format(new Date(iso));
}

/** Last calendar date fully known in a context (an exclusive local-midnight endpoint knows the day before). */
export function contextKnownDate(ctx) {
  const tz = ctx.timezone || 'Australia/Melbourne';
  const d = zonedDate(ctx.evidence_as_of, tz);
  if (ctx.evidence_boundary !== 'exclusive_stock_endpoint') return d;
  const before = zonedDate(new Date(Date.parse(ctx.evidence_as_of) - 1).toISOString(), tz);
  return before !== d ? before : d;
}

export function instantKnownIn(iso, ctx) {
  const t = Date.parse(iso); const c = Date.parse(ctx.evidence_as_of);
  return ctx.evidence_boundary === 'exclusive_stock_endpoint' ? t < c : t <= c;
}

function timeKnownIn(tv, ctx) {
  if (!tv) return true;
  if (tv.precision === 'instant') return instantKnownIn(tv.instant, ctx);
  if (tv.precision === 'day') return tv.date <= contextKnownDate(ctx);
  return true; // coarser or unknown precision: not decidable here
}

export function knowledgeChecks(request, response) {
  const ctx = response.context;
  if (response.status !== 'ok' || !ctx?.evidence_as_of) return [];
  const p = response.payload;
  const errs = [];
  const late = (what) => errs.push(`${what} is later than this context's evidence cutoff (${ctx.evidence_as_of}, ${ctx.evidence_boundary})`);
  const finding = (rec, label) => {
    if (rec?.kind === 'finding' && rec.data?.assessed_snapshot && !instantKnownIn(rec.data.assessed_snapshot, ctx)) late(`${label} ${rec.data.finding_id} assessment`);
    if (rec?.membership_version_id && rec.assessed_evidence_as_of && !instantKnownIn(rec.assessed_evidence_as_of, ctx)) late(`membership ${rec.membership_version_id} assessment`);
  };
  switch (request.operation) {
    case 'case.get':
      if (!instantKnownIn(p.case_record.envelope.available_at, ctx)) late('case record');
      // An "assessed as of" value names an evidence boundary, not an observation: assessing
      // as of the context's own (exclusive) endpoint is consistent; only a later boundary leaks.
      if (Date.parse(p.state_projection.assessed_evidence_as_of) > Date.parse(ctx.evidence_as_of)) late('case state projection');
      for (const e of p.events) if (!timeKnownIn(e.occurrence, ctx)) late(`event ${e.event_id}`);
      break;
    case 'case.sources.list':
      for (const s of p.items) if (!instantKnownIn(s.scenario_available_at, ctx)) late(`source ${s.source_id}`);
      break;
    case 'case.files.list':
      for (const f of p.items) if (!instantKnownIn(f.asset.scenario_available_at, ctx)) late(`file ${f.asset.asset_version_id}`);
      break;
    case 'source.get':
      if (!instantKnownIn(p.record.envelope.available_at, ctx)) late(`source ${p.record.data?.source_id}`);
      break;
    case 'entity.get':
      finding(p.record, 'finding');
      break;
    case 'findings.list':
      for (const r of p.items) finding(r, 'finding');
      break;
    default:
      break;
  }
  return errs;
}

/** Verify every immutable population binding the UI may navigate with. */
export async function bindingChecks(request, response) {
  if (response.status !== 'ok') return [];
  const p = response.payload;
  if (request.operation === 'analysis.query') {
    const all = await Promise.all(p.cells.map((c) => verifyBinding(c.population, `cell ${c.key}`)));
    return all.flat();
  }
  if (request.operation === 'contributors.list') return verifyBinding(p.population, 'contributor page');
  return [];
}

/** Monotonic slot guard: only the latest request for a slot may apply its result. */
export class RequestSlots {
  constructor() { this.slots = new Map(); }

  begin(slot) {
    const prev = this.slots.get(slot);
    if (prev) prev.controller.abort();
    const entry = { controller: new AbortController(), seq: (prev?.seq || 0) + 1 };
    this.slots.set(slot, entry);
    const seq = entry.seq;
    return {
      signal: entry.controller.signal,
      isCurrent: () => this.slots.get(slot)?.seq === seq && !entry.controller.signal.aborted,
    };
  }

  abortAll() {
    for (const e of this.slots.values()) e.controller.abort();
    this.slots.clear();
  }
}
