// @ts-check
// Ponto de entrada do painel LiveStats: sessão, login/logout e atualização
// periódica. Os dados do servidor só chegam à tela via textContent.

import { api, setCsrfToken, clearCsrfToken, errorText } from './api.js';
import {
  byId, inputById, buttonById, setVisible, showMessage, clearMessage,
} from './dom.js';
import {
  initSlots, resetSlots, loadSlots, saveSlots, renderEndpoints,
} from './slots.js';
import { loadStatus, resetStatus } from './status.js';
import { saveCookies, resetCookies } from './cookies.js';

const REFRESH_MS = 15000;

/** Incrementa a cada entrada/saída do painel; respostas atrasadas de uma
 *  sessão anterior são descartadas. */
let generation = 0;
/** @type {number | null} */
let pollTimer = null;
/** @type {string | null} */
let publicBase = null;

/**
 * @param {'loading' | 'login' | 'panel'} view
 */
function showView(view) {
  setVisible(byId('loadingView'), view === 'loading');
  setVisible(byId('loginView'), view === 'login');
  setVisible(byId('panelView'), view === 'panel');
  setVisible(byId('userBox'), view === 'panel');
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/**
 * Descarta TODO dado de sessão: token, campos, estatísticas, status.
 */
function clearSession() {
  generation += 1;
  stopPolling();
  clearCsrfToken();
  publicBase = null;
  resetSlots();
  resetStatus();
  resetCookies();
  clearMessage(byId('panelNotice'));
  byId('userName').textContent = '';
}

/**
 * @param {string} [message] aviso (texto puro) a exibir sobre o formulário
 */
function goToLogin(message) {
  clearSession();
  inputById('loginPass').value = '';
  if (message) {
    showMessage(byId('loginMsg'), message);
  } else {
    clearMessage(byId('loginMsg'));
  }
  showView('login');
}

function onUnauthorized() {
  goToLogin('Sessão expirada. Entre novamente.');
}

/**
 * @param {import('./api.js').ApiResponse} resp
 * @returns {boolean} true se a resposta foi 401 (já tratada)
 */
function handleAuthLoss(resp) {
  if (resp.status === 401) {
    onUnauthorized();
    return true;
  }
  return false;
}

async function refreshAll() {
  const gen = generation;
  const notice = byId('panelNotice');
  try {
    const [slots, status] = await Promise.all([loadSlots(), loadStatus()]);
    if (gen !== generation) return;
    if (handleAuthLoss(slots) || handleAuthLoss(status)) return;
    if (!slots.ok || !status.ok) {
      const bad = !slots.ok ? slots : status;
      showMessage(notice, `Falha ao atualizar os dados: ${errorText(bad, `HTTP ${bad.status}`)}`);
    } else {
      clearMessage(notice);
    }
  } catch (e) {
    if (gen !== generation) return;
    showMessage(notice, 'Sem conexão com o servidor. Tentando de novo em instantes.');
  }
}

/**
 * @param {{username: string, csrf_token: string, public_base_url?: string | null}} session
 */
function enterPanel(session) {
  clearSession();
  setCsrfToken(session.csrf_token);
  publicBase = typeof session.public_base_url === 'string' && session.public_base_url
    ? session.public_base_url.replace(/\/+$/, '')
    : window.location.origin;
  byId('userName').textContent = session.username;
  renderEndpoints(publicBase);
  clearMessage(byId('loginMsg'));
  showView('panel');
  void refreshAll();
  pollTimer = window.setInterval(() => void refreshAll(), REFRESH_MS);
}

async function boot() {
  try {
    const resp = await api('GET', '/api/admin/session');
    if (resp.ok && resp.data) {
      enterPanel(resp.data);
      return;
    }
    if (resp.status === 401) {
      goToLogin();
      return;
    }
    goToLogin(`Não foi possível verificar a sessão: ${errorText(resp, `HTTP ${resp.status}`)}`);
  } catch (e) {
    goToLogin('Não foi possível contatar o servidor.');
  }
}

/**
 * @param {Event} ev
 */
async function onLoginSubmit(ev) {
  ev.preventDefault();
  const user = inputById('loginUser');
  const pass = inputById('loginPass');
  const btn = buttonById('loginBtn');
  const msg = byId('loginMsg');
  clearMessage(msg);
  btn.disabled = true;
  try {
    const resp = await api('POST', '/api/admin/login', { username: user.value, password: pass.value });
    pass.value = '';
    if (resp.ok && resp.data) {
      // A resposta do login não traz public_base_url; o token CSRF é o mesmo.
      setCsrfToken(resp.data.csrf_token);
      const sess = await api('GET', '/api/admin/session');
      enterPanel(sess.ok && sess.data ? sess.data : resp.data);
      return;
    }
    if (resp.status === 401) {
      showMessage(msg, 'Credenciais inválidas');
    } else if (resp.status === 429) {
      const espera = resp.retryAfter ? ` Aguarde ${resp.retryAfter} s.` : '';
      showMessage(msg, `Muitas tentativas. Tente novamente em instantes.${espera}`);
    } else {
      showMessage(msg, errorText(resp, `Erro ao entrar (HTTP ${resp.status})`));
    }
  } catch (e) {
    pass.value = '';
    showMessage(msg, 'Não foi possível contatar o servidor.');
  } finally {
    btn.disabled = false;
  }
}

async function onLogout() {
  const gen = generation;
  try {
    await api('POST', '/api/admin/logout');
  } catch (e) {
    // Sem rede: o servidor não confirmou, mas a tela não pode manter a sessão aberta.
    console.warn('Falha ao chamar o logout:', e);
  }
  if (gen !== generation) return;
  goToLogin();
}

function wire() {
  initSlots();
  byId('loginForm').addEventListener('submit', (ev) => void onLoginSubmit(ev));
  byId('logoutBtn').addEventListener('click', () => void onLogout());
  byId('saveBtn').addEventListener('click', () => {
    void saveSlots({ onUnauthorized, reloadAll: () => void refreshAll() });
  });
  byId('cookiesBtn').addEventListener('click', () => {
    void saveCookies({ onUnauthorized, onSaved: () => void refreshAll() });
  });
}

wire();
void boot();
