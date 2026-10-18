// Application tests against the labelled TEST FIXTURE companion double
// (tests/fixture/companion-double.js). They check behaviour the live pilot
// cannot show on demand; live browser acceptance is recorded separately.
import { test, ok, eq, waitFor, $, $$, text } from './harness.js';
import { registry } from './unit.test.js';
import { App } from '../src/app.js';
import { CompanionDouble } from './fixture/companion-double.js';
import { closeAllDrawers } from '../src/ui/drawer.js';
import { rankBars } from '../src/ui/viz.js';
import { windowTotal, zeroNote } from '../src/views/impact.js';
import { completionMeasures } from '../src/views/experience.js';
import { previousRanking } from '../src/views/model.js';
import { arrivalsIndex } from '../src/lib/calc.js';

let current = null;
async function start(path, setup = () => {}) {
  if (current) current.app.dispose();
  closeAllDrawers();
  document.getElementById('app').textContent = '';
  history.replaceState({}, '', path);
  const dbl = new CompanionDouble();
  setup(dbl);
  const app = new App({ config: { contract_version: '1.0.0', intelligence_schema: '1.1.0', operation_path: '/v1/operations', release_id: 'REL-fixture', asset_redeem_path: null }, registry: await registry(), transport: dbl.transport() });
  app.renderShell();
  app.router.start();
  current = { app, dbl };
  await waitFor(() => $('main .page-head h1') || $('main .state:not(.state-loading)'), { label: 'first view', timeout: 8000 });
  return current;
}
const rankRows = () => $$('.panel .rank-row').map((r) => ({ id: r.dataset.issue, n: text(r.querySelector('.rank-value strong')), note: text(r.querySelector('.rank-note')) }));

test('Fixture A01/A02: strip shows real-response coverage and the bound publication; Overview has four KPI cards', async () => {
  await start('/overview');
  ok(text($('.strip')).includes('6/7'), text($('.strip')));
  eq($$('.kpis .kpi').length, 4);
  ok(text($('#k-incidence-t').closest('.kpi')).includes('5 received cases'), 'selected period defaults to the last served bucket (Sep 2026)');
  ok(text($('#k-incidence-t').closest('.kpi')).includes('1 unassessed'));
});

test('Fixture B01/B02: Sep ranking on all received (holdout in denominator), ties by ID, Top N does not change shares', async () => {
  await start('/overview');
  const rows = rankRows();
  eq(rows.map((r) => r.id), ['ISS-B', 'ISS-A', 'ISS-C']);
  eq(rows.map((r) => r.n), ['3', '2', '2']);
  ok(rows[0].note.startsWith('60% of 5'), `primary share uses all 5 received cases, not 3/4 assessed: ${rows[0].note}`);
  $('[data-topn="10"]').click();
  await waitFor(() => $('[data-topn="10"][aria-pressed="true"]'), { label: 'Top 10' });
  await waitFor(() => rankRows().length === 3, { label: 're-render' });
  eq(rankRows()[0].note, rows[0].note, 'Top N toggle changes display only');
});

test('Fixture B01/B04: another period re-ranks (Jul tie → ID order); a month without arrivals has n/a shares', async () => {
  const { app } = await start('/overview?p=2026-07');
  eq(rankRows().map((r) => [r.id, r.n]), [['ISS-A', '1'], ['ISS-B', '1'], ['ISS-C', '0']]);
  app.router.go('/overview?p=2026-08');
  await waitFor(() => text($('.page-sub')).includes('Aug 2026') && rankRows().length, { label: 'Aug' });
  ok(rankRows().every((r) => r.n === '0' && r.note.includes('n/a')), JSON.stringify(rankRows()));
  ok(text($('#k-incidence-t').closest('.kpi')).includes('0 received cases'), 'measured zero shown as 0, not blank');
});

test('Fixture B04: a clipped final week is compared with the same elapsed days of the previous week', async () => {
  await start('/overview?g=week&p=2026-09-28/W');
  const k = text($('#k-incidence-t').closest('.kpi'));
  ok(k.includes('2 received cases'), k);
  ok(k.includes('first 3 days of Week 21 Sep 2026 – 27 Sep 2026 (1)'), k);
});

