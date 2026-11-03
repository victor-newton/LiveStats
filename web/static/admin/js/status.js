// @ts-check
// Estado dos workers: aba "Inscritos" (leitura atual, cookies, worker de inscritos e autenticação)
// e aba "Lives" (worker de lives).

import { api } from './api.js';
import { byId, setVisible, showMessage, clearMessage } from './dom.js';

/** @type {Record<string, string>} */
const STATE_LABELS = {
  starting: 'iniciando',
  ok: 'ok',
  degraded: 'degradado',
  error: 'erro',
  stopped: 'parado',
  unknown: 'desconhecido',
};

/** Worker, nome e lista (uma por aba) onde aparece. */
const WORKER_LISTS = [
  ['lives', 'Lives', 'workerListLives'],
  ['inscritos', 'Inscritos', 'workerList'],
];

/**
 * @param {Record<string, any>} worker
 * @returns {string}
 */
function workerDetail(worker) {
  const partes = [];
  if (worker.last_heartbeat_at) partes.push(`último sinal: ${worker.last_heartbeat_at}`);
  if (typeof worker.consecutive_failures === 'number' && worker.consecutive_failures > 0) {
    partes.push(`falhas seguidas: ${worker.consecutive_failures}`);
  }
  if (worker.last_error_message) partes.push(String(worker.last_error_message));
  else if (worker.last_error_code) partes.push(String(worker.last_error_code));
  return partes.join(' · ');
}

/**
 * Aviso destacado para problemas de autenticação do worker de inscritos.
 * @param {string | undefined} authState
 * @returns {string}
 */
function authMessage(authState) {
  switch (authState) {
    case 'login_required':
      return 'Atenção: cookies expirados. O YouTube Studio pediu login novamente; cole cookies novos no cartão Cookies, abaixo.';
    case 'decrypt_failed':
      return 'Atenção: o worker não conseguiu decifrar os cookies salvos. Cole os cookies novamente no cartão Cookies, abaixo.';
    case 'no_cookies':
      return 'Atenção: nenhum cookie foi aplicado ainda; o scraper não está autenticado. Cole os cookies no cartão Cookies, abaixo.';
    default:
      return '';
  }
}

/**
 * @param {any} ws corpo de GET /api/admin/workers/status
 */
export function renderStatus(ws) {
  const subs = ws && ws.subscribers ? ws.subscribers : null;
  byId('subsCurrent').textContent = subs && subs.inscritos ? String(subs.inscritos) : '—';
  byId('subsLast').textContent = subs && subs.atualizado_em ? String(subs.atualizado_em) : '—';

  const cookies = ws && ws.cookies ? ws.cookies : { configured: false, count: 0 };
  const badge = byId('cookieBadge');
  const loaded = !!cookies.configured && cookies.count > 0;
  badge.textContent = loaded
    ? `✅ ${cookies.count} cookies carregados`
    : '❌ Nenhum cookie — scraper não autenticado';
  badge.className = `status-badge ${loaded ? 'ok' : 'bad'}`;

  const workers = ws && ws.workers ? ws.workers : {};
  for (const [, , listId] of WORKER_LISTS) byId(listId).replaceChildren();
  for (const [key, nome, listId] of WORKER_LISTS) {
    const list = byId(listId);
    const w = workers[key];
    if (!w) continue;
    const li = document.createElement('li');
    li.className = 'worker';
    li.dataset.worker = key;
    const name = document.createElement('span');
    name.className = 'worker-name';
    name.textContent = nome;
    const state = document.createElement('span');
    state.className = 'worker-state';
    state.dataset.state = String(w.state);
    state.textContent = STATE_LABELS[w.state] || String(w.state);
    const detail = document.createElement('span');
    detail.className = 'worker-detail';
    detail.textContent = workerDetail(w);
    li.append(name, state, detail);
    list.appendChild(li);
  }

  const alert = byId('authAlert');
  const authState = workers.inscritos ? workers.inscritos.auth_state : undefined;
  const text = authMessage(authState);
  if (text) {
    showMessage(alert, text);
    alert.dataset.authState = String(authState);
  } else {
    clearMessage(alert);
    delete alert.dataset.authState;
  }
}

/**
 * Esvazia a seção (sessão encerrada).
 */
export function resetStatus() {
  byId('subsCurrent').textContent = '—';
  byId('subsLast').textContent = '—';
  const badge = byId('cookieBadge');
  badge.textContent = '❌ Nenhum cookie — scraper não autenticado';
  badge.className = 'status-badge bad';
  byId('workerList').replaceChildren();
  byId('workerListLives').replaceChildren();
  clearMessage(byId('authAlert'));
  setVisible(byId('authAlert'), false);
}

/**
 * @returns {Promise<import('./api.js').ApiResponse>}
 */
export async function loadStatus() {
  const resp = await api('GET', '/api/admin/workers/status');
  if (resp.ok && resp.data) {
    renderStatus(resp.data);
  }
  return resp;
}
