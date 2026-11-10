// @ts-check
// Navegação (D53): UMA barra de abas, sem barra lateral. No desktop ela fica no cabeçalho fixo (marca, abas, estado
// geral e menu da conta); até 820 px o CSS a leva para baixo da tela, como nos aplicativos do celular. Aqui moram
// também a troca de tela com entrada suave, o chip de estado e o menu da conta.

import { byId } from './dom.js';
import { h } from './h.js';
import {
  state, services, counterService, isMaster,
} from './store.js';
import { relTime, toEpoch } from './util.js';

/** @typedef {import('./store.js').Route} Route */

/** @type {(route: Route) => void} */
let onNavigate = () => {};
/** @type {() => void} */
let onLogout = () => {};
/** @type {number | null} */
let enterTimer = null;

const SCREENS = ['visao', 'conjuntos', 'conjunto', 'inscritos', 'conta', 'saude', 'mestre'];
const ENTER_MS = 1800;
/** Abas por papel (D53). A Conta do mestre abre pelo menu do cabeçalho. */
const TABS_USER = ['visao', 'conjuntos', 'inscritos', 'conta'];
const TABS_MASTER = ['visao', 'conjuntos', 'inscritos', 'saude', 'mestre'];

/** @param {(route: Route) => void} fn */
export function setNavHandler(fn) {
  onNavigate = fn;
}

/** @param {() => void} fn */
export function setLogoutHandler(fn) {
  onLogout = fn;
}

/** @returns {string[]} abas visíveis para o usuário da sessão */
export function visibleTabs() {
  return isMaster() ? TABS_MASTER : TABS_USER;
}

/** Telas que o usuário atual pode abrir (o servidor também nega as do mestre). @param {string} screen */
export function allowed(screen) {
  if (screen === 'saude' || screen === 'mestre') return isMaster();
  return true;
}

function closeAccount() {
  byId('accountMenu').hidden = true;
  byId('accountBtn').setAttribute('aria-expanded', 'false');
}

/** Liga as abas, o chip e o menu da conta. */
export function initNav() {
  for (const name of ['visao', 'conjuntos', 'inscritos', 'conta', 'saude', 'mestre']) {
    byId(`nav-${name}`).addEventListener('click', () => {
      /** @type {Route} */
      const r = { screen: /** @type {any} */ (name), setId: null, focus: null };
      if (name === 'mestre') r.seg = 'usuarios';
      onNavigate(r);
    });
  }
  byId('healthChip').addEventListener('click', () => onNavigate({ screen: isMaster() ? 'saude' : 'inscritos', setId: null, focus: null }));
  byId('accountBtn').addEventListener('click', () => {
    const menu = byId('accountMenu');
    const open = menu.hidden;
    menu.hidden = !open;
    byId('accountBtn').setAttribute('aria-expanded', String(open));
  });
  byId('menuConta').addEventListener('click', () => {
    closeAccount();
    onNavigate({ screen: 'conta', setId: null, focus: null });
  });
  byId('logoutBtn').addEventListener('click', () => {
    closeAccount();
    onLogout();
  });
  document.addEventListener('click', (ev) => {
    if (!byId('accountMenu').hidden && !(/** @type {HTMLElement} */ (ev.target)).closest('#account')) closeAccount();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !byId('accountMenu').hidden) {
      closeAccount();
      byId('accountBtn').focus();
    }
  });
}

/** Esvazia o cabeçalho (sessão encerrada). */
export function resetNav() {
  closeAccount();
  for (const t of visibleTabsAll()) byId(`nav-${t}`).hidden = true;
  byId('userName').textContent = '';
  byId('accountInitial').textContent = '';
  byId('accountRole').textContent = '';
  if (enterTimer !== null) clearTimeout(enterTimer);
  enterTimer = null;
}

/** @returns {string[]} */
function visibleTabsAll() {
  return SCREENS.filter((x) => x !== 'conjunto');
}

