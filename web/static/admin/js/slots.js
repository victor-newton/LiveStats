// @ts-check
// Painel de lives: 4 cartões de slot (URL, meta de likes e cor da barra do overlay),
// salvar e lista de endpoints públicos com botão "Copiar".

import { api, errorText } from './api.js';
import {
  byId, buttonById, cloneTemplate, query, setVisible, showMessage, clearMessage,
} from './dom.js';

const SLOTS = 4;
const STATUS_CLASSES = ['ao_vivo', 'encerrada', 'agendada'];
const SAVE_MSG_MS = 2500;
const RELOAD_AFTER_SAVE_MS = 3000;
const COPIED_MSG_MS = 1500;
const GOAL_MAX = 1_000_000_000;
const DEFAULT_COLOR = '#FF0033';
const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;

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
 * @property {number | null} [goal]
 * @property {string} [color]
 * @property {LiveStats | null} stats
 */

/**
 * @typedef {object} Card
 * @property {HTMLElement} root
 * @property {HTMLInputElement} input
 * @property {HTMLInputElement} goal
 * @property {HTMLInputElement} color
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

    const goal = /** @type {HTMLInputElement} */ (query(root, '.slot-goal'));
    goal.id = `goal${n}`;
    labelWithHint(query(root, '.slot-goal-label'), goal.id, 'Meta de likes', `do slot ${n}`);
    const color = /** @type {HTMLInputElement} */ (query(root, '.slot-color'));
    color.id = `color${n}`;
    labelWithHint(query(root, '.slot-color-label'), color.id, 'Cor da barra', `do slot ${n}`);
    color.value = DEFAULT_COLOR.toLowerCase();
    for (const field of [goal, color]) {
      field.addEventListener('input', () => {
        field.dataset.dirty = '1';
      });
    }

    grid.appendChild(root);
    cards.push({
      root,
      input,
      goal,
      color,
      dot: query(root, '.slot-status'),
      statsBox: query(root, '.slot-stats'),
      title: query(root, '.slot-title'),
    });
    renderStats(cards[i], null, '');
  }
}

/**
 * Rótulo visível curto + complemento só para leitores de tela (nomes únicos por slot).
 * @param {HTMLElement} label
 * @param {string} forId
 * @param {string} text
 * @param {string} hint
 */
function labelWithHint(label, forId, text, hint) {
  label.setAttribute('for', forId);
  const hidden = document.createElement('span');
  hidden.className = 'sr-only';
  hidden.textContent = ` ${hint}`;
  label.replaceChildren(document.createTextNode(text), hidden);
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
    if (cards[i].goal.dataset.dirty !== '1') {
      cards[i].goal.value = typeof item.goal === 'number' ? String(item.goal) : '';
    }
    if (cards[i].color.dataset.dirty !== '1') {
      cards[i].color.value = normalizeColor(item.color);
    }
    renderStats(cards[i], item.stats, url);
  }
}

/**
 * Cor do servidor -> valor aceito por <input type="color"> (minúsculas; cai no padrão se vier algo estranho).
 * @param {unknown} value
 * @returns {string}
 */
function normalizeColor(value) {
  return typeof value === 'string' && COLOR_RE.test(value) ? value.toLowerCase() : DEFAULT_COLOR.toLowerCase();
}

/**
 * @typedef {object} Endpoint
 * @property {string} label
 * @property {string} path
 */

/**
 * Grupos de endereços públicos prontos para consumo (nenhum é de admin).
 * @returns {{title: string, items: Endpoint[]}[]}
 */
