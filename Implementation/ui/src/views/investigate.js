// Connected investigation (issue profile, "Investigate" tab). Three coordinated
// areas, all from published outputs and read operations only:
//
//   Finding & population — selected-period count/share/comparison, the exact
//     supported / uncertain / not-assessed / excluded populations as selectable
//     compact case cards, and the publication-window recorded impact.
//   Assessment & chronology — for the selected case only: its published
//     membership reason, supporting and contrary statements (equal weight), the
//     mechanism, published contradictions and open questions, complaint status ·
//     service-action completion · member response (each with its basis and
//     citations), the AI case summary and recorded events.
//   Evidence inspector — the selected statement's exact passage, verified
//     against its source version, with source/date facts and the full source and
//     authentic original one step away (the existing source inspector).
//
// Selections live in the URL (`ip` population, `case`, `ev` statement) and are
// recorded in place, so deep links and Back restore them. Only the selected
// case's memory, case record and cited sources are read; nothing is preloaded
// for other cases and no exact-cost calculation is triggered.

import { h, mount, announce } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { loading, outcomeState, stateBox } from '../ui/states.js';
import { spark } from '../ui/viz.js';
import { ids, reach, compare } from '../lib/calc.js';
import { audMinor, secondsToHours, count, instant, humanise, shortHash, codepointSlice } from '../lib/format.js';
import { fmtDate } from '../lib/periods.js';
import { kindTag, membershipTag, completionDisplay, info, MODEL_NOTE } from '../ui/labels.js';
import { icon } from '../ui/icons.js';
import { shareText, deltaText, comparablePeriod, popHref, label } from './model.js';
import { unassessedForIssue, caseHref, short } from './population.js';
import { periodStates } from './brief.js';

const PAGE = 12;
const FIRST_STATEMENTS = 4;
const BEFORE_CPS = 120;
const AFTER_CPS = 220;

const byStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/** Population groups for the investigation: selected-period states first, then whole publication. */
export function investigationGroups(iss, detail, bucket, periodLabel) {
  const st = periodStates(iss, bucket);
  const sorted = (set) => [...set].sort(byStr);
  const period = reach(ids(iss.membership?.included), bucket).ids;
  return [
    { id: 'period', scope: 'period', state: 'included', label: 'Supported', ids: period, basis: `Supported (included) cases received in ${periodLabel}` },
    { id: 'puncertain', scope: 'period', state: 'uncertain', label: 'Uncertain', ids: st ? sorted(st.uncertain) : [], basis: `Cases received in ${periodLabel} whose assessment for this issue is uncertain — not supported, not excluded` },
    { id: 'punassessed', scope: 'period', state: 'unassessed', label: 'Not assessed', ids: st ? sorted(st.unassessed) : [], basis: `Cases received in ${periodLabel} with no assessment for this issue (holdout, pending or unassessed pair) — not a negative finding` },
    { id: 'included', scope: 'all', state: 'included', label: 'Supported', ids: sorted(ids(iss.membership?.included)), basis: 'Supported cases across the whole publication (all received to date)' },
    { id: 'uncertain', scope: 'all', state: 'uncertain', label: 'Uncertain', ids: sorted(ids(iss.membership?.uncertain)), basis: 'Uncertain across the whole publication' },
    { id: 'excluded', scope: 'all', state: 'excluded', label: 'Excluded', ids: sorted(ids(iss.membership?.excluded)), basis: 'Assessed and excluded, with the published reason — not the same as not assessed' },
    { id: 'unassessed', scope: 'all', state: 'unassessed', label: 'Not assessed', ids: unassessedForIssue(iss, detail), basis: 'Not assessed for this issue across the whole publication' },
  ];
}

/**
 * Statements for one case/issue, partitioned exactly as published. Claim IDs
 * that do not resolve in the memory are counted, never dropped silently.
 */
export function partitionEvidence(memory, issueId) {
  const claims = new Map((memory.items || []).map((c) => [c.claim_id, c]));
  const d = memory.detail || {};
  const ms = (d.memberships || []).find((x) => x.issue_id === issueId) || null;
  const factor = ms?.factor_id ? (d.factors || []).find((f) => f.factor_id === ms.factor_id) || null : null;
  const take = (list) => { const got = []; let missing = 0; for (const id of list || []) { const c = claims.get(id); if (c) got.push(c); else missing += 1; } return { got, missing }; };
  return {
    membership: ms,
    factor,
    support: take(ms?.support_claim_ids),
    contrary: take(ms?.contrary_claim_ids),
    factorSupport: take(factor?.support_claim_ids),
    factorContrary: take(factor?.contrary_claim_ids),
    contradictions: (d.contradictions || []).map((x) => ({ note: x.note, ...take(x.claim_ids) })),
    questions: d.unresolved_questions || [],
    claims,
  };
}

