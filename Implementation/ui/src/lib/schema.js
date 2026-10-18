// Minimal Draft 2020-12 validator for the exact keyword set used by the frozen
// Shared 1.0.0 schema and pinned Data v0.2 runtime schema. It never mutates or
// coerces data; it only reports whether a response matches the contract so the
// UI can refuse to render a non-conforming payload.

const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export class SchemaRegistry {
  constructor() {
    this.docs = new Map();
    this.patterns = new Map();
  }

  add(doc) {
    if (!doc.$id) throw new Error('schema without $id');
    this.docs.set(doc.$id, doc);
  }

  resolve(ref, baseId) {
    const [uri, frag] = ref.split('#');
    const doc = this.docs.get(uri || baseId);
    if (!doc) throw new Error(`unresolved schema ${uri || baseId}`);
    let node = doc;
    for (const part of (frag || '').split('/').filter(Boolean)) {
      node = node[decodeURIComponent(part.replace(/~1/g, '/').replace(/~0/g, '~'))];
      if (node === undefined) throw new Error(`unresolved pointer ${ref}`);
    }
    return { node, baseId: doc.$id };
  }

  /** Validate `value` against `$defs/<name>` of the document `docId`. */
  validateDef(docId, name, value) {
    const errors = [];
    const { node, baseId } = this.resolve(`${docId}#/$defs/${name}`, docId);
    this._v(node, value, baseId, '', errors);
    return errors;
  }

  _re(p) {
    let r = this.patterns.get(p);
    if (!r) { r = new RegExp(p, 'u'); this.patterns.set(p, r); }
    return r;
  }

  _v(s, x, base, path, errors) {
    if (s === true) return;
    if (s === false) { errors.push(`${path}: forbidden`); return; }
    const before = errors.length;
    if (s.$ref) {
      const { node, baseId } = this.resolve(s.$ref, base);
      this._v(node, x, baseId, path, errors);
    }
    if (s.type !== undefined && !typeOk(s.type, x)) { errors.push(`${path}: expected ${s.type}`); return; }
    if ('const' in s && !deepEqual(s.const, x)) errors.push(`${path}: const mismatch`);
    if (s.enum && !s.enum.some((e) => deepEqual(e, x))) errors.push(`${path}: not in enum`);
    if (typeof x === 'string') {
      const len = [...x].length;
      if (s.minLength !== undefined && len < s.minLength) errors.push(`${path}: too short`);
      if (s.maxLength !== undefined && len > s.maxLength) errors.push(`${path}: too long`);
      if (s.pattern && !this._re(s.pattern).test(x)) errors.push(`${path}: pattern`);
      if (s.format === 'date-time' && !(DATE_TIME.test(x) && !Number.isNaN(Date.parse(x)))) errors.push(`${path}: date-time`);
      if (s.format === 'date' && !DATE.test(x)) errors.push(`${path}: date`);
    }
    if (typeof x === 'number') {
      if (s.minimum !== undefined && x < s.minimum) errors.push(`${path}: below minimum`);
      if (s.maximum !== undefined && x > s.maximum) errors.push(`${path}: above maximum`);
    }
    if (Array.isArray(x)) {
      if (s.minItems !== undefined && x.length < s.minItems) errors.push(`${path}: too few items`);
      if (s.uniqueItems) {
        const seen = new Set(x.map((i) => stable(i)));
        if (seen.size !== x.length) errors.push(`${path}: duplicate items`);
      }
      if (s.items) x.forEach((item, i) => this._v(s.items, item, base, `${path}/${i}`, errors));
    }
    if (x && typeof x === 'object' && !Array.isArray(x)) {
      for (const r of s.required || []) if (!(r in x)) errors.push(`${path}: missing ${r}`);
      const props = s.properties || {};
      for (const [k, v] of Object.entries(x)) {
        if (props[k]) this._v(props[k], v, base, `${path}/${k}`, errors);
        else if (s.additionalProperties === false) errors.push(`${path}: unexpected property ${k}`);
      }
    }
    if (s.allOf) for (const sub of s.allOf) this._v(sub, x, base, path, errors);
    if (s.anyOf && !s.anyOf.some((sub) => this._ok(sub, x, base))) errors.push(`${path}: anyOf`);
    if (s.oneOf) {
      const n = s.oneOf.filter((sub) => this._ok(sub, x, base)).length;
      if (n !== 1) errors.push(`${path}: oneOf matched ${n}`);
    }
    if (s.not && this._ok(s.not, x, base)) errors.push(`${path}: not`);
    if (s.if) {
      if (this._ok(s.if, x, base)) { if (s.then) this._v(s.then, x, base, path, errors); }
      else if (s.else) this._v(s.else, x, base, path, errors);
    }
    return errors.length === before;
  }

  _ok(s, x, base) {
    const e = [];
    this._v(s, x, base, '', e);
    return e.length === 0;
  }
}

function typeOk(t, x) {
  if (Array.isArray(t)) return t.some((one) => typeOk(one, x));
  switch (t) {
    case 'object': return x !== null && typeof x === 'object' && !Array.isArray(x);
    case 'array': return Array.isArray(x);
    case 'string': return typeof x === 'string';
    case 'integer': return Number.isInteger(x);
    case 'number': return typeof x === 'number' && Number.isFinite(x);
    case 'boolean': return typeof x === 'boolean';
    case 'null': return x === null;
    default: return false;
  }
}

function stable(v) {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`;
  if (v && typeof v === 'object') return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${stable(v[k])}`).join(',')}}`;
  return JSON.stringify(v);
}

export function deepEqual(a, b) {
  return stable(a) === stable(b);
}

export const SHARED_ID = 'urn:example-super:shared:1.0.0';
export const INTEL_ID = 'urn:example-super:backend:intelligence:1.1.0';
