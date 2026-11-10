// @ts-check
// Pop-up de confirmação por digitação do nome (#dlgDanger), único para toda ação perigosa: limpar dados de um
// usuário, apagar conta e limpar os PRÓPRIOS dados (`clearMyData`, usada no cartão do mestre e na aba Conta).
// O botão de confirmar só habilita quando o texto digitado é exatamente o nome esperado.

import { byId, inputById, buttonById, clearMessage } from './dom.js';
import { write, requestRefresh } from './net.js';
import { state } from './store.js';
import { toast } from './ui.js';

/** @type {null | ((confirm: string) => Promise<boolean>)} */
let run = null;
let wired = false;

function sync() {
  /** @type {HTMLButtonElement} */ (byId('dangerGo')).disabled = inputById('dangerConfirm').value !== (byId('dangerConfirm').dataset.expected || '\u0000');
}

async function submit() {
  const input = inputById('dangerConfirm');
  const expected = input.dataset.expected || '';
  if (!run || input.value !== expected) return;
  const btn = buttonById('dangerGo');
  btn.disabled = true;
  const ok = await run(input.value);
  if (ok) {
    /** @type {HTMLDialogElement} */ (byId('dlgDanger')).close();
    input.value = '';
    run = null;
  } else sync();
}

/** Liga o formulário do pop-up (uma vez). */
export function initDanger() {
  if (wired) return;
  wired = true;
  inputById('dangerConfirm').addEventListener('input', sync);
  byId('dangerForm').addEventListener('submit', (ev) => { ev.preventDefault(); void submit(); });
}

/** Limpa o pop-up (sessão encerrada). */
export function resetDanger() {
  inputById('dangerConfirm').value = '';
  run = null;
}

/**
 * Abre a confirmação por digitação do nome.
 * @param {{title: string, text: string, expected: string, go: string, run: (confirm: string) => Promise<boolean>}} o
 */
export function openDanger(o) {
  byId('dlgDangerT').textContent = o.title;
  byId('dangerText').textContent = o.text;
  byId('dangerLabel').textContent = `Digite ${o.expected} para confirmar`;
  byId('dangerGo').textContent = o.go;
  const input = inputById('dangerConfirm');
  input.value = '';
  input.dataset.expected = o.expected;
  run = o.run;
  clearMessage(byId('dangerErr'));
  sync();
  /** @type {HTMLDialogElement} */ (byId('dlgDanger')).showModal();
  input.focus();
}

/**
 * "Limpar meus dados": apaga conjuntos, lives, leituras e contador da própria conta (a conta e a senha ficam).
 * Confirma digitando o nome de usuário; chama `POST /api/admin/account/clear-data`.
 */
export function clearMyData() {
  const me = state.user ? String(state.user.username) : '';
  if (!me) return;
  openDanger({
    title: 'Limpar meus dados',
    text: 'Apaga todos os seus conjuntos, lives, leituras e o contador. A conta e a senha continuam. Não dá para desfazer.',
    expected: me,
    go: 'Limpar meus dados',
    run: async (confirm) => {
      const resp = await write('POST', '/api/admin/account/clear-data', { confirm }, byId('dangerErr'));
      if (resp) { toast('Seus dados foram apagados'); void requestRefresh(); }
      return !!resp;
    },
  });
}