test('Fixture C01/D01: rank bar opens the exact population with separate uncertain/unassessed groups; Back restores', async () => {
  const { app } = await start('/overview');
  $('.rank-row[data-issue="ISS-A"] .rank-track').click();
  await waitFor(() => $('dialog.drawer .pop-title'), { label: 'population drawer' });
  const d = $('dialog.drawer');
  ok(text(d).includes('Fixture issue A · Sep 2026'), text(d).slice(0, 200));
  eq($$('[data-group]', d).map((b) => text(b)), ['Supported (included) · 2', 'Uncertain · 1', 'Received, unassessed · 1']);
  eq($$('.card', d).map((c) => c.dataset.case), ['c3', 'c4']);
  ok(location.search.includes('pop=reach'), 'selection is in the URL');
  history.back();
  await waitFor(() => !location.search.includes('pop='), { label: 'back' });
  await waitFor(() => !$('dialog.drawer'), { label: 'drawer closed' });
  ok(app.loc.section === 'overview');
});

test('Fixture D01/E01: case Back returns to the open population; closing it refocuses the bar that opened it', async () => {
  await start('/overview');
  $('.rank-row[data-issue="ISS-A"] .rank-track').click();
  await waitFor(() => $('dialog.drawer .card a[href^="/cases/"]'), { label: 'population cards' });
  $('dialog.drawer .card a[href^="/cases/"]').click();
  await waitFor(() => location.pathname.startsWith('/cases/') && $('.back-link'), { label: 'case page' });
  ok(text($('.back-link')).includes('Fixture issue A · Sep 2026'), text($('.back-link')));
  $('.back-link').click();
  await waitFor(() => location.pathname === '/overview' && $('dialog.drawer .pop-title'), { label: 'drawer restored' });
  ok(location.search.includes('pop=reach'), location.search);
  $('dialog.drawer .icon-button').click();
  await waitFor(() => !$('dialog.drawer') && !location.search.includes('pop='), { label: 'closed' });
  await waitFor(() => document.activeElement?.dataset?.focusKey === 'bar:ISS-A', { label: 'focus returned to the bar' });
});

test('Fixture D02: changing the reporting period clears an open population selection with a notice', async () => {
  const { app } = await start('/overview?pop=reach~ISS-A~month~2026-09');
  await waitFor(() => $('dialog.drawer .pop-title'), { label: 'drawer' });
  $('#ctl-period').value = '2026-07';
  $('#ctl-period').dispatchEvent(new Event('change'));
  await waitFor(() => text($('.page-sub')).includes('Jul 2026'), { label: 'Jul' });
  ok(!location.search.includes('pop='), 'incompatible selection cleared');
  ok(text($('.notice')).includes('Selection cleared'), 'user is told why');
  ok(!$('dialog.drawer'));
  ok(app.scope().period === '2026-07');
});

test('Fixture B02: heatmap marks a bucket with no received cases as not applicable, not zero share', async () => {
  await start('/issues');
  await waitFor(() => $('.heat'), { label: 'heatmap' });
  const aug = $$('.heat-cell').filter((c) => c.dataset.focusKey.endsWith(':2026-08'));
  ok(aug.length === 3 && aug.every((c) => c.classList.contains('is-na')), aug.map((c) => c.className).join());
  ok($$('.heat-cell').some((c) => c.getAttribute('aria-label').includes('3 of 5 received cases')));
});

test('Fixture B05: a signal state outside the leaders is shown with its published rule', async () => {
  await start('/issues');
  await waitFor(() => $('#h-allsig'), { label: 'signals' });
  const s = $('.signal[data-issue="ISS-C"]');
  ok(s && text(s).toLowerCase().includes('insufficient baseline'), s ? text(s) : 'missing');
});

test('Fixture: composed reach that disagrees with the published series is reported as a binding failure', async () => {
  await start('/overview', (d) => { d.wrongSeries = 'ISS-B'; });
  ok(text($('main')).includes('Reconciliation failed for 1 issue'), 'mismatch is surfaced');
});

test('Fixture: a malformed companion response is withheld, never rendered', async () => {
  await start('/overview', (d) => { d.breakSection = 'alerts'; });
  await waitFor(() => $('main [data-state="contract"]'), { label: 'contract state' });
  ok(!$('.kpis'), 'no populated cards from a partially valid set');
});