const refKey = (r) => `r:${r.source_id}:${r.anchor?.start_codepoint ?? r.anchor?.json_pointer ?? ''}:${r.anchor?.end_codepoint ?? ''}`;

export async function tabInvestigate(app, loc, ctx, model, iss, ser, body) {
  const d = ser.detail?.issue || {};
  const bucket = model.arr.get(ctx.selected.key);
  const groups = investigationGroups(iss, d, bucket, ctx.selected.label);
  const params = new URL(window.location.href).searchParams;
  let group = groups.find((g) => g.id === params.get('ip')) || (groups[0].ids.length ? groups[0] : groups.find((g) => g.ids.length) || groups[0]);
  const state = { caseId: null, evKey: params.get('ev') || null, items: new Map() };

  const wrap = h('div', { class: 'inv', dataset: { pane: 'assess' } });
  const popArea = h('aside', { class: 'inv-pop', 'aria-labelledby': 'h-inv-pop' });
  const assessArea = h('section', { class: 'inv-assess', 'aria-labelledby': 'h-inv-assess', tabindex: '-1' });
  const evArea = h('section', { class: 'inv-evidence', 'aria-labelledby': 'h-inv-ev', tabindex: '-1' });
  const paneSwitch = h('div', { class: 'seg inv-switch', role: 'group', 'aria-label': 'Show panel' },
    [['assess', 'Assessment'], ['evidence', 'Evidence']].map(([v, l]) => h('button', { type: 'button', class: 'seg-b', 'aria-pressed': v === 'assess' ? 'true' : 'false', dataset: { pane: v }, onclick: () => showPane(v, true) }, l)));
  const showPane = (v, focus) => {
    wrap.dataset.pane = v;
    for (const b of paneSwitch.children) b.setAttribute('aria-pressed', String(b.dataset.pane === v));
    if (focus) (v === 'evidence' ? evArea : assessArea).focus({ preventScroll: false });
  };
  const switchVisible = () => getComputedStyle(paneSwitch).display !== 'none';
  wrap.append(paneSwitch, h('div', { class: 'inv-grid' }, popArea, assessArea, evArea));
  mount(body, wrap);

  // ---- 1. finding & population -------------------------------------------
  const cmpP = await comparablePeriod(app, ctx, model);
  if (!ctx.isCurrent()) return;
  const cur = reach(ids(iss.membership?.included), bucket);
  const pr = cmpP.bucket ? reach(ids(iss.membership?.included), cmpP.bucket) : null;
  const vals = ser.items.map((x) => x.affected_new_cases?.value ?? null);
  const list = h('ul', { class: 'pick-list', 'aria-label': 'Cases in the selected population' });
  const listFoot = h('div', { class: 'list-foot' });
  const groupBasis = h('span', { class: 'grp-info' });
  const groupRow = (scope, title) => h('div', { class: 'grp-row' }, h('span', { class: 'grp-scope' }, title),
    h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': `${title} population` }, groups.filter((g) => g.scope === scope).map((g) => h('button', {
      type: 'button', class: 'seg-b', 'aria-pressed': g === group ? 'true' : 'false', dataset: { ip: g.id },
      onclick: () => { group = g; app.router.replaceParams({ ip: g.id === 'period' ? null : g.id, case: null, ev: null }); state.evKey = null; renderGroups(); renderList(true); },
    }, `${g.label} · ${count(g.ids.length)}`))));
  const groupSlot = h('div', { class: 'grp' });
  const renderGroups = () => {
    mount(groupSlot, groupRow('period', ctx.selected.label), groupRow('all', 'Whole publication'));
    mount(groupBasis, info(`${group.basis}.${group.state === 'unassessed' ? '' : ` ${MODEL_NOTE}.`}`, 'About this group'));
  };
  let shown = 0;
  const renderList = (reselect) => {
    list.textContent = ''; shown = 0;
    const want = new URL(window.location.href).searchParams.get('case');
    const initial = !reselect && want && group.ids.includes(want) ? want : group.ids[0] || null;
    const idx = initial ? group.ids.indexOf(initial) : 0;
    morePicks(Math.max(PAGE, Math.ceil((idx + 1) / PAGE) * PAGE));
    if (initial) selectCase(initial, { record: reselect, focus: false });
    else mount(assessArea, h('h2', { id: 'h-inv-assess', class: 'inv-h' }, 'Assessment'), stateBox('empty', `No cases in “${group.label}” for this selection`, 'Measured zero for this exact population.', { compact: true })), evidenceEmpty();
  };
  const morePicks = (n = PAGE) => {
    for (const cid of group.ids.slice(shown, shown + n)) list.appendChild(pickCard(app, cid, () => selectCase(cid, { record: true, focus: true }), cid === state.caseId));
    shown = Math.min(group.ids.length, shown + n);
    mount(listFoot, h('span', { class: 'muted small' }, `Showing ${shown} of ${count(group.ids.length)}`),
      shown < group.ids.length ? h('button', { type: 'button', class: 'button-secondary small', onclick: () => morePicks() }, `Show ${Math.min(PAGE, group.ids.length - shown)} more`) : null);
  };
  const ab = d.associated_burden || {};
  const impactRow = (k, l, fmt) => h('div', { class: 'imp-row' }, h('span', {}, l),
    ab[k] ? h('a', { href: popHref(app, `iburden~${iss.issue_id}~${k}`), 'data-nav': '', class: 'tnum' }, fmt(ab[k].value)) : h('span', { class: 'muted' }, 'Not recorded'));
  mount(popArea,
    h('h2', { id: 'h-inv-pop', class: 'inv-h' }, 'Finding & population'),
    h('div', { class: 'inv-metric' },
      h('p', { class: 'finding-num tnum' }, cur.n === null ? '—' : count(cur.n), h('small', {}, ` in ${ctx.selected.label}`)),
      h('p', {}, `${shareText(cur)} received`),
      h('p', { class: 'muted small' }, deltaText(compare(cur, pr), cmpP.label || cmpP.reason)),
      vals.length ? h('div', { class: 'finding-trend', title: `Supported cases per ${ctx.scope.grain === 'observation_year' ? 'year' : ctx.scope.grain}` }, spark(vals)) : null),
    h('div', { class: 'grp-head' }, h('span', { class: 'grp-scope' }, 'Cases'), groupBasis), groupSlot, list, listFoot,
    h('details', { class: 'inv-impact' }, h('summary', {}, 'Cost · full year', info('Held records associated with this issue’s supported cases, 1 Oct 2025 – 30 Sep 2026. Association, not cause; shared records also count for other issues.', 'About this cost')),
      impactRow('remedies_paid', 'Compensation', audMinor),
      impactRow('complaint_expenses_paid', 'Expenses', audMinor),
      impactRow('recorded_work', 'Staff hours', secondsToHours),
      h('a', { href: app.link(`/issues/${iss.issue_id}`, { tab: 'impact' }), 'data-nav': '', class: 'card-link' }, `${ctx.selected.label} values →`)));
  renderGroups();

  // ---- 2. assessment for the selected case -------------------------------
  function selectCase(cid, { record, focus }) {
    state.caseId = cid;
    for (const b of list.querySelectorAll('[data-pick]')) b.setAttribute('aria-pressed', String(b.dataset.pick === cid));
    if (record) { state.evKey = null; app.router.replaceParams({ case: cid, ev: null }); }
    renderAssessment(cid, focus);
  }

  async function renderAssessment(cid, focus) {
    mount(assessArea, h('h2', { id: 'h-inv-assess', class: 'inv-h' }, 'Why the AI linked this case'), loading('Reading the case assessment'));
    evidenceEmpty('Loading…');
    const [mem, cg] = await Promise.all([app.store.memory(cid, { signal: ctx.signal }), app.store.shared((c) => ({ operation: 'case.get', context_id: c.context_id, case_id: cid, cursor: null, page_size: 100 }), { signal: ctx.signal })]);
    if (!ctx.isCurrent() || state.caseId !== cid) return;
    state.items = new Map();
    const m = mem.kind === Outcome.OK ? mem : null;
    const ev = m ? partitionEvidence(m, iss.issue_id) : null;
    const desc = `reach~${iss.issue_id}~${ctx.scope.grain}~${ctx.selected.key}`;
    const rec = cg.kind === Outcome.OK ? cg.payload : null;
    const cd = rec?.case_record?.data;
    const refClaim = new Map(); if (m) for (const c of m.items) if (c.ref) refClaim.set(refKey(c.ref), c);
    const item = (key, data) => { if (!state.items.has(key)) state.items.set(key, data); return key; }; // first (most specific) role wins
    const claimItem = (c, role) => item(c.claim_id, { key: c.claim_id, ref: c.ref, claim: c, role });
    const refItem = (r, role) => { const c = refClaim.get(refKey(r)); return item(c ? c.claim_id : refKey(r), { key: c ? c.claim_id : refKey(r), ref: r, claim: c || null, role }); };
    const stmt = (key, roleCtx = null) => statementButton(state.items.get(key), key === state.evKey, () => selectEvidence(key, true, roleCtx), roleCtx);

    const head = h('div', { class: 'inv-case-head' },
      h('div', {},
        h('p', { class: 'eyebrow' }, 'Selected case'),
        h('p', { class: 'inv-case-id' }, h('span', { class: 'mono' }, short(cid)),
          cd ? h('span', { class: 'muted' }, ` · received ${fmtDate(cd.original_receipt_date)} · ${humanise(cd.product_or_journey || 'journey not stated')} · ${cd.channel || 'channel unknown'}`) : null)),
      h('a', { href: caseHref(app, cid, desc, `${label(iss)} · ${ctx.selected.label}`), 'data-nav': '', class: 'link-button', dataset: { focusKey: `case:${cid}` } }, 'Full case →'));

    if (!m) {
      mount(assessArea, h('h2', { id: 'h-inv-assess', class: 'inv-h' }, 'Why the AI linked this case'), head,
        h('div', { class: 'inv-block' }, kindTag('na'), h('p', {}, mem.kind === Outcome.UNAVAILABLE || mem.kind === Outcome.OK
          ? 'No AI assessment for this case yet (holdout or pending). It still counts as received — this is not a negative finding.'
          : 'The case assessment could not be read.'), mem.kind !== Outcome.OK && mem.kind !== Outcome.UNAVAILABLE ? outcomeState(mem, { compact: true }) : null),
        rec ? dimensions(rec.state_projection, null, refItem, stmt) : null,
        rec ? eventsBlock(rec.events, rec.total_permitted_event_count) : outcomeState(cg, { compact: true }));
      finish(focus);
      return;
    }
    const ms = ev.membership;
    const whyTitle = !ms ? 'Not assessed for this issue' : ms.state === 'included' ? 'Why it fits' : ms.state === 'uncertain' ? 'Why it’s unclear' : ms.state === 'excluded' ? 'Why it was ruled out' : 'Assessment';
    for (const c of ev.support.got) claimItem(c, 'support');
    for (const c of ev.contrary.got) claimItem(c, 'contrary');
    for (const c of ev.factorSupport.got) if (!state.items.has(c.claim_id)) claimItem(c, 'factor-support');
    for (const c of ev.factorContrary.got) if (!state.items.has(c.claim_id)) claimItem(c, 'factor-contrary');
    for (const x of ev.contradictions) for (const c of x.got) if (!state.items.has(c.claim_id)) claimItem(c, 'contradiction');
    const columnsFor = (sup, con, supTitle, conTitle) => h('div', { class: 'ev-cols' },
      statementColumn(supTitle, sup, 'support', stmt),
      statementColumn(conTitle, con, 'contrary', stmt, con.got.length ? null : 'No contrary statement is published for this assessment.'));
    const narrative = (m.detail.narrative || []).map((n) => {
      const keys = (n.claim_ids || []).map((x) => ev.claims.get(x)).filter(Boolean).map((c) => (state.items.has(c.claim_id) ? c.claim_id : claimItem(c, 'summary')));
      return h('li', {}, h('p', {}, n.text), keys.length ? h('div', { class: 'cite-row' }, keys.map((k, i) => citeChip(state.items.get(k), i + 1, () => selectEvidence(k, true)))) : null);
    });

    mount(assessArea,
      h('h2', { id: 'h-inv-assess', class: 'inv-h' }, 'Why the AI linked this case'),
      head,
      h('div', { class: 'inv-block why', dataset: { state: ms?.state || 'unassessed' } },
        h('div', { class: 'why-head' }, membershipTag(ms?.state), h('h3', {}, whyTitle)),
        ms ? h('p', { class: 'why-reason' }, kindTag('ai'), ' ', ms.reason || 'No reason recorded.') : h('p', { class: 'muted' }, 'This issue was not assessed for this case in the publication.'),
        ms ? columnsFor(ev.support, ev.contrary, 'For', 'Against') : null,
        ms ? h('p', { class: 'muted small' }, `Basis: ${humanise(ms.evidence_basis || 'not stated')}`, info(`The assessor was shown ${ms.claims_shown ?? '?'} of ${ms.claims_total ?? '?'} statements in this case memory.`, 'About the basis')) : null,
        ev.support.missing + ev.contrary.missing ? h('p', { class: 'warn-text' }, `${ev.support.missing + ev.contrary.missing} cited statement(s) are outside this view's permitted context.`) : null),
      ev.factor ? h('div', { class: 'inv-block' },
        h('div', { class: 'why-head' }, kindTag('ai', 'What went wrong'), h('h3', {}, ev.factor.label), h('span', { class: 'badge' }, `${humanise(ev.factor.status)} · ${humanise(ev.factor.prominence || '')}`)),
        h('p', {}, ev.factor.description),
        columnsFor(ev.factorSupport, ev.factorContrary, 'For', 'Against')) : null,
      ev.contradictions.length ? h('div', { class: 'inv-block conflicts' },
        h('div', { class: 'why-head' }, kindTag('conflict'), h('h3', {}, `${ev.contradictions.length === 1 ? 'The evidence disagrees' : `${ev.contradictions.length} places the evidence disagrees`}`)),
        ev.contradictions.map((x) => h('div', { class: 'conflict' }, h('p', {}, kindTag('ai'), ' ', x.note), h('div', { class: 'stmt-list' }, x.got.map((c) => stmt(c.claim_id, 'contradiction')))))) : null,
      ev.questions.length ? h('div', { class: 'inv-block questions' },
        h('div', { class: 'why-head' }, kindTag('uncertain', 'Open questions'), h('h3', {}, 'Still unknown')),
        h('ul', { class: 'q-list' }, ev.questions.map((q) => h('li', {}, q)))) : null,
      rec ? dimensions(rec.state_projection, m, refItem, stmt) : h('div', { class: 'inv-block' }, outcomeState(cg, { compact: true })),
      narrative.length ? h('div', { class: 'inv-block' },
        h('div', { class: 'why-head' }, kindTag('ai', 'Case summary'), h('h3', {}, 'What happened')),
        h('ol', { class: 'narrative' }, narrative)) : null,
      rec ? eventsBlock(rec.events, rec.total_permitted_event_count) : null,
      producedPanel(m));
    finish(focus);
  }

  function finish(focus) {
    const keys = [...state.items.keys()];
    const first = state.items.get(state.evKey) ? state.evKey : keys.find((k) => state.items.get(k).role === 'support') || keys.find((k) => state.items.get(k).role === 'contradiction') || keys[0];
    if (first) selectEvidence(first, false);
    else evidenceEmpty('No cited statement for this case and issue.');
    if (focus) assessArea.focus({ preventScroll: true });
    announce(`Showing the assessment for case ${short(state.caseId)}.`);
  }

  // ---- 3. evidence inspector ---------------------------------------------
  function evidenceEmpty(msg = 'Pick a statement to see its source.') {
    mount(evArea, h('h2', { id: 'h-inv-ev', class: 'inv-h' }, 'Source evidence'), h('p', { class: 'muted' }, msg));
  }

  async function selectEvidence(key, user, roleCtx = null) {
    const it = state.items.get(key); if (!it) return;
    const roleKey = roleCtx || it.role;
    state.evKey = key;
    for (const b of assessArea.querySelectorAll('[data-ev]')) b.setAttribute('aria-pressed', String(b.dataset.ev === key));
    if (user) { app.router.replaceParams({ ev: key }); if (switchVisible()) showPane('evidence', true); }
    const back = h('button', { type: 'button', class: 'link-button inv-back', onclick: () => { showPane('assess', false); assessArea.querySelector(`[data-ev="${CSS.escape(key)}"]`)?.focus(); } }, icon('back', { size: 14 }), 'Back to assessment');
    mount(evArea, h('h2', { id: 'h-inv-ev', class: 'inv-h' }, 'Source evidence'), back, loading('Checking the passage'));
    const r = it.ref;
    const citation = r?.anchor && r.content_sha256 ? { source_id: r.source_id, content_sha256: r.content_sha256, anchor: r.anchor } : null;
    const out = r ? await app.store.shared((c) => ({ operation: 'source.get', context_id: c.context_id, source_id: r.source_id, content_sha256: r.content_sha256 || null, citation }), { signal: ctx.signal }) : null;
    if (state.evKey !== key || !ctx.isCurrent()) return;
    const role = ROLE_TEXT[roleKey] || 'Cited statement';
    const c = it.claim;
    let passage = null; let verified = null; let meta = null;
    if (out?.kind === Outcome.OK) {
      const p = out.payload; const sd = p.record.data; const env = p.record.envelope;
      verified = p.citation_state;
      if (p.citation_state === 'valid' && p.citation.anchor.kind === 'text') {
        const sl = codepointSlice(sd.text, p.citation.anchor.start_codepoint, p.citation.anchor.end_codepoint);
        if (sl) passage = h('blockquote', { class: 'cited inv-passage' }, trimBefore(sl.before), h('mark', { class: 'flash' }, sl.match), trimAfter(sl.after));
      }
      meta = h('dl', { class: 'kv compact' },
        h('dt', {}, 'Source'), h('dd', {}, `${sd.source_family} · ${humanise(sd.subtype)}`),
        h('dt', {}, 'Author'), h('dd', {}, humanise(sd.author_role)),
        h('dt', {}, 'Occurred'), h('dd', {}, env.occurred_at ? instant(env.occurred_at) : env.occurrence_date ? `${fmtDate(env.occurrence_date)} (date only)` : 'Unknown'),
        h('dt', {}, 'Held from'), h('dd', {}, instant(env.available_at)),
        h('dt', {}, 'Source version'), h('dd', { class: 'mono' }, shortHash(p.source_binding.content_sha256)));
    }
    const href = (() => { const u = new URL(window.location.href); u.searchParams.set('src', `${r.source_id}${r.content_sha256 ? `~${r.content_sha256}` : ''}`); if (r.anchor?.kind === 'text') u.searchParams.set('cite', `t~${r.anchor.start_codepoint}~${r.anchor.end_codepoint}`); else u.searchParams.delete('cite'); u.searchParams.delete('asset'); return u.pathname + u.search; })();
    mount(evArea,
      h('h2', { id: 'h-inv-ev', class: 'inv-h' }, 'Source evidence'), back,
      h('div', { class: 'ev-head' }, kindTag(roleKey === 'contradiction' ? 'conflict' : 'source'), h('span', { class: `ev-role role-${roleKey}` }, role)),
      c ? h('p', { class: 'ev-claim' }, h('span', { class: 'badge' }, humanise(c.kind)), ' ', h('span', { class: 'badge' }, humanise(c.modality)), c.business_date ? h('span', { class: 'muted small' }, ` · dated ${fmtDate(c.business_date)}`) : null) : null,
      passage || (c?.quote ? h('blockquote', { class: 'cited inv-passage' }, h('mark', { class: 'flash' }, c.quote)) : null),
      verified === 'valid' && passage ? h('p', { class: 'ev-verified' }, icon('check', { size: 14 }), 'Verified in the source')
        : out?.kind === Outcome.OK ? stateBox('citation-invalid', 'The cited position does not resolve in this source version', 'The statement is shown as published in the case memory; no nearby text is substituted.', { compact: true })
          : r ? h('div', {}, h('p', { class: 'muted small' }, 'Shown as published in the case memory; the source could not be read for verification.'), out ? outcomeState(out, { compact: true }) : null) : null,
      c?.note ? h('p', { class: 'small' }, kindTag('ai', 'Reader note'), ' ', c.note) : null,
      meta,
      r ? h('div', { class: 'doc-actions' },
        h('a', { href, 'data-nav': '', class: 'button-primary', dataset: { action: 'open-source', focusKey: `src:${r.source_id}` } }, icon('doc', { size: 16 }), 'Open original'),
        info('Opens the full text and the authentic original, integrity-checked, at this passage.', 'About the original')) : null);
    const mk = evArea.querySelector('.inv-passage mark'); const bq = mk?.closest('blockquote');
    if (bq && mk.offsetTop - bq.offsetTop > bq.clientHeight / 2) bq.scrollTop = mk.offsetTop - bq.offsetTop - 24; // the quoted passage, not the preceding context, in view
    if (user && !switchVisible()) evArea.querySelector('.inv-passage')?.scrollIntoView({ block: 'nearest' });
  }

  renderList(false);
}

