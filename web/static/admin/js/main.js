// @ts-check
// Ponto de entrada do painel LiveStats: sessão, login/logout e atualização
// periódica. Os dados do servidor só chegam à tela via textContent.

import { api, setCsrfToken, clearCsrfToken, errorText } from './api.js';
import {
  byId, inputById, buttonById, setVisible, showMessage, clearMessage,
} from './dom.js';
import {
  initSlots, resetSlots, loadSlots, renderSlotEndpoints, setSlotHooks,
} from './slots.js';
import { renderEndpointList, subscribersEndpoints, healthEndpoints, clearEndpointTimers } from './endpoints.js';
import { initTabs } from './tabs.js';
import { loadFonts, resetFonts } from './fonts.js';
import { resetStatus } from './status.js';
import { saveCookies, resetCookies } from './cookies.js';
import {
  initSettings, applySettings, resetSettings, saveChannel, saveLooks, wireSettingsInputs,
} from './settings.js';
import { startHealth, resetHealth, refreshHealthNow } from './health.js';

const REFRESH_MS = 15000;

/** Avisos por origem (dados dos slots/configurações e status dos workers): aparece o primeiro não vazio. */
const notices = { data: '', status: '' };

/**
 * @param {'data' | 'status'} source
 * @param {string} text vazio = sem aviso
 */
function setNotice(source, text) {
  notices[source] = text;
  const shown = notices.data || notices.status;
  if (shown) showMessage(byId('panelNotice'), shown);
  else clearMessage(byId('panelNotice'));
}

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
  resetSettings();
  resetFonts();
  clearEndpointTimers();
  byId('epSubs').replaceChildren();
  byId('epHealth').replaceChildren();
  resetHealth();
  notices.data = '';
  notices.status = '';
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

/** Cada atualização leva um número; uma resposta mais antiga que a última já aplicada é descartada. */
let refreshSeq = 0;
let appliedSeq = 0;

async function refreshAll() {
  const gen = generation;
  const seq = ++refreshSeq;
  try {
    const [slots, settings] = await Promise.all([loadSlots(), api('GET', '/api/admin/settings')]);
    if (gen !== generation || seq < appliedSeq) return;
    appliedSeq = seq;
    if (handleAuthLoss(slots) || handleAuthLoss(settings)) return;
    if (settings.ok && settings.data) applySettings(settings.data);
    if (!slots.ok || !settings.ok) {
      const bad = !slots.ok ? slots : settings;
      setNotice('data', `Falha ao atualizar os dados: ${errorText(bad, `HTTP ${bad.status}`)}`);
    } else {
      setNotice('data', '');
    }
  } catch (e) {
    if (gen !== generation || seq < appliedSeq) return;
    appliedSeq = seq;
    setNotice('data', 'Sem conexão com o servidor. Tentando de novo em instantes.');
  }
}

/**
 * @param {{username: string, csrf_token: string, public_base_url?: string | null}} session
 */
function enterPanel(session) {
  clearSession();
  const gen = generation;
  setCsrfToken(session.csrf_token);
  publicBase = typeof session.public_base_url === 'string' && session.public_base_url
    ? session.public_base_url.replace(/\/+$/, '')
    : window.location.origin;
  byId('userName').textContent = session.username;
  renderSlotEndpoints(publicBase);
  renderEndpointList(byId('epSubs'), publicBase, subscribersEndpoints());
  renderEndpointList(byId('epHealth'), publicBase, healthEndpoints());
  clearMessage(byId('loginMsg'));
  showView('panel');
  // A lista de fontes vem primeiro: os dropdowns precisam das opções antes de receber os valores salvos.
  void loadFonts().then((fonts) => {
    if (gen !== generation || handleAuthLoss(fonts)) return;
    void refreshAll();
  }).catch(() => {
    if (gen !== generation) return;
    void refreshAll();
  });
  startHealth({
    onUnauthorized,
    onStatus: (resp) => {
      if (resp === null) setNotice('status', 'Sem conexão com o servidor. Tentando de novo em instantes.');
      else setNotice('status', resp.ok ? '' : `Falha ao atualizar os dados: ${errorText(resp, `HTTP ${resp.status}`)}`);
    },
  });
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
  initTabs();
  initSlots();
  initSettings();
  wireSettingsInputs();
  setSlotHooks({ onUnauthorized, reloadAll: () => void refreshAll() });
  byId('channelForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    void saveChannel({ onUnauthorized });
  });
  byId('lookBtn').addEventListener('click', () => {
    void saveLooks({ onUnauthorized, reload: () => void refreshAll() });
  });
  byId('loginForm').addEventListener('submit', (ev) => void onLoginSubmit(ev));
  byId('logoutBtn').addEventListener('click', () => void onLogout());
  byId('cookiesBtn').addEventListener('click', () => {
    void saveCookies({ onUnauthorized, onSaved: () => { void refreshAll(); refreshHealthNow(); } });
  });
}

wire();
void boot();
