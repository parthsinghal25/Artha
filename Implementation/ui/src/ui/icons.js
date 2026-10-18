// Monochrome stroke icons (24×24 grid), drawn with currentColor. Decorative:
// every icon sits beside a text label and is hidden from assistive technology.

import { h } from './dom.js';

const PATHS = {
  overview: ['M4 4h7v7H4z', 'M13 4h7v4h-7z', 'M13 10h7v10h-7z', 'M4 13h7v7H4z'],
  issues: ['M4 19h16', 'M6 16V9', 'M11 16V5', 'M16 16v-4', 'M20 16v-7'],
  experience: ['M12 7v5l3 2', 'M12 3a9 9 0 1 0 0.01 0'],
  impact: ['M4 17l5-5 4 3 7-8', 'M15 7h5v5'],
  afca: ['M4 21h16', 'M6 21V10', 'M18 21V10', 'M12 21V10', 'M3 10l9-6 9 6z'],
  cases: ['M6 3h9l4 4v14H6z', 'M14 3v5h5', 'M9 13h7', 'M9 17h5'],
  menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
  prev: ['M15 6l-6 6 6 6'],
  next: ['M9 6l6 6-6 6'],
  signal: ['M12 4l9 16H3z', 'M12 10v4', 'M12 17v.01'],
  doc: ['M7 3h7l4 4v14H7z', 'M14 3v4h4'],
  link: ['M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1', 'M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1'],
  back: ['M19 12H5', 'M11 6l-6 6 6 6'],
  search: ['M11 4a7 7 0 1 0 0.01 0', 'M20 20l-4-4'],
  // evidence vocabulary (always shown beside its text label)
  quote: ['M7 7h4v4c0 3-1.5 5-4 6', 'M14 7h4v4c0 3-1.5 5-4 6'],
  assess: ['M12 3l2.2 5.3L20 9l-4.4 3.8L17 18.5 12 15.6 7 18.5l1.4-5.7L4 9l5.8-.7z'],
  measure: ['M5 19V5', 'M5 19h14', 'M9 15v-4', 'M13 15V8', 'M17 15v-6'],
  uncertain: ['M12 3a9 9 0 1 0 0.01 0', 'M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14', 'M12 17v.01'],
  unassessed: ['M12 3a9 9 0 1 0 0.01 0', 'M8 12h8'],
  conflict: ['M12 3a9 9 0 1 0 0.01 0', 'M9 9l6 6', 'M15 9l-6 6'],
  status: ['M4 6h16', 'M4 12h10', 'M4 18h6'],
  check: ['M5 12l4 4 10-10'],
  member: ['M12 12a4 4 0 1 0 0.01 0', 'M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6'],
  lens: ['M10.5 4a6.5 6.5 0 1 0 0.01 0', 'M20 20l-4.8-4.8', 'M8 10.5h5'],
  expand: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5'],
};

export function icon(name, { size = 18 } = {}) {
  return h('svg', { class: 'icon', viewBox: '0 0 24 24', width: size, height: size, 'aria-hidden': 'true', focusable: 'false' },
    (PATHS[name] || []).map((d) => h('path', { d })));
}