const ROLE_TEXT = {
  support: 'Supports the finding', contrary: 'Contrary to the finding',
  'factor-support': 'Supports the mechanism', 'factor-contrary': 'Against the mechanism',
  contradiction: 'Cited in a published contradiction', summary: 'Cited in the case summary',
  status: 'Basis of the complaint status', service: 'Basis of a service-action state', response: 'Basis of a member response',
};

function trimBefore(s) { const cps = Array.from(s); return cps.length > BEFORE_CPS ? `…${cps.slice(-BEFORE_CPS).join('')}` : s; }
function trimAfter(s) { const cps = Array.from(s); return cps.length > AFTER_CPS ? `${cps.slice(0, AFTER_CPS).join('')}…` : s; }

/** Compact selectable case card; facts load lazily from the (cached) case record. */
function pickCard(app, cid, onPick, pressed) {
  const facts = h('span', { class: 'pick-facts muted' }, 'Loading…');
  const btn = h('button', { type: 'button', class: 'pick', 'aria-pressed': pressed ? 'true' : 'false', dataset: { pick: cid, focusKey: `pick:${cid}` }, onclick: onPick },
    h('span', { class: 'pick-id mono' }, short(cid)), facts);
  app.store.shared((c) => ({ operation: 'case.get', context_id: c.context_id, case_id: cid, cursor: null, page_size: 1 })).then((cg) => {
    if (cg.kind !== Outcome.OK) { mount(facts, 'Case facts unavailable'); return; }
    const d = cg.payload.case_record.data; const sp = cg.payload.state_projection;
    mount(facts, `Received ${fmtDate(d.original_receipt_date)} · ${humanise(d.product_or_journey || 'journey not stated')} · ${humanise(sp.administrative?.value || 'status unknown')}`);
  });
  return h('li', {}, btn);
}

