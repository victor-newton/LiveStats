// @ts-check
// Lista fechada de fontes (GET /api/admin/fonts) e dropdowns de fonte do painel.
// Cada opção do dropdown é desenhada na própria fonte via CSSOM (style.setProperty).

import { api } from './api.js';
import { dropdown, setDropdownOptions } from './dropdown.js';

const FONT_NAME_RE = /^[A-Za-z0-9 ]{1,40}$/;
export const DEFAULT_FONT = 'Roboto';

/** @type {string[]} */
let fonts = [];
/** @type {Set<HTMLElement>} */
const selects = new Set();

/**
 * Família CSS segura: só nomes da lista fechada (letras, números e espaço), entre aspas, com genérica.
 * @param {string} name
 * @returns {string}
 */
export function fontStack(name) {
  return FONT_NAME_RE.test(name) ? `"${name}", sans-serif` : 'sans-serif';
}

/** @param {HTMLElement} el */
function fill(el) {
  setDropdownOptions(el, fonts.map((name) => ({ value: name, text: name, font: fontStack(name) })));
}

/**
 * Dropdown de fonte: cada nome aparece na própria fonte. Mantém `.value` e o evento `change`.
 * @param {string} id
 * @param {HTMLButtonElement} [el] botão já existente no HTML (vira o dropdown)
 * @returns {HTMLButtonElement}
 */
export function fontDropdown(id, el) {
  const dd = dropdown({ id, label: 'Fonte', el, value: DEFAULT_FONT });
  registerFontSelect(dd.el);
  return dd.el;
}

/**
 * Registra um dropdown de fonte: recebe as opções (agora ou quando a lista chegar).
 * @param {HTMLElement} el
 */
export function registerFontSelect(el) {
  selects.add(el);
  fill(el);
}

/**
 * Seleciona a fonte vinda do servidor (cai em Roboto se não estiver na lista).
 * @param {HTMLElement} el
 * @param {unknown} font
 */
export function setFontValue(el, font) {
  /** @type {HTMLButtonElement} */ (el).value = typeof font === 'string' && fonts.includes(font) ? font : DEFAULT_FONT;
}

/** Carrega a lista fechada de fontes. @returns {Promise<import('./api.js').ApiResponse>} */
export async function loadFonts() {
  const resp = await api('GET', '/api/admin/fonts');
  if (resp.ok && resp.data && Array.isArray(resp.data.fonts)) {
    fonts = resp.data.fonts.filter((/** @type {unknown} */ f) => typeof f === 'string' && FONT_NAME_RE.test(f));
    for (const s of selects) {
      if (s.isConnected) fill(s);
      else selects.delete(s); // cartão descartado ao reconstruir a tela
    }
  }
  return resp;
}

/** Descarta a lista (sessão encerrada). */
export function resetFonts() {
  fonts = [];
}
