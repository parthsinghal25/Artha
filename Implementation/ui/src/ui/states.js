// Distinct, non-zero interface states (Shared DECISIONS §9). None of these
// renders a number; each says what the user can still do.

import { h } from './dom.js';
import { Outcome } from '../api/client.js';

const ERROR_TEXT = {
  RESOURCE_UNAVAILABLE: ['Not available', 'This item is not available in your current access scope.'],
  CONTEXT_EXPIRED: ['Context expired', 'The analysis context expired. Reload this view to resolve it again.'],
  CONTEXT_INCOMPATIBLE: ['Incompatible context', 'This selection cannot be resolved with the current dates, mode or versions.'],
  INVALID_REQUEST: ['Request not accepted', 'The service rejected this request shape. This is reported as a UI defect.'],
  DEFINITION_UNAVAILABLE: ['Measure not available', 'This measure or parameter is not supported by the current release.'],
  HISTORY_UNAVAILABLE: ['No stored history', 'No analysis version was published by the selected as-reported date. Reconstruction is not substituted.'],
  NOT_CALCULATED: ['Not calculated', 'The service has not published this result for the current release and analysis set.'],
  INTEGRITY_FAILURE: ['Integrity check failed', 'The service withheld this representation because an integrity check failed.'],
  UNSUPPORTED_REPRESENTATION: ['Unsupported format', 'This representation cannot be displayed safely. Other representations may still be available.'],
  LIMIT_REACHED: ['Limit reached', 'The service stopped this request at a processing limit.'],
  INTERNAL_FAILURE: ['Service failure', 'The service could not complete this request.'],
};

const TRANSPORT_TEXT = {
  not_configured: ['Analysis service not connected', 'No AI/backend service is configured for this UI. Figures, findings and sources appear only from the accepted service; nothing is shown in its place.'],
  unreachable: ['Analysis service unreachable', 'The configured service did not respond. Try again shortly.'],
  unauthenticated: ['Sign-in required', 'You are not signed in, or your session has expired or been signed out. Sign in through the analysis service, then reload this view.'],
  forbidden: ['Request refused by the service', 'The service refused this request because its session security check failed (missing or outdated security token, or an unrecognised origin). Reload the page; if it persists, sign in again. Nothing is shown in place of the result.'],
  http_error: ['Service error', 'The service returned a non-contract response.'],
};

