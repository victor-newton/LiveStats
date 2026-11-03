// @ts-check
// Seção "Configurações" (D35): ID do canal do contador de inscritos e, para o contador e
// para cada live, a fonte (lista fechada vinda de GET /api/admin/fonts) e a cor do texto.
// Cada opção do dropdown é desenhada na própria fonte via CSSOM (style.setProperty).

import { api, errorText } from './api.js';
import {
  byId, inputById, buttonById, cloneTemplate, query, setVisible, showMessage, clearMessage,
} from './dom.js';
import { savedSlotItems } from './slots.js';

const SLOTS = 4;
const CHANNEL_RE = /^UC[0-9A-Za-z_-]{22}$/;
const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
const FONT_NAME_RE = /^[A-Za-z0-9 ]{1,40}$/;
const DEFAULT_FONT = 'Roboto';
const DEFAULT_TEXT_COLOR = '#ffffff';
const OK_MSG_MS = 2500;

/** @typedef {{key: string, label: string, hint: string, font: HTMLSelectElement, color: HTMLInputElement}} Look */

/** @type {Look[]} índice 0 = contador; 1..4 = lives */
let looks = [];
/** @type {string[]} */
let fonts = [];
/** @type {number[]} */
let timers = [];

/**
 * Família CSS segura: só nomes da lista fechada (letras, números e espaço), entre aspas, com genérica.
 * @param {string} name
 * @returns {string}
 */
export function fontStack(name) {
  return FONT_NAME_RE.test(name) ? `"${name}", sans-serif` : 'sans-serif';
}

/** @param {unknown} v @returns {string} */
function colorValue(v) {
  return typeof v === 'string' && COLOR_RE.test(v) ? v.toLowerCase() : DEFAULT_TEXT_COLOR;
}

/** Monta as 5 linhas (contador + 4 lives), uma vez. */
export function initSettings() {
  const list = byId('lookList');
  list.replaceChildren();
  looks = [];
  const entries = [['subs', 'Contador de inscritos', 'do contador'], ...Array.from({ length: SLOTS }, (_, i) => [
    String(i + 1), `Live ${i + 1}`, `da live ${i + 1}`,
  ])];
  for (const [key, name, hint] of entries) {
    const row = cloneTemplate('lookTemplate');
    query(row, '.look-name').textContent = name;
    const font = /** @type {HTMLSelectElement} */ (query(row, '.look-font'));
    font.id = `font-${key}`;
    labelWithHint(query(row, '.look-font-label'), font.id, 'Fonte', hint);
    const color = /** @type {HTMLInputElement} */ (query(row, '.look-color'));
    color.id = `textcolor-${key}`;
    labelWithHint(query(row, '.look-color-label'), color.id, 'Cor do texto', hint);
    for (const field of [font, color]) {
      field.addEventListener('input', () => {
        field.dataset.dirty = '1';
      });
    }
    const look = { key, label: name, hint, font, color };
    font.addEventListener('change', () => syncPreview(look));
    list.appendChild(row);
    looks.push(look);
  }
  fillFontOptions();
}

/**
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

/** Preenche os dropdowns com a lista de fontes, mantendo a escolha atual. */
function fillFontOptions() {
  for (const look of looks) {
    const current = look.font.value;
    look.font.replaceChildren();
    for (const name of fonts) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      opt.style.setProperty('font-family', fontStack(name));
      look.font.appendChild(opt);
    }
    if (fonts.includes(current)) look.font.value = current;
    look.font.style.setProperty('font-family', fontStack(look.font.value));
  }
}

/**
 * @param {Look} look
 */
function syncPreview(look) {
  look.font.style.setProperty('font-family', fontStack(look.font.value));
}

/** Carrega a lista fechada de fontes. @returns {Promise<import('./api.js').ApiResponse>} */
export async function loadFonts() {
  const resp = await api('GET', '/api/admin/fonts');
  if (resp.ok && resp.data && Array.isArray(resp.data.fonts)) {
    fonts = resp.data.fonts.filter((/** @type {unknown} */ f) => typeof f === 'string' && FONT_NAME_RE.test(f));
    fillFontOptions();
  }
  return resp;
}

/**
 * Aplica GET /api/admin/settings (campos sujos não são sobrescritos).
 * @param {any} data
 */
export function applySettings(data) {
  const channel = inputById('channelInput');
  if (channel.dataset.dirty !== '1') {
    channel.value = typeof data.channel_id === 'string' ? data.channel_id : '';
  }
  setLook(looks[0], data.subs_font, data.subs_text_color);
}

