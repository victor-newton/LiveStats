// @ts-check
// Ponto de entrada do painel LiveStats: sessão (usuário e papel, D48), login/logout, troca de senha obrigatória,
// navegação e atualização periódica.
// Ritmo (D46): números atuais a cada 2 s, numa só requisição por tela; histórico e demais dados a cada 10 s;
// Saúde e Mestre (só do mestre) a cada 5 s. As requisições não se acumulam (setTimeout encadeado: a próxima só sai
// depois da resposta) e param com a aba escondida. Os dados do servidor só chegam à tela via textContent.

import { api, setCsrfToken, clearCsrfToken, errorText } from './api.js';
import {
  byId, inputById, buttonById, setVisible, showMessage, clearMessage,
} from './dom.js';
import { loadFonts, resetFonts } from './fonts.js';
import { setNetHooks, isPasswordRequired } from './net.js';
import {
  state, resetState, setById, isMaster,
} from './store.js';
import {
  initNav, setNavHandler, setLogoutHandler, renderChip, renderAccount, showScreen, resetNav, syncNav, allowed,
} from './nav.js';
import { initDialogs, resetDialogs } from './dialogs.js';
import { clearToast, toast } from './ui.js';
import { syncClock } from './util.js';
import {
  routeFromLocation, pathOf, writePath, currentRoute, DEFAULT_PATH,
} from './rotas.js';
import { initGlow } from './glow.js';
import * as visao from './visao.js';
import * as conjuntos from './conjuntos.js';
import * as conjunto from './conjunto.js';
import * as inscritos from './inscritos.js';
import * as conta from './conta.js';
import * as saude from './saude.js';
import * as mestre from './mestre.js';

const FAST_MS = 2000;
const HEALTH_MS = 5000;
const SLOW_MS = 10000;

const views = {
  visao, conjuntos, conjunto, inscritos, conta, saude, mestre,
};

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

/** @param {'loading' | 'login' | 'password' | 'panel'} view */
function showView(view) {
  setVisible(byId('loadingView'), view === 'loading');
  setVisible(byId('loginView'), view === 'login');
  setVisible(byId('pwView'), view === 'password');
  setVisible(byId('panelView'), view === 'panel');
}

function stopTimers() {
  if (fastTimer !== null) clearTimeout(fastTimer);
  if (slowTimer !== null) clearTimeout(slowTimer);
  fastTimer = null;
  slowTimer = null;
}

