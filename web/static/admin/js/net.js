// @ts-check
// Escritas no /api/admin (com CSRF pelo api.js): trata 401 (volta ao login) e mostra o erro do servidor como TEXTO.

import { api, errorText } from './api.js';
import { showMessage, clearMessage } from './dom.js';

/** @type {() => void} */
let onUnauthorized = () => {};
/** @type {() => Promise<void>} */
let refresh = async () => {};

/**
 * @param {{onUnauthorized: () => void, refresh: () => Promise<void>}} hooks
 */
export function setNetHooks(hooks) {
  onUnauthorized = hooks.onUnauthorized;
  refresh = hooks.refresh;
}

/** Pede uma atualização imediata dos dados do painel. @returns {Promise<void>} */
export function requestRefresh() {
  return refresh();
}

/** Avisa que a sessão acabou (usado por quem faz leituras próprias). */
export function sessionLost() {
  onUnauthorized();
}

/**
 * Envia uma escrita; devolve a resposta só se deu certo (senão mostra o erro em `errEl`).
 * @param {string} method
 * @param {string} path
 * @param {unknown} body
 * @param {HTMLElement | null} errEl
 * @returns {Promise<import('./api.js').ApiResponse | null>}
 */
export async function write(method, path, body, errEl) {
  if (errEl) clearMessage(errEl);
  try {
    const resp = await api(method, path, body);
    if (resp.status === 401) {
      onUnauthorized();
      return null;
    }
    if (!resp.ok) {
      if (errEl) showMessage(errEl, errorText(resp, `Erro (HTTP ${resp.status})`));
      return null;
    }
    return resp;
  } catch (e) {
    if (errEl) showMessage(errEl, 'Falha na requisição. Verifique a conexão e tente de novo.');
    return null;
  }
}

const GOAL_MAX = 1_000_000_000;

/**
 * Meta de um campo de texto: `{goal}` (null = sem meta) ou texto de erro.
 * @param {string} raw
 * @returns {{goal: number | null} | string}
 */
export function readGoal(raw) {
  const t = raw.trim().replace(/[.\s]/g, '');
  if (t === '') return { goal: null };
  const goal = Number(t);
  if (!/^\d+$/.test(t) || !Number.isInteger(goal) || goal < 1 || goal > GOAL_MAX) {
    return 'A meta de likes deve ser um número inteiro de 1 a 1.000.000.000 (ou fique em branco para não ter meta).';
  }
  return { goal };
}
