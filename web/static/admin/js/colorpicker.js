// @ts-check
// Seletor de cor próprio (sem o popup nativo do navegador): um botão com a cor atual abre um painel com a área de
// saturação/brilho, a faixa de matiz, o campo #RRGGBB, as cores sugeridas e, se o navegador tiver, o conta-gotas
// (EyeDropper). Arrastar funciona com mouse, toque e caneta (pointer events); as setas movem com o teclado. O valor
// final é sempre `#RRGGBB` maiúsculo. Toda cor dinâmica entra pelo CSSOM (CSP estrita: nada de atributo style).

import { h, setStyle } from './h.js';
import { openFloating } from './float.js';
import { COLOR_RE, SUGGESTED_COLORS, safeColor } from './util.js';

/**
 * @typedef {object} ColorField
 * @property {HTMLElement} root
 * @property {() => string | null} get cor `#RRGGBB` maiúscula, ou null se o campo de texto está inválido
 * @property {() => string} last última cor válida (a prévia a mantém enquanto o texto está inválido)
 * @property {(hex: string) => void} set
 * @property {HTMLButtonElement} trigger botão que abre o painel
 * @property {HTMLInputElement} text
 */

/**
 * `#RRGGBB` -> [h 0-360, s 0-1, v 0-1].
 * @param {string} hex
 * @returns {[number, number, number]}
 */
export function hexToHsv(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = ((n >> 16) & 255) / 255;
  const g = ((n >> 8) & 255) / 255;
  const b = (n & 255) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let hue = 0;
  if (d) {
    if (max === r) hue = ((g - b) / d) % 6;
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue *= 60;
    if (hue < 0) hue += 360;
  }
  return [hue, max ? d / max : 0, max];
}

/**
 * [h, s, v] -> `#RRGGBB` maiúsculo.
 * @param {number} hue
 * @param {number} s
 * @param {number} v
 * @returns {string}
 */
export function hsvToHex(hue, s, v) {
  const c = v * s;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = v - c;
  const seg = Math.floor((((hue % 360) + 360) % 360) / 60);
  const [r, g, b] = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][seg];
  const to = (/** @type {number} */ q) => Math.round((q + m) * 255).toString(16).padStart(2, '0');
  return `#${to(r)}${to(g)}${to(b)}`.toUpperCase();
}

/** @param {string} raw @returns {string | null} `#RRGGBB` maiúsculo ou null */
function parseHex(raw) {
  let v = raw.trim();
  if (/^[0-9a-f]{6}$/i.test(v)) v = `#${v}`;
  return COLOR_RE.test(v) ? v.toUpperCase() : null;
}

const clamp01 = (/** @type {number} */ x) => Math.max(0, Math.min(1, x));

/**
 * Painel do seletor (SV + matiz + hex + sugestões + conta-gotas) ligado a `get`/`apply`.
 * @param {string} label
 * @param {() => string} getColor
 * @param {(hex: string) => void} apply
 * @returns {{root: HTMLElement, sync: () => void, focus: () => void}}
 */
