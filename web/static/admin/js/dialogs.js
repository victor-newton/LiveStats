// @ts-check
// Pop-ups: Novo conjunto / Configurar conjunto (mesmo formulário) e Adicionar live.
// A detecção da plataforma enquanto se digita é SÓ visual; quem decide é a api (o erro dela aparece como texto).

import { byId, inputById, buttonById, showMessage, clearMessage } from './dom.js';
import { h } from './h.js';
import { fontDropdown, setFontValue } from './fonts.js';
import { write, readGoal, requestRefresh } from './net.js';
import {
  colorField, overlayPreview, armedButton, platBadge, toast,
} from './ui.js';
import { setTotals, state } from './store.js';
import { PLATFORM_CLASS, PLATFORM_NAME, safeColor } from './util.js';

/** @typedef {import('./store.js').Route} Route */

/** @type {(route: Route) => void} */
let go = () => {};
/** @type {any} */
let editing = null;
/** @type {ReturnType<typeof colorField>} */
let barField;
/** @type {ReturnType<typeof colorField>} */
let textField;
/** @type {ReturnType<typeof overlayPreview>} */
let preview;
/** @type {{likes: number | null, viewers: number | null}} */
let sample = { likes: null, viewers: null };

/** @returns {HTMLButtonElement} */
const fontSel = () => /** @type {HTMLButtonElement} */ (byId('stFont'));
/** @returns {HTMLDialogElement} */
const dlg = (/** @type {string} */ id) => /** @type {HTMLDialogElement} */ (byId(id));

function refreshPreview() {
  const parsed = readGoal(inputById('stGoal').value);
  preview.update({
    likes: sample.likes,
    goal: typeof parsed === 'string' ? null : parsed.goal,
    viewers: sample.viewers,
    bar: barField.get() || barField.last(),
    text: textField.get() || textField.last(),
    font: fontSel().value,
  });
}

/** @param {(route: Route) => void} navigate */
export function initDialogs(navigate) {
  go = navigate;
  for (const d of document.querySelectorAll('dialog')) {
    for (const b of d.querySelectorAll('[data-close]')) b.addEventListener('click', () => /** @type {HTMLDialogElement} */ (d).close());
  }
  // --- conjunto
  barField = colorField('Cor da barra', '#FF4141', refreshPreview);
  textField = colorField('Cor do texto', '#FFFFFF', refreshPreview);
  byId('setColors').append(barField.root, textField.root);
  preview = overlayPreview();
  byId('setPreview').appendChild(preview.root);
  fontDropdown('stFont', fontSel());
  fontSel().addEventListener('change', refreshPreview);
  inputById('stGoal').addEventListener('input', refreshPreview);
  byId('stChat').addEventListener('click', () => {
    const sw = byId('stChat');
    sw.setAttribute('aria-checked', String(sw.getAttribute('aria-checked') !== 'true'));
  });
  byId('setForm').addEventListener('submit', (ev) => { ev.preventDefault(); void saveSet(); });
  armedButton(byId('setDelete'), 'Confirmar: apaga o conjunto e as lives', () => void deleteSet());
  // --- live
  inputById('addUrl').addEventListener('input', detect);
  byId('addForm').addEventListener('submit', (ev) => { ev.preventDefault(); void addLive(); });
}

/** Abre o pop-up de conjunto no modo "novo". */
export function openNewSet() {
  editing = null;
  fillSetForm({ name: '', goal: null, color: '#FF4141', text_color: '#FFFFFF', font: 'Roboto' });
  sample = { likes: 1070, viewers: 59992 };
  byId('dlgSetT').textContent = 'Novo conjunto';
  byId('setDelete').hidden = true;
  byId('setSave').textContent = 'Criar conjunto';
  byId('setChatBox').hidden = true; // a chave do chat é de um conjunto que já existe
  clearMessage(byId('setErr'));
  refreshPreview();
  dlg('dlgSet').showModal();
  inputById('stName').focus();
}

/** @param {any} set */
export function openConfigSet(set) {
  editing = set;
  fillSetForm(set);
  const t = setTotals(set);
  sample = { likes: t.likes, viewers: t.viewers };
  byId('dlgSetT').textContent = 'Configurar conjunto';
  byId('setDelete').hidden = false;
  byId('setSave').textContent = 'Salvar';
  byId('setChatBox').hidden = false;
  byId('stChat').setAttribute('aria-checked', String(!!(set.chat && set.chat.enabled)));
  clearMessage(byId('setErr'));
  refreshPreview();
  dlg('dlgSet').showModal();
}

/** @param {any} set */
function fillSetForm(set) {
  inputById('stName').value = set.name || '';
  inputById('stGoal').value = typeof set.goal === 'number' ? String(set.goal) : '';
  barField.set(safeColor(set.color, '#FF4141'));
  textField.set(safeColor(set.text_color, '#FFFFFF'));
  setFontValue(fontSel(), set.font);
}

