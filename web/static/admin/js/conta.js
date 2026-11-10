// @ts-check
// Conta (D48/D53): nome, trocar a senha, sair e o endereço do painel; e a troca de senha OBRIGATÓRIA em tela cheia
// (primeiro acesso com senha provisória). As senhas só existem nos campos e na requisição: nunca vão para storage,
// URL ou log, e os campos são esvaziados ao enviar e ao sair.

import { byId, inputById, buttonById, showMessage, clearMessage } from './dom.js';
import { h } from './h.js';
import { write, requestRefresh } from './net.js';
import { linkItem, toast } from './ui.js';
import { state, isMaster } from './store.js';
import { titleTip } from './infotip.js';
import { clearMyData } from './danger.js';

const MIN_PASSWORD = 8;

/** @type {Record<string, any>} */
const R = {};
/** @type {() => void} */
let afterChange = () => {};
let builtKey = '';

/**
 * Regras da senha nova (D48): mínimo 8 caracteres, diferente do usuário e da atual; as duas digitações iguais.
 * @param {string} current
 * @param {string} next
 * @param {string} repeat
 * @param {string} username
 * @returns {string} mensagem de erro, ou vazio
 */
export function passwordProblem(current, next, repeat, username) {
  if (!current) return 'Informe a senha atual.';
  if (next.length < MIN_PASSWORD) return `A senha nova precisa ter pelo menos ${MIN_PASSWORD} caracteres.`;
  if (next.toLowerCase() === username.toLowerCase()) return 'A senha nova não pode ser igual ao nome de usuário.';
  if (next === current) return 'A senha nova precisa ser diferente da atual.';
  if (next !== repeat) return 'As duas senhas novas não conferem.';
  return '';
}

/**
 * Envia a troca de senha (204). Esvazia os campos em qualquer caso.
 * @param {HTMLInputElement[]} fields atual, nova, repetição
 * @param {HTMLElement} errEl
 * @returns {Promise<boolean>}
 */
async function submitPassword(fields, errEl) {
  const [cur, nw, rep] = fields;
  const username = state.user ? state.user.username : '';
  const problem = passwordProblem(cur.value, nw.value, rep.value, username);
  if (problem) { showMessage(errEl, problem); return false; }
  const resp = await write('POST', '/api/admin/me/password', { current: cur.value, new: nw.value }, errEl);
  for (const f of fields) f.value = '';
  return !!resp;
}

/** Monta a estrutura fixa da tela Conta. */
export function initConta() {
  R.name = h('input', { id: 'acName', type: 'text', maxlength: 60, autocomplete: 'off' });
  R.nameOk = h('span', { class: 'save-msg', role: 'status', hidden: true, text: '✓ Nome salvo' });
  R.nameErr = h('p', { class: 'msg err', role: 'alert', hidden: true });
  R.nameBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'acNameSave', text: 'Salvar nome' });
  R.nameBtn.addEventListener('click', () => void saveName());
  R.info = h('div', { class: 'set-stats', id: 'acInfo' });
  R.who = h('p', { id: 'acWho' });
  const profile = h('div', { class: 'card glow-card acc-indigo' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Perfil' }), R.who)),
    h('div', { class: 'field' }, h('label', { for: 'acName', text: 'Nome exibido' }), R.name),
    h('div', { class: 'form-actions' }, R.nameBtn, R.nameOk), R.nameErr,
    R.info);

  R.cur = h('input', { id: 'acCur', type: 'password', autocomplete: 'current-password', maxlength: 1024 });
  R.new1 = h('input', { id: 'acNew', type: 'password', autocomplete: 'new-password', maxlength: 1024 });
  R.new2 = h('input', { id: 'acNew2', type: 'password', autocomplete: 'new-password', maxlength: 1024 });
  R.pwOk = h('span', { class: 'save-msg', role: 'status', hidden: true, text: '✓ Senha alterada' });
  R.pwErr = h('p', { class: 'msg err', role: 'alert', hidden: true });
  R.pwBtn = h('button', { type: 'submit', class: 'btn btn-primary', id: 'acPwSave', text: 'Trocar senha' });
  R.pwForm = h('form', { class: 'stack', id: 'acPwForm', novalidate: true },
    h('div', { class: 'field' }, h('label', { for: 'acCur', text: 'Senha atual' }), R.cur),
    h('div', { class: 'field' }, h('label', { for: 'acNew', text: 'Senha nova' }), R.new1, h('span', { class: 'hint', text: `Mínimo ${MIN_PASSWORD} caracteres, diferente do nome de usuário.` })),
    h('div', { class: 'field' }, h('label', { for: 'acNew2', text: 'Repita a senha nova' }), R.new2),
    h('div', { class: 'form-actions' }, R.pwBtn, R.pwOk), R.pwErr);
  R.pwForm.addEventListener('submit', (ev) => { ev.preventDefault(); void savePassword(); });
  const pw = h('div', { class: 'card glow-card acc-amber' },
    h('div', { class: 'card-h' }, titleTip('h2', 'Trocar senha', 'Use uma senha que você não usa em outro lugar.')),
    R.pwForm);

  R.url = h('div', { class: 'links-grid', id: 'acUrl' });
  R.out = h('button', { type: 'button', class: 'btn', id: 'acOut', text: 'Sair deste navegador' });
  const sess = h('div', { class: 'card glow-card acc-fuchsia' },
    h('div', { class: 'card-h' }, titleTip('h2', 'Sessão e endereço do painel', 'O endereço abaixo é o do painel nesta instalação.')),
    R.url,
    h('div', { class: 'form-actions' }, R.out));

  const clearBtn = h('button', { type: 'button', class: 'btn danger', id: 'acClearData', text: 'Limpar meus dados' });
  clearBtn.addEventListener('click', () => clearMyData());
  const risk = h('div', { class: 'card glow-card acc-rose', id: 'acRisk' },
    h('div', { class: 'card-h' }, titleTip('h2', 'Zona de risco', 'Apaga todos os seus conjuntos, lives, leituras e o contador. A conta e a senha continuam. Pede para digitar o nome de usuário e não dá para desfazer.')),
    h('div', { class: 'form-actions' }, clearBtn));

  byId('scr-conta').replaceChildren(
    h('div', { class: 'head' }, titleTip('h1', 'Conta', 'Seu nome, sua senha e a sua sessão.')),
    h('div', { class: 'grid-2' }, profile, pw),
    sess,
    risk);
}