function panel(label, getColor, apply) {
  let [hue, sat, val] = hexToHsv(getColor());
  const thumb = h('i', { class: 'cp-thumb' });
  const area = h('div', {
    class: 'cp-sv', role: 'slider', tabindex: '0', 'aria-label': `${label}: saturação e brilho`,
  }, thumb);
  const hueThumb = h('i', { class: 'cp-thumb' });
  const strip = h('div', {
    class: 'cp-hue', role: 'slider', tabindex: '0', 'aria-label': `${label}: matiz`, 'aria-valuemin': '0', 'aria-valuemax': '360',
  }, hueThumb);
  const hex = /** @type {HTMLInputElement} */ (h('input', {
    type: 'text', class: 'cp-hex', maxlength: 9, spellcheck: 'false', autocomplete: 'off', 'aria-label': `${label}: código #RRGGBB`,
  }));
  const chip = h('span', { class: 'cp-chip', 'aria-hidden': 'true' });
  const sw = h('div', { class: 'swatches', role: 'group', 'aria-label': 'Cores sugeridas' });
  const root = h('div', { class: 'cp-pop float-pop', role: 'dialog', 'aria-label': `${label}: seletor de cor`, hidden: true });
  const row = h('div', { class: 'cp-row' }, chip, hex);
  const eyeSupported = typeof /** @type {any} */ (window).EyeDropper === 'function';
  if (eyeSupported) {
    const eye = h('button', { type: 'button', class: 'btn cp-eye', 'aria-label': 'Conta-gotas: pegar uma cor da tela', text: 'Conta-gotas' });
    eye.addEventListener('click', async () => {
      try {
        const res = await new /** @type {any} */ (window).EyeDropper().open();
        const c = parseHex(String(res.sRGBHex));
        if (c) commit(c, true);
      } catch (e) {
        // cancelado pelo usuário (Esc): nada a fazer
      }
    });
    row.appendChild(eye);
  }
  root.append(area, strip, row, sw);

  const paint = () => {
    const cur = hsvToHex(hue, sat, val);
    setStyle(area, { '--cp-hue': hsvToHex(hue, 1, 1) });
    setStyle(thumb, { left: `${(sat * 100).toFixed(2)}%`, top: `${((1 - val) * 100).toFixed(2)}%`, background: cur });
    setStyle(hueThumb, { left: `${((hue / 360) * 100).toFixed(2)}%`, background: hsvToHex(hue, 1, 1) });
    setStyle(chip, { background: cur });
    area.setAttribute('aria-valuetext', `saturação ${Math.round(sat * 100)}%, brilho ${Math.round(val * 100)}%`);
    strip.setAttribute('aria-valuenow', String(Math.round(hue)));
    if (document.activeElement !== hex) hex.value = cur;
    hex.removeAttribute('aria-invalid');
    return cur;
  };
  /** @param {string} c @param {boolean} fromHex true = recalcula o HSV (mantém o matiz em cinzas) */
  function commit(c, fromHex) {
    if (fromHex) {
      const [nh, ns, nv] = hexToHsv(c);
      if (ns > 0 && nv > 0) hue = nh;
      sat = ns;
      val = nv;
    }
    const cur = paint();
    apply(fromHex ? c : cur);
  }
  const fromHsv = () => commit(hsvToHex(hue, sat, val), false);

  /** @param {HTMLElement} el @param {(x: number, y: number) => void} move */
  const drag = (el, move) => {
    /** @param {PointerEvent} ev */
    const at = (ev) => {
      const r = el.getBoundingClientRect();
      move(clamp01((ev.clientX - r.left) / (r.width || 1)), clamp01((ev.clientY - r.top) / (r.height || 1)));
    };
    el.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      el.focus();
      el.setPointerCapture(ev.pointerId);
      at(ev);
    });
    el.addEventListener('pointermove', (ev) => { if (el.hasPointerCapture(ev.pointerId)) at(ev); });
  };
  drag(area, (x, y) => { sat = x; val = 1 - y; fromHsv(); });
  drag(strip, (x) => { hue = x * 360; fromHsv(); });
  area.addEventListener('keydown', (ev) => {
    const step = ev.shiftKey ? 0.1 : 0.01;
    const k = ev.key;
    if (k === 'ArrowLeft') sat = clamp01(sat - step);
    else if (k === 'ArrowRight') sat = clamp01(sat + step);
    else if (k === 'ArrowUp') val = clamp01(val + step);
    else if (k === 'ArrowDown') val = clamp01(val - step);
    else return;
    ev.preventDefault();
    fromHsv();
  });
  strip.addEventListener('keydown', (ev) => {
    const step = ev.shiftKey ? 10 : 1;
    const k = ev.key;
    if (k === 'ArrowLeft' || k === 'ArrowDown') hue = (hue - step + 360) % 360;
    else if (k === 'ArrowRight' || k === 'ArrowUp') hue = (hue + step) % 360;
    else return;
    ev.preventDefault();
    fromHsv();
  });
  hex.addEventListener('input', () => {
    const c = parseHex(hex.value);
    if (c) commit(c, true);
    else hex.setAttribute('aria-invalid', 'true');
  });
  hex.addEventListener('blur', () => { hex.value = hsvToHex(hue, sat, val); hex.removeAttribute('aria-invalid'); });
  for (const c of SUGGESTED_COLORS) {
    const b = h('button', { type: 'button', class: 'sw', 'aria-label': `${label}: ${c}`, 'data-sw': c });
    setStyle(b, { background: c });
    b.addEventListener('click', () => commit(c, true));
    sw.appendChild(b);
  }
  paint();
  return {
    root,
    sync() {
      const [nh, ns, nv] = hexToHsv(getColor());
      if (ns > 0 && nv > 0) hue = nh;
      sat = ns;
      val = nv;
      paint();
    },
    focus: () => area.focus(),
  };
}