/** A statement shown as a selectable evidence item (never truncated: the full quote is in the inspector). */
function statementButton(it, pressed, onPick, roleCtx = null) {
  const c = it.claim;
  const text = c?.quote || c?.note || `Source ${short(it.ref?.source_id || '')}`;
  return h('button', { type: 'button', class: `stmt role-${roleCtx || it.role}`, 'aria-pressed': pressed ? 'true' : 'false', dataset: { ev: it.key, focusKey: `ev:${it.key}` }, onclick: onPick },
    h('span', { class: 'stmt-quote' }, `“${text}”`),
    h('span', { class: 'stmt-meta muted' }, [c ? humanise(c.author_role || c.actor || '') : 'Cited source', c?.business_date ? fmtDate(c.business_date) : null, c?.modality && c.modality !== 'recorded' ? humanise(c.modality) : null].filter(Boolean).join(' · ')));
}

function citeChip(it, n, onPick) {
  return h('button', { type: 'button', class: 'cite-chip', dataset: { ev: it.key }, 'aria-pressed': 'false', onclick: onPick, 'aria-label': `Inspect cited statement ${n}` }, icon('quote', { size: 12 }), String(n));
}

function statementColumn(title, part, tone, stmt, emptyText = null) {
  const all = part.got.map((c) => stmt(c.claim_id));
  const col = h('div', { class: `ev-col ev-${tone}` }, h('h4', {}, title, h('span', { class: 'tnum muted' }, ` · ${part.got.length}`)));
  if (!all.length) { col.appendChild(h('p', { class: 'muted small ev-empty' }, emptyText || 'None published.')); return col; }
  const listEl = h('div', { class: 'stmt-list' }, all.slice(0, FIRST_STATEMENTS));
  col.appendChild(listEl);
  if (all.length > FIRST_STATEMENTS) {
    const more = h('button', { type: 'button', class: 'link-button small', onclick: () => { listEl.append(...all.slice(FIRST_STATEMENTS)); more.remove(); } }, `Show all ${all.length} statements`);
    col.appendChild(more);
  }
  return col;
}

