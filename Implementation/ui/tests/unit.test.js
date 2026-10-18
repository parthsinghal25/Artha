import { test, ok, eq } from './harness.js';
import { audMinor, secondsToHours, codepointSlice, jsonPointer, pointValue, dateOnly, timeValue } from '../src/lib/format.js';
import { SchemaRegistry, SHARED_ID } from '../src/lib/schema.js';
import { verifyBinding, canonicalJson } from '../src/lib/digest.js';
import { parseCsv, parseA1, colLetters, readXlsx } from '../src/viewers/parsers.js';
import { encodeRef, decodeRef, encodeCitation, decodeCitation } from '../src/ui/router.js';
import { melbourneDate, melbourneMidnightUtc } from '../src/lib/periods.js';
import { analysisFilters } from '../src/views/population.js';

export async function registry() {
  if (registry.cached) return registry.cached;
  const reg = new SchemaRegistry();
  for (const n of ['runtime.schema.json', 'shared.schema.json', 'intelligence-v1.schema.json']) reg.add(await (await fetch(`/contracts/${n}`)).json());
  registry.cached = reg;
  return reg;
}

test('cents display exactly, including large and negative amounts', () => {
  eq(audMinor(123456), 'A$1,234.56');
  eq(audMinor(5), 'A$0.05');
  eq(audMinor(-300), '−A$3.00');
  eq(audMinor(900719925474099), 'A$9,007,199,254,740.99');
  eq(audMinor(12.5), null, 'non-integer cents are not formatted');
});

test('seconds convert to hours half-up at 2dp without float drift', () => {
  eq(secondsToHours(3600), '1.00 h');
  eq(secondsToHours(18), '0.01 h'); // 0.005 h rounds half-up
  eq(secondsToHours(17), '0.00 h');
  eq(secondsToHours(45000), '12.50 h');
  eq(secondsToHours(-1), null);
});

test('metric points: unavailable is not zero; unknown units shown raw', () => {
  eq(pointValue({ value: null, unit: 'cases', state: 'unavailable' }), null);
  eq(pointValue({ value: 0, unit: 'cases', state: 'known' }), '0');
  eq(pointValue({ value: 123456, unit: 'AUD_minor', state: 'known' }), 'A$1,234.56');
  eq(pointValue({ value: 12.5, unit: 'hours', state: 'known' }), '12.5 h');
  eq(pointValue({ value: 3, unit: 'request occasions', state: 'known' }), '3');
});

test('code-point spans handle astral characters (not UTF-16 units)', () => {
  const s = 'a😀bcd';
  const sl = codepointSlice(s, 1, 3);
  eq(sl.match, '😀b');
  eq(codepointSlice(s, 3, 3), null, 'empty span rejected');
  eq(codepointSlice(s, 2, 99), null, 'out of range rejected');
});

test('JSON pointers resolve exactly or return undefined', () => {
  const o = { native_facts: [{ value: { value: 5 } }], 'a/b': 1 };
  eq(jsonPointer(o, '/native_facts/0/value/value'), 5);
  eq(jsonPointer(o, '/a~1b'), 1);
  eq(jsonPointer(o, '/missing'), undefined);
});

test('date-only values are not shifted by timezone; unknown time stays unknown', () => {
  eq(dateOnly('2026-03-01'), '1 Mar 2026');
  ok(timeValue({ precision: 'unknown', reason: 'x' }).startsWith('Unknown time'));
  ok(timeValue({ precision: 'day', date: '2026-03-01' }).includes('date only'));
});

test('Melbourne calendar dates across DST', () => {
  eq(melbourneDate('2025-09-30T14:00:00Z'), '2025-10-01');
  eq(melbourneDate('2026-01-31T13:00:00Z'), '2026-02-01'); // AEDT +11
  eq(melbourneDate('2026-05-31T14:00:00Z'), '2026-06-01'); // AEST +10
});

test('exact Shared analysis filters carry every required typed key for issue, case and matter scopes', async () => {
  const reg = await registry();
  const start = melbourneMidnightUtc('2026-09-01'); const end = melbourneMidnightUtc('2026-10-01');
  for (const [issues, extra] of [[[], {}], [['ISS-1'], {}], [[], { case_ids: ['C-1'] }], [[], { afca_matter_ids: ['M-1'] }]]) {
    const req = { operation: 'analysis.query', context_id: 'ctx-1', query_id: 'UI-cashremediespaid',
      definition: { registry_id: 'r', measure_id: 'cash.remedies_paid', definition_version: '0.2.0' }, filters: analysisFilters(issues, start, end, extra), population_reference: null };
    eq(reg.validateDef(SHARED_ID, 'analysis.query.request', req), []);
    eq(req.filters.selection_operator, issues.length ? 'any' : 'none', 'issue filter is never dropped');
  }
  eq(start, '2026-08-31T14:00:00Z'); eq(end, '2026-09-30T14:00:00Z');
});