test('Fixture D03: a newly completed publication is offered whole; bound results stay until adopted', async () => {
  const { app, dbl } = await start('/overview');
  dbl.publication = 'IPUB-FIXTURE-2'; dbl.pubN = 2;
  $('[data-action="check-updates"]').click();
  await waitFor(() => $('[data-action="adopt"]'), { label: 'adopt offered' });
  ok(text($('.strip')).includes('Completed publication available'));
  eq(app.store.binding.publication.publication_id, 'IPUB-FIXTURE-1', 'still bound to the first publication');
  ok($('.kpis'), 'completed results remain visible');
  $('[data-action="adopt"]').click();
  await waitFor(() => app.store.binding.publication.publication_id === 'IPUB-FIXTURE-2' && $('.kpis'), { label: 'adopted' });
  ok(!$('[data-action="adopt"]'));
});

test('Fixture D03: a permission-revision change discards held results and reloads', async () => {
  const { app, dbl } = await start('/overview');
  const before = app.store.cache.size;
  ok(before > 0);
  dbl.revision = 'PERM-fixture-2';
  app.store.binding.context.expires_at = new Date(0).toISOString(); // next read re-resolves
  app.router.go('/issues');
  await waitFor(() => app.store.binding?.context.permission_revision === 'PERM-fixture-2' && $('.heat'), { label: 'reloaded under new authority', timeout: 8000 });
  ok([...app.store.cache.keys()].every((k) => k.includes('PERM-fixture-2')), 'no cached result from the old revision remains');
});

test('Fixture A05: interactions only read published sections (no other operation is sent)', async () => {
  const { app, dbl } = await start('/overview');
  app.router.go('/issues');
  await waitFor(() => $('.heat'), { label: 'issues' });
  const ops = new Set(dbl.ops.map((o) => o.split(':')[0]));
  ok([...ops].every((o) => ['intelligence.get', 'case.get', 'case.memory.get'].includes(o)), [...ops].join());
  current.app.dispose();
  closeAllDrawers();
});

// ---- UIR18 repair regressions (18 Oct review) ------------------------------
const drawerCases = () => $$('dialog.drawer .card').map((c) => c.dataset.case);
const closeDrawer = async () => { $('dialog.drawer .icon-button').click(); await waitFor(() => !$('dialog.drawer'), { label: 'drawer closed' }); };

test('Fixture UIR18-01: each plotted issue opens its own exact population for the same bucket', async () => {
  await start('/overview?iss=ISS-A,ISS-B');
  await waitFor(() => $('.tp-hit[data-issue="ISS-B"][data-bucket="2026-09"]'), { label: 'series marks' });
  ok(!$('button.tp-bucket'), 'the bucket itself is not a single button for all series');
  $('.tp-hit[data-issue="ISS-B"][data-bucket="2026-09"]').click();
  await waitFor(() => $('dialog.drawer .pop-title'), { label: 'B drawer' });
  ok(text($('dialog.drawer .pop-title')).startsWith('Fixture issue B · Sep 2026'), text($('dialog.drawer .pop-title')));
  await waitFor(() => drawerCases().length === 3, { label: 'B cases' });
  eq(drawerCases(), ['c3', 'c4', 'c5']);
  await closeDrawer();
  $('.tp-hit[data-issue="ISS-A"][data-bucket="2026-09"]').click();
  await waitFor(() => text($('dialog.drawer .pop-title')).startsWith('Fixture issue A · Sep 2026') && drawerCases().length === 2, { label: 'A drawer' });
  eq(drawerCases(), ['c3', 'c4']);
  ok(location.search.includes('pop=reach%7EISS-A') || location.search.includes('pop=reach~ISS-A'), location.search);
});

