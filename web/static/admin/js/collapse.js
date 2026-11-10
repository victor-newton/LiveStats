// @ts-check
// Área recolhível que abre expandindo a altura e fecha recolhendo com a mesma curva (grid 0fr -> 1fr, sem altura
// fixa). O conteúdo ganha um leve fade e deslize. O gatilho recebe `aria-expanded` e `aria-controls`; a área fechada
// fica `inert` e, no fim da animação, `hidden` (some do leitor de tela e da ordem de tabulação). Com
// prefers-reduced-motion não há transição: abre e fecha na hora.

import { h } from './h.js';
import { reduced } from './util.js';

const MS = 280;

/**
 * @typedef {object} Collapse
 * @property {HTMLElement} root
 * @property {(open: boolean) => void} set
 * @property {() => boolean} isOpen
 * @property {(trigger: HTMLElement) => void} bind liga um gatilho: mantém aria-expanded e aria-controls em dia
 */

/**
 * @param {Node} content
 * @param {string} [id]
 * @param {{onShow?: () => void, onHidden?: () => void, open?: boolean}} [o] onShow: antes de abrir; onHidden: depois de fechado
 * @returns {Collapse}
 */
export function collapse(content, id, o = {}) {
  const inner = h('div', { class: 'collapse-in' }, content);
  const root = h('div', { class: 'collapse', id }, inner);
  /** @type {HTMLElement[]} */
  const triggers = [];
  /** @type {number | null} */
  let timer = null;
  let opened = false;

  const sync = () => {
    for (const t of triggers) t.setAttribute('aria-expanded', String(opened));
  };
  const finishClose = () => {
    timer = null;
    if (opened) return;
    root.hidden = true;
    if (o.onHidden) o.onHidden();
  };
  /** @param {boolean} open */
  const set = (open) => {
    if (open === opened) return;
    opened = open;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    sync();
    if (open) {
      if (o.onShow) o.onShow();
      root.hidden = false;
      inner.removeAttribute('inert');
      void root.offsetHeight; // a altura parte de 0 e anima até o conteúdo
      root.classList.add('open');
      return;
    }
    inner.setAttribute('inert', '');
    root.classList.remove('open');
    if (reduced()) finishClose();
    else timer = window.setTimeout(finishClose, MS + 40);
  };

  root.hidden = !o.open;
  if (o.open) { opened = true; root.classList.add('open'); } else inner.setAttribute('inert', '');
  return {
    root,
    set,
    isOpen: () => opened,
    bind(trigger) {
      triggers.push(trigger);
      if (root.id) trigger.setAttribute('aria-controls', root.id);
      trigger.setAttribute('aria-expanded', String(opened));
    },
  };
}