test('validator accepts every frozen contract example and rejects mutations', async () => {
  const reg = await registry();
  const ex = await (await fetch('/tests/contract-examples.json')).json();
  eq(reg.validateDef(SHARED_ID, 'result', ex.two_endpoint_result), []);
  eq(reg.validateDef(SHARED_ID, 'contributor_page', ex.contributor_page), []);
  eq(reg.validateDef(SHARED_ID, 'taxonomy', ex.taxonomy), []);
  eq(reg.validateDef(SHARED_ID, 'source_inventory', ex.source_only_inventory), []);
  eq(reg.validateDef(SHARED_ID, 'file_inventory', ex.independent_file_inventory), []);
  eq(reg.validateDef(SHARED_ID, 'state_projection', ex.independent_case_dimensions), []);
  const bad = structuredClone(ex.two_endpoint_result);
  bad.cells[0].extra = 1;
  ok(reg.validateDef(SHARED_ID, 'result', bad).length > 0, 'unexpected property rejected');
  const bad2 = structuredClone(ex.contributor_page);
  bad2.items[0].entity_id = 'has space';
  ok(reg.validateDef(SHARED_ID, 'contributor_page', bad2).length > 0, 'ID pattern enforced');
});

test('population digest reproduces the frozen examples and detects tampering', async () => {
  const ex = await (await fetch('/tests/contract-examples.json')).json();
  for (const c of ex.two_endpoint_result.cells) eq(await verifyBinding(c.population, c.key), [], c.key);
  eq(await verifyBinding(ex.contributor_page.population, 'page'), []);
  const t = structuredClone(ex.two_endpoint_result.cells[0].population);
  t.predicate.case_statuses = ['closed'];
  ok((await verifyBinding(t, 'tampered')).some((e) => e.includes('digest')), 'tampered predicate detected');
  eq(canonicalJson({ b: 1, a: [2, { d: 'é', c: null }] }), '{"a":[2,{"c":null,"d":"é"}],"b":1}');
});

test('CSV: header excluded from record index, quoted newlines, leading zeros kept', () => {
  const rows = parseCsv('ref,note\n00123,"a\nb"\n00456,"x, ""y"""\n');
  eq(rows.length, 3);
  eq(rows[1], ['00123', 'a\nb']);
  eq(rows[2], ['00456', 'x, "y"']);
});

test('A1 ranges and column letters', () => {
  eq(parseA1('B3'), { c1: 2, r1: 3, c2: 2, r2: 3 });
  eq(parseA1('AA10:B2'), { c1: 2, r1: 2, c2: 27, r2: 10 });
  eq(parseA1('b3'), null);
  eq(colLetters(28), 'AB');
});

test('XLSX reader shows stored values and marks formulas without evaluating', async () => {
  const buf = await (await fetch('/tests/specimens/specimen.xlsx')).arrayBuffer();
  const wb = await readXlsx(buf);
  eq(wb.sheets.map((s) => s.name), ['Ledger', 'Notes']);
  const s = wb.sheets[0];
  eq(s.cells.get('A2').value, '00123', 'shared string with leading zero');
  eq(s.cells.get('B3').value, '1250', 'cached value');
  eq(s.cells.get('B3').formula, 'SUM(B2:B2)');
  eq(s.cells.get('A3').value, 'total');
});

test('router references round-trip and reject malformed values', () => {
  const r = { result_id: 'res-1', cell_key: 'k', population_id: 'p', predicate_sha256: 'a'.repeat(64) };
  eq(decodeRef(encodeRef(r)), r);
  eq(decodeRef('res~k~p~short'), null);
  const a = { kind: 'text', start_codepoint: 3, end_codepoint: 9 };
  eq(decodeCitation(encodeCitation(a)), a);
  eq(decodeCitation(encodeCitation({ kind: 'field', json_pointer: '/native_facts/0' })), { kind: 'field', json_pointer: '/native_facts/0' });
});

test('page security policy lets the built-in PDF viewer show app-created blobs only', async () => {
  const csp = (await fetch('/overview')).headers.get('Content-Security-Policy') || '';
  const objectSrc = csp.split(';').map((d) => d.trim()).find((d) => d.startsWith('object-src'));
  eq(objectSrc, 'object-src blob:', 'object-src allows blob: (inherited by PDF blob frames) and nothing else');
  ok(/frame-src 'self' blob:/.test(csp), 'blob frames permitted');
  ok(/script-src 'self'(;|$)/.test(csp), 'scripts still self-only');
  ok(/frame-ancestors 'self'(;|$)/.test(csp), 'own page may frame the PDF blob (Safari); other origins may not');
});

test('transport limiter: at most N operations in flight, all complete in order, a queued abort rejects', async () => {
  const { limiter } = await import('../src/api/client.js');
  const gate = limiter(2); let active = 0; let peak = 0; const done = [];
  const job = (i) => () => new Promise((r) => { active += 1; peak = Math.max(peak, active); setTimeout(() => { active -= 1; done.push(i); r(i); }, 5); });
  const ac = new AbortController();
  const ps = [0, 1, 2, 3].map((i) => gate(job(i)));
  const aborted = gate(job(9), ac.signal); ac.abort();
  eq(await Promise.all(ps), [0, 1, 2, 3]);
  let err = null; try { await aborted; } catch (e) { err = e.name; }
  eq(err, 'AbortError'); eq(peak, 2); ok(!done.includes(9), 'aborted job never ran');
});

