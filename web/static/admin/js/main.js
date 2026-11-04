// @ts-check
// Ponto de entrada do painel LiveStats: sessão, login/logout, navegação e atualização periódica.
// Ritmo (D46): números atuais a cada 2 s, numa só requisição por tela; histórico e demais dados a cada 10 s;
// Saúde a cada 5 s. As requisições não se acumulam (setTimeout encadeado: a próxima só sai depois da resposta)
// e param com a aba escondida. Os dados do servidor só chegam à tela via textContent.

import { api, setCsrfToken, clearCsrfToken, errorText } from './api.js';
import {
  byId, inputById, buttonById, setVisible, showMessage, clearMessage,
} from './dom.js';
import { loadFonts, resetFonts } from './fonts.js';
import { setNetHooks } from './net.js';
import {
  state, resetState, setById,
} from './store.js';
import {
  initNav, setNavHandler, renderNav, renderChip, showScreen, resetNav,
} from './nav.js';
import { initDialogs, openNewSet, resetDialogs } from './dialogs.js';
import { clearToast } from './ui.js';
import { syncClock } from './util.js';
import * as visao from './visao.js';
import * as conjunto from './conjunto.js';
import * as inscritos from './inscritos.js';
import * as saude from './saude.js';

const FAST_MS = 2000;
const HEALTH_MS = 5000;
const SLOW_MS = 10000;

const views = { visao, conjunto, inscritos, saude };

/** Avisos de falha ao atualizar: aparece o primeiro não vazio. */
let notice = '';

/** Início (carimbo) da passada que gerou o aviso atual: resposta de uma passada MAIS ANTIGA não o apaga nem o troca. */
let noticeStamp = 0;

/**
 * @param {string} text vazio = sem aviso
 * @param {number} [st] carimbo da passada (sem ele, vale sempre: erros de conexão, saída da sessão)
 */
function setNotice(text, st) {
  if (st !== undefined) {
    if (st < noticeStamp) return;
    noticeStamp = st;
  }
  notice = text;
  if (text) showMessage(byId('panelNotice'), text);
  else clearMessage(byId('panelNotice'));
}

/** Incrementa a cada entrada/saída do painel; respostas atrasadas de uma sessão anterior são descartadas. */
let generation = 0;
/** @type {number | null} */
let fastTimer = null;
/** @type {number | null} */
let slowTimer = null;
let stamp = 0;
let setsStamp = 0;
let statusStamp = 0;

/** @param {'loading' | 'login' | 'panel'} view */
function showView(view) {
  setVisible(byId('loadingView'), view === 'loading');
  setVisible(byId('loginView'), view === 'login');
  setVisible(byId('panelView'), view === 'panel');
}

function stopTimers() {
  if (fastTimer !== null) clearTimeout(fastTimer);
  if (slowTimer !== null) clearTimeout(slowTimer);
  fastTimer = null;
  slowTimer = null;
}

/** Descarta TODO dado de sessão: token, campos, estatísticas, status. */
function clearSession() {
  generation += 1;
  stopTimers();
  clearCsrfToken();
  resetState();
  noticeStamp = 0;
  visao.resetVisao();
  conjunto.resetConjunto();
  inscritos.resetInscritos();
  saude.resetSaude();
  resetNav();
  resetDialogs();
  resetFonts();
  clearToast();
  setNotice('');
  clearMessage(byId('authAlert'));
  byId('userName').textContent = '';
  for (const scr of document.querySelectorAll('.screen')) /** @type {HTMLElement} */ (scr).hidden = true;
}

/** @param {string} [message] aviso (texto puro) a exibir sobre o formulário */
function goToLogin(message) {
  clearSession();
  inputById('loginPass').value = '';
  if (message) showMessage(byId('loginMsg'), message);
  else clearMessage(byId('loginMsg'));
  showView('login');
}

function onUnauthorized() {
  goToLogin('Sessão expirada. Entre novamente.');
}

/**
 * @param {import('./api.js').ApiResponse[]} resps
 * @returns {boolean} true se alguma resposta foi 401 (já tratada)
 */
function authLost(resps) {
  if (resps.some((r) => r && r.status === 401)) {
    onUnauthorized();
    return true;
  }
  return false;
}

// ------------------------------------------------------------------ atualização

