// Case detail: Case Chronology (recorded events with date precision, actor,
// short proposition and citation) beside Case Context (issue memberships with
// reasons, administrative/service/member-response distinctions, per-action
// service state, recorded impact, coverage). Model interpretation stays
// separate from recorded events. Sources and original files are independent,
// paged card groups; selecting either opens the proven source/original viewer.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { timeline, plural } from '../ui/viz.js';
import { ids } from '../lib/calc.js';
import { audMinor, count, bytes, instant, humanise, shortHash } from '../lib/format.js';
import { fmtDate, knownThrough, melbourneMidnightUtc } from '../lib/periods.js';
import { label, popHref } from './model.js';
import { sharedCell, evidenceChip, short, serviceSummary } from './population.js';
import { loading, outcomeState, stateBox } from '../ui/states.js';
import { info } from '../ui/labels.js';
import { icon } from '../ui/icons.js';

const VIEWS = [['all', 'All chronology'], ['communications', 'Communications'], ['money', 'Financial & work'], ['afca', 'AFCA']];

export async function renderCase(app, loc, ctx) {
  const caseId = decodeURIComponent(loc.parts[1]);
  const casePath = `/cases/${encodeURIComponent(caseId)}`;
  const [cg, mem, iss, af] = await Promise.all([
    allEvents(app, caseId, ctx.signal),
    app.store.memory(caseId, { signal: ctx.signal }),
    app.sec('issues', {}, ctx.signal),
    app.sec('afca', {}, ctx.signal)]);
  if (!ctx.isCurrent()) return;
  const back = loc.params.get('o') || loc.params.get('ob')
    ? h('button', { type: 'button', class: 'back-link', onclick: () => {
      const entry = app.caseEntryDepth; const depth = history.state?.depth;
      if (entry > 0 && depth >= entry) { history.go(-(depth - entry + 1)); return; } // restores the origin's scroll, focus and open selection
      const ob = loc.params.get('ob');
      if (ob && ob.startsWith('/') && !ob.startsWith('//')) app.router.go(ob); else app.router.go(app.link('/cases'));
    } }, icon('back'), `Back to ${loc.params.get('ot') || 'the selection'}`)
    : h('a', { href: app.link('/cases'), 'data-nav': '', class: 'back-link' }, icon('back'), 'Cases & Evidence');
  if (cg.kind !== Outcome.OK) { mount(app.main, back, h('h1', {}, `Case ${short(caseId)}`), outcomeState(cg)); return; }
  const p = cg.payload; const d = p.case_record.data; const sp = p.state_projection;
  const m = mem.kind === Outcome.OK ? mem : null;
  const claims = new Map((m?.items || []).map((c) => [c.claim_id, c]));
  const view = VIEWS.some(([v]) => v === loc.params.get('cv')) ? loc.params.get('cv') : 'all';
  const matters = af.kind === Outcome.OK ? af.items.filter((x) => x.case_ids.includes(caseId)) : [];
  const desc = loc.params.get('o');

  const chron = chronology(app, caseId, p.events, matters, view, desc);
  const impactSlot = h('div', { class: 'stack' }, loading('Requesting recorded impact'));
  mount(app.main,
    back,
    h('div', { class: 'page-head' },
      h('h1', {}, `Case ${short(caseId)}`),
      h('p', { class: 'page-sub mono' }, caseId),
      h('div', { class: 'chips' },
        h('span', { class: 'chip' }, `Received ${fmtDate(d.original_receipt_date)}${d.complaints_receipt_date && d.complaints_receipt_date !== d.original_receipt_date ? ` · complaints team ${fmtDate(d.complaints_receipt_date)}` : ''}`),
        h('span', { class: 'chip' }, humanise(d.product_or_journey || 'journey not stated')),
        h('span', { class: 'chip' }, `Channel ${d.channel || 'unknown'}`),
        m ? h('span', { class: 'chip' }, `Member ${short(m.detail.member_references?.[0] || 'unknown')}`) : null,
        m ? h('span', { class: 'badge badge-prov' }, `Model-assessed memory r${m.detail.revision}`) : h('span', { class: 'badge' }, 'Not assessed (holdout, pending assessment or withheld) — source and native inspection only'))),
    h('div', { class: 'grid g-8-4 case-grid' },
      h('section', { class: 'panel', 'aria-labelledby': 'h-chron' },
        h('div', { class: 'panel-head' }, h('h2', { id: 'h-chron' }, 'Case Chronology'), h('span', { class: 'panel-meta' }, `${p.events.length} of ${p.total_permitted_event_count} recorded events`)),
        h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Chronology view' }, VIEWS.map(([v, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': v === view ? 'true' : 'false', onclick: () => app.setLocal({ cv: v === 'all' ? null : v }) }, l))),
        chron,
        m?.detail.narrative?.length ? h('details', { class: 'interp' }, h('summary', {}, 'Model-assessed case summary (interpretation, not recorded events)'),
          h('ul', { class: 'notes' }, m.detail.narrative.map((n) => h('li', {}, n.text, ' ', h('span', { class: 'chips inline' }, n.claim_ids.slice(0, 2).map((cid) => { const c = claims.get(cid); return c ? evidenceChip(app, c.ref, c.quote, caseId, desc) : null; })))))) : null),
      h('aside', { class: 'stack', 'aria-label': 'Case context' },
        statePanel(sp, m),
        membershipPanel(app, caseId, m, iss, claims, desc),
        m ? servicePanel(app, caseId, m, claims, desc) : null,
        h('section', { class: 'panel' }, h('h2', {}, 'Recorded Impact'), impactSlot),
        matters.length ? h('section', { class: 'panel' }, h('h2', {}, 'AFCA'), matters.map((x) => h('p', {}, h('a', { href: app.link(`/impact/afca/${x.afca_matter_id}`), 'data-nav': '' }, `Matter ${short(x.afca_matter_id)}`), ` · ${humanise(x.current_stage)}${x.multi_case ? ` · shared by ${x.case_ids.length} cases` : ''}`))) : null,
        coveragePanel(m))),
    h('div', { class: 'grid g-6-6' }, ...(await sourcesAndFiles(app, casePath, caseId))));

  const start = melbourneMidnightUtc('2025-10-01'); const end = app.store.binding.publication.knowledge_end_exclusive;
  const ms = [['cash.remedies_paid', 'Paid compensation / remedies'], ['cash.expenses_paid', 'Paid complaint expenses'], ['work.recorded_hours', 'Recorded handling hours']];
  const outs = await Promise.all(ms.map(([mm]) => sharedCell(app, mm, [], start, end, ctx.signal, { case_ids: [caseId] })));
  if (!impactSlot.isConnected) return;
  mount(impactSlot,
    h('p', { class: 'muted small' }, 'Full year', info('This case’s held records over the publication window; shared records also count for other cases.', 'About recorded impact')),
    ms.map(([mm, l], i) => {
      const o = outs[i];
      if (o.kind !== Outcome.OK) return h('div', { class: 'mini-row' }, h('span', {}, l), outcomeState(o, { compact: true }));
      const pt = o.cell.point;
      const v = pt.value === null ? 'Not available' : pt.unit === 'AUD_minor' ? audMinor(pt.value) : `${Number(pt.value).toFixed(2)} h`;
      return h('div', { class: 'mini-row', dataset: { measure: mm } }, h('span', {}, l), h('strong', { class: 'tnum' }, v),
        o.items.length ? h('a', { href: popHref(app, `sqc~${mm}~${caseId}~${start}~${end}`), 'data-nav': '', class: 'card-link' }, plural(o.items.length, 'record')) : h('span', { class: 'muted small' }, pt.state));
    }));
}