test('Fixture UIR18-02: an unpublished daily burden series gives unavailable totals, never A$0.00; unavailable issue results are not measured zero', async () => {
  await start('/impact?g=day&tg=day', (d) => { d.noBurdenGrains = ['day']; });
  await waitFor(() => $$('[data-window-total]').length === 3, { label: 'series panels' });
  ok($$('[data-window-total]').every((e) => e.dataset.windowTotal === 'unavailable'), $$('[data-window-total]').map((e) => e.dataset.windowTotal).join());
  ok(!/Window total (A\$0\.00|0\.00 h)/.test(text($('main'))), 'no zero window total from missing buckets');
  ok(!$$('.kpi .kpi-num').some((e) => /A\$0\.00/.test(text(e))), 'KPI cards do not show zero for a missing bucket');
  await waitFor(() => $('[data-burden-state]'), { label: 'issue burden state' });
  ok($$('[data-burden-state]').every((e) => e.dataset.burdenState === 'unavailable'), 'Shared issue requests are unavailable in the fixture');
  ok(!text($('main')).includes('measured zero'), 'unavailable results are never called measured zero');
});

test('UIR18-02: window totals and issue zero wording distinguish complete zero, partial subtotal and unavailable', () => {
  const f = (v) => String(v);
  const pt = (v) => (v === null ? { state: 'unavailable', value: null } : { state: 'value', value: v });
  eq(windowTotal([pt(null), pt(null)], f).state, 'unavailable');
  const part = windowTotal([pt(5), pt(null), pt(-2)], f);
  eq([part.state, part.text], ['partial', 'Known subtotal 3 · 1 of 3 buckets unavailable (not zero).']);
  eq(windowTotal([pt(0), pt(0)], f).text, 'Window total 0');
  const row = (v, state = 'known') => ({ v, state });
  eq(zeroNote([row(null)], [], 'amounts').dataset.burdenState, 'unavailable');
  eq(zeroNote([row(0), row(0)], [row(0), row(0)], 'amounts').dataset.burdenState, 'zero');
  eq(zeroNote([row(0), row(null)], [row(0)], 'amounts').dataset.burdenState, 'partial');
  eq(zeroNote([row(0, 'partial')], [row(0, 'partial')], 'amounts').dataset.burdenState, 'partial-zero');
  eq(zeroNote([row(5)], [row(5)], 'amounts'), null);
});

test('Fixture UIR18-03: uncertain and unassessed states survive case navigation and Back, with their own count and basis', async () => {
  await start('/overview');
  $('.rank-row[data-issue="ISS-A"] .rank-track').click();
  await waitFor(() => $('dialog.drawer [data-group="uncertain"]'), { label: 'groups' });
  for (const [g, expect] of [['uncertain', ['c5']], ['unassessed', ['h1']]]) {
    $(`dialog.drawer [data-group="${g}"]`).click();
    await waitFor(() => location.search.includes(`pg=${g}`) && drawerCases().join() === expect.join(), { label: `${g} selected` });
    eq(drawerCases(), expect, `${g} cards`);
    const active = text($('dialog.drawer .pop-facts-active'));
    ok(active.includes('· 1 case') && /not supported contributors/.test(active), active);
    ok(text($('dialog.drawer .pop-facts')).includes('Chart mark') && text($('dialog.drawer .pop-facts')).includes('2 cases'), 'original chart mark kept');
    $('dialog.drawer .card a[href^="/cases/"]').click();
    await waitFor(() => location.pathname === `/cases/${expect[0]}` && $('.back-link'), { label: 'case page' });
    $('.back-link').click();
    await waitFor(() => location.pathname === '/overview' && $(`dialog.drawer [data-group="${g}"][aria-pressed="true"]`), { label: `${g} restored` });
    await waitFor(() => drawerCases().length === expect.length, { label: 'cards restored' });
    eq(drawerCases(), expect, `${g} restored after Back`);
    ok(text($('dialog.drawer .pop-facts-active')).includes('· 1 case'));
  }
});

