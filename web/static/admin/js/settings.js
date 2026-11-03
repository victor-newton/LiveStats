// @ts-check
// Aba "Inscritos" (D35/D36): ID do canal do contador de inscritos e fonte/cor do texto do overlay
// do contador (GET/PUT /api/admin/settings). A aparência das lives fica no cartão de cada live.

import { api, errorText } from './api.js';
import {
  byId, inputById, buttonById, setVisible, showMessage, clearMessage,
} from './dom.js';
import { registerFontSelect, setFontValue } from './fonts.js';

const CHANNEL_RE = /^UC[0-9A-Za-z_-]{22}$/;
const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
const DEFAULT_TEXT_COLOR = '#ffffff';
const OK_MSG_MS = 2500;

/** @type {number[]} */
let timers = [];

/** @returns {HTMLSelectElement} */
function fontSelect() {
  return /** @type {HTMLSelectElement} */ (byId('font-subs'));
}

/** @param {unknown} v @returns {string} */
function colorValue(v) {
  return typeof v === 'string' && COLOR_RE.test(v) ? v.toLowerCase() : DEFAULT_TEXT_COLOR;
}

/** Liga os campos estáticos da aba (uma vez): dirty, dropdown de fonte. */
export function initSettings() {
  const font = fontSelect();
  delete font.dataset.dirty;
  delete inputById('textcolor-subs').dataset.dirty;
  registerFontSelect(font);
}

/** Marca os campos da aparência como editados (a atualização periódica não os sobrescreve). */
export function wireSettingsInputs() {
  for (const el of [inputById('channelInput'), fontSelect(), inputById('textcolor-subs')]) {
    const mark = () => {
      el.dataset.dirty = '1';
    };
    el.addEventListener('input', mark);
    el.addEventListener('change', mark);
  }
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
  const font = fontSelect();
  if (font.dataset.dirty !== '1') setFontValue(font, data.subs_font);
  const color = inputById('textcolor-subs');
  if (color.dataset.dirty !== '1') color.value = colorValue(data.subs_text_color);
}

/** Esvazia tudo (sessão encerrada). */
export function resetSettings() {
  for (const t of timers) clearTimeout(t);
  timers = [];
  const channel = inputById('channelInput');
  channel.value = '';
  delete channel.dataset.dirty;
  inputById('textcolor-subs').value = DEFAULT_TEXT_COLOR;
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
 * Salva fonte e cor do texto do contador de inscritos (só settings).
 * @param {{onUnauthorized: () => void, reload: () => void}} hooks
 */
export async function saveLooks(hooks) {
  const err = byId('lookErr');
  const btn = buttonById('lookBtn');
  clearMessage(err);
  setVisible(byId('lookOk'), false);
  btn.disabled = true;
  try {
    const font = fontSelect();
    const color = inputById('textcolor-subs');
    const resp = await api('PUT', '/api/admin/settings', {
      subs_font: font.value,
      subs_text_color: color.value,
    });
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    if (!resp.ok) {
      showMessage(err, errorText(resp, `Erro ao salvar a aparência (HTTP ${resp.status})`));
      return;
    }
    delete font.dataset.dirty;
    delete color.dataset.dirty;
    flash(byId('lookOk'));
    hooks.reload();
  } catch (e) {
    showMessage(err, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    btn.disabled = false;
  }
}