/**
 * Campo de cor: botão com a cor + campo `#RRGGBB`; o botão abre o painel do seletor.
 * @param {string} label
 * @param {string} value
 * @param {(hex: string) => void} [onChange] chamado a cada cor válida
 * @returns {ColorField}
 */
export function colorField(label, value, onChange) {
  const start = safeColor(value, '#FFFFFF');
  let lastValid = start;
  const trigger = /** @type {HTMLButtonElement} */ (h('button', {
    type: 'button', class: 'cp-trigger', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', 'aria-label': `${label}: escolher cor`,
  }, h('span', { class: 'cp-chip', 'aria-hidden': 'true' })));
  const chip = /** @type {HTMLElement} */ (trigger.firstElementChild);
  const text = /** @type {HTMLInputElement} */ (h('input', {
    type: 'text', value: start, maxlength: 9, spellcheck: 'false', autocomplete: 'off', class: 'hex', 'aria-label': `${label}: código #RRGGBB`,
  }));
  /** @type {ReturnType<typeof panel> | null} */
  let pnl = null;
  /** @type {null | {close: () => void}} */
  let floating = null;
  /** @param {string} hex @param {boolean} notify */
  const apply = (hex, notify) => {
    const up = hex.toUpperCase();
    lastValid = up;
    text.value = up;
    text.removeAttribute('aria-invalid');
    setStyle(chip, { background: up });
    if (notify) {
      // o painel flutua fora do formulário: avisa o formulário como o <input type=color> nativo fazia (input que borbulha)
      trigger.dispatchEvent(new Event('input', { bubbles: true }));
      if (onChange) onChange(up);
    }
  };
  apply(start, false);
  const open = () => {
    if (floating) { floating.close(); return; }
    pnl = panel(label, () => lastValid, (hex) => apply(hex, true));
    trigger.setAttribute('aria-expanded', 'true');
    floating = openFloating({
      anchor: trigger,
      pop: pnl.root,
      focusBack: true,
      onClose: () => { floating = null; trigger.setAttribute('aria-expanded', 'false'); },
    });
    pnl.sync();
    pnl.focus();
  };
  trigger.addEventListener('click', open);
  text.addEventListener('input', () => {
    const c = parseHex(text.value);
    if (c) { apply(c, true); if (pnl && floating) pnl.sync(); } else text.setAttribute('aria-invalid', 'true');
  });
  const root = h('div', { class: 'field' }, h('span', { class: 'field-label', text: label }), h('div', { class: 'color-field' }, trigger, text));
  return {
    root,
    trigger,
    text,
    get: () => parseHex(text.value),
    last: () => lastValid,
    set: (hex) => { apply(hex, false); if (pnl && floating) pnl.sync(); },
  };
}