/** Descarta TODO dado de sessão: token, campos, estatísticas, status, senhas. */
function clearSession() {
  generation += 1;
  stopTimers();
  clearCsrfToken();
  resetState();
  noticeStamp = 0;
  visao.resetVisao();
  conjuntos.resetConjuntos();
  conjunto.resetConjunto();
  inscritos.resetInscritos();
  conta.resetConta();
  conta.resetPasswordView();
  saude.resetSaude();
  mestre.resetMestre();
  resetNav();
  resetDialogs();
  resetFonts();
  clearToast();
  setNotice('');
  counterProblem = '';
  clearMessage(byId('authAlert'));
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

/** A conta precisa trocar a senha antes de qualquer outra coisa (D48): tela cheia, sem timers, sem dados. */
function showPasswordView() {
  generation += 1;
  stopTimers();
  resetDialogs();
  clearToast();
  setNotice('');
  if (state.user) state.user = { ...state.user, must_change_password: true };
  conta.resetPasswordView();
  showView('password');
  inputById('pwCur').focus();
}

/**
 * @param {import('./api.js').ApiResponse[]} resps
 * @returns {boolean} true se alguma resposta encerrou a sessão (401) ou exige a troca de senha (403): já tratada
 */
function authLost(resps) {
  if (resps.some((r) => r && r.status === 401)) {
    onUnauthorized();
    return true;
  }
  if (resps.some((r) => r && isPasswordRequired(r))) {
    showPasswordView();
    return true;
  }
  return false;
}

/** O servidor negou (404) uma rota do mestre: esconde Saúde e Mestre e volta ao Início, sem quebrar. */
function denyMaster() {
  if (state.masterDenied) return;
  state.masterDenied = true;
  renderAccount();
  toast('Esta área não está disponível para a sua conta.');
  if (state.route.screen === 'saude' || state.route.screen === 'mestre') navigate({ screen: 'visao', setId: null, focus: null }, 'replace');
}

/** @param {import('./api.js').ApiResponse[]} resps @returns {boolean} */
function masterRouteDenied(resps) {
  const r = state.route.screen;
  if ((r === 'saude' || r === 'mestre') && resps.some((x) => x && x.status === 404)) {
    denyMaster();
    return true;
  }
  return false;
}

// ------------------------------------------------------------------ atualização

/** Falha ao ler o contador (resposta de erro da passada completa): vale só onde o contador é o assunto. */
let counterProblem = '';

/**
 * Aviso do contador de inscritos (cookies expirados, ilegíveis ou ausentes; falha de leitura): aparece SÓ no Início,
 * dentro da seção do contador, e na aba Contador. Nas demais telas fica escondido: o assunto é outro.
 */
function renderAuthAlert() {
  const el = byId('authAlert');
  const rota = currentRoute();
  const here = rota === 'inicio' || rota === 'contador';
  // Trocando cookies/canal: o aviso antigo (cookies expirados etc.) ainda vale até a nova leitura; o cartão "Conectando" cuida disso.
  const auth = state.counter && !inscritos.isConnecting() ? inscritos.authMessage(state.counter.auth_state) : '';
  const msg = here ? (auth || counterProblem) : '';
  if (here) {
    const host = rota === 'inicio' ? byId('subsZoneBox') : byId('content');
    if (el.parentElement !== host) {
      if (rota === 'inicio') host.insertBefore(el, host.children[1] || null);
      else host.insertBefore(el, byId('scr-visao'));
    }
  }
  if (msg) {
    showMessage(el, msg);
    if (auth) el.dataset.authState = String(state.counter.auth_state); else delete el.dataset.authState;
  } else { clearMessage(el); delete el.dataset.authState; }
}

/** Redesenha a tela atual e o cabeçalho (só troca valores). */
function renderAll() {
  renderAuthAlert();
  views[state.route.screen].render(false);
  renderAccount();
  syncNav();
  renderChip();
}

/** Para onde voltar quando o conjunto aberto some (apagado em outro lugar). @param {import('./store.js').Route} r @returns {import('./store.js').Route} */
function fallbackRoute(r) {
  if (r.userId !== undefined && r.userId !== null && isMaster()) return { screen: 'mestre', setId: null, focus: null, seg: 'dados', userId: r.userId };
  return { screen: 'conjuntos', setId: null, focus: null };
}

/**
 * Mostra uma tela (com entrada suave) e busca os dados dela já. O endereço acompanha a tela: `push` empilha no
 * histórico, `replace` troca a entrada atual e `pop` (voltar/avançar) só corrige o endereço se a rota foi redirecionada.
 * @param {import('./store.js').Route} route
 * @param {'push' | 'replace' | 'pop'} [how]
 */
function navigate(route, how = 'push') {
  let r = route;
  if (!allowed(r.screen)) r = { screen: 'visao', setId: null, focus: null };
  if (r.screen === 'mestre') {
    r = { ...r, seg: r.seg || 'usuarios' };
    if (r.seg === 'dados' && (r.userId === undefined || r.userId === null)) r = { ...r, seg: 'usuarios' };
  }
  if (r.screen === 'conjunto' && !setById(r.setId)) {
    r = fallbackRoute(r);
    if (!r.userId) toast('Conjunto não encontrado.');
    if (how === 'push') how = 'replace';
  }
  writePath(pathOf(r), how === 'push' ? 'push' : 'replace');
  conjunto.resetFocus();
  showScreen(r);
  views[r.screen].render(true);
  renderAuthAlert();
  renderAccount();
  syncNav();
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

/** A tela do conjunto sumiu (apagado em outro lugar): volta à lista. */
function ensureRoute() {
  if (state.route.screen === 'conjunto' && !setById(state.route.setId)) {
    navigate(fallbackRoute(state.route), 'replace');
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
    const wantCounter = !!state.user && (state.user.has_counter !== false || state.user.role === 'master');
    const [sets, counter, status] = await Promise.all([
      api('GET', '/api/admin/sets'),
      wantCounter ? inscritos.fetchCounter() : Promise.resolve(null),
      isMaster() ? api('GET', '/api/admin/workers/status') : Promise.resolve(null),
    ]);
    if (gen !== generation) return;
    resps = [sets, counter, status].filter((x) => x !== null);
    if (!wantCounter) state.counterOff = true;
    if (authLost(resps)) return;
    if (status && status.status === 404) { denyMaster(); return; }
    applySets(sets, st);
    if (status) applyStatus(status, st);
    state.apiOk = true;
    if (route.screen === 'saude') state.apiMs = Math.round(performance.now() - t0);
    counterProblem = counter && !counter.ok && !(counter.status === 403 && counter.data && counter.data.code === 'counter_disabled')
      ? `Contador: ${errorText(counter, `HTTP ${counter.status}`)}` : '';
    setNotice(firstProblem(resps.filter((r) => r !== counter)), st);
    if (route === state.route && ensureRoute()) renderAll(); // os números já na tela; o resto (histórico etc.) vem a seguir
    // dados próprios da tela (histórico do gráfico, minigráficos, saúde...): precisam dos conjuntos já carregados
    const extra = views[route.screen].fetchExtra ? await views[route.screen].fetchExtra().catch(() => []) : [];
    if (gen !== generation) return;
    if (authLost(extra)) return;
    if (masterRouteDenied(extra)) return;
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
    if (route.screen === 'visao' || route.screen === 'conjuntos') {
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
        else if (state.foreign.has(one.id) && Array.isArray(one.lives)) state.foreign.set(one.id, one);
      }
    } else if (route.screen === 'inscritos') {
      if (state.counterOff) return;
      const r = await inscritos.fetchCounter();
      resps = [r];
    } else if (route.screen === 'conta') {
      return;
    } else if (route.screen === 'mestre') {
      resps = await mestre.fetchExtra();
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
  if (gen !== generation) return;
  if (masterRouteDenied(resps)) return;
  if (route.screen === 'conjunto' && resps[0] && resps[0].status === 404) {
    state.foreign.delete(/** @type {number} */ (route.setId));
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
  const scr = state.route.screen;
  const wait = kind === 'slow' ? SLOW_MS : (scr === 'saude' || scr === 'mestre' ? HEALTH_MS : FAST_MS);
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

/**
 * Usuário da sessão (D48). Sem `user` na resposta (servidor antigo), a interface assume o papel mais restrito.
 * @param {any} session
 * @returns {any}
 */
function userOf(session) {
  const u = session.user && typeof session.user === 'object' ? session.user : null;
  return u || {
    id: 0, username: session.username || '', display_name: '', role: 'user', must_change_password: false, has_counter: true, max_sets: 0, max_lives: 0,
  };
}

/** @param {any} session resposta de GET /api/admin/session */
function enterPanel(session) {
  clearSession();
  const gen = generation;
  setCsrfToken(session.csrf_token);
  state.user = userOf(session);
  state.publicBase = typeof session.public_base_url === 'string' && session.public_base_url
    ? session.public_base_url.replace(/\/+$/, '')
    : window.location.origin;
  state.adminUrl = typeof session.public_admin_url === 'string' && session.public_admin_url
    ? session.public_admin_url.replace(/\/+$/, '')
    : window.location.origin;
  clearMessage(byId('loginMsg'));
  if (state.user.must_change_password) {
    showPasswordView();
    return;
  }
  renderAccount();
  showView('panel');
  // A lista de fontes vem primeiro: os dropdowns precisam das opções antes de receber os valores salvos.
  const start = async () => {
    if (gen !== generation) return;
    const first = routeFromLocation();
    if (first.screen === 'conjunto') {
      // Link direto para um conjunto: a lista precisa estar carregada para validar o id.
      try {
        const sets = await api('GET', '/api/admin/sets');
        if (gen !== generation || authLost([sets])) return;
        applySets(sets, ++stamp);
      } catch (e) {
        if (gen !== generation) return;
      }
    }
    navigate(first, 'replace');
    startLoops();
  };
  void loadFonts().then((fonts) => {
    if (gen !== generation || authLost([fonts])) return;
    void start();
  }).catch(() => void start());
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
      // O token CSRF é o mesmo da sessão; a sessão completa (usuário, papel, endereços) vem de GET /session.
      setCsrfToken(resp.data.csrf_token);
      const sess = await api('GET', '/api/admin/session');
      enterPanel(sess.ok && sess.data ? sess.data : resp.data);
      return;
    }
    if (resp.status === 401) {
      showMessage(msg, 'Usuário ou senha inválidos.');
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
  writePath(DEFAULT_PATH, 'replace');
  goToLogin();
}

async function afterPasswordChange() {
  try {
    const sess = await api('GET', '/api/admin/session');
    if (sess.ok && sess.data) {
      enterPanel(sess.data);
      return;
    }
  } catch (e) {
    console.warn('Falha ao recarregar a sessão:', e);
  }
  goToLogin('Senha alterada. Entre novamente.');
}

function wire() {
  setNetHooks({ onUnauthorized, refresh: () => refreshAll(), onPasswordRequired: showPasswordView });
  initGlow();
  conjunto.initConjunto();
  conjunto.setNavigator(navigate);
  visao.initVisao();
  conjuntos.initConjuntos();
  inscritos.initInscritos();
  conta.initConta();
  conta.wireConta({ onLogout: () => void onLogout() });
  conta.onProfileChange(() => renderAccount());
  conta.initPasswordView({ onDone: () => void afterPasswordChange(), onLogout: () => void onLogout() });
  saude.initSaude();
  mestre.initMestre();
  mestre.setMestreNavigator(navigate);
  setNavHandler(navigate);
  setLogoutHandler(() => void onLogout());
  initNav();
  initDialogs(navigate);
  const open = (/** @type {number} */ id) => navigate({ screen: 'conjunto', setId: id, focus: null, userId: null });
  visao.setOpenHandler(open);
  conjuntos.setOpenHandler(open);
  byId('loginForm').addEventListener('submit', (ev) => void onLoginSubmit(ev));
  window.addEventListener('popstate', () => {
    if (!state.user || byId('panelView').hidden) return;
    navigate(routeFromLocation(), 'pop');
  });
}

wire();
void boot();
