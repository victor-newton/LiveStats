// @ts-check
// infoTip(texto): botão "i" que guarda a explicação de uma seção. Desktop: aparece ao passar o mouse ou focar com o
// teclado. Celular: aparece ao TOCAR (ou segurar) e fecha ao tocar de novo, tocar fora ou com Esc. O painel é um
// `role="tooltip"` ligado ao botão por `aria-describedby` enquanto está aberto. A área de toque tem 44x44 no celular.
// O texto entra por textContent.

import { h } from './h.js';
import { openFloating } from './float.js';

let seq = 0;
/** @type {WeakMap<HTMLElement, HTMLElement>} */
const pops = new WeakMap();
const HOVER_MS = 120;

/**
 * @param {string} text explicação (texto puro)
 * @param {{label?: string, align?: 'start' | 'center' | 'end'}} [o] label: nome acessível do botão
 * @returns {HTMLButtonElement}
 */
export function infoTip(text, o = {}) {
  const id = `info-tip-${++seq}`;
  const btn = /** @type {HTMLButtonElement} */ (h('button', {
    type: 'button', class: 'info-tip', 'aria-label': o.label || 'Mais informações', 'aria-expanded': 'false', 'data-tip': text,
  }, h('span', { class: 'info-i', 'aria-hidden': 'true', text: 'i' })));
  const pop = h('div', { class: 'info-pop float-pop', role: 'tooltip', id, hidden: true, text });
  pops.set(btn, pop);
  /** @type {null | {close: () => void, reposition: () => void}} */
  let fl = null;
  let pinned = false;
  /** @type {number | null} */
  let timer = null;

  const show = () => {
    if (fl) return;
    btn.setAttribute('aria-describedby', id);
    btn.setAttribute('aria-expanded', 'true');
    fl = openFloating({
      anchor: btn,
      pop,
      align: o.align || 'center',
      onClose: () => {
        fl = null;
        pinned = false;
        btn.removeAttribute('aria-describedby');
        btn.setAttribute('aria-expanded', 'false');
      },
    });
  };
  const hide = () => {
    if (timer !== null) { clearTimeout(timer); timer = null; }
    if (fl) fl.close();
  };

  btn.addEventListener('pointerenter', (ev) => {
    if (ev.pointerType !== 'mouse' || fl) return;
    timer = window.setTimeout(show, HOVER_MS);
  });
  btn.addEventListener('pointerleave', (ev) => {
    if (ev.pointerType !== 'mouse') return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    if (!pinned) hide();
  });
  btn.addEventListener('focus', () => { if (btn.matches(':focus-visible')) show(); });
  btn.addEventListener('blur', () => { if (!pinned) hide(); });
  btn.addEventListener('click', () => {
    if (fl && pinned) { hide(); return; }
    if (!fl) show();
    pinned = true;
  });
  btn.addEventListener('contextmenu', (ev) => ev.preventDefault()); // segurar no celular não abre o menu do sistema
  return btn;
}

/**
 * Troca o texto de uma dica já criada (texto que muda com os dados).
 * @param {HTMLElement} btn botão devolvido por `infoTip`
 * @param {string} text
 */
export function setInfoTip(btn, text) {
  const pop = pops.get(btn);
  if (pop && pop.textContent !== text) pop.textContent = text;
  btn.dataset.tip = text;
}

/**
 * Título curto com o "i" ao lado (a explicação mora na dica, não numa frase fixa).
 * @param {'h1' | 'h2' | 'h3'} tag
 * @param {string} title
 * @param {string} tip
 * @param {string} [label] nome acessível do botão
 * @returns {HTMLElement}
 */
export function titleTip(tag, title, tip, label) {
  return h('div', { class: 'title-row' }, h(tag, { text: title }), infoTip(tip, { label: label || `Sobre: ${title}` }));
}
