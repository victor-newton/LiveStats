// @ts-check
// Painel de lives: 4 cartões de slot, salvar URLs e lista "Endpoints JSON".

import { api, errorText } from './api.js';
import {
  byId, buttonById, cloneTemplate, query, setVisible, showMessage, clearMessage,
} from './dom.js';

const SLOTS = 4;
const STATUS_CLASSES = ['ao_vivo', 'encerrada', 'agendada'];
const SAVE_MSG_MS = 2500;
const RELOAD_AFTER_SAVE_MS = 3000;

/**
 * @typedef {object} LiveStats
 * @property {string} status
 * @property {number} views
 * @property {number} likes
 * @property {string} views_formatado
 * @property {string} likes_formatado
 * @property {string} titulo
 */

/**
 * @typedef {object} SlotItem
 * @property {number} slot
 * @property {string} url
 * @property {LiveStats | null} stats
 */

/**
 * @typedef {object} Card
 * @property {HTMLElement} root
 * @property {HTMLInputElement} input
 * @property {HTMLElement} dot
 * @property {HTMLElement} statsBox
 * @property {HTMLElement} title
 */

/** @type {Card[]} */
let cards = [];
/** @type {number[]} */
let timers = [];

/**
 * Monta os 4 cartões (uma vez).
 */
export function initSlots() {
  const grid = byId('slotsGrid');
  grid.replaceChildren();
  cards = [];
  for (let i = 0; i < SLOTS; i++) {
    const n = i + 1;
    const root = cloneTemplate('slotTemplate');
    const label = query(root, '.slot-label');
    const input = /** @type {HTMLInputElement} */ (query(root, '.slot-input'));
    input.id = `inp${n}`;
    label.setAttribute('for', input.id);
    label.textContent = `Slot ${n}`;
    input.addEventListener('input', () => {
      input.dataset.dirty = '1';
    });
    grid.appendChild(root);
    cards.push({
      root,
      input,
      dot: query(root, '.slot-status'),
      statsBox: query(root, '.slot-stats'),
      title: query(root, '.slot-title'),
    });
    renderStats(cards[i], null, '');
  }
}

/**
 * Esvazia tudo o que veio do servidor (usado ao perder a sessão ou sair).
 */
export function resetSlots() {
  for (const t of timers) clearTimeout(t);
  timers = [];
  clearMessage(byId('saveErr'));
  setVisible(byId('saveMsg'), false);
  byId('epList').replaceChildren();
  initSlots();
}

/**
 * @param {Card} card
 * @param {LiveStats | null} stats
 * @param {string} url URL gravada no servidor
 */
function renderStats(card, stats, url) {
  const status = stats && typeof stats.status === 'string' ? stats.status : '';
  card.dot.className = 'slot-status';
  if (STATUS_CLASSES.includes(status)) {
    card.dot.classList.add(status);
  }
  card.dot.title = status && status !== 'sem_url' ? status : 'sem url';

  const temDados = !!stats && typeof stats.views === 'number' && stats.views > 0;
  card.statsBox.replaceChildren();
  if (temDados && stats) {
    card.statsBox.appendChild(statItem(String(stats.views_formatado), 'Views'));
    card.statsBox.appendChild(statItem(String(stats.likes_formatado), 'Likes'));
  } else {
    const empty = document.createElement('span');
    empty.className = 'stat-empty';
    empty.textContent = url ? 'Aguardando dados...' : 'Sem dados — insira uma URL';
    card.statsBox.appendChild(empty);
  }

  if (stats && stats.titulo) {
    card.title.textContent = stats.titulo;
    card.title.title = stats.titulo;
    card.title.hidden = false;
  } else {
    card.title.textContent = '';
    card.title.title = '';
    card.title.hidden = true;
  }
}

/**
 * @param {string} value
 * @param {string} label
 * @returns {HTMLElement}
 */
function statItem(value, label) {
  const item = document.createElement('div');
  item.className = 'stat-item';
  const v = document.createElement('span');
  v.className = 'stat-val';
  v.textContent = value;
  const l = document.createElement('span');
  l.className = 'stat-label';
  l.textContent = label;
  item.append(v, l);
  return item;
}

/**
 * Aplica o que o servidor devolveu em GET /api/admin/slots.
 * Campos que o usuário está editando (não salvos) NÃO são sobrescritos.
 * @param {{slots: SlotItem[]}} data
 */
export function applySlots(data) {
  const items = Array.isArray(data.slots) ? data.slots : [];
  for (let i = 0; i < SLOTS; i++) {
    const item = items[i];
    if (!item || !cards[i]) continue;
    const url = typeof item.url === 'string' ? item.url : '';
    if (cards[i].input.dataset.dirty !== '1') {
      cards[i].input.value = url;
    }
    renderStats(cards[i], item.stats, url);
  }
}

/**
 * Preenche a lista "Endpoints JSON".
 * @param {string} base origem pública (sem barra final)
 */
export function renderEndpoints(base) {
  const list = byId('epList');
  list.replaceChildren();
  for (let n = 1; n <= SLOTS; n++) {
    const row = cloneTemplate('endpointTemplate');
    query(row, '.ep-url').textContent = `${base}/live${n}.json`;
    list.appendChild(row);
  }
}

/**
 * Busca slots + estatísticas. Devolve a resposta crua para o chamador tratar 401.
 * @returns {Promise<import('./api.js').ApiResponse>}
 */
export async function loadSlots() {
  const resp = await api('GET', '/api/admin/slots');
  if (resp.ok && resp.data) {
    applySlots(resp.data);
  }
  return resp;
}

/**
 * Envia os 4 textos. `onUnauthorized` é chamado em 401; `reloadAll` recarrega
 * as estatísticas 3 s depois de um salvamento bem-sucedido.
 * @param {{onUnauthorized: () => void, reloadAll: () => void}} hooks
 */
export async function saveSlots(hooks) {
  const btn = buttonById('saveBtn');
  const err = byId('saveErr');
  const ok = byId('saveMsg');
  clearMessage(err);
  setVisible(ok, false);
  const values = cards.map((c) => c.input.value);
  btn.disabled = true;
  try {
    const resp = await api('PUT', '/api/admin/slots', { slots: values });
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!resp.ok) {
      showMessage(err, errorText(resp, `Erro ao salvar (HTTP ${resp.status})`));
      return;
    }
    const saved = resp.data && Array.isArray(resp.data.slots) ? resp.data.slots : values;
    for (let i = 0; i < SLOTS; i++) {
      const text = typeof saved[i] === 'string' ? saved[i] : values[i];
      cards[i].input.value = text;
      delete cards[i].input.dataset.dirty;
    }
    setVisible(ok, true);
    timers.push(window.setTimeout(() => setVisible(ok, false), SAVE_MSG_MS));
    timers.push(window.setTimeout(hooks.reloadAll, RELOAD_AFTER_SAVE_MS));
  } catch (e) {
    showMessage(err, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    btn.disabled = false;
  }
}