/** Complaint status · Service-action completion · Member response — three separate published dimensions. */
function dimensions(sp, m, refItem, stmt) {
  const refsList = (refs, role) => (refs || []).map((r) => stmt(refItem(r, role)));
  const admin = sp?.administrative;
  const services = sp?.service || [];
  const responses = m?.detail?.responses || [];
  const claims = m ? new Map(m.items.map((c) => [c.claim_id, c])) : new Map();
  return h('div', { class: 'inv-block dims' },
    h('div', { class: 'why-head' }, h('h3', {}, 'Status · Work done · Member view'), info('Three separate dimensions, each with its own basis. Closing a complaint is not proof the work was done; missing completion evidence is unknown, not “unfinished”. “C” numbers in bases refer to statements in the assessor’s input.', 'About these dimensions')),
    h('div', { class: 'dim-grid' },
      h('section', { class: 'dim' }, h('h4', {}, icon('status', { size: 15 }), 'Complaint status'),
        admin ? [h('p', { class: 'dim-value' }, humanise(admin.value || 'unknown')), admin.reason ? h('p', { class: 'muted small' }, admin.reason) : null,
          disclose(refsList(admin.evidence_refs, 'status'), 'status record')] : h('p', { class: 'muted' }, 'Not published')),
      h('section', { class: 'dim' }, h('h4', {}, icon('check', { size: 15 }), 'Work done?'),
        services.length ? services.map((s) => {
          const disp = completionDisplay(s.value);
          return h('div', { class: 'svc' }, kindTag(disp.kind, disp.text),
            h('p', { class: 'small' }, kindTag('ai'), ' ', s.reason || 'No basis recorded.'),
            s.contradictory_refs?.length ? h('p', { class: 'small' }, kindTag('conflict', `${s.contradictory_refs.length} contradicting`)) : null,
            disclose([...refsList(s.confirmation_refs, 'service'), ...refsList(s.assertion_refs, 'service'), ...refsList(s.contradictory_refs, 'service')], 'cited statement'));
        }) : h('p', { class: 'muted' }, 'No service action published — unknown, not unfinished.')),
      h('section', { class: 'dim' }, h('h4', {}, icon('member', { size: 15 }), 'Member view'),
        responses.length ? responses.map((r) => h('div', { class: 'svc' },
          h('p', { class: 'dim-value small' }, `${humanise(r.position)} · ${r.dimension}`), h('p', { class: 'small' }, kindTag('ai'), ` ${r.target}`),
          disclose((r.claim_ids || []).map((x) => claims.get(x)).filter(Boolean).map((c) => stmt(refItem(c.ref, 'response'))), 'cited statement')))
          : (sp?.responses || []).length ? sp.responses.map((r) => h('p', {}, `${humanise(r.target_reference || 'target')}: ${humanise(r.value)}`))
            : h('p', { class: 'muted' }, 'No member response position recorded.'))),
  );
}