/** Aviso de cookies do Studio (expirados, ilegíveis ou ausentes): vale em QUALQUER tela, para o usuário saber que precisa trocá-los. */
function renderAuthAlert() {
  const w = state.status && state.status.workers ? state.status.workers.inscritos : null;
  const msg = inscritos.authMessage(w ? w.auth_state : undefined);
  const el = byId('authAlert');
  if (msg) { showMessage(el, msg); el.dataset.authState = String(w && w.auth_state); } else { clearMessage(el); delete el.dataset.authState; }
}

/** Redesenha a tela atual, a barra lateral e a faixa de saúde (só troca valores). */
function renderAll() {
  renderAuthAlert();
  views[state.route.screen].render(false);
  renderNav();
  renderChip();
}

/**
 * Mostra uma tela (com entrada suave) e busca os dados dela já.
 * @param {import('./store.js').Route} route
 */
function navigate(route) {
  let r = route;
  if (r.screen === 'conjunto' && !setById(r.setId)) r = { screen: 'visao', setId: null, focus: null };
  conjunto.resetFocus();
  showScreen(r);
  views[r.screen].render(true);
  renderNav();
  renderChip();
  void refreshAll();
}

/** @param {import('./api.js').ApiResponse} r @param {number} st */
function applySets(r, st) {
  if (!r.ok || !r.data || !Array.isArray(r.data.sets) || st < setsStamp) return;
  setsStamp = st;
  state.sets = r.data.sets;
  if (r.data.limits) state.limits = r.data.limits;
}

/** @param {import('./api.js').ApiResponse} r @param {number} st */
function applyStatus(r, st) {
  if (!r.ok || !r.data || st < statusStamp) return;
  statusStamp = st;
  state.status = r.data;
  syncClock(r.data.server_time);
}

/** Texto do primeiro erro (ou vazio) das respostas de dados. @param {import('./api.js').ApiResponse[]} resps */
function firstProblem(resps) {
  const bad = resps.find((r) => !r.ok);
  return bad ? `Falha ao atualizar os dados: ${errorText(bad, `HTTP ${bad.status}`)}` : '';
}

/** A tela do conjunto sumiu (apagado em outro lugar): volta à Visão geral. */
function ensureRoute() {
  if (state.route.screen === 'conjunto' && !setById(state.route.setId)) {
    navigate({ screen: 'visao', setId: null, focus: null });
    return false;
  }
  return true;
}

/** Cada passada de atualização completa (10 s e ao trocar de tela). Uma de cada vez. */
let slowBusy = /** @type {Promise<void> | null} */ (null);
let again = false;

function refreshAll() {
  if (slowBusy) {
    again = true;
    return slowBusy;
  }
  slowBusy = (async () => {
    do {
      again = false;
      await slowOnce();
    } while (again);
  })().finally(() => { slowBusy = null; });
  return slowBusy;
}

async function slowOnce() {
  const gen = generation;
  const route = state.route;
  const st = ++stamp;
  /** @type {import('./api.js').ApiResponse[]} */
  let resps;
  try {
    const t0 = performance.now();
    const [sets, settings, status] = await Promise.all([
      api('GET', '/api/admin/sets'),
      api('GET', '/api/admin/settings'),
      api('GET', '/api/admin/workers/status'),
    ]);
    if (gen !== generation) return;
    resps = [sets, settings, status];
    if (authLost(resps)) return;
    applySets(sets, st);
    applyStatus(status, st);
    if (settings.ok && settings.data) state.settings = settings.data;
    state.apiOk = true;
    if (route.screen === 'saude') state.apiMs = Math.round(performance.now() - t0);
    setNotice(firstProblem(resps), st);
    if (route === state.route && ensureRoute()) renderAll(); // os números já na tela; o resto (histórico etc.) vem a seguir
    // dados próprios da tela (histórico do gráfico, minigráficos, saúde...): precisam dos conjuntos já carregados
    const extra = views[route.screen].fetchExtra ? await views[route.screen].fetchExtra().catch(() => []) : [];
    if (gen !== generation) return;
    if (authLost(extra)) return;
  } catch (e) {
    if (gen !== generation) return;
    state.apiOk = false;
    setNotice('Sem conexão com o servidor. Tentando de novo em instantes.');
    renderChip();
    return;
  }
  if (route !== state.route) return; // trocou de tela enquanto carregava: a tela nova tem a própria passada
  if (ensureRoute()) renderAll();
}

