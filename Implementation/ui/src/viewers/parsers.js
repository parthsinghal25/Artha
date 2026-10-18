// Read-only display parsers for authorised bytes. They exist only to *show* a
// file the backend already validated; they produce no business facts, no
// accepted extraction and no location mappings. Spreadsheet formulas are never
// evaluated: the cached value stored in the file is shown, marked as such.

/** RFC 4180 CSV → records. Quoted newlines stay inside one record. */
export function parseCsv(text, delimiter = ',') {
  const rows = [];
  let row = [];
  let field = '';
  let i = 0;
  let quoted = false;
  if (text.charCodeAt(0) === 0xfeff) i = 1; // BOM
  for (; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; } else quoted = false;
      } else field += ch;
    } else if (ch === '"' && field === '') quoted = true;
    else if (ch === delimiter) { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}

/** Column letters → 1-based index. */
export function colIndex(letters) {
  let n = 0;
  for (const c of letters) n = n * 26 + (c.charCodeAt(0) - 64);
  return n;
}

export function colLetters(index) {
  let s = '';
  let n = index;
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

/** Parse "B3" or "B3:D9" into a 1-based inclusive rectangle. */
export function parseA1(range) {
  const m = /^([A-Z]+)([1-9][0-9]*)(?::([A-Z]+)([1-9][0-9]*))?$/.exec(range);
  if (!m) return null;
  const c1 = colIndex(m[1]); const r1 = Number(m[2]);
  const c2 = m[3] ? colIndex(m[3]) : c1; const r2 = m[4] ? Number(m[4]) : r1;
  return { c1: Math.min(c1, c2), r1: Math.min(r1, r2), c2: Math.max(c1, c2), r2: Math.max(r1, r2) };
}

// ---- minimal ZIP (central directory) reader --------------------------------
async function unzip(buffer) {
  const view = new DataView(buffer);
  let eocd = -1;
  for (let i = buffer.byteLength - 22; i >= Math.max(0, buffer.byteLength - 65557); i -= 1) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('not a zip container');
  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);
  const entries = new Map();
  const dec = new TextDecoder();
  for (let n = 0; n < count; n += 1) {
    if (view.getUint32(p, true) !== 0x02014b50) throw new Error('bad central directory');
    const method = view.getUint16(p + 10, true);
    const csize = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const local = view.getUint32(p + 42, true);
    const name = dec.decode(new Uint8Array(buffer, p + 46, nameLen));
    entries.set(name, { method, csize, local });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return {
    has: (name) => entries.has(name),
    async text(name) {
      const e = entries.get(name);
      if (!e) return null;
      const lNameLen = view.getUint16(e.local + 26, true);
      const lExtraLen = view.getUint16(e.local + 28, true);
      const data = new Uint8Array(buffer, e.local + 30 + lNameLen + lExtraLen, e.csize);
      if (e.method === 0) return dec.decode(data);
      if (e.method !== 8) throw new Error('unsupported compression');
      const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      return new Response(stream).text();
    },
  };
}

function xml(text) {
  return new DOMParser().parseFromString(text, 'application/xml');
}

/**
 * XLSX → {sheets:[{sheetId,name,cells:Map<"A1",{value,type,formula}>,maxRow,maxCol}]}
 * Values are exactly as stored (shared/inline strings, cached numbers as text).
 * Macro-enabled or encrypted workbooks are refused upstream by media type.
 */
export async function readXlsx(buffer) {
  const zip = await unzip(buffer);
  const wb = xml(await zip.text('xl/workbook.xml'));
  const rels = xml(await zip.text('xl/_rels/workbook.xml.rels'));
  const relTarget = new Map([...rels.getElementsByTagName('Relationship')].map((r) => [r.getAttribute('Id'), r.getAttribute('Target')]));
  const shared = [];
  if (zip.has('xl/sharedStrings.xml')) {
    const ss = xml(await zip.text('xl/sharedStrings.xml'));
    for (const si of ss.getElementsByTagName('si')) shared.push([...si.getElementsByTagName('t')].map((t) => t.textContent).join(''));
  }
  const sheets = [];
  for (const s of wb.getElementsByTagName('sheet')) {
    const rid = s.getAttribute('r:id') || s.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id');
    let target = relTarget.get(rid) || '';
    target = target.startsWith('/') ? target.slice(1) : `xl/${target}`;
    const doc = xml(await zip.text(target) || '<worksheet/>');
    const cells = new Map();
    let maxRow = 0; let maxCol = 0;
    for (const c of doc.getElementsByTagName('c')) {
      const ref = c.getAttribute('r');
      const m = /^([A-Z]+)(\d+)$/.exec(ref || '');
      if (!m) continue;
      const type = c.getAttribute('t') || 'n';
      const v = c.getElementsByTagName('v')[0]?.textContent ?? null;
      const f = c.getElementsByTagName('f')[0]?.textContent ?? null;
      let value = v;
      if (type === 's' && v !== null) value = shared[Number(v)] ?? '';
      if (type === 'inlineStr') value = [...c.getElementsByTagName('t')].map((t) => t.textContent).join('');
      if (type === 'b' && v !== null) value = v === '1' ? 'TRUE' : 'FALSE';
      cells.set(ref, { value, type, formula: f });
      maxRow = Math.max(maxRow, Number(m[2]));
      maxCol = Math.max(maxCol, colIndex(m[1]));
    }
    sheets.push({ sheetId: s.getAttribute('sheetId'), name: s.getAttribute('name'), cells, maxRow, maxCol });
  }
  return { sheets };
}

/** Prepare a sanitised-preview document: CSP blocks every remote load and script. */
export function sandboxedSrcdoc(html) {
  const csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:\">";
  // The meta precedes any supplied markup so it lands in the (implicit) head and
  // applies before the preview's own content. The iframe is also sandbox="".
  return `${csp}<meta charset="utf-8">${html}`;
}

// ---- RFC 5322 / MIME email (read-only decoding of verified original bytes) ------------------------------------
const latin1 = (u8) => Array.from(u8, (b) => String.fromCharCode(b)).join('');
const toBytes = (s) => Uint8Array.from(s, (c) => c.charCodeAt(0) & 0xff);

function decodeCharset(u8, charset) {
  try { return new TextDecoder((charset || 'utf-8').trim().toLowerCase()).decode(u8); } catch { return new TextDecoder('utf-8').decode(u8); }
}

function qpDecode(s) {
  return toBytes(s.replace(/=\r?\n/g, '').replace(/=([0-9A-Fa-f]{2})/g, (_, x) => String.fromCharCode(parseInt(x, 16))));
}

function b64Decode(s) {
  try { return toBytes(atob(s.replace(/[^A-Za-z0-9+/=]/g, ''))); } catch { return toBytes(''); }
}

/** RFC 2047 encoded words in header values (=?charset?B|Q?text?=). */
export function decodeHeaderValue(v) {
  return v.replace(/=\?([^?]+)\?([BbQq])\?([^?]*)\?=(\s+(?==\?))?/g, (_, cs, enc, txt) => decodeCharset(
    enc.toUpperCase() === 'B' ? b64Decode(txt) : qpDecode(txt.replace(/_/g, ' ')), cs));
}

function splitEntity(raw) {
  const m = /\r?\n\r?\n/.exec(raw);
  const head = m ? raw.slice(0, m.index) : raw;
  const body = m ? raw.slice(m.index + m[0].length) : '';
  const headers = {};
  for (const line of head.replace(/\r?\n[ \t]+/g, ' ').split(/\r?\n/)) {
    const i = line.indexOf(':');
    if (i > 0) { const k = line.slice(0, i).trim().toLowerCase(); if (!(k in headers)) headers[k] = line.slice(i + 1).trim(); }
  }
  return { headers, body };
}

const param = (v, name) => (new RegExp(`${name}\\s*=\\s*("([^"]*)"|[^;\\s]+)`, 'i').exec(v || '') || [])[2] ?? (new RegExp(`${name}\\s*=\\s*([^;\\s]+)`, 'i').exec(v || '') || [])[1] ?? null;

function entityText(e) {
  const enc = (e.headers['content-transfer-encoding'] || '').toLowerCase();
  const u8 = enc === 'base64' ? b64Decode(e.body) : enc === 'quoted-printable' ? qpDecode(e.body) : toBytes(e.body);
  return decodeCharset(u8, param(e.headers['content-type'], 'charset'));
}

/**
 * Decode an email's verified bytes into its headers, first text body (text/plain preferred; HTML is reduced to
 * text) and attachment names. Nothing is fetched or executed; attachments are listed, never opened.
 */
export function parseEml(buf) {
  const root = splitEntity(latin1(new Uint8Array(buf)));
  let text = null; let html = null; const attachments = [];
  const walk = (e, depth) => {
    const ct = (e.headers['content-type'] || 'text/plain').toLowerCase();
    const disp = (e.headers['content-disposition'] || '').toLowerCase();
    if (ct.startsWith('multipart/') && depth < 8) {
      const b = param(e.headers['content-type'], 'boundary');
      if (!b) return;
      for (const part of e.body.split(`--${b}`).slice(1)) {
        if (part.startsWith('--')) break;
        walk(splitEntity(part.replace(/^\r?\n/, '')), depth + 1);
      }
      return;
    }
    const name = param(e.headers['content-disposition'], 'filename') || param(e.headers['content-type'], 'name');
    if (disp.startsWith('attachment') || (name && !ct.startsWith('text/'))) { attachments.push(decodeHeaderValue(name || 'attachment')); return; }
    if (ct.startsWith('text/plain') && text === null) text = entityText(e);
    else if (ct.startsWith('text/html') && html === null) html = entityText(e);
  };
  walk(root, 0);
  if (text === null && html !== null) {
    text = new DOMParser().parseFromString(html, 'text/html').body.textContent || '';
  }
  const hdr = (k) => (root.headers[k] ? decodeHeaderValue(root.headers[k]) : null);
  return { from: hdr('from'), to: hdr('to'), cc: hdr('cc'), date: hdr('date'), subject: hdr('subject'), text: text ?? '', attachments };
}