async function allEvents(app, caseId, signal) {
  const events = []; let cursor = null; let first = null;
  do {
    const out = await app.store.shared((c) => ({ operation: 'case.get', context_id: c.context_id, case_id: caseId, cursor, page_size: 100 }), { signal });
    if (out.kind !== Outcome.OK) return out;
    first = first || out;
    events.push(...out.payload.events);
    cursor = out.payload.next_cursor;
  } while (cursor);
  return { ...first, payload: { ...first.payload, events } };
}

const COMMS = new Set(['contact', 'correspondence', 'letter', 'email', 'call', 'request', 'response', 'acknowledgement']);
const MONEY = /payment|financial|remedy|expense|work|fee|compensation/i;

function chronology(app, caseId, events, matters, view, desc) {
  const evs = [...events].sort((a, b) => occ(a).localeCompare(occ(b)));
  const filtered = evs.filter((e) => {
    if (view === 'all') return true;
    if (view === 'communications') return COMMS.has(e.event_type) || /contact|correspond/.test(e.event_type);
    if (view === 'money') return MONEY.test(e.event_type) || MONEY.test(e.proposition || '');
    if (view === 'afca') return /afca|external/i.test(e.event_type) || /AFCA|external dispute/i.test(e.proposition || '');
    return true;
  });
  const items = filtered.map((e) => ({
    date: occLabel(e), kind: humanise(e.event_type), tone: e.state === 'supported' ? 'a' : e.state === 'contradicted' ? 'w' : 'u',
    title: e.proposition || humanise(e.event_type), data: { event: e.event_id },
    chips: [e.state !== 'supported' ? h('span', { class: 'badge' }, humanise(e.state)) : null, ...(e.source_refs || []).slice(0, 2).map((r) => evidenceChip(app, r, null, caseId, desc))].filter(Boolean),
  }));
  if (view === 'afca') {
    for (const mt of matters) for (const s of mt.stage_history) items.push({ date: fmtDate(s.date), kind: 'AFCA stage', tone: 'b', title: `${humanise(s.stage)} — “${s.quote}”`, chips: [evidenceChip(app, s.ref, s.quote, caseId, desc)] });
  }
  const LIMIT = 12;
  const full = timeline(items, { empty: view === 'all' ? 'No permitted recorded events.' : 'No recorded events of this kind for this case.' });
  if (items.length <= LIMIT) return full;
  const rest = [...full.children].slice(LIMIT);
  for (const li of rest) li.hidden = true;
  const more = h('button', { type: 'button', class: 'button-secondary small', onclick: () => { for (const li of rest) li.hidden = false; more.remove(); } }, `Show all ${items.length} events`);
  return h('div', {}, full, more);
}

