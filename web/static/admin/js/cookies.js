// @ts-check
// Formulário de cookies do EditThisCookie. O conteúdo colado é segredo: só
// existe no textarea e na requisição; nunca é guardado, logado ou ecoado.

import { api, errorText } from './api.js';
import { byId, buttonById, textareaById, clearMessage } from './dom.js';

/**
 * @param {boolean} ok
 * @param {string} text
 */
function mostrarMsg(ok, text) {
  const el = byId('cookiesMsg');
  el.textContent = text;
  el.className = `msg ${ok ? 'ok' : 'err'}`;
  el.hidden = false;
}

export function resetCookies() {
  textareaById('cookiesInput').value = '';
  clearMessage(byId('cookiesMsg'));
}

/**
 * Valida no navegador e envia. Devolve true se o servidor aceitou.
 * @param {{onUnauthorized: () => void, onSaved: () => void}} hooks
 * @returns {Promise<boolean>}
 */
export async function saveCookies(hooks) {
  const area = textareaById('cookiesInput');
  const btn = buttonById('cookiesBtn');
  clearMessage(byId('cookiesMsg'));

  const raw = area.value.trim();
  if (!raw) {
    mostrarMsg(false, 'Cole o JSON dos cookies antes de salvar.');
    return false;
  }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('Deve ser um array JSON');
  } catch (e) {
    const detalhe = e instanceof Error ? e.message : 'formato inesperado';
    mostrarMsg(false, `JSON inválido: ${detalhe}`);
    return false;
  }

  btn.disabled = true;
  try {
    const resp = await api('POST', '/api/admin/cookies', parsed);
    if (resp.status === 401) {
      hooks.onUnauthorized();
      return false;
    }
    if (!resp.ok) {
      mostrarMsg(false, `❌ Erro: ${errorText(resp, 'desconhecido')}`);
      return false;
    }
    area.value = '';
    const message = resp.data && typeof resp.data.message === 'string' ? resp.data.message : 'Cookies salvos.';
    mostrarMsg(true, `✅ ${message}`);
    hooks.onSaved();
    return true;
  } catch (e) {
    mostrarMsg(false, '❌ Falha na requisição. Verifique a conexão e tente de novo.');
    return false;
  } finally {
    btn.disabled = false;
  }
}
