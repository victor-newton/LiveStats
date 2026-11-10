// @ts-check
// Cartão com brilho (GlowCard): o visual vive em CSS (pseudo-elementos ::before/::after e a borda em gradiente radial);
// aqui só se acompanha o cursor. A posição vai para as variáveis CSS --mx/--my (CSSOM: `style.setProperty`, permitido
// pela CSP; nunca o atributo `style`) e a cor do cartão para --accent ("R,G,B"). Vale para todo elemento `.glow-card`,
// inclusive os criados depois (os ouvintes ficam no documento). Mouse (pointerenter/leave/move), toque (touchstart/
// touchmove/touchend) e teclado (foco). Escrita limitada a 1 por quadro (requestAnimationFrame).

import { COLOR_RE } from './util.js';

const CARD = '.glow-card';
const ON = 'glow-on';

/** @type {WeakMap<Element, {x: number, y: number, raf: number}>} */
const pending = new WeakMap();
let started = false;

/**
 * "#RRGGBB" -> "R,G,B" (para `rgba(var(--accent), .5)`).
 * @param {string} hex
 * @returns {string}
 */
export function rgbOf(hex) {
  const n = parseInt(hex.slice(1), 16);
  return `${n >> 16},${(n >> 8) & 255},${n & 255}`;
}

/**
 * Define a cor do brilho de um cartão (só aceita #RRGGBB).
 * @param {HTMLElement} el
 * @param {string} hex
 */
export function setAccent(el, hex) {
  if (COLOR_RE.test(hex)) el.style.setProperty('--accent', rgbOf(hex));
}

/**
 * @param {Element} card
 * @param {number} clientX
 * @param {number} clientY
 */
function place(card, clientX, clientY) {
  const r = card.getBoundingClientRect();
  let p = pending.get(card);
  if (!p) { p = { x: 0, y: 0, raf: 0 }; pending.set(card, p); }
  p.x = clientX - r.left;
  p.y = clientY - r.top;
  if (p.raf) return;
  const q = p;
  q.raf = requestAnimationFrame(() => {
    q.raf = 0;
    const el = /** @type {HTMLElement} */ (card);
    el.style.setProperty('--mx', `${q.x.toFixed(1)}px`);
    el.style.setProperty('--my', `${q.y.toFixed(1)}px`);
  });
}

/**
 * @param {EventTarget | null} t
 * @returns {HTMLElement | null}
 */
function cardOf(t) {
  return t instanceof Element ? /** @type {HTMLElement | null} */ (t.closest(CARD)) : null;
}

/** @param {HTMLElement} card @param {boolean} on */
function setOn(card, on) {
  card.classList.toggle(ON, on);
}

/** Liga os ouvintes (uma vez só). */
export function initGlow() {
  if (started) return;
  started = true;
  const opts = { capture: true, passive: true };

  document.addEventListener('pointerenter', (ev) => {
    const t = ev.target;
    if (!(t instanceof Element) || !t.matches(CARD)) return;
    place(t, /** @type {PointerEvent} */ (ev).clientX, /** @type {PointerEvent} */ (ev).clientY);
    setOn(/** @type {HTMLElement} */ (t), true);
  }, opts);
  document.addEventListener('pointermove', (ev) => {
    const card = cardOf(ev.target);
    if (!card) return;
    place(card, ev.clientX, ev.clientY);
    if (!card.classList.contains(ON)) setOn(card, true);
  }, { passive: true });
  document.addEventListener('pointerleave', (ev) => {
    const t = ev.target;
    if (!(t instanceof Element) || !t.matches(CARD)) return;
    const card = /** @type {HTMLElement} */ (t);
    if (card.dataset.kbd === undefined) setOn(card, false);
  }, opts);

  // toque: o brilho segue o dedo enquanto ele estiver sobre o cartão
  /** @param {TouchEvent} ev */
  const touch = (ev) => {
    const tc = ev.touches[0];
    const card = cardOf(ev.target);
    if (!tc || !card) return;
    place(card, tc.clientX, tc.clientY);
    setOn(card, true);
  };
  document.addEventListener('touchstart', touch, { passive: true });
  document.addEventListener('touchmove', touch, { passive: true });
  /** @param {TouchEvent} ev */
  const touchEnd = (ev) => {
    if (ev.touches.length) return;
    for (const c of document.querySelectorAll(`${CARD}.${ON}`)) {
      if (/** @type {HTMLElement} */ (c).dataset.kbd === undefined) c.classList.remove(ON);
    }
  };
  document.addEventListener('touchend', touchEnd, { passive: true });
  document.addEventListener('touchcancel', touchEnd, { passive: true });

  // teclado: o foco acende o cartão (no centro do elemento focado) e sair do cartão apaga
  document.addEventListener('focusin', (ev) => {
    const card = cardOf(ev.target);
    if (!card) return;
    const t = /** @type {Element} */ (ev.target);
    if (!t.matches(':focus-visible')) return;
    const r = t.getBoundingClientRect();
    place(card, r.left + r.width / 2, r.top + r.height / 2);
    card.dataset.kbd = '';
    setOn(card, true);
  });
  document.addEventListener('focusout', (ev) => {
    const card = cardOf(ev.target);
    if (!card) return;
    const to = /** @type {FocusEvent} */ (ev).relatedTarget;
    if (to instanceof Node && card.contains(to)) return;
    delete card.dataset.kbd;
    setOn(card, false);
  });
}