/** Mostra só as abas do papel e o nome da conta. */
export function renderAccount() {
  const show = new Set(visibleTabs());
  for (const t of visibleTabsAll()) byId(`nav-${t}`).hidden = !show.has(t);
  byId('tabs').dataset.count = String(show.size);
  const u = state.user;
  const name = u ? (u.display_name || u.username) : '';
  byId('userName').textContent = name;
  byId('accountInitial').textContent = name ? name.trim().slice(0, 1).toUpperCase() : '';
  byId('accountRole').textContent = u ? `${u.username} · ${isMaster() ? 'mestre' : 'usuário'}` : '';
}

/** Marca a aba da tela atual (aria-current). */
export function syncNav() {
  const r = state.route;
  for (const b of document.querySelectorAll('#tabs [aria-current]')) b.removeAttribute('aria-current');
  const tab = r.screen === 'conjunto' ? 'conjuntos' : r.screen;
  const el = document.getElementById(`nav-${tab}`);
  if (el && !el.hidden) el.setAttribute('aria-current', 'page');
}

/**
 * Mostra só a tela pedida, com entrada suave (as animações vivem sob a classe `enter`).
 * @param {Route} route
 */
export function showScreen(route) {
  state.route = route;
  for (const name of SCREENS) {
    const scr = byId(`scr-${name}`);
    const on = name === route.screen;
    if (on && scr.hidden) scr.hidden = false;
    if (!on) {
      scr.hidden = true;
      scr.classList.remove('enter');
    }
  }
  const scr = byId(`scr-${route.screen}`);
  scr.classList.remove('enter');
  void scr.offsetWidth; // reinicia as animações de entrada
  scr.classList.add('enter');
  if (enterTimer !== null) clearTimeout(enterTimer);
  enterTimer = window.setTimeout(() => scr.classList.remove('enter'), ENTER_MS);
  closeAccount();
  syncNav();
  window.scrollTo({ top: 0 });
}

/** Texto e estado do chip "Tudo funcionando" do cabeçalho: serviços (mestre) ou contador próprio (usuário). */
export function renderChip() {
  const chip = byId('healthChip');
  if (isMaster()) {
    const list = services();
    const bad = list.filter((x) => x.level === 'bad');
    const warn = list.filter((x) => x.level === 'warn' || x.level === 'off');
    const okN = list.filter((x) => x.level === 'ok').length;
    chip.dataset.level = bad.length ? 'bad' : warn.length ? 'warn' : 'ok';
    byId('chipTitle').textContent = bad.length ? 'Atenção: há falhas' : warn.length ? 'Funcionando com avisos' : 'Tudo funcionando';
    const beat = state.status && state.status.workers && state.status.workers.lives
      ? toEpoch(state.status.workers.lives.last_heartbeat_at) : null;
    byId('chipSub').textContent = `${okN} de ${list.length} serviços ok${beat !== null ? ` · leitura ${relTime(beat)}` : ''}`;
    return;
  }
  const cs = counterService();
  const lost = !state.apiOk;
  const level = lost || cs.level === 'bad' ? 'bad' : cs.level === 'ok' || cs.level === 'off' ? 'ok' : 'warn';
  chip.dataset.level = level;
  byId('chipTitle').textContent = lost ? 'Sem conexão' : level === 'bad' ? 'Atenção: há falhas' : level === 'warn' ? 'Funcionando com avisos' : 'Tudo funcionando';
  const upd = state.counter && state.counter.reading ? toEpoch(state.counter.reading.read_at) : null;
  byId('chipSub').textContent = state.counterOff ? 'contador desativado' : `contador ${cs.label}${upd !== null ? ` · ${relTime(upd)}` : ''}`;
}

/**
 * Cabeçalho de página com o caminho (migalhas). `items`: texto ou {text, go}.
 * @param {(string | {text: string, go: () => void})[]} items
 * @returns {HTMLElement}
 */
export function crumbs(items) {
  const nav = h('nav', { class: 'crumbs', 'aria-label': 'Caminho' });
  items.forEach((it, i) => {
    if (i) nav.appendChild(h('span', { class: 'crumb-sep', 'aria-hidden': 'true', text: '›' }));
    if (typeof it === 'string') nav.appendChild(h('b', { text: it }));
    else {
      const b = h('button', { type: 'button', class: 'crumb-link', text: it.text });
      b.addEventListener('click', it.go);
      nav.appendChild(b);
    }
  });
  return nav;
}