let signInUrl = null;
/** Backend-owned sign-in page (from runtime config); the UI only links to it. */
export function setSignInUrl(url) { signInUrl = typeof url === 'string' && /^https?:\/\//.test(url) ? url : null; }

export function stateBox(kind, title, detail, { compact = false, extra = null, code = null } = {}) {
  return h('div', { class: `state state-${kind}${compact ? ' state-compact' : ''}`, role: 'status', dataset: { state: kind, code: code || '' } },
    h('p', { class: 'state-title' }, title),
    detail ? h('p', { class: 'state-detail' }, detail) : null,
    extra);
}

export function loading(label = 'Loading this view') {
  return h('div', { class: 'state state-loading', role: 'status', 'aria-busy': 'true', dataset: { state: 'loading' } },
    h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', {}, label));
}

/** Render any non-ok outcome as its distinct state. */
export function outcomeState(out, opts = {}) {
  if (!out) return stateBox('internal', 'No response', null, opts);
  switch (out.kind) {
    case 'no_release':
      return stateBox('no-release', 'No accepted release configured',
        'No accepted release identifier is configured for this UI. No figures, findings or sample data are shown.', opts);
    case Outcome.TRANSPORT: {
      const [t, d] = TRANSPORT_TEXT[out.reason] || TRANSPORT_TEXT.http_error;
      const signIn = (out.reason === 'unauthenticated' || out.reason === 'forbidden') && signInUrl
        ? h('p', {}, h('a', { href: signInUrl, class: 'button-primary', rel: 'noopener', dataset: { action: 'sign-in' } }, 'Sign in to the analysis service')) : null;
      return stateBox(`transport-${out.reason}`, t, d, { ...opts, extra: signIn });
    }
    case Outcome.UNAVAILABLE: {
      if (out.error.code === 'INVALID_REQUEST' && /^Unsupported parameter for this definition/.test(out.error.message || '')) {
        // The release refuses this exact filter/parameter/grouping for this measure: a capability gap,
        // not a UI defect. The meaning-bearing parameter is not dropped to obtain a number.
        return stateBox('error-unsupported-request', 'Not supported for this exact request',
          'The release does not support this measure with the requested parameter, filter or grouping. Nothing is dropped or substituted to obtain a number.', {
            ...opts, code: out.error.code,
            extra: h('details', { class: 'state-details' }, h('summary', {}, 'Service detail'), h('p', {}, out.error.message),
              h('p', { class: 'state-ref' }, `Reference ${out.error.correlation_id}`)),
          });
      }
      const [t, d] = ERROR_TEXT[out.error.code] || ERROR_TEXT.INTERNAL_FAILURE;
      return stateBox(`error-${out.error.code.toLowerCase()}`, t, d, {
        ...opts,
        code: out.error.code,
        extra: h('p', { class: 'state-ref' }, `Reference ${out.error.correlation_id}${out.error.retryable ? ' · retry may succeed' : ''}`),
      });
    }
    case Outcome.CONTRACT_VIOLATION:
      return stateBox('contract', 'Response withheld — contract mismatch',
        `The ${out.side} did not match its contract (frozen Shared 1.0.0 or intelligence companion 1.1.0), so it is not displayed.`, {
          ...opts,
          extra: h('details', { class: 'state-details' }, h('summary', {}, 'Technical detail'),
            h('ul', {}, (out.errors || []).slice(0, 8).map((e) => h('li', {}, e)))),
        });
    case 'unsupported':
      return stateBox('unsupported', 'Measure not supported by this release',
        `The current context does not list ${out.measure} as a supported definition. No proxy measure is substituted.`, opts);
    case 'unsupported_grouping':
      return stateBox('unsupported-grouping', 'Not offered for this grouping',
        `Linked arrivals are composed from one service request per receipt period; ${out.granularity} receipt periods are not offered. Choose monthly, quarterly or annual.`, opts);
    case 'publication_changed':
      return stateBox('publication-changed', 'A newer completed publication is being served',
        `This view is bound to ${out.bound?.publication_id || 'the earlier publication'}; the service now serves ${out.publication?.publication_id}. Results are not mixed: use “Load completed publication” to move every view together.`, opts);
    case 'incomplete':
      return stateBox('incomplete', 'Published section incomplete',
        `The ${out.section || 'requested'} section could not be fully paged (${out.reason}). A partial list is not shown as a smaller complete population.`, opts);
    case 'authority_changed':
      return stateBox('authority', 'Access changed', 'Your access changed; views reload without previously shown content.', opts);
    case 'missing':
      return stateBox('missing', 'Selection not in this publication', 'This selection does not exist in the bound publication (for example a bucket or record from another period). Nothing is substituted.', opts);
    case Outcome.ABORTED:
      return stateBox('aborted', 'Superseded', 'A newer selection replaced this request.', opts);
    default:
      return stateBox('internal', 'Unexpected state', null, opts);
  }
}

/** Badge for result computation/assessment states shown beside a populated view. */
export function computationBadge(result) {
  const s = result.computation_state;
  if (s === 'ready') return null;
  const text = { partial: 'Partial result', failed: 'Calculation failed', cancelled: 'Calculation cancelled', limit_reached: 'Stopped at limit' }[s] || s;
  return h('span', { class: `badge badge-${s}` }, text);
}

export function pointStateBadge(point) {
  if (point.state === 'known') return null;
  const text = { partial: 'Partial', unavailable: 'Unavailable', not_applicable: 'Not applicable' }[point.state] || point.state;
  return h('span', { class: `badge badge-${point.state}`, title: point.reason || '' }, text);
}
