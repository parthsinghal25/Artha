// Source inspector: one common drawer, opened from any case/finding/record via
// the `src` (+ optional `cite`) URL params so Back/Forward and direct links
// work. Quoted passage, full text and original document stay separate
// representations, but the user has one "View document" action: the best
// permitted representation is chosen and the quote located automatically.

import { h, mount } from '../ui/dom.js';
import { Outcome } from '../api/client.js';
import { loading, outcomeState, stateBox } from '../ui/states.js';
import { openDrawer } from '../ui/drawer.js';
import { decodeRef, decodeCitation, encodeRef } from '../ui/router.js';
import { assetViewer, redeem, chooseAction, describeAction } from '../viewers/viewers.js';
import { codepointSlice, jsonPointer, instant, timeValue, humanise, shortHash } from '../lib/format.js';

const CONTEXT_CPS = 280;

export function parseSrcParam(v) {
  if (!v) return null;
  const [id, sha] = v.split('~');
  if (!id) return null;
  return { source_id: id, content_sha256: sha && /^[0-9a-f]{64}$/.test(sha) ? sha : null };
}

/** Open overlays requested by URL params after the page renders. */
export function openOverlays(app, loc) {
  const src = parseSrcParam(loc.params.get('src'));
  const asset = loc.params.get('asset');
  const close = () => app.router.setParams({ src: null, cite: null, asset: null, parent: null });
  if (src) openSourceInspector(app, loc, src, close);
  else if (asset) openAssetDrawer(app, loc, asset, close);
}