/**
 * Aplica as aparências das 4 lives (de GET /api/admin/slots).
 * @param {{slots?: any[]}} data
 */
export function applySlotLooks(data) {
  const items = Array.isArray(data.slots) ? data.slots : [];
  for (let i = 0; i < SLOTS; i++) {
    if (items[i]) setLook(looks[i + 1], items[i].font, items[i].text_color);
  }
}

/**
 * @param {Look | undefined} look
 * @param {unknown} font
 * @param {unknown} color
 */
function setLook(look, font, color) {
  if (!look) return;
  if (look.font.dataset.dirty !== '1') {
    look.font.value = typeof font === 'string' && fonts.includes(font) ? font : DEFAULT_FONT;
    syncPreview(look);
  }
  if (look.color.dataset.dirty !== '1') look.color.value = colorValue(color);
}

/** Esvazia tudo (sessão encerrada). */
export function resetSettings() {
  for (const t of timers) clearTimeout(t);
  timers = [];
  fonts = [];
  inputById('channelInput').value = '';
  delete inputById('channelInput').dataset.dirty;
  for (const id of ['channelErr', 'lookErr']) clearMessage(byId(id));
  for (const id of ['channelOk', 'lookOk']) setVisible(byId(id), false);
  initSettings();
}

/** @param {HTMLElement} el */
function flash(el) {
  setVisible(el, true);
  timers.push(window.setTimeout(() => setVisible(el, false), OK_MSG_MS));
}

/**
 * Valida no navegador (a api valida de novo) e salva o canal.
 * @param {{onUnauthorized: () => void}} hooks
 */
export async function saveChannel(hooks) {
  const input = inputById('channelInput');
  const err = byId('channelErr');
  const btn = buttonById('channelBtn');
  clearMessage(err);
  setVisible(byId('channelOk'), false);
  const value = input.value.trim();
  if (!CHANNEL_RE.test(value)) {
    showMessage(err, 'ID de canal inválido: comece com UC e use 24 caracteres (letras, números, _ ou -).');
    return;
  }
  btn.disabled = true;
  try {
    const resp = await api('PUT', '/api/admin/settings', { channel_id: value });
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!resp.ok) {
      showMessage(err, errorText(resp, `Erro ao salvar o canal (HTTP ${resp.status})`));
      return;
    }
    delete input.dataset.dirty;
    input.value = resp.data && typeof resp.data.channel_id === 'string' ? resp.data.channel_id : value;
    flash(byId('channelOk'));
  } catch (e) {
    showMessage(err, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    btn.disabled = false;
  }
}

/**
 * Salva fonte e cor do contador (settings) e das 4 lives (slots; url, meta e cor ficam como já gravados).
 * @param {{onUnauthorized: () => void, reload: () => void}} hooks
 */
export async function saveLooks(hooks) {
  const err = byId('lookErr');
  const btn = buttonById('lookBtn');
  clearMessage(err);
  setVisible(byId('lookOk'), false);
  const saved = savedSlotItems();
  if (!saved) {
    showMessage(err, 'Os slots ainda não foram carregados. Aguarde um instante e tente de novo.');
    return;
  }
  btn.disabled = true;
  try {
    const subs = looks[0];
    const first = await api('PUT', '/api/admin/settings', {
      subs_font: subs.font.value,
      subs_text_color: subs.color.value,
    });
    if (first.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!first.ok) {
      showMessage(err, errorText(first, `Erro ao salvar a aparência (HTTP ${first.status})`));
      return;
    }
    const slots = saved.map((item, i) => ({
      url: item.url,
      goal: item.goal,
      color: item.color,
      font: looks[i + 1].font.value,
      text_color: looks[i + 1].color.value,
    }));
    const second = await api('PUT', '/api/admin/slots', { slots });
    if (second.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!second.ok) {
      showMessage(err, errorText(second, `Erro ao salvar a aparência (HTTP ${second.status})`));
      return;
    }
    for (const look of looks) {
      delete look.font.dataset.dirty;
      delete look.color.dataset.dirty;
    }
    flash(byId('lookOk'));
    hooks.reload();
  } catch (e) {
    showMessage(err, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    btn.disabled = false;
  }
}

/** Marca o campo do canal como editado (para a atualização periódica não sobrescrever). */
export function wireChannelInput() {
  inputById('channelInput').addEventListener('input', () => {
    inputById('channelInput').dataset.dirty = '1';
  });
}
