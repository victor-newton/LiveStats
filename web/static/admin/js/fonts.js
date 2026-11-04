// @ts-check
// Lista fechada de fontes (GET /api/admin/fonts) e seletores de fonte do painel.
// Cada opção do dropdown é desenhada na própria fonte via CSSOM (style.setProperty).

import { api } from './api.js';

const FONT_NAME_RE = /^[A-Za-z0-9 ]{1,40}$/;
export const DEFAULT_FONT = 'Roboto';

/** @type {string[]} */
let fonts = [];
/** @type {Set<HTMLSelectElement>} */
const selects = new Set();

/**
 * Família CSS segura: só nomes da lista fechada (letras, números e espaço), entre aspas, com genérica.
 * @param {string} name
 * @returns {string}
 */
export function fontStack(name) {
  return FONT_NAME_RE.test(name) ? `"${name}", sans-serif` : 'sans-serif';
}

/** @param {HTMLSelectElement} select */
function fill(select) {
  const current = select.value;
  select.replaceChildren();
  for (const name of fonts) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    opt.style.setProperty('font-family', fontStack(name));
    select.appendChild(opt);
  }
  if (fonts.includes(current)) select.value = current;
  select.style.setProperty('font-family', fontStack(select.value));
}

/**
 * Registra um <select> de fonte: recebe as opções e mostra a fonte escolhida.
 * @param {HTMLSelectElement} select
 */
export function registerFontSelect(select) {
  if (selects.has(select)) {
    fill(select);
    return;
  }
  selects.add(select);
  fill(select);
  select.addEventListener('change', () => select.style.setProperty('font-family', fontStack(select.value)));
}

/**
 * Seleciona a fonte vinda do servidor (cai em Roboto se não estiver na lista).
 * @param {HTMLSelectElement} select
 * @param {unknown} font
 */
export function setFontValue(select, font) {
  select.value = typeof font === 'string' && fonts.includes(font) ? font : DEFAULT_FONT;
  select.style.setProperty('font-family', fontStack(select.value));
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