/** @param {{onLogout: () => void}} hooks */
export function wireConta(hooks) {
  R.out.addEventListener('click', hooks.onLogout);
}

/** @param {() => void} fn chamado quando o nome muda (o cabeçalho se atualiza) */
export function onProfileChange(fn) {
  afterChange = fn;
}

/** Descarta o conteúdo e esvazia as senhas (sessão encerrada). */
export function resetConta() {
  builtKey = '';
  if (!R.name) return;
  for (const f of [R.cur, R.new1, R.new2]) f.value = '';
  R.name.value = '';
  R.url.replaceChildren();
  clearMessage(R.pwErr);
  clearMessage(R.nameErr);
  R.pwOk.hidden = true;
  R.nameOk.hidden = true;
}

/** Pop-up de confirmação por digitação do próprio nome de usuário (autoatendimento). */
async function saveName() {
  clearMessage(R.nameErr);
  R.nameOk.hidden = true;
  R.nameBtn.disabled = true;
  const resp = await write('PUT', '/api/admin/me', { display_name: R.name.value.trim() }, R.nameErr);
  R.nameBtn.disabled = false;
  if (!resp) return;
  if (state.user) state.user = { ...state.user, display_name: R.name.value.trim() };
  R.nameOk.hidden = false;
  window.setTimeout(() => { R.nameOk.hidden = true; }, 2500);
  toast('Nome salvo');
  afterChange();
}

async function savePassword() {
  clearMessage(R.pwErr);
  R.pwOk.hidden = true;
  R.pwBtn.disabled = true;
  const ok = await submitPassword([R.cur, R.new1, R.new2], R.pwErr);
  R.pwBtn.disabled = false;
  if (!ok) return;
  R.pwOk.hidden = false;
  window.setTimeout(() => { R.pwOk.hidden = true; }, 2500);
  toast('Senha alterada');
}

/** @param {boolean} first */
export function render(first) {
  const u = state.user;
  if (!u) return;
  const key = JSON.stringify([u, state.adminUrl]);
  if (!first && key === builtKey) return;
  const keepName = !first && R.name === document.activeElement;
  builtKey = key;
  if (!keepName) R.name.value = u.display_name || '';
  R.who.textContent = `${u.username} · ${isMaster() ? 'mestre' : 'usuário'}`;
  const stat = (/** @type {string} */ v, /** @type {string} */ label) => h('div', null, h('b', { class: 'num', 'data-static': '', text: v }), h('span', { text: label }));
  R.info.replaceChildren(
    stat(isMaster() ? 'sem limite' : String(u.max_sets), 'conjuntos permitidos'),
    stat(isMaster() ? 'sem limite' : String(u.max_lives), 'lives permitidas'),
    stat(u.has_counter ? 'liberado' : 'desativado', 'contador de inscritos'));
  R.url.replaceChildren(linkItem('Endereço do painel', state.adminUrl, 'link-card'));
}

// ------------------------------------------------------------------ troca obrigatória

/** @param {{onDone: () => void, onLogout: () => void}} hooks */
export function initPasswordView(hooks) {
  byId('pwForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    void (async () => {
      const err = byId('pwMsg');
      clearMessage(err);
      const btn = buttonById('pwBtn');
      btn.disabled = true;
      const ok = await submitPassword([inputById('pwCur'), inputById('pwNew'), inputById('pwNew2')], err);
      btn.disabled = false;
      if (ok) hooks.onDone();
    })();
  });
  byId('pwOut').addEventListener('click', hooks.onLogout);
}

/** Esvazia os campos e a mensagem da tela de troca obrigatória. */
export function resetPasswordView() {
  for (const id of ['pwCur', 'pwNew', 'pwNew2']) inputById(id).value = '';
  clearMessage(byId('pwMsg'));
}