test('email viewer decodes verified bytes: headers, RFC 2047 subject, quoted-printable, multipart base64, attachments listed', async () => {
  const { parseEml } = await import('../src/viewers/parsers.js');
  const enc = (s) => new TextEncoder().encode(s).buffer;
  const a = parseEml(enc('From: Harbour Super <help@example.test>\r\nTo: Member <m@example.test>\r\nSubject: =?utf-8?Q?Payment_update_=E2=80=94_ref_42?=\r\nDate: Tue, 1 Sep 2026 10:00:00 +1000\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\nYour amendment was accepted =E2=80=94 no further docu=\r\nments are needed.\r\n'));
  eq(a.subject, 'Payment update — ref 42'); eq(a.from, 'Harbour Super <help@example.test>');
  ok(a.text.startsWith('Your amendment was accepted — no further documents are needed.'), a.text);
  const body = btoa(String.fromCharCode(...new TextEncoder().encode('Kia ora — plain part')));
  const b = parseEml(enc(`Subject: Multi\r\nContent-Type: multipart/mixed; boundary="XX"\r\n\r\n--XX\r\nContent-Type: multipart/alternative; boundary=YY\r\n\r\n--YY\r\nContent-Type: text/plain; charset="utf-8"\r\nContent-Transfer-Encoding: base64\r\n\r\n${body}\r\n--YY\r\nContent-Type: text/html\r\n\r\n<p>html</p>\r\n--YY--\r\n--XX\r\nContent-Type: application/pdf; name="letter.pdf"\r\nContent-Disposition: attachment; filename="letter.pdf"\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--XX--\r\n`));
  eq(b.text.trim(), 'Kia ora — plain part'); eq(b.attachments, ['letter.pdf']);
  const c = parseEml(enc('Subject: Html only\r\nContent-Type: text/html\r\n\r\n<p>Hello <b>there</b></p>'));
  eq(c.text, 'Hello there');
});

test('Impact period issue queries: bounded candidates by association, at most N in flight, order kept', async () => {
  const { periodCandidates, inPool, PERIOD_ISSUE_LIMIT } = await import('../src/views/impact.js');
  const issues = Array.from({ length: 14 }, (_, k) => ({ issue_id: `ISS-${String(k).padStart(2, '0')}`, v: k === 3 ? null : k % 5 }));
  const c = periodCandidates(issues, (i) => i.v);
  eq(c.length, PERIOD_ISSUE_LIMIT);
  eq(c.slice(0, 3).map((i) => i.issue_id), ['ISS-04', 'ISS-09', 'ISS-08']);
  ok(!c.some((i) => i.issue_id === 'ISS-03'), 'missing association sorts last, never ahead of a known value');
  let active = 0; let peak = 0;
  const out = await inPool([5, 1, 4, 2, 3], 2, async (x) => { active++; peak = Math.max(peak, active); await new Promise((r) => setTimeout(r, x * 3)); active--; return x * 10; });
  eq(out, [50, 10, 40, 20, 30]); eq(peak, 2);
});

test('Brief selection: lead, emerging signal preferring outside Top N, persistent — deterministic, distinct', async () => {
  const { selectBrief } = await import('../src/views/brief.js');
  const rows = [{ id: 'I1', n: 5 }, { id: 'I2', n: 4 }, { id: 'I3', n: 0 }, { id: 'I4', n: 0 }];
  const al = (id, state, change, persistent = 0) => ({ issue_id: id, state, absolute_change: change, persistent_open_over_90_days: { value: persistent } });
  const alerts = [al('I1', 'emerging_concentration', 9, 7), al('I2', 'emerging_concentration', 3), al('I4', 'emerging_concentration', 1, 2), al('I3', 'no_alert', 0, 2)];
  const picks = selectBrief(rows, alerts, 2);
  eq(picks.map((p) => [p.role, p.id, !!p.outsideTop]), [['lead', 'I1', false], ['signal', 'I4', true], ['persistent', 'I3', false]]);
  eq(selectBrief([{ id: 'I1', n: 0 }], [], 5), [], 'nothing qualifies → nothing invented');
});

test('Investigation evidence partition keeps unresolved claim IDs counted, not dropped', async () => {
  const { partitionEvidence } = await import('../src/views/investigate.js');
  const mem = { items: [{ claim_id: 'a' }, { claim_id: 'b' }], detail: { memberships: [{ issue_id: 'X', support_claim_ids: ['a', 'zz'], contrary_claim_ids: ['b'] }], contradictions: [{ note: 'n', claim_ids: ['a', 'b'] }], unresolved_questions: ['q'] } };
  const ev = partitionEvidence(mem, 'X');
  eq([ev.support.got.length, ev.support.missing, ev.contrary.got.length, ev.contradictions[0].got.length, ev.questions.length], [1, 1, 1, 2, 1]);
  eq(partitionEvidence(mem, 'Y').membership, null);
});