const occ = (e) => e.occurrence?.instant || e.occurrence?.date || e.occurrence?.start || '';
function occLabel(e) {
  const o = e.occurrence || {};
  if (o.precision === 'instant') return instant(o.instant);
  if (o.precision === 'day') return fmtDate(o.date);
  if (o.date) return `${fmtDate(o.date)} (${o.precision})`;
  return 'Date unknown';
}

function statePanel(sp, m) {
  const svc = sp.service || [];
  return h('section', { class: 'panel' }, h('h2', {}, 'Where this case stands', info('Status, service completion and member response are separate; closing a complaint is not proof the work was done.', 'About case state')),
    h('dl', { class: 'kv compact' },
      h('dt', {}, 'Administrative'), h('dd', {}, h('span', { class: `badge badge-admin-${sp.administrative?.value}` }, humanise(sp.administrative?.value || 'unknown')), ' ', h('span', { class: 'muted small' }, sp.administrative?.reason || '')),
      h('dt', {}, 'Service actions'), h('dd', {}, serviceSummary(svc)),
      h('dt', {}, 'Member response'), h('dd', {}, (sp.responses || []).length ? sp.responses.map((r) => h('span', { class: 'badge' }, `${humanise(r.target_reference || 'target')}: ${humanise(r.value)}`)) : (m?.detail.responses?.length ? m.detail.responses.map((r) => h('span', { class: 'badge' }, `${r.dimension}: ${humanise(r.position)}`)) : 'None recorded'))),
  );
}

function membershipPanel(app, caseId, m, iss, claims, desc) {
  if (!m) return h('section', { class: 'panel' }, h('h2', {}, 'Issue Memberships'), h('p', { class: 'muted' }, 'Not assessed in this publication: no issue is attributed. Not a negative finding.'));
  const ms = [...(m.detail.memberships || [])].sort((a, b) => ['included', 'uncertain', 'excluded', 'unassessed'].indexOf(a.state) - ['included', 'uncertain', 'excluded', 'unassessed'].indexOf(b.state));
  return h('section', { class: 'panel' }, h('h2', {}, 'Issues the AI found', info('Model-assessed against the publication’s definitions; reasons and citations as published.', 'About issue links')),
    h('ul', { class: 'ms-list' }, ms.map((x) => h('li', { dataset: { issue: x.issue_id, state: x.state } },
      h('div', {}, h('span', { class: `badge badge-ms-${x.state}` }, x.state), ' ', h('a', { href: app.link(`/issues/${x.issue_id}`, { tab: 'population', ms: x.state === 'included' ? 'included' : x.state }), 'data-nav': '' }, x.label)),
      x.state !== 'excluded' || x.reason ? h('details', {}, h('summary', {}, 'Reason and evidence'),
        h('p', {}, x.reason || 'No reason recorded'),
        h('div', { class: 'chips' }, [...(x.support_claim_ids || []).slice(0, 2).map((c) => ({ c, t: 'support' })), ...(x.contrary_claim_ids || []).slice(0, 2).map((c) => ({ c, t: 'contrary' }))].map(({ c, t }) => {
          const cl = claims.get(c); return cl ? h('span', { class: `cite-${t}` }, h('span', { class: 'muted small' }, t === 'support' ? 'Support ' : 'Contrary '), evidenceChip(app, cl.ref, cl.quote, caseId, desc)) : null;
        }))) : null))));
}

