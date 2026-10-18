// Evidence vocabulary used everywhere a finding is explained. Every tag pairs a
// colour with a text label and an icon, so colour never carries meaning alone.
//
//   Source statement  — text recorded in a held source (a cited passage)
//   AI assessment     — the published model interpretation (reasons, mechanisms, summaries)
//   Calculated measure — a count/share/amount computed from published sets
//   Uncertain         — published as uncertain, or an open question
//   Not assessed      — no assessment exists (holdout, pending, unassessed pair)
//   Contradiction     — published conflicting evidence or an explicit adverse state

import { h } from './dom.js';
import { icon } from './icons.js';

export const KINDS = {
  source: { label: 'Source statement', icon: 'quote', cls: 'k-source' },
  ai: { label: 'AI assessment', icon: 'assess', cls: 'k-ai' },
  calc: { label: 'Calculated measure', icon: 'measure', cls: 'k-calc' },
  uncertain: { label: 'Uncertain', icon: 'uncertain', cls: 'k-uncertain' },
  na: { label: 'Not assessed', icon: 'unassessed', cls: 'k-na' },
  conflict: { label: 'Contradiction', icon: 'conflict', cls: 'k-conflict' },
};

/** Small labelled tag, e.g. kindTag('ai') → "✦ AI assessment". `text` overrides the label. */
export function kindTag(kind, text = null) {
  const k = KINDS[kind];
  return h('span', { class: `ktag ${k.cls}`, dataset: { kind } }, icon(k.icon, { size: 13 }), h('span', {}, text || k.label));
}

/** Membership state as published, in the shared vocabulary (excluded is assessed, never "not assessed"). */
export function membershipTag(state) {
  if (state === 'included') return kindTag('ai', 'Supported');
  if (state === 'uncertain') return kindTag('uncertain', 'Uncertain');
  if (state === 'excluded') return kindTag('ai', 'Excluded after assessment');
  return kindTag('na', 'Not assessed');
}

/**
 * Service-action completion wording. Administrative closure is not proof of
 * completion, and missing completion evidence is unknown — never "unfinished".
 * Returns { text, kind, evidenced } with the published value kept alongside.
 */
export function completionDisplay(value) {
  switch (value) {
    case 'confirmed_complete': return { text: 'Completion evidenced', kind: 'source', evidenced: true };
    case 'partially_complete': return { text: 'Partly evidenced', kind: 'uncertain', evidenced: true };
    case 'attempted': return { text: 'Attempted · completion not evidenced', kind: 'uncertain', evidenced: false };
    case 'pending': return { text: 'Pending · completion not evidenced', kind: 'uncertain', evidenced: false };
    case 'not_started': return { text: 'Not started · per cited statements', kind: 'uncertain', evidenced: false };
    case 'not_assessed': return { text: 'Not assessed', kind: 'na', evidenced: false };
    default: return { text: 'Unknown — no completion evidence', kind: 'na', evidenced: false };
  }
}

export const MODEL_NOTE = 'Model-assessed · not independently reviewed';

let infoN = 0;
/**
 * Compact "ⓘ" disclosure: the caveat stays one click away instead of printed in
 * full on every panel. Keyboard operable; the note is announced when opened.
 */
export function info(text, name = 'About this') {
  if (!text) return null;
  infoN += 1;
  const id = `info-${infoN}`;
  const body = h('span', { class: 'info-body', id, hidden: '' }, text);
  const btn = h('button', { type: 'button', class: 'info-btn', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': name, title: name,
    onclick: () => { const open = body.hidden; body.hidden = !open; btn.setAttribute('aria-expanded', String(open)); } }, 'i');
  return h('span', { class: 'info' }, btn, body);
}
