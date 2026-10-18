// Cases & Evidence landing: search and filter within the permitted received
// population of the bound publication (no organisation-wide search), paged
// compact case cards, and labelled deterministic example cases.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { ids } from '../lib/calc.js';
import { loadPortfolio, label, failed } from './model.js';
import { info } from '../ui/labels.js';
import { caseCard, caseHref, short } from './population.js';

const PAGE = 12;

export async function renderCases(app, loc, ctx) {
  const model = await loadPortfolio(app, ctx, { series: false });
  if (!ctx.isCurrent()) return;
  if (model.kind !== Outcome.OK) { mount(app.main, h('h1', {}, 'Cases & Evidence'), failed(model.out)); return; }
  const ov = model.ov;
  const all = [...ids(ov.cases_received_to_date)].sort();
  const open = ids(ov.backlog.open); const closed = ids(ov.backlog.closed);
  const assessed = ids(ov.assessed_cases);
  const p = loc.params;
  const q = (p.get('q') || '').trim();
  const st = p.get('st') || 'all';
  const issue = p.get('fi') || '';
  const asmt = p.get('as') || 'all';
  let list = all;
  if (q) list = list.filter((id) => id.toLowerCase().includes(q.toLowerCase()));
  if (st === 'open') list = list.filter((id) => open.has(id));
  if (st === 'closed') list = list.filter((id) => closed.has(id));
  if (asmt === 'assessed') list = list.filter((id) => assessed.has(id));
  if (asmt === 'unassessed') list = list.filter((id) => !assessed.has(id));
  if (issue) { const inc = ids(model.byId.get(issue)?.membership?.included); list = list.filter((id) => inc.has(id)); }
  const ages = ov.backlog.open_age_days?.by_case || {};
  const oldest = [...open].filter((id) => Number.isFinite(ages[id])).sort((a, b) => ages[b] - ages[a])[0];
  const afca = await app.sec('afca', {}, ctx.signal);
  const afcaCase = afca.kind === Outcome.OK ? afca.items.find((m) => m.multi_case)?.case_ids?.[0] : null;

  const grid = h('div', { class: 'card-grid' });
  const foot = h('div', { class: 'list-foot' });
  let shown = 0;
  const page = () => {
    for (const id of list.slice(shown, shown + PAGE)) grid.appendChild(caseCard(app, ctx, id, { age: open.has(id) ? ages[id] : null, from: 'Cases & Evidence' }));
    shown = Math.min(list.length, shown + PAGE);
    mount(foot, h('span', { class: 'muted' }, `Showing ${shown} of ${list.length}`), shown < list.length ? h('button', { type: 'button', class: 'button-secondary', onclick: page }, 'Load more') : null);
  };
  const apply = (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    app.setLocal({ q: f.q.value.trim() || null, st: f.st.value === 'all' ? null : f.st.value, as: f.as.value === 'all' ? null : f.as.value, fi: f.fi.value || null });
  };
  const sel = (name, labelText, value, opts) => h('label', {}, labelText, h('select', { name }, opts.map(([v, l]) => h('option', { value: v, selected: v === value ? true : null }, l))));
  mount(app.main,
    h('div', { class: 'page-head' }, h('h1', {}, 'Cases & Evidence'),
      h('p', { class: 'page-sub' }, `${all.length} cases · ${assessed.size} assessed by AI`, info('Search covers the received cases of this publication only — there is no organisation-wide search. Member references appear on each case.', 'About search'))),
    h('form', { class: 'filters', role: 'search', onsubmit: apply },
      h('label', {}, 'Case ID', h('input', { name: 'q', type: 'search', value: q, placeholder: 'Part of a case ID' })),
      sel('st', 'Status', st, [['all', 'Any'], ['open', 'Open'], ['closed', 'Closed']]),
      sel('as', 'Assessment', asmt, [['all', 'Any'], ['assessed', 'Assessed'], ['unassessed', 'Unassessed (holdout or pending)']]),
      sel('fi', 'Supported issue', issue, [['', 'Any issue'], ...model.issues.map((i) => [i.issue_id, label(i)])]),
      h('button', { type: 'submit', class: 'button-primary small' }, 'Apply'),
      q || st !== 'all' || asmt !== 'all' || issue ? h('a', { href: app.link('/cases'), 'data-nav': '', class: 'link-button' }, 'Clear') : null),
    h('div', { class: 'examples' }, h('span', { class: 'eyebrow' }, 'Examples:'),
      oldest ? h('a', { href: caseHref(app, oldest, null, 'Oldest known open case'), 'data-nav': '', class: 'chip' }, `Oldest known open case · ${ages[oldest]} days · ${short(oldest)}`) : null,
      afcaCase ? h('a', { href: caseHref(app, afcaCase, null, 'Case with a shared AFCA matter'), 'data-nav': '', class: 'chip' }, `Case with a shared AFCA matter · ${short(afcaCase)}`) : null),
    list.length ? [grid, foot] : h('p', { class: 'muted' }, 'No permitted case matches these filters.'));
  if (list.length) page();
}
