// Modal drawer using the native <dialog> element: focus is trapped by the
// browser, Escape closes it, and focus returns to the trigger on close.

import { h } from './dom.js';

export function openDrawer({ title, body, trigger, wide = false, onClose }) {
  let finished = false;
  // Runs once, from the Close button directly or from the dialog's close event (Escape); the event can be
  // deferred to the next rendering opportunity, so the button does not wait for it.
  const finish = () => {
    if (finished) return;
    finished = true;
    if (dialog.open) dialog.close();
    dialog.remove();
    if (dialog.dataset.silent) return; // closed by navigation, not by the user
    if (trigger && document.contains(trigger)) trigger.focus();
    if (onClose) onClose();
  };
  const dialog = h('dialog', { class: `drawer${wide ? ' drawer-wide' : ''}`, 'aria-labelledby': 'drawer-title' },
    h('header', { class: 'drawer-head' },
      h('h2', { id: 'drawer-title' }, title),
      h('button', { type: 'button', class: 'icon-button', 'aria-label': 'Close', onclick: finish }, '×')),
    h('div', { class: 'drawer-body' }, body));
  dialog.addEventListener('cancel', (e) => { e.preventDefault(); finish(); }); // Escape
  dialog.addEventListener('close', finish);
  document.body.appendChild(dialog);
  dialog.showModal();
  return dialog;
}

/** Close every drawer immediately without user-close side effects (navigation/teardown). */
export function closeAllDrawers() {
  for (const d of document.querySelectorAll('dialog.drawer')) {
    d.dataset.silent = '1';
    d.close();
    d.remove();
  }
}
