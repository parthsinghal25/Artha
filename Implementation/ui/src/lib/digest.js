// Population selection digest (Shared DECISIONS §6): SHA-256 of canonical JSON
// (sorted keys, compact separators, Unicode retained) over the binding's
// selection fields, with predicate population_id/version/sha256 removed.
// The UI recomputes it only to *verify* that a published cell or contributor
// page carries a self-consistent immutable population; it never mints one.

const FIELDS = ['predicate', 'selectors', 'effective_evidence_as_of', 'evidence_boundary', 'versions', 'input_set_sha256',
  'assessment_id', 'assessed_evidence_as_of', 'analysis_mode', 'analysis_as_of'];

export function canonicalJson(v) {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(',')}]`;
  const keys = Object.keys(v).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`).join(',')}}`;
}

export async function selectionDigest(binding) {
  const body = {};
  for (const f of FIELDS) body[f] = binding[f];
  const { population_id: _a, population_version: _b, population_sha256: _c, ...predicate } = binding.predicate;
  body.predicate = predicate;
  const bytes = new TextEncoder().encode(canonicalJson(body));
  const d = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Returns a list of problems; empty when the binding is self-consistent. */
export async function verifyBinding(binding, label) {
  const errs = [];
  const digest = await selectionDigest(binding);
  if (digest !== binding.predicate_sha256) errs.push(`${label}: population digest does not match its canonical predicate`);
  if (binding.predicate.population_sha256 !== binding.predicate_sha256) errs.push(`${label}: predicate and binding digests differ`);
  if (binding.predicate.population_id !== binding.population_id) errs.push(`${label}: population id differs between binding and predicate`);
  if (binding.predicate.population_version !== binding.binding_version) errs.push(`${label}: predicate version differs from binding version`);
  if (binding.predicate.evidence_cutoff !== binding.effective_evidence_as_of) errs.push(`${label}: predicate cutoff differs from effective cutoff`);
  return errs;
}
