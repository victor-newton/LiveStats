// @ts-check
// Peças de interface reutilizadas: avisos rápidos, Copiar, prévia do overlay, pílulas de estado e botão de duas
// etapas. O seletor de cor (colorpicker.js) e a área recolhível (collapse.js) moram em arquivos próprios e são
// reexportados aqui.

import { h, s, setStyle } from './h.js';
import { fontStack } from './fonts.js';
import {
  PLATFORM_CLASS, PLATFORM_LOGO, fmt, safeColor,
} from './util.js';

const COPIED_MS = 1500;

/** @type {number | null} */
let toastTimer = null;

/**
 * Aviso rápido no canto (texto puro).
 * @param {string} text
 */
export function toast(text) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = text;
  t.hidden = false;
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { t.hidden = true; }, 2600);
}

/** Descarta o aviso pendente (saída da sessão). */
export function clearToast() {
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = null;
  const t = document.getElementById('toast');
  if (t) { t.textContent = ''; t.hidden = true; }
}

/**
 * Item de link: rótulo, endereço completo e botão Copiar.
 * Sem a Clipboard API, o endereço é selecionado para Ctrl+C.
 * @param {string} label
 * @param {string} url
 * @param {string} [cls] classe do contêiner
 * @returns {HTMLElement}
 */
export function linkItem(label, url, cls = 'link-item') {
  const code = h('code', { class: 'ep-url', text: url });
  const btn = h('button', { type: 'button', class: 'btn copy ep-copy', 'aria-label': `Copiar ${label}`, text: 'Copiar' });
  /** @type {number | null} */
  let timer = null;
  btn.addEventListener('click', async () => {
    let msg = 'Copiado';
    try {
      await navigator.clipboard.writeText(url);
    } catch (e) {
      const sel = window.getSelection();
      if (sel) sel.selectAllChildren(code);
      msg = 'Selecionado (Ctrl+C)';
    }
    btn.textContent = msg;
    btn.classList.add('done');
    if (timer !== null) clearTimeout(timer);
    timer = window.setTimeout(() => { btn.textContent = 'Copiar'; btn.classList.remove('done'); }, COPIED_MS);
  });
  return h('div', { class: `${cls} ep` }, h('span', { class: 'link-text' }, h('b', { class: 'ep-label', text: label }), code), btn);
}

export { collapse } from './collapse.js';
export { colorField } from './colorpicker.js';

/** @type {Record<string, [string, string]>} */
const STATUS = {
  ao_vivo: ['live', 'ao vivo'],
  encerrada: ['off', 'encerrada'],
  agendada: ['off', 'agendada'],
  offline: ['off', 'offline'],
  erro: ['err', 'erro'],
};

/**
 * Pílula de estado (texto e classe vêm de uma lista fechada).
 * @param {string | null | undefined} status
 * @param {string} [textOverride]
 * @returns {HTMLElement}
 */
export function statusPill(status, textOverride) {
  const pill = h('span', { class: 'pill' }, h('span', { class: 'dot' }), h('span', { class: 'pill-text' }));
  setPill(pill, status, textOverride);
  return pill;
}

/**
 * @param {HTMLElement} pill
 * @param {string | null | undefined} status
 * @param {string} [textOverride]
 */
export function setPill(pill, status, textOverride) {
  const [cls, text] = (status && STATUS[status]) || ['off', 'aguardando'];
  pill.className = `pill ${cls}`;
  const t = /** @type {HTMLElement} */ (pill.querySelector('.pill-text'));
  t.textContent = textOverride || text;
}

/** @type {Record<string, string>} */
const LEVEL_CLASS = { ok: 'live', warn: 'warn', bad: 'err', off: 'off' };

/**
 * Pílula por nível de serviço (ok, warn, bad, off).
 * @param {'ok' | 'warn' | 'bad' | 'off'} level
 * @param {string} text
 * @returns {HTMLElement}
 */
export function levelPill(level, text) {
  return h('span', { class: `pill ${LEVEL_CLASS[level] || 'off'}` }, h('span', { class: 'dot' }), h('span', { class: 'pill-text', text }));
}

/**
 * Logotipo oficial da plataforma (SVG desenhado pelo JS; a cor vem do `fill` herdado, `currentColor`).
 * @param {string} platform
 * @returns {SVGElement | Text}
 */