function servicePanel(app, caseId, m, claims, desc) {
  const s = m.detail.service || {};
  const cm = m.detail.commitments || [];
  const contra = m.detail.contradictions || [];
  const chip = (cid) => { const c = claims.get(cid); return c ? evidenceChip(app, c.ref, c.quote, caseId, desc) : null; };
  return h('section', { class: 'panel' }, h('h2', {}, 'Service Actions & Commitments'),
    (s.items || []).length ? h('ul', { class: 'svc-list' }, s.items.map((it) => h('li', {}, h('span', { class: 'badge' }, humanise(it.state)), ` ${it.action}`, it.completion_claim_ids?.length ? h('span', { class: 'chips inline' }, chip(it.completion_claim_ids[0])) : null))) : h('p', { class: 'muted' }, `Service state ${humanise(s.state || 'unknown')}; no per-action items recorded.`),
    cm.length ? h('ul', { class: 'svc-list' }, cm.map((c) => h('li', {}, h('span', { class: 'badge' }, humanise(c.status)), ` ${c.action}`, h('span', { class: 'muted small' }, ` · due ${c.due_date ? fmtDate(c.due_date) : 'not stated'}`), h('span', { class: 'chips inline' }, chip(c.promise_claim_id))))) : null,
    contra.length ? h('div', { class: 'conflict' }, h('p', { class: 'eyebrow' }, 'Conflicting evidence'), contra.map((x) => h('p', {}, x.note, ' ', h('span', { class: 'chips inline' }, x.claim_ids.map(chip))))) : null);
}

function coveragePanel(m) {
  if (!m) return null;
  const c = m.detail.coverage || {};
  return h('section', { class: 'panel' }, h('h2', {}, 'Evidence Coverage'),
    h('p', {}, h('span', { class: `badge${c.extraction === 'complete' ? ' badge-ok' : ' badge-warn'}` }, `Extraction ${humanise(c.extraction || 'unknown')}`), ' ', h('span', { class: 'badge' }, humanise(c.reading || c.state || ''))),
    c.partial_extraction_originals?.length ? h('p', { class: 'warn-text' }, `${c.partial_extraction_originals.length} original(s) partially extracted; the original remains inspectable.`) : null,
    c.failed_or_unextracted_originals?.length ? h('p', { class: 'warn-text' }, `${c.failed_or_unextracted_originals.length} original(s) failed or unextracted.`) : null,
    h('p', { class: 'muted small' }, `${c.narrative_sources ?? '?'} narrative and ${c.table_sources ?? '?'} table sources · ${m.detail.revision_history?.length ?? 0} memory revisions (latest reason: ${humanise(m.detail.reason || '')}) · ${c.note || ''}`));
}

const versionLabel = (v) => (v == null ? '' : /^v/i.test(String(v)) ? String(v) : `v${v}`);