function endpointGroups() {
  const numbers = Array.from({ length: SLOTS }, (_, i) => i + 1);
  return [
    {
      title: 'JSON por live',
      items: [
        ...numbers.map((n) => ({ label: `Live ${n} (legado: lista com 1 objeto)`, path: `/live${n}.json` })),
        ...numbers.map((n) => ({ label: `Live ${n} (objeto, com meta e cor)`, path: `/api/public/lives/${n}` })),
      ],
    },
    {
      title: 'JSON de inscritos',
      items: [
        { label: 'Inscritos (legado)', path: '/inscritos.json' },
        { label: 'Inscritos (formato novo)', path: '/api/public/subscribers' },
      ],
    },
    {
      title: 'Overlays',
      items: [
        { label: 'Contador de inscritos', path: '/contador' },
        { label: 'Contador de inscritos (alias)', path: '/overlay/inscritos' },
        ...numbers.map((n) => ({ label: `Likes da live ${n} (meta e barra)`, path: `/overlay/live.html?slot=${n}` })),
      ],
    },
    {
      title: 'Saúde',
      items: [{ label: 'Estado do serviço', path: '/health' }],
    },
  ];
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
 * Preenche a lista de endpoints públicos (agrupados, com "Copiar").
 * @param {string} base origem pública (sem barra final)
 */
export function renderEndpoints(base) {
  const list = byId('epList');
  list.replaceChildren();
  for (const group of endpointGroups()) {
    const box = cloneTemplate('endpointGroupTemplate');
    query(box, '.ep-group-title').textContent = group.title;
    box.setAttribute('aria-label', group.title);
    const rows = query(box, '.ep-rows');
    for (const item of group.items) {
      const row = cloneTemplate('endpointTemplate');
      const url = `${base}${item.path}`;
      const urlEl = query(row, '.ep-url');
      query(row, '.ep-label').textContent = item.label;
      urlEl.textContent = url;
      const button = /** @type {HTMLButtonElement} */ (query(row, '.ep-copy'));
      button.setAttribute('aria-label', `Copiar ${item.label}`);
      button.addEventListener('click', () => void copyEndpoint(button, urlEl, url));
      rows.appendChild(row);
    }
    list.appendChild(box);
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
 * Lê meta e cor de um cartão. Devolve um texto de erro quando a meta não é válida.
 * @param {Card} card
 * @param {number} n número do slot (1..4)
 * @returns {{goal: number | null, color: string} | string}
 */
function readExtras(card, n) {
  const raw = card.goal.value.trim();
  /** @type {number | null} */
  let goal = null;
  if (raw !== '') {
    goal = Number(raw);
    if (!Number.isInteger(goal) || goal < 1 || goal > GOAL_MAX) {
      return `Slot ${n}: a meta de likes deve ser um número inteiro de 1 a 1.000.000.000 (ou fique em branco para não ter meta).`;
    }
  }
  return { goal, color: card.color.value };
}

/**
 * Envia os 4 slots (URL, meta e cor). `onUnauthorized` é chamado em 401; `reloadAll` recarrega
 * as estatísticas 3 s depois de um salvamento bem-sucedido.
 * @param {{onUnauthorized: () => void, reloadAll: () => void}} hooks
 */
export async function saveSlots(hooks) {
  const btn = buttonById('saveBtn');
  const err = byId('saveErr');
  const ok = byId('saveMsg');
  clearMessage(err);
  setVisible(ok, false);
  const items = [];
  for (let i = 0; i < SLOTS; i++) {
    const extras = readExtras(cards[i], i + 1);
    if (typeof extras === 'string') {
      showMessage(err, extras);
      return;
    }
    items.push({ url: cards[i].input.value, goal: extras.goal, color: extras.color });
  }
  btn.disabled = true;
  try {
    const resp = await api('PUT', '/api/admin/slots', { slots: items });
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!resp.ok) {
      showMessage(err, errorText(resp, `Erro ao salvar (HTTP ${resp.status})`));
      return;
    }
    const saved = resp.data && Array.isArray(resp.data.slots) ? resp.data.slots : items;
    for (let i = 0; i < SLOTS; i++) {
      const item = saved[i] && typeof saved[i] === 'object' ? saved[i] : items[i];
      cards[i].input.value = typeof item.url === 'string' ? item.url : items[i].url;
      cards[i].goal.value = typeof item.goal === 'number' ? String(item.goal) : '';
      cards[i].color.value = normalizeColor(item.color);
      delete cards[i].input.dataset.dirty;
      delete cards[i].goal.dataset.dirty;
      delete cards[i].color.dataset.dirty;
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