function disclose(nodes, noun) {
  if (!nodes.length) return null;
  return h('details', { class: 'disclose' }, h('summary', {}, `${nodes.length} ${noun}${nodes.length === 1 ? '' : 's'}`), h('div', { class: 'stmt-list' }, nodes));
}

function eventsBlock(events, total) {
  const evs = [...events].sort((a, b) => occ(a).localeCompare(occ(b)));
  const row = (e) => h('li', { class: `ev-row state-${e.state}` }, h('span', { class: 'tnum muted small' }, occLabel(e)), h('span', { class: 'ev-kind' }, humanise(e.event_type)),
    h('span', { class: 'ev-prop' }, firstLine(e.proposition || humanise(e.event_type))), e.state !== 'supported' ? h('span', { class: 'badge' }, humanise(e.state)) : null);
  const listEl = h('ol', { class: 'ev-list' }, evs.slice(0, 6).map(row));
  return h('details', { class: 'inv-block events' },
    h('summary', {}, kindTag('source', 'Recorded events'), ` ${events.length}${total > events.length ? ` of ${total}` : ''} in the case record`),
    listEl,
    evs.length > 6 ? h('button', { type: 'button', class: 'link-button small', onclick: (e) => { listEl.append(...evs.slice(6).map(row)); e.currentTarget.remove(); } }, `Show all ${evs.length} events`) : null);
}

