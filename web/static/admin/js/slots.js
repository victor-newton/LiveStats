// @ts-check
// Aba "Lives" (D36): 4 cartões autocontidos (URL, meta de likes, cor da barra, fonte, cor do texto,
// estatísticas, os 3 endereços públicos do slot e um botão Salvar próprio).

import { api, errorText } from './api.js';
import {
  byId, cloneTemplate, query, setVisible, showMessage, clearMessage,
} from './dom.js';
import { registerFontSelect, setFontValue, DEFAULT_FONT } from './fonts.js';
import { renderEndpointList, liveEndpoints, clearEndpointTimers } from './endpoints.js';

const SLOTS = 4;
const STATUS_CLASSES = ['ao_vivo', 'encerrada', 'agendada'];
const SAVE_MSG_MS = 2500;
const RELOAD_AFTER_SAVE_MS = 3000;
const GOAL_MAX = 1_000_000_000;
const DEFAULT_COLOR = '#FF0033';
const DEFAULT_TEXT_COLOR = '#ffffff';
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
 * @property {string} [font]
 * @property {string} [text_color]
 * @property {LiveStats | null} stats
 */

/**
 * @typedef {object} Card
 * @property {HTMLElement} root
 * @property {HTMLInputElement} input
 * @property {HTMLInputElement} goal
 * @property {HTMLInputElement} color
 * @property {HTMLSelectElement} font
 * @property {HTMLInputElement} textColor
 * @property {HTMLButtonElement} saveBtn
 * @property {HTMLElement} saveOk
 * @property {HTMLElement} saveErr
 * @property {HTMLElement} endpoints
 * @property {HTMLElement} dot
 * @property {HTMLElement} statsBox
 * @property {HTMLElement} title
 */

/** @typedef {{url: string, goal: number | null, color: string, font: string, text_color: string}} SavedItem */
/** @typedef {{onUnauthorized: () => void, reloadAll: () => void}} SlotHooks */

/** @type {Card[]} */
let cards = [];
/** Últimos valores GRAVADOS no servidor: salvar um cartão regrava os outros três com eles.
 * @type {SavedItem[] | null} */
let serverItems = null;
/** @type {number[]} */
let timers = [];
/** @type {SlotHooks | null} */
let currentHooks = null;

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
 * Marca o campo como editado (a atualização periódica não o sobrescreve).
 * @param {HTMLElement} field
 */
function trackDirty(field) {
  const mark = () => {
    field.dataset.dirty = '1';
  };
  field.addEventListener('input', mark);
  field.addEventListener('change', mark);
}

/**
 * Define o que fazer em 401 e depois de salvar (chamado uma vez pelo ponto de entrada).
 * @param {SlotHooks} hooks
 */