test('Fixture UIR18-04: AFCA receipt, closure-route and stage marks select exactly their matters', async () => {
  await start('/impact/afca');
  await waitFor(() => $$('.card[data-matter]').length === 3, { label: 'matters' });
  const matters = () => $$('.card[data-matter]').map((c) => c.dataset.matter);
  const col = (lbl) => $$('.cols-b').find((b) => (b.getAttribute('aria-label') || '').startsWith(lbl));
  ok(col('Jul 2026').tagName === 'BUTTON');
  col('Jul 2026').click();
  await waitFor(() => $('[data-matter-selection="recv"]'), { label: 'receipt selection' });
  eq(matters(), ['M-FIX-1']);
  $$('.part-item').find((b) => text(b).startsWith('withdrawn')).click();
  await waitFor(() => $('[data-matter-selection="route"]'), { label: 'route selection' });
  eq(matters(), ['M-FIX-2']);
  $$('.part-item').find((b) => text(b).startsWith('decision')).click();
  await waitFor(() => $('[data-matter-selection="stage"]'), { label: 'stage selection' });
  eq(matters(), ['M-FIX-3']);
  ok($('.card[data-matter="M-FIX-3"] a[href="/impact/afca/M-FIX-3"]'), 'selected matter keeps its journey link');
  $$('.selection-bar button').find((b) => text(b) === 'Show all matters').click();
  await waitFor(() => matters().length === 3, { label: 'cleared' });
});

test('Fixture UIR18-04: issue profile First Evidenced marks open the exact published first-evidenced cases', async () => {
  await start('/issues/ISS-B?tab=dynamics');
  const col = () => $$('.cols-b').find((b) => (b.getAttribute('aria-label') || '').startsWith('Sep 2026') && /first evidenced/.test(b.getAttribute('aria-label')));
  await waitFor(() => col(), { label: 'first-evidenced columns' });
  ok(col().tagName === 'BUTTON');
  col().click();
  await waitFor(() => text($('dialog.drawer .pop-title')).includes('first evidenced in Sep 2026') && drawerCases().length === 3, { label: 'fe drawer' });
  eq(drawerCases(), ['c3', 'c4', 'c5']);
});

test('Fixture UIR18-05: the issue profile compares a clipped week with the same elapsed days (a prior case outside them is excluded)', async () => {
  await start('/issues/ISS-C?tab=dynamics&g=week&p=2026-09-28/W');
  await waitFor(() => $$('.panel h2').some((e) => text(e) === 'Period Comparison'), { label: 'comparison' });
  const panel = $$('.panel').find((p) => text(p.querySelector('h2')) === 'Period Comparison');
  ok(text(panel).includes('+1 vs first 3 days of Week 21 Sep 2026 – 27 Sep 2026 (0 → 1)'), text(panel));
});

test('UIR18-05: previous-period leaders use the matched elapsed bounds of the comparison', () => {
  const counted = (l) => ({ contributors: l, unit: 'cases', value: l.length });
  const iss = (id, inc) => ({ issue_id: id, membership: { included: counted(inc) } });
  const model = { issues: [iss('ISS-A', ['x']), iss('ISS-C', ['late'])] };
  // matched first 3 days hold only "x"; "late" was received later in the previous full week
  const matched = arrivalsIndex([{ bucket: 'm', start: '2026-09-21', end_exclusive: '2026-09-24', cases: counted(['x']), members: counted(['x']), assessed_cases: counted(['x']), unassessed_cases: [] }]).get('m');
  const rows = previousRanking(model, { bucket: matched });
  eq(rows.map((r) => [r.id, r.n]), [['ISS-A', 1], ['ISS-C', 0]]);
  eq(previousRanking(model, { bucket: null }), [], 'withheld comparison has no previous leaders');
});

test('Fixture UIR18-05: selected-period issue population cards keep the exact receipt-period descriptor', async () => {
  await start('/issues/ISS-A?tab=population');
  await waitFor(() => $('main .card a[href^="/cases/"]'), { label: 'cards' });
  const o = new URL($('main .card a[href^="/cases/"]').href).searchParams.get('o');
  eq(o, 'reach~ISS-A~month~2026-09');
});

test('UIR18-06: a negative net value keeps signed geometry (left of zero), not a zero-length bar', () => {
  const el = rankBars({ rows: [{ id: 'P', label: 'Pos', value: 10000, display: 'A$100.00' }, { id: 'N', label: 'Neg', value: -4500, display: '−A$45.00' }] });
  document.body.appendChild(el);
  try {
    const neg = el.querySelector('[data-issue="N"] .rank-bar'); const pos = el.querySelector('[data-issue="P"] .rank-bar');
    ok(neg.classList.contains('is-neg'));
    ok(parseFloat(neg.style.width) > 25, `negative bar width ${neg.style.width}`);
    const zero = parseFloat(el.querySelector('.rank-zero').style.left);
    ok(Math.abs(parseFloat(neg.style.left) + parseFloat(neg.style.width) - zero) < 0.01, 'negative bar ends at the zero line');
    ok(Math.abs(parseFloat(pos.style.left) - zero) < 0.01, 'positive bar starts at the zero line');
  } finally { el.remove(); }
});