/** Passada rápida: UMA requisição por tela (D46). */
async function fastOnce() {
  const gen = generation;
  const route = state.route;
  const st = ++stamp;
  /** @type {import('./api.js').ApiResponse[]} */
  let resps = [];
  try {
    if (route.screen === 'visao') {
      const r = await api('GET', '/api/admin/sets');
      resps = [r];
      if (gen === generation) applySets(r, st);
    } else if (route.screen === 'conjunto') {
      const r = await api('GET', `/api/admin/sets/${route.setId}`);
      resps = [r];
      if (gen === generation && r.ok && r.data) {
        const one = r.data.set && typeof r.data.set === 'object' ? r.data.set : r.data;
        const i = state.sets.findIndex((x) => x.id === one.id);
        if (i >= 0 && Array.isArray(one.lives)) state.sets[i] = one;
      }
    } else if (route.screen === 'inscritos') {
      const r = await api('GET', '/api/public/subscribers');
      if (gen === generation && r.ok && r.data) state.subscribers = r.data;
      return finishFast(gen, route, [], st);
    } else {
      const [status, health] = await Promise.all([
        api('GET', '/api/admin/workers/status'),
        saude.fetchExtra(),
      ]);
      resps = [status, ...health];
      if (gen === generation) applyStatus(status, st);
    }
  } catch (e) {
    if (gen !== generation) return;
    state.apiOk = false;
    setNotice('Sem conexão com o servidor. Tentando de novo em instantes.');
    renderChip();
    return;
  }
  if (route.screen === 'conjunto' && resps[0] && resps[0].status === 404) {
    void refreshAll(); // o conjunto pode ter sido apagado: a passada completa decide
    return;
  }
  finishFast(gen, route, resps, st);
}

/**
 * @param {number} gen
 * @param {import('./store.js').Route} route
 * @param {import('./api.js').ApiResponse[]} resps
 * @param {number} st
 */
function finishFast(gen, route, resps, st) {
  if (gen !== generation) return;
  if (authLost(resps)) return;
  state.apiOk = true;
  setNotice(firstProblem(resps), st);
  if (route !== state.route) return;
  renderAll();
}

/**
 * Agenda a próxima passada só depois de a anterior terminar (nada se acumula).
 * @param {'fast' | 'slow'} kind
 */
function schedule(kind) {
  const gen = generation;
  const wait = kind === 'slow' ? SLOW_MS : (state.route.screen === 'saude' ? HEALTH_MS : FAST_MS);
  const timer = window.setTimeout(async () => {
    if (gen !== generation) return;
    if (!document.hidden) {
      try {
        if (kind === 'slow') await refreshAll(); else await fastOnce();
      } catch (e) {
        console.warn('Falha na atualização:', e);
      }
    }
    if (gen === generation && !document.hidden) schedule(kind);
  }, wait);
  if (kind === 'fast') fastTimer = timer; else slowTimer = timer;
}

function startLoops() {
  stopTimers();
  schedule('fast');
  schedule('slow');
}

document.addEventListener('visibilitychange', () => {
  if (!byId('panelView').hidden) {
    if (document.hidden) {
      stopTimers();
    } else {
      void refreshAll();
      startLoops();
    }
  }
});

// ------------------------------------------------------------------ sessão

/** @param {{username: string, csrf_token: string, public_base_url?: string | null}} session */
function enterPanel(session) {
  clearSession();
  const gen = generation;
  setCsrfToken(session.csrf_token);
  state.publicBase = typeof session.public_base_url === 'string' && session.public_base_url
    ? session.public_base_url.replace(/\/+$/, '')
    : window.location.origin;
  byId('userName').textContent = session.username;
  clearMessage(byId('loginMsg'));
  showView('panel');
  // A lista de fontes vem primeiro: os dropdowns precisam das opções antes de receber os valores salvos.
  const start = () => {
    if (gen !== generation) return;
    navigate({ screen: 'visao', setId: null, focus: null });
    startLoops();
  };
  void loadFonts().then((fonts) => {
    if (gen !== generation || authLost([fonts])) return;
    start();
  }).catch(() => start());
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

/** @param {Event} ev */
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
  setNetHooks({ onUnauthorized, refresh: () => refreshAll() });
  conjunto.initConjunto();
  visao.initVisao();
  inscritos.initInscritos();
  saude.initSaude();
  setNavHandler(navigate);
  initNav();
  initDialogs(navigate);
  visao.setOpenHandler((id) => navigate({ screen: 'conjunto', setId: id, focus: null }));
  byId('btnNovoConjunto').addEventListener('click', () => {
    byId('side').classList.remove('open');
    openNewSet();
  });
  byId('loginForm').addEventListener('submit', (ev) => void onLoginSubmit(ev));
  byId('logoutBtn').addEventListener('click', () => void onLogout());
}

wire();
void boot();