async function saveSet() {
  const err = byId('setErr');
  clearMessage(err);
  const name = inputById('stName').value.trim();
  const parsed = readGoal(inputById('stGoal').value);
  const bar = barField.get();
  const txt = textField.get();
  if (!name) { showMessage(err, 'Dê um nome ao conjunto.'); return; }
  if (typeof parsed === 'string') { showMessage(err, parsed); return; }
  if (!bar || !txt) { showMessage(err, 'Cor inválida: use o formato #RRGGBB.'); return; }
  const body = { name, goal: parsed.goal, color: bar, font: fontSel().value, text_color: txt };
  const btn = buttonById('setSave');
  btn.disabled = true;
  const resp = editing
    ? await write('PUT', `/api/admin/sets/${editing.id}`, body, err)
    : await write('POST', '/api/admin/sets', body, err);
  if (!resp) { btn.disabled = false; return; }
  // D57: a chave "Chat do conjunto" só grava se mudou, e depois de o conjunto ter sido salvo
  const chatOn = byId('stChat').getAttribute('aria-checked') === 'true';
  if (editing && chatOn !== !!(editing.chat && editing.chat.enabled)) {
    const chatResp = await write('PUT', `/api/admin/sets/${editing.id}/chat`, { enabled: chatOn }, err);
    if (!chatResp) { btn.disabled = false; void requestRefresh(); return; }
  }
  btn.disabled = false;
  const wasNew = !editing;
  const newId = resp.data && resp.data.set ? resp.data.set.id : null;
  dlg('dlgSet').close();
  toast(wasNew ? 'Conjunto criado' : 'Conjunto salvo');
  await requestRefresh();
  if (wasNew && typeof newId === 'number') go({ screen: 'conjunto', setId: newId, focus: null });
}

async function deleteSet() {
  if (!editing) return;
  const resp = await write('DELETE', `/api/admin/sets/${editing.id}`, undefined, byId('setErr'));
  if (!resp) return;
  dlg('dlgSet').close();
  toast('Conjunto apagado');
  const uid = state.route.userId ?? null;
  state.foreign.delete(editing.id);
  go(uid === null ? { screen: 'conjuntos', setId: null, focus: null } : { screen: 'mestre', setId: null, focus: null, seg: 'dados', userId: uid });
  await requestRefresh();
}

// ------------------------------------------------------------------ adicionar live

/** @type {[RegExp, string, (u: string) => string][]} */
const DETECT = [
  [/youtu(\.be|be\.com)/i, 'youtube', (u) => (u.match(/[?&]v=([\w-]{11})/) || u.match(/youtu\.be\/([\w-]{11})/) || u.match(/(@[\w.-]+)/) || [, 'vídeo'])[1] || 'vídeo'],
  [/twitch\.tv/i, 'twitch', (u) => ((u.match(/twitch\.tv\/(\w+)/i) || [, '?'])[1] || '?').toLowerCase()],
  [/kick\.com/i, 'kick', (u) => ((u.match(/kick\.com\/([\w-]+)/i) || [, '?'])[1] || '?').toLowerCase()],
];

/** Reconhece a plataforma só para mostrar; a api é quem valida de verdade. */
function detect() {
  const box = byId('detect');
  const nameIn = inputById('addName');
  const u = inputById('addUrl').value.trim();
  box.classList.remove('bad');
  if (!u) {
    box.replaceChildren(h('span', { class: 'hint', text: 'Cole o link: a plataforma aparece sozinha.' }));
    nameIn.placeholder = 'Automático (canal · plataforma)';
    return;
  }
  const hit = DETECT.find(([re]) => re.test(u));
  if (!hit) {
    box.classList.add('bad');
    box.replaceChildren(
      h('span', { class: 'pill err' }, h('span', { class: 'dot' }), 'Link não reconhecido'),
      h('span', { class: 'hint', text: 'Use um link do YouTube, da Twitch ou do Kick.' }),
    );
    nameIn.placeholder = 'Automático (canal · plataforma)';
    return;
  }
  const [, plat, ref] = hit;
  const r = ref(u);
  box.replaceChildren(
    platBadge(plat),
    h('span', { class: 'detected' }, h('b', { text: PLATFORM_NAME[plat] }), ' · ', h('span', { class: 'num', text: r })),
  );
  nameIn.placeholder = `Automático: ${r.replace(/^@/, '')} · ${PLATFORM_NAME[plat]}`;
  box.dataset.platform = PLATFORM_CLASS[plat];
}

/** @param {any} set */
export function openAddLive(set) {
  dlg('dlgAdd').dataset.setId = String(set.id);
  byId('dlgAddT').textContent = `Adicionar live a ${set.name}`;
  inputById('addUrl').value = '';
  inputById('addName').value = '';
  clearMessage(byId('addErr'));
  detect();
  dlg('dlgAdd').showModal();
  inputById('addUrl').focus();
}

async function addLive() {
  const err = byId('addErr');
  const id = Number(dlg('dlgAdd').dataset.setId);
  /** @type {Record<string, string>} */
  const body = { url: inputById('addUrl').value };
  const label = inputById('addName').value.trim();
  if (label) body.label = label;
  const btn = buttonById('addSave');
  btn.disabled = true;
  const resp = await write('POST', `/api/admin/sets/${id}/lives`, body, err);
  btn.disabled = false;
  if (!resp) return;
  const liveId = resp.data && resp.data.live ? resp.data.live.id : null;
  dlg('dlgAdd').close();
  toast('Live adicionada');
  await requestRefresh();
  if (typeof liveId === 'number') go({ screen: 'conjunto', setId: id, focus: { live: liveId }, userId: state.route.userId ?? null });
}

/** Fecha pop-ups abertos e limpa os campos (saída da sessão). */
export function resetDialogs() {
  for (const d of document.querySelectorAll('dialog')) {
    if (/** @type {HTMLDialogElement} */ (d).open) /** @type {HTMLDialogElement} */ (d).close();
  }
  editing = null;
  byId('dlgAddT').textContent = 'Adicionar live';
  inputById('addUrl').value = '';
  inputById('addName').value = '';
  inputById('stName').value = '';
  clearMessage(byId('setErr'));
  clearMessage(byId('addErr'));
}