test('Fixture UIR18-06: a negative publication-window issue amount is drawn signed and opens its contributing record', async () => {
  await start('/impact?bs=pub&bm=remedies_paid');
  await waitFor(() => $('.rank-row[data-issue="ISS-C"] .rank-bar'), { label: 'issue bars' });
  const bar = $('.rank-row[data-issue="ISS-C"] .rank-bar');
  ok(bar.classList.contains('is-neg') && parseFloat(bar.style.width) > 0, bar.getAttribute('style'));
  ok(text($('.rank-row[data-issue="ISS-C"]')).includes('−A$45.00') || text($('.rank-row[data-issue="ISS-C"]')).includes('-A$45.00'), text($('.rank-row[data-issue="ISS-C"]')));
  $('.rank-row[data-issue="ISS-C"] .rank-track').click();
  await waitFor(() => $('dialog.drawer .pop-title'), { label: 'drawer' });
  ok(text($('dialog.drawer .pop-facts')).includes('1 financial event'), text($('dialog.drawer .pop-facts')));
});

test('UIR18-07: completion intervals and reopenings are separate measures, never one partition', () => {
  const el = completionMeasures({}, { closed_intervals_with_recorded_completion: ['c1'], closed_intervals_without_completion_evidence: ['c1', 'c2'], reopened_cases: ['c1'] });
  document.body.appendChild(el);
  try {
    ok(!el.querySelector('.part'), 'no partition bar');
    eq($$('.band', el).map((b) => text(b.querySelector('.band-value'))), ['1', '2']);
    ok(text(el).includes('2 distinct cases · 1 in both rows'), text(el));
    ok(text(el).includes('1 reopened case') && text(el).includes('do not add up'), text(el));
  } finally { el.remove(); }
});


test('Fixture partial publication: assessed = case memories; pending and holdout are unassessed and named apart', async () => {
  await start('/overview', (d) => { d.pending = ['c6']; });
  const strip = text($('.strip [data-coverage="assessed"]'));
  ok(strip.includes('5/7') && strip.includes('cases assessed by AI'), strip);
  ok(!text($('.strip')).includes('6/7'), 'eligible count is never shown as assessed');
  $('.strip [data-action="coverage"]').click();
  await waitFor(() => $('dialog.drawer [data-coverage-summary]'), { label: 'coverage drawer' });
  const sum = text($('dialog.drawer [data-coverage-summary]'));
  ok(sum.includes('7 received cases: 5 assessed, 1 eligible but pending assessment, 1 not eligible (holdout)') && /lower bound/.test(sum), sum);
  ok(!/500 customers|not yet merged|March and June/.test(text($('dialog.drawer'))), 'no fixed population or historical-view claims');
  await closeDrawer();
  // Sep population of ISS-B: received c3 c4 c5 c6 h1; c6 pending, h1 holdout
  $('.rank-row[data-issue="ISS-B"] .rank-track').click();
  await waitFor(() => $('dialog.drawer [data-group="unassessed"]'), { label: 'groups' });
  $('dialog.drawer [data-group="unassessed"]').click();
  await waitFor(() => drawerCases().join() === 'c6,h1', { label: 'unassessed selected' });
  const active = text($('dialog.drawer .pop-facts-active'));
  ok(active.includes('1 holdout, not eligible · 1 pending assessment') && /not supported contributors/.test(active), active);
  await closeDrawer();
  await start('/issues/ISS-C?tab=population&ms=unassessed', (d) => { d.pending = ['c6']; });
  await waitFor(() => $('main .seg-b[data-ms="unassessed"][aria-pressed="true"]'), { label: 'issue unassessed tab' });
  await waitFor(() => $$('main .card-grid .card').length === 2, { label: `unassessed cards: ${$$('main .card-grid .card').map((c) => c.dataset.case)} ${text($('main .seg'))}` });
  eq($$('main .card-grid .card').map((c) => c.dataset.case), ['c6', 'h1']);
  ok(/1 holdout, not eligible · 1 pending assessment/.test(text($('main .panel-sub'))), text($('main .panel-sub')));
});

