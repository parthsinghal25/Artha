import { boot } from './app.js';

boot().catch(() => {
  const el = document.getElementById('app');
  el.textContent = 'The workspace could not start: contracts or configuration were unavailable.';
});