const occ = (e) => e.occurrence?.instant || e.occurrence?.date || e.occurrence?.start || '';
function occLabel(e) { const o = e.occurrence || {}; if (o.precision === 'instant') return instant(o.instant); if (o.date) return fmtDate(o.date); return 'Date unknown'; }
function firstLine(s) { const t = String(s).split('\n')[0]; return t.length > 160 ? `${t.slice(0, 158)}…` : t; }

function producedPanel(m) {
  const p = m.detail.producer || {}; const cov = m.detail.coverage || {}; const cr = m.detail.critique || {};
  const row = (k, v) => [h('dt', {}, k), h('dd', {}, v ?? '—')];
  return h('details', { class: 'inv-block produced' }, h('summary', {}, 'How the AI produced this'),
    h('dl', { class: 'kv compact' },
      row('Assessment', `${m.detail.model_assessed ? 'Model-assessed' : 'Not model-assessed'} · human review ${humanise(m.detail.human_review || 'not stated')}`),
      row('Model', `${p.provider || '—'} · ${p.model || '—'}`),
      row('Prompts', [p.reader_prompt, p.factor_prompt, p.synthesis_prompt].filter(Boolean).join(' · ')),
      row('Critic', `${cr.version || p.critic || '—'}${cr.flags?.length ? ` · ${cr.flags.length} flag(s)` : ' · no flags'}${cr.refinalised ? ' · refinalised' : ''}`),
      row('Memory', h('span', { class: 'mono' }, `${m.detail.memory_id} · revision ${m.detail.revision} · ${p.memory_schema || ''}`)),
      row('Reading coverage', `${humanise(cov.state || '')} · extraction ${humanise(cov.extraction || 'unknown')} · ${cov.narrative_sources ?? '?'} narrative and ${cov.table_sources ?? '?'} table sources`),
      row('Statements', `${m.total} in this case memory${m.detail.claims_withheld_outside_context ? ` · ${m.detail.claims_withheld_outside_context} withheld outside this context` : ''}`)),
    cov.note ? h('p', { class: 'muted small' }, cov.note) : null);
}