test('Fixture investigation brief: deterministic entry points from published results, no repeated issue', async () => {
  await start('/overview');
  await waitFor(() => $('.brief .finding'), { label: 'brief' });
  const cards = $$('.brief .finding');
  eq(cards.map((c) => [c.dataset.role, c.dataset.issue]), [['lead', 'ISS-B']], 'fixture has no emerging or persistent alert, so only the lead is shown (nothing invented)');
  ok(cards[0].classList.contains('is-featured'));
  const t = text(cards[0]);
  ok(t.includes('60% of 5') && t.includes('Sep 2026'), t);
  ok(t.includes('1 not assessed') && t.includes('do not add to 100%'), t);
  const inv = cards[0].querySelector('[data-action="investigate"]');
  ok(inv && new URL(inv.href).pathname === '/issues/ISS-B');
});

test('Fixture investigation: case selection updates assessment; citation reveals its passage; contrary evidence shown; URL keeps state', async () => {
  const { dbl } = await start('/issues/ISS-A');
  await waitFor(() => $('.inv .pick[aria-pressed="true"]'), { label: 'selected case' });
  eq($$('.inv .pick').map((b) => b.dataset.pick), ['c3', 'c4'], 'selected-period supported cases, received in Sep 2026');
  await waitFor(() => text($('.inv-assess')).includes('Fixture reason: status shown'), { label: 'c3 assessment' });
  ok(text($('.ev-contrary')).includes('No contrary statement is published'), 'absence of contrary evidence is explicit');
  await waitFor(() => text($('.inv-evidence')).includes('the status was shown as complete'), { label: 'default passage' });
  const memReads = () => dbl.ops.filter((o) => o === 'case.memory.get').length;
  eq(memReads(), 1, 'only the selected case memory is read');
  $('.inv .pick[data-pick="c4"]').click();
  await waitFor(() => text($('.inv-assess')).includes('Fixture contradiction'), { label: 'c4 assessment' });
  ok(new URL(location.href).searchParams.get('case') === 'c4');
  ok(text($('.inv-assess .questions')).includes('Fixture question'));
  const contrary = $('.ev-contrary .stmt');
  ok(contrary && text(contrary).includes('activation on 6 September'));
  contrary.click();
  await waitFor(() => text($('.inv-evidence')).includes('Contrary to the finding') && text($('.inv-evidence')).includes('activation on 6 September'), { label: 'contrary passage' });
  eq(new URL(location.href).searchParams.get('ev'), 'CL-4b');
  eq(memReads(), 2);
  ok(!dbl.ops.includes('analysis.query'), 'no exact-cost fan-out');
  // population switch: whole-publication uncertain
  $('.inv [data-ip="uncertain"]').click();
  await waitFor(() => $$('.inv .pick').map((b) => b.dataset.pick).join() === 'c5', { label: 'uncertain group' });
  eq(new URL(location.href).searchParams.get('ip'), 'uncertain');
  await waitFor(() => text($('.inv-assess')).includes('No AI assessment for this case yet'), { label: 'c5 has no fixture memory' });
});

test('Fixture investigation: deep link restores population, case and statement', async () => {
  await start('/issues/ISS-A?case=c4&ev=CL-4b');
  await waitFor(() => $('.inv .pick[data-pick="c4"][aria-pressed="true"]'), { label: 'c4 selected' });
  await waitFor(() => $('.inv-assess [data-ev="CL-4b"][aria-pressed="true"]') && text($('.inv-evidence')).includes('activation on 6 September'), { label: 'statement selected' });
});

test('Fixture: a reporting-period change clears investigation selections (not relabelled)', async () => {
  const { app } = await start('/issues/ISS-A?case=c4&ev=CL-4b');
  await waitFor(() => $('.inv .pick[data-pick="c4"][aria-pressed="true"]'), { label: 'c4 selected' });
  app.changeScope({ p: '2026-07' }, 'reporting period');
  await waitFor(() => !location.search.includes('case=') && $('.inv .pick'), { label: 'cleared' });
  eq($$('.inv .pick').map((b) => b.dataset.pick), ['c1']);
});
