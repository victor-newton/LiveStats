// @ts-check
// Lista de endereços públicos de um assunto (rótulo, URL completa e botão "Copiar").
// Cada aba/cartão chama renderEndpointList com os SEUS endereços; nenhum é de admin.

import { cloneTemplate, query } from './dom.js';

const COPIED_MSG_MS = 1500;

/**
 * @typedef {object} Endpoint
 * @property {string} label
 * @property {string} path
 */

/** @type {number[]} */
let timers = [];

/** Cancela os temporizadores de "Copiado". */
export function clearEndpointTimers() {
  for (const t of timers) clearTimeout(t);
  timers = [];
}

/**
 * Copia o texto; se a Clipboard API não estiver disponível, seleciona o endereço para Ctrl+C.
 * @param {HTMLButtonElement} button
 * @param {HTMLElement} urlEl
 * @param {string} text
 */
async function copyEndpoint(button, urlEl, text) {
  /** @type {string} */
  let feedback;
  try {
    await navigator.clipboard.writeText(text);
    feedback = 'Copiado';
  } catch (e) {
    const selection = window.getSelection();
    if (selection) selection.selectAllChildren(urlEl);
    feedback = 'Selecionado (Ctrl+C)';
  }
  button.textContent = feedback;
  timers.push(window.setTimeout(() => {
    button.textContent = 'Copiar';
  }, COPIED_MSG_MS));
}

/**
 * Preenche um contêiner com os endereços.
 * @param {HTMLElement} container
 * @param {string} base origem pública (sem barra final)
 * @param {Endpoint[]} items
 */
export function renderEndpointList(container, base, items) {
  container.replaceChildren();
  for (const item of items) {
    const row = cloneTemplate('endpointTemplate');
    const url = `${base}${item.path}`;
    const urlEl = query(row, '.ep-url');
    query(row, '.ep-label').textContent = item.label;
    urlEl.textContent = url;
    const button = /** @type {HTMLButtonElement} */ (query(row, '.ep-copy'));
    button.setAttribute('aria-label', `Copiar ${item.label}`);
    button.addEventListener('click', () => void copyEndpoint(button, urlEl, url));
    container.appendChild(row);
  }
}

/** Endereços do assunto "inscritos". @returns {Endpoint[]} */
export function subscribersEndpoints() {
  return [
    { label: 'Inscritos (legado)', path: '/inscritos.json' },
    { label: 'Inscritos (formato novo, com fonte e cor)', path: '/api/public/subscribers' },
    { label: 'Overlay do contador de inscritos', path: '/contador' },
  ];
}

/**
 * Endereços de uma live.
 * @param {number} n
 * @returns {Endpoint[]}
 */
export function liveEndpoints(n) {
  return [
    { label: `Live ${n} (legado: lista com 1 objeto)`, path: `/live${n}.json` },
    { label: `Live ${n} (objeto, com meta, cor e fonte)`, path: `/api/public/lives/${n}` },
    { label: `Likes da live ${n} (meta e barra)`, path: `/overlay/live.html?slot=${n}` },
  ];
}

/** Endereços do assunto "saúde". @returns {Endpoint[]} */
export function healthEndpoints() {
  return [{ label: 'Estado do serviço', path: '/health' }];
}