function triggerFor(loc) {
  const key = loc.params.get('src') ? `src:${parseSrcParam(loc.params.get('src')).source_id}` : `asset:${loc.params.get('asset')}`;
  return document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`) || document.querySelector('main h1');
}

/**
 * Context for a source/file opened on this page: the selected population's
 * child context, except on an explicit later-assessment view (`later=1`),
 * whose citations are read under the current scope and labelled so.
 */
function overlayRef(loc) {
  return loc.params.get('later') === '1' ? null : decodeRef(loc.params.get('ref'));
}

async function openSourceInspector(app, loc, src, onClose) {
  const ref = overlayRef(loc);
  const anchor = decodeCitation(loc.params.get('cite'));
  const parent = parseSrcParam(loc.params.get('parent'));
  const body = h('div', { class: 'stack' }, loading('Loading source'));
  openDrawer({ title: `Source ${src.source_id}`, body, trigger: triggerFor(loc), wide: true, onClose });
  const citation = anchor && src.content_sha256 ? { source_id: src.source_id, content_sha256: src.content_sha256, anchor } : null;
  const out = await app.op(ref, (c) => ({ operation: 'source.get', context_id: c.context_id, source_id: src.source_id, content_sha256: src.content_sha256, citation }));
  if (out.kind !== Outcome.OK) { mount(body, outcomeState(out)); return; }
  if (loc.params.get('later') === '1' && loc.params.get('ref')) {
    body.before(h('p', { class: 'chip chip-warn' }, 'Opened from a later assessment — read under the current scope, not the selected population’s cutoff.'));
  }
  const p = out.payload;
  const rec = p.record;
  const d = rec.data;
  const env = rec.envelope;
  const partialExtraction = p.extractions.some((x) => x.state === 'partial');

  // ---- 1. citation --------------------------------------------------------
  let citationSection;
  if (p.citation_state === 'not_requested') {
    citationSection = h('p', { class: 'muted' }, 'Opened for reading; no specific passage was quoted.');
  } else if (p.citation_state === 'invalid') {
    citationSection = stateBox('citation-invalid', 'Citation does not resolve in this source version', 'The full source is shown without a highlight. No nearby text is substituted.', { compact: true });
  } else if (p.citation.anchor.kind === 'text') {
    const sl = codepointSlice(d.text, p.citation.anchor.start_codepoint, p.citation.anchor.end_codepoint);
    citationSection = sl
      ? h('blockquote', { class: 'cited' }, contextBefore(sl.before), h('mark', {}, sl.match), contextAfter(sl.after))
      : stateBox('citation-invalid', 'Citation span is outside the source text', null, { compact: true });
  } else {
    const val = jsonPointer(d, p.citation.anchor.json_pointer);
    citationSection = val === undefined
      ? stateBox('citation-invalid', 'Field pointer did not resolve', `Pointer ${p.citation.anchor.json_pointer}`, { compact: true })
      : h('div', { class: 'cited' }, h('p', { class: 'mono' }, p.citation.anchor.json_pointer), h('pre', {}, JSON.stringify(val, null, 2)));
  }

  // ---- 2. full source -------------------------------------------------------
  const fullText = p.citation_state === 'valid' && p.citation.anchor.kind === 'text'
    ? (() => {
      const sl = codepointSlice(d.text, p.citation.anchor.start_codepoint, p.citation.anchor.end_codepoint);
      return sl ? h('pre', { class: 'source-text' }, sl.before, h('mark', { id: 'full-hit' }, sl.match), sl.after) : h('pre', { class: 'source-text' }, d.text);
    })()
    : h('pre', { class: 'source-text' }, d.text || '');
  // Structured fields of the source record itself, shown as recorded (a field list, not analysis).
  const facts = d.native_facts.length
    ? h('dl', { class: 'facts-list' }, d.native_facts.map((f, i) => {
      const hit = p.citation_state === 'valid' && p.citation.anchor.kind === 'field' && p.citation.anchor.json_pointer.startsWith(`/native_facts/${i}`);
      return h('div', { class: hit ? 'hit-row' : null },
        h('dt', {}, humanise(f.field_key)),
        h('dd', {}, f.value.value === null ? `— (${humanise(f.value.state)}${f.value.missing_reason ? `: ${f.value.missing_reason}` : ''})` : Array.isArray(f.value.value) ? f.value.value.join(', ') : String(f.value.value),
          h('span', { class: 'muted mono small' }, ` ${f.entity_id}`)));
    }))
    : null;

  // ---- 3. original document: one action; the representation is chosen for the user
  const viewerSlot = h('div', { class: 'viewer-slot' });
  const primary = chooseAction(p.permitted_asset_actions);
  const originalPermitted = p.permitted_asset_actions.some((a) => a.representation === 'original');
  const others = p.permitted_asset_actions.filter((a) => a !== primary);
  const documentActions = primary
    ? h('div', { class: 'doc-actions' },
      h('button', { type: 'button', class: 'button-primary', dataset: { action: 'view-document' }, onclick: () => viewDocument(app, ref, p, primary, viewerSlot) }, 'View document'),
      h('span', { class: 'muted' }, p.citation_state === 'valid' ? `${describeAction(primary)} · opens at the quoted passage when it can be located` : describeAction(primary)),
      others.length ? h('details', { class: 'other-formats' }, h('summary', {}, 'Other formats'),
        h('ul', {}, others.map((o) => h('li', {}, h('button', { type: 'button', class: 'link-button', onclick: () => viewDocument(app, ref, p, o, viewerSlot) }, describeAction(o)))))) : null)
    : h('p', { class: 'muted' }, 'No document file is available to you for this source. The available source text is shown above; extraction may be incomplete.');

  const attachments = p.permitted_attachments.length
    ? h('ul', { class: 'attachments' }, p.permitted_attachments.map((a) => h('li', {},
      h('span', { class: 'badge' }, humanise(a.relation)), ' ',
      a.advertised_filename ?? (a.child_asset_version_id ? 'File name not supplied by the service' : 'Unnamed attachment'),
      a.child_asset_version_id ? h('span', { class: 'muted mono', dataset: { assetVersion: a.child_asset_version_id } }, ` ${a.child_asset_version_id}`) : null,
      a.mime_part_path ? h('span', { class: 'muted mono' }, ` part ${a.mime_part_path}`) : null,
      a.relation === 'advertised_unheld' || !a.child_asset_version_id
        ? h('span', { class: 'muted' }, ' — mentioned in the message but no file is held')
        : [' ', h('button', { type: 'button', class: 'link-button', onclick: () => openChild(app, ref, a, viewerSlot) }, 'View attachment')],
      a.permitted_copy_of_attachment_occurrence_id ? h('span', { class: 'muted' }, ` · copy of ${a.permitted_copy_of_attachment_occurrence_id}`) : null)))
    : h('p', { class: 'muted' }, 'No permitted attachments.');

  const extractions = p.extractions.length
    ? h('ul', {}, p.extractions.map((x) => h('li', {},
      h('span', { class: `badge badge-x-${x.state}` }, humanise(x.state)), ` ${x.extractor} ${x.extractor_version} · processed ${instant(x.processed_at)}`,
      x.omissions.length ? h('ul', { class: 'limitations' }, x.omissions.map((o) => h('li', {}, `Omitted: ${o}`))) : null,
      x.lineage_disclosure === 'limited' ? h('p', { class: 'muted' }, 'Lineage disclosure limited.') : null)))
    : h('p', { class: 'muted' }, 'No extraction record is permitted for this source.');

  const here = window.location.pathname;
  mount(body,
    parent ? h('p', {}, h('a', { href: app.link(here, { ref: ref ? encodeRef(ref) : null, src: `${parent.source_id}${parent.content_sha256 ? `~${parent.content_sha256}` : ''}` }), 'data-nav': '' }, '← Back to parent message')) : null,
    h('dl', { class: 'kv compact' },
      h('dt', {}, 'Family / type'), h('dd', {}, `${d.source_family} · ${humanise(d.subtype)}`),
      h('dt', {}, 'Author role'), h('dd', {}, humanise(d.author_role)),
      h('dt', {}, 'Occurred'), h('dd', {}, env.occurred_at ? instant(env.occurred_at) : env.occurrence_date ? `${env.occurrence_date} (date only)` : 'Unknown'),
      h('dt', {}, 'Available (scenario time)'), h('dd', {}, instant(env.available_at)),
      h('dt', {}, 'Recorded'), h('dd', {}, instant(env.recorded_at)),
      h('dt', {}, 'Cases'), h('dd', {}, d.case_ids.join(', ') || '—'),
      d.copy_of_source_id ? [h('dt', {}, 'Copy of'), h('dd', {}, h('a', { href: app.link(here, { ref: ref ? encodeRef(ref) : null, src: d.copy_of_source_id }), 'data-nav': '' }, d.copy_of_source_id))] : null,
      h('dt', {}, 'Content hash'), h('dd', { class: 'mono' }, shortHash(p.source_binding.content_sha256)),
      env.access.redaction_state !== 'none' ? [h('dt', {}, 'Redaction'), h('dd', {}, humanise(env.access.redaction_state))] : null),
    h('section', {}, h('h3', {}, '1 · Quoted passage'), citationSection),
    h('section', {}, h('h3', {}, partialExtraction ? '2 · Extracted text' : '2 · Full text'),
      partialExtraction ? h('p', { class: 'chip chip-warn' }, 'Partial extraction — some original content may be missing or inaccurate. See Processing details for the recorded limitations.') : null,
      fullText, facts),
    h('section', {}, h('h3', {}, originalPermitted ? '3 · Original document' : '3 · Available document'),
      primary && !originalPermitted ? h('p', { class: 'muted' }, 'The exact original file is unavailable in this view; the permitted readable version or redacted copy is labelled below.') : null,
      documentActions,
      viewerSlot,
      h('h4', {}, 'Attachments'), attachments,
      h('details', { class: 'tech-details' }, h('summary', {}, 'Processing details'), extractions)));
  document.getElementById('full-hit')?.scrollIntoView({ block: 'center' });
}

function contextBefore(s) {
  const cps = Array.from(s);
  return cps.length > CONTEXT_CPS ? `…${cps.slice(-CONTEXT_CPS).join('')}` : s;
}
function contextAfter(s) {
  const cps = Array.from(s);
  return cps.length > CONTEXT_CPS ? `${cps.slice(0, CONTEXT_CPS).join('')}…` : s;
}

function refetcher(app, ref, assetVersionId) {
  return () => app.op(ref, (c) => ({ operation: 'asset.get', context_id: c.context_id, asset_version_id: assetVersionId, action: 'inline' }), { cache: false });
}

function showAsset(app, ref, action, locator, slot) {
  mount(slot, assetViewer(app, action, { locator, refetch: refetcher(app, ref, action.asset_version_id) }));
  slot.scrollIntoView({ block: 'start' });
}

/**
 * Open a representation. For the original, with a valid quoted passage, find
 * where that passage sits automatically: a verified mapping for this exact file
 * version, else ask the service (location.resolve). A location is applied only
 * when verified; otherwise the whole document opens and says why.
 */
async function viewDocument(app, ref, p, action, slot) {
  let locator = null;
  if (action.representation === 'original' && p.citation_state === 'valid') {
    mount(slot, loading('Finding the quoted passage in the document'));
    const forThisFile = (m) => m.asset_version_id === action.asset_version_id && m.bytes_sha256 === action.bytes_sha256;
    locator = p.mappings.find((m) => m.state === 'verified' && forThisFile(m)) || p.mappings.find(forThisFile) || null;
    const ex = p.extractions.find((x) => x.permitted_asset_version_id === action.asset_version_id);
    if (!locator && ex) {
      const r = await app.op(ref, (c) => ({ operation: 'location.resolve', context_id: c.context_id, citation: p.citation, extraction_id: ex.extraction_id, asset_version_id: action.asset_version_id }), { cache: false });
      if (r.kind === Outcome.OK && (r.payload.state !== 'verified' || forThisFile(r.payload))) locator = r.payload;
    }
  }
  showAsset(app, ref, action, locator, slot);
}

async function openChild(app, ref, attachment, slot) {
  mount(slot, loading('Requesting attachment'));
  const out = await refetcher(app, ref, attachment.child_asset_version_id)();
  if (out.kind !== Outcome.OK) { mount(slot, outcomeState(out)); return; }
  mount(slot, h('p', { class: 'muted' }, `Attachment of source ${attachment.parent_source_id} (${humanise(attachment.relation)}).`),
    assetViewer(app, out.payload, { locator: null, refetch: refetcher(app, ref, attachment.child_asset_version_id), filename: attachment.advertised_filename || '' }));
}

async function openAssetDrawer(app, loc, assetVersionId, onClose) {
  const ref = overlayRef(loc);
  const body = h('div', { class: 'stack' }, loading('Requesting file'));
  openDrawer({ title: 'File', body, trigger: triggerFor(loc), wide: true, onClose });
  const out = await refetcher(app, ref, assetVersionId)();
  if (out.kind !== Outcome.OK) { mount(body, outcomeState(out)); return; }
  const canDownload = (app.rootContext?.capabilities || []).includes('download');
  mount(body,
    canDownload ? h('button', {
      type: 'button', class: 'button-secondary',
      onclick: async () => {
        const dl = await app.op(ref, (c) => ({ operation: 'asset.get', context_id: c.context_id, asset_version_id: assetVersionId, action: 'download' }), { cache: false });
        if (dl.kind !== Outcome.OK) { body.prepend(outcomeState(dl, { compact: true })); return; }
        const got = await redeem(app, dl.payload, refetcher(app, ref, assetVersionId));
        if (got.kind !== 'bytes') { body.prepend(outcomeState(got.kind === 'undocumented' ? { kind: 'transport', reason: 'http_error' } : got, { compact: true })); return; }
        const a = h('a', { href: URL.createObjectURL(new Blob([got.buffer], { type: dl.payload.media_type })), download: '' });
        document.body.appendChild(a); a.click(); a.remove();
      },
    }, 'Download') : null,
    assetViewer(app, out.payload, { locator: null, refetch: refetcher(app, ref, assetVersionId) }));
}

export { timeValue };