export function setSlotHooks(hooks) {
  currentHooks = hooks;
}

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
    root.dataset.slot = String(n);
    const label = query(root, '.slot-label');
    const input = /** @type {HTMLInputElement} */ (query(root, '.slot-input'));
    input.id = `inp${n}`;
    label.setAttribute('for', input.id);
    label.textContent = `Slot ${n}`;
    trackDirty(input);

    const goal = /** @type {HTMLInputElement} */ (query(root, '.slot-goal'));
    goal.id = `goal${n}`;
    labelWithHint(query(root, '.slot-goal-label'), goal.id, 'Meta de likes', `do slot ${n}`);
    const color = /** @type {HTMLInputElement} */ (query(root, '.slot-color'));
    color.id = `color${n}`;
    labelWithHint(query(root, '.slot-color-label'), color.id, 'Cor da barra', `do slot ${n}`);
    color.value = DEFAULT_COLOR.toLowerCase();

    const font = /** @type {HTMLSelectElement} */ (query(root, '.look-font'));
    font.id = `font-${n}`;
    labelWithHint(query(root, '.look-font-label'), font.id, 'Fonte', `da live ${n}`);
    const textColor = /** @type {HTMLInputElement} */ (query(root, '.look-color'));
    textColor.id = `textcolor-${n}`;
    labelWithHint(query(root, '.look-color-label'), textColor.id, 'Cor do texto', `da live ${n}`);
    textColor.value = DEFAULT_TEXT_COLOR;
    registerFontSelect(font);
    for (const field of [goal, color, font, textColor]) trackDirty(field);

    const saveBtn = /** @type {HTMLButtonElement} */ (query(root, '.slot-save'));
    saveBtn.id = `saveBtn${n}`;
    saveBtn.setAttribute('aria-label', `Salvar slot ${n}`);
    const saveOk = query(root, '.slot-ok');
    saveOk.id = `saveMsg${n}`;
    const saveErr = query(root, '.slot-err');
    saveErr.id = `saveErr${n}`;
    saveBtn.addEventListener('click', () => {
      if (currentHooks) void saveSlot(n, currentHooks);
    });

    const heading = query(root, '.slot-heading');
    heading.id = `slot-title-${n}`;
    heading.textContent = `Live ${n}`;
    root.setAttribute('aria-labelledby', heading.id);
    query(root, '.slot-ep-title').textContent = `Endereços da live ${n}`;

    grid.appendChild(root);
    cards.push({
      root,
      input,
      goal,
      color,
      font,
      textColor,
      saveBtn,
      saveOk,
      saveErr,
      endpoints: query(root, '.slot-endpoints'),
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
  clearEndpointTimers();
  serverItems = null;
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
 * Cor do servidor -> valor aceito por <input type="color"> (minúsculas; cai no padrão se vier algo estranho).
 * @param {unknown} value
 * @param {string} [fallback]
 * @returns {string}
 */
function normalizeColor(value, fallback = DEFAULT_COLOR) {
  return typeof value === 'string' && COLOR_RE.test(value) ? value.toLowerCase() : fallback.toLowerCase();
}

/**
 * @param {Partial<SlotItem>} s
 * @returns {SavedItem}
 */
function toSaved(s) {
  return {
    url: typeof s.url === 'string' ? s.url : '',
    goal: typeof s.goal === 'number' ? s.goal : null,
    color: normalizeColor(s.color).toUpperCase(),
    font: typeof s.font === 'string' && s.font ? s.font : DEFAULT_FONT,
    text_color: normalizeColor(s.text_color, DEFAULT_TEXT_COLOR).toUpperCase(),
  };
}

/**
 * Aplica o que o servidor devolveu em GET /api/admin/slots.
 * Campos que o usuário está editando (não salvos) NÃO são sobrescritos.
 * @param {{slots: SlotItem[]}} data
 */
export function applySlots(data) {
  const items = Array.isArray(data.slots) ? data.slots : [];
  if (items.length === SLOTS) serverItems = items.map(toSaved);
  for (let i = 0; i < SLOTS; i++) {
    const item = items[i];
    const card = cards[i];
    if (!item || !card) continue;
    const url = typeof item.url === 'string' ? item.url : '';
    if (card.input.dataset.dirty !== '1') card.input.value = url;
    if (card.goal.dataset.dirty !== '1') card.goal.value = typeof item.goal === 'number' ? String(item.goal) : '';
    if (card.color.dataset.dirty !== '1') card.color.value = normalizeColor(item.color);
    if (card.font.dataset.dirty !== '1') setFontValue(card.font, item.font);
    if (card.textColor.dataset.dirty !== '1') card.textColor.value = normalizeColor(item.text_color, DEFAULT_TEXT_COLOR);
    renderStats(card, item.stats, url);
  }
}

/**
 * Preenche os endereços públicos de cada live (cada um no cartão do seu slot).
 * @param {string} base origem pública (sem barra final)
 */
export function renderSlotEndpoints(base) {
  cards.forEach((card, i) => renderEndpointList(card.endpoints, base, liveEndpoints(i + 1)));
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
 * Lê a meta de um cartão. Devolve um texto de erro quando não é válida.
 * @param {Card} card
 * @param {number} n número do slot (1..4)
 * @returns {{goal: number | null} | string}
 */
function readGoal(card, n) {
  const raw = card.goal.value.trim();
  /** @type {number | null} */
  let goal = null;
  if (raw !== '') {
    goal = Number(raw);
    if (!Number.isInteger(goal) || goal < 1 || goal > GOAL_MAX) {
      return `Slot ${n}: a meta de likes deve ser um número inteiro de 1 a 1.000.000.000 (ou fique em branco para não ter meta).`;
    }
  }
  return { goal };
}

/**
 * Salva UM cartão: envia o PUT de slots completo, com os valores deste cartão e os já gravados
 * nos outros três (rascunhos de outros cartões não são enviados).
 * @param {number} n 1..4
 * @param {SlotHooks} hooks
 */
export async function saveSlot(n, hooks) {
  const card = cards[n - 1];
  clearMessage(card.saveErr);
  setVisible(card.saveOk, false);
  card.saveBtn.disabled = true;
  try {
    // Clicou antes da primeira carga terminar: busca o que está gravado (os outros cartões vão como estão).
    if (!serverItems) {
      const first = await loadSlots();
      if (first.status === 401) {
        hooks.onUnauthorized();
        return;
      }
    }
  } catch (e) {
    showMessage(card.saveErr, 'Falha na requisição. Verifique a conexão e tente de novo.');
    return;
  } finally {
    card.saveBtn.disabled = false;
  }
  if (!serverItems) {
    showMessage(card.saveErr, 'Os slots ainda não foram carregados. Aguarde um instante e tente de novo.');
    return;
  }
  const goal = readGoal(card, n);
  if (typeof goal === 'string') {
    showMessage(card.saveErr, goal);
    return;
  }
  const items = serverItems.map((item) => ({ ...item }));
  items[n - 1] = {
    url: card.input.value,
    goal: goal.goal,
    color: card.color.value,
    font: card.font.value,
    text_color: card.textColor.value,
  };
  card.saveBtn.disabled = true;
  try {
    const resp = await api('PUT', '/api/admin/slots', { slots: items });
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!resp.ok) {
      showMessage(card.saveErr, errorText(resp, `Erro ao salvar (HTTP ${resp.status})`));
      return;
    }
    const saved = resp.data && Array.isArray(resp.data.slots) ? resp.data.slots : items;
    serverItems = saved.map((/** @type {SlotItem} */ s, /** @type {number} */ i) => toSaved({ ...items[i], ...s }));
    const item = serverItems[n - 1];
    card.input.value = item.url;
    card.goal.value = item.goal === null ? '' : String(item.goal);
    card.color.value = item.color.toLowerCase();
    setFontValue(card.font, item.font);
    card.textColor.value = item.text_color.toLowerCase();
    for (const f of [card.input, card.goal, card.color, card.font, card.textColor]) delete f.dataset.dirty;
    setVisible(card.saveOk, true);
    timers.push(window.setTimeout(() => setVisible(card.saveOk, false), SAVE_MSG_MS));
    timers.push(window.setTimeout(hooks.reloadAll, RELOAD_AFTER_SAVE_MS));
  } catch (e) {
    showMessage(card.saveErr, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    card.saveBtn.disabled = false;
  }
}