export function platLogo(platform) {
  const d = PLATFORM_LOGO[platform];
  if (!d) return document.createTextNode('?');
  return s('svg', { viewBox: '0 0 24 24', class: 'plat-logo', 'aria-hidden': 'true', focusable: 'false' }, s('path', { d, fill: 'currentColor' }));
}

/**
 * Selo da plataforma: logotipo oficial sobre a cor da plataforma.
 * @param {string} platform
 * @param {string} [extra]
 * @returns {HTMLElement}
 */
export function platBadge(platform, extra = '') {
  return h('span', { class: `plat ${PLATFORM_CLASS[platform] || ''} ${extra}`.trim(), 'aria-hidden': 'true' }, platLogo(platform));
}

/**
 * Botão de duas etapas, sem diálogo do navegador: o 1º clique pede confirmação; o 2º executa.
 * @param {HTMLButtonElement | HTMLElement} button
 * @param {string} confirmText
 * @param {() => void} action
 */
export function armedButton(button, confirmText, action) {
  const original = button.textContent || '';
  let armed = false;
  /** @type {number | null} */
  let timer = null;
  const reset = () => {
    armed = false;
    button.textContent = original;
    button.classList.remove('armed');
  };
  button.addEventListener('click', () => {
    if (!armed) {
      armed = true;
      button.textContent = confirmText;
      button.classList.add('armed');
      timer = window.setTimeout(reset, 4000);
      return;
    }
    if (timer !== null) clearTimeout(timer);
    reset();
    action();
  });
}

/**
 * @typedef {object} PreviewState
 * @property {number | null} likes
 * @property {number | null} goal
 * @property {number | null} viewers
 * @property {string} bar
 * @property {string} text
 * @property {string} font
 * @property {boolean} [counter] modo contador de inscritos (um número só, sem barra)
 */

/**
 * Prévia do overlay (likes/meta com barra, ou espectadores). Cor e fonte entram por CSSOM.
 * @param {{counter?: boolean}} [opts]
 * @returns {{root: HTMLElement, box: HTMLElement, update: (st: PreviewState) => void}}
 */
export function overlayPreview(opts = {}) {
  let metric = 'likes';
  /** @type {PreviewState | null} */
  let last = null;
  const big = h('span', { class: 'pv-big num' });
  const fill = h('i');
  const bar = h('span', { class: 'pbar' }, fill);
  const box = h('div', { class: `preview-ov${opts.counter ? ' counter' : ''}`, role: 'img', 'aria-label': 'Prévia do overlay' }, big, bar);
  const hint = h('span', { class: 'hint', hidden: true });
  const tabs = h('div', { class: 'preview-tabs', role: 'group', 'aria-label': 'Métrica da prévia' });
  /** @type {HTMLButtonElement[]} */
  const tabBtns = [];
  if (!opts.counter) {
    for (const [key, text] of [['likes', 'Likes'], ['views', 'Espectadores']]) {
      const b = /** @type {HTMLButtonElement} */ (h('button', { type: 'button', 'data-pv': key, 'aria-pressed': key === metric ? 'true' : 'false', text }));
      b.addEventListener('click', () => { metric = key; if (last) update(last); });
      tabs.appendChild(b);
      tabBtns.push(b);
    }
  }
  /** @param {PreviewState} st */
  function update(st) {
    last = st;
    setStyle(box, { '--pv-bar': safeColor(st.bar), '--pv-text': safeColor(st.text, '#FFFFFF'), '--pv-font': fontStack(st.font) });
    for (const b of tabBtns) b.setAttribute('aria-pressed', String(b.dataset.pv === metric));
    if (opts.counter) {
      big.textContent = fmt(st.viewers);
      bar.hidden = true;
      return;
    }
    const noLikes = st.likes === null;
    if (metric === 'likes') {
      big.textContent = `${fmt(st.likes)}${st.goal ? `/${fmt(st.goal)}` : ''}`;
      const pct = st.goal && st.likes !== null ? Math.min(100, (st.likes / st.goal) * 100) : 0;
      fill.style.width = `${pct.toFixed(1)}%`;
      bar.hidden = !st.goal;
    } else {
      big.textContent = fmt(st.viewers);
      bar.hidden = true;
    }
    hint.hidden = !(noLikes && metric === 'likes');
    hint.textContent = 'Sem likes nesta plataforma: use o overlay de espectadores.';
  }
  const head = h('div', { class: 'preview-head' }, h('span', { class: 'field-label', text: 'Prévia do overlay' }), opts.counter ? null : tabs);
  return { root: h('div', { class: 'field preview' }, head, box, hint), box, update };
}
