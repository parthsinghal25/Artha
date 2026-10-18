// Minimal in-browser test harness (no dependencies).
export const tests = [];
export function test(name, fn, { timeout = 10000 } = {}) { tests.push({ name, fn, timeout }); }

export class AssertionError extends Error {}
export function ok(cond, msg = 'expected truthy') { if (!cond) throw new AssertionError(msg); }
export function eq(actual, expected, msg = '') {
  const a = JSON.stringify(actual); const e = JSON.stringify(expected);
  if (a !== e) throw new AssertionError(`${msg} expected ${e} got ${a}`);
}

export async function waitFor(fn, { timeout = 5000, label = 'condition' } = {}) {
  const start = performance.now();
  for (;;) {
    let v;
    try { v = await fn(); } catch { v = null; }
    if (v) return v;
    if (performance.now() - start > timeout) throw new AssertionError(`timed out waiting for ${label}`);
    await new Promise((r) => setTimeout(r, 25));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

// `?only=a|b` runs only tests whose names contain one of the fragments (debugging aid).
const ONLY = (new URLSearchParams(window.location.search).get('only') || '').split('|').filter(Boolean);

export async function run(out) {
  const results = [];
  for (const t of tests.filter((x) => !ONLY.length || x.name === 'Cleanup' || ONLY.some((o) => x.name.includes(o)))) {
    const started = performance.now();
    try {
      await Promise.race([t.fn(), new Promise((_, j) => setTimeout(() => j(new AssertionError(`timeout ${t.timeout}ms`)), t.timeout))]);
      results.push({ name: t.name, ok: true, ms: Math.round(performance.now() - started) });
    } catch (e) {
      results.push({ name: t.name, ok: false, error: String(e && e.message || e) });
    }
    out.textContent = results.map((r) => `${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : `\n      ${r.error}`}`).join('\n');
  }
  const failed = results.filter((r) => !r.ok).length;
  document.title = failed ? `FAIL ${failed}/${results.length}` : `PASS ${results.length}/${results.length}`;
  window.__results = results;
  const done = document.createElement('div');
  done.id = 'tests-done';
  done.dataset.failed = String(failed);
  done.dataset.total = String(results.length);
  document.body.appendChild(done);
  try { await fetch('/tests/report', { method: 'POST', body: JSON.stringify({ failed, total: results.length, results }) }); } catch { /* report is optional */ }
  return results;
}