/** Sources and original files: independent, paged card groups (a source is not necessarily a separate file). */
async function sourcesAndFiles(app, casePath, caseId) {
  const srcReq = (cursor) => (c) => ({ operation: 'case.sources.list', context_id: c.context_id, case_id: caseId, source_families: [], cursor, page_size: 12 });
  const fileReq = (cursor) => (c) => ({ operation: 'case.files.list', context_id: c.context_id, case_id: caseId, media_types: [], cursor, page_size: 12 });
  const [src, files] = await Promise.all([app.store.shared(srcReq(null)), app.store.shared(fileReq(null))]);
  const srcLink = (pid) => h('a', { href: app.link(casePath, { src: pid, o: app.loc.params.get('o'), ot: app.loc.params.get('ot'), ob: app.loc.params.get('ob') }), 'data-nav': '' }, short(pid));
  const srcCard = (s) => h('article', { class: 'card src-card', dataset: { source: s.source_id } },
    h('p', { class: 'card-eyebrow' }, `${s.source_family} · ${humanise(s.subtype)}`),
    h('p', {}, timeOf(s.occurrence), h('span', { class: 'muted small' }, ` · held ${instant(s.scenario_available_at)}`)),
    h('p', { class: 'muted small mono' }, `${short(s.source_id)} · sha ${shortHash(s.content_sha256)}`),
    h('p', { class: 'muted small' }, s.permitted_asset_version_ids.length ? `${s.permitted_asset_version_ids.length} original file(s)` : 'No separately held original'),
    h('a', { href: app.link(casePath, { src: `${s.source_id}~${s.content_sha256}`, o: app.loc.params.get('o'), ot: app.loc.params.get('ot'), ob: app.loc.params.get('ob') }), 'data-nav': '', class: 'card-link', dataset: { focusKey: `src:${s.source_id}` } }, 'Open source →'));
  const fileCard = (f) => {
    const own = f.asset.permitted_source_ids || []; const parents = f.permitted_parent_source_ids || []; const derived = f.asset.permitted_parent_asset_version_ids || [];
    return h('article', { class: 'card file-card', dataset: { assetVersion: f.asset.asset_version_id } },
      h('p', { class: 'card-eyebrow' }, `${humanise(f.asset.representation_role)} · ${f.asset.media_type}`),
      h('p', { class: 'file-name' }, f.asset.filename, h('span', { class: 'muted mono' }, ` ${versionLabel(f.asset.version)}`)),
      h('p', { class: 'muted small mono' }, `${f.asset.asset_version_id} · ${bytes(f.asset.byte_size)}`),
      h('p', { class: 'small' }, h('span', { class: `badge badge-x-${f.extraction_state}` }, `Extraction ${humanise(f.extraction_state)}`), ' ', h('span', { class: 'badge' }, `Location ${humanise(f.mapping_state)}`)),
      own.length ? h('p', { class: 'small', dataset: { linkKind: 'record' } }, 'Record of ', own.map((x, i) => [i ? ', ' : '', srcLink(x)])) : null,
      parents.length ? h('p', { class: 'small', dataset: { linkKind: 'attached' } }, 'Attached to ', parents.map((x, i) => [i ? ', ' : '', srcLink(x)])) : null,
      derived.length ? h('p', { class: 'small', dataset: { linkKind: 'derived' } }, 'Derived from file ', h('span', { class: 'mono' }, derived.join(', '))) : null,
      !own.length && !parents.length && !derived.length ? h('p', { class: 'muted small' }, f.asset.lineage_disclosure === 'limited' ? 'Limited lineage disclosure' : 'Unmapped to a source') : null,
      f.asset.permitted_actions.length
        ? h('a', { href: app.link(casePath, { asset: f.asset.asset_version_id, o: app.loc.params.get('o'), ot: app.loc.params.get('ot'), ob: app.loc.params.get('ob') }), 'data-nav': '', class: 'card-link', dataset: { focusKey: `asset:${f.asset.asset_version_id}` } }, f.asset.representation_role === 'original' ? 'View original →' : 'View readable version →')
        : h('span', { class: 'muted small' }, 'Viewing not permitted'));
  };
  const group = (title, sub, out, cardOf, idOf, req, noun) => {
    const panel = h('section', { class: 'panel' }, h('h2', {}, title), h('p', { class: 'panel-sub' }, sub));
    if (out.kind !== Outcome.OK) { panel.appendChild(outcomeState(out, { compact: true })); return panel; }
    const seen = new Set(); const grid = h('div', { class: 'card-grid tight' }); const status = h('p', { class: 'muted', role: 'status' });
    let cursor = out.payload.next_cursor;
    const add = (items) => { for (const it of items) { const k = idOf(it); if (!seen.has(k)) { seen.add(k); grid.appendChild(cardOf(it)); } } status.textContent = `${count(seen.size)} of ${count(out.payload.total_permitted_count)} permitted ${noun}${cursor ? ' · more available' : ''}`; };
    add(out.payload.items);
    const more = h('button', { type: 'button', class: 'button-secondary', hidden: cursor ? null : true }, `Load more ${noun}`);
    more.addEventListener('click', async () => {
      more.disabled = true;
      const nxt = await app.store.shared(req(cursor), { cache: false });
      if (nxt.kind !== Outcome.OK) { more.after(outcomeState(nxt, { compact: true })); more.disabled = false; return; }
      cursor = nxt.payload.next_cursor; add(nxt.payload.items);
      if (cursor) more.disabled = false; else more.hidden = true;
    });
    panel.append(status, out.payload.items.length ? grid : stateBox('empty', `No permitted ${noun}`, null, { compact: true }), more);
    return panel;
  };
  return [
    group('Sources', 'Every record held for this case', src, srcCard, (s) => `${s.source_id}~${s.content_sha256}`, srcReq, 'sources'),
    group('Original files', 'Authentic originals and readable versions — clearly labelled', files, fileCard, (f) => f.asset.asset_version_id, fileReq, 'files'),
  ];
}

function timeOf(o) {
  if (!o) return 'Date unknown';
  if (o.precision === 'instant') return instant(o.instant);
  if (o.date) return fmtDate(o.date);
  return humanise(o.precision || 'unknown');
}
