// @ts-check
// Barra lateral em lista que abre (Visão geral; Conjuntos com submenu por conjunto: Painel, cada live com a
// sua cor, Links; Inscritos; Saúde), migalhas e troca de tela com entrada suave.

import { byId } from './dom.js';
import { h, icon, setStyle } from './h.js';
import { collapse } from './ui.js';
import {
  state, setById, liveName, sortedLives, services,
} from './store.js';
import { safeColor, relTime, toEpoch } from './util.js';

/** @typedef {import('./store.js').Route} Route */

/** @type {(route: Route) => void} */
let onNavigate = () => {};
let sig = '';
/** @type {Map<number, {toggle: HTMLElement, col: ReturnType<typeof collapse>, painel: HTMLElement, links: HTMLElement, lives: Map<number, HTMLElement>}>} */
let items = new Map();
/** @type {number | null} */
let enterTimer = null;

const SCREENS = ['visao', 'conjunto', 'inscritos', 'saude'];
const ENTER_MS = 1800;

/** @param {(route: Route) => void} fn */
export function setNavHandler(fn) {
  onNavigate = fn;
}

/** Liga os botões fixos da barra, o menu do celular e o botão de novo conjunto. */
export function initNav() {
  for (const name of ['visao', 'inscritos', 'saude']) {
    byId(`nav-${name}`).addEventListener('click', () => onNavigate({ screen: /** @type {any} */ (name), setId: null, focus: null }));
  }
  byId('healthChip').addEventListener('click', () => onNavigate({ screen: 'saude', setId: null, focus: null }));
  byId('btnMenu').addEventListener('click', () => {
    const side = byId('side');
    const open = !side.classList.contains('open');
    side.classList.toggle('open', open);
    byId('btnMenu').setAttribute('aria-expanded', String(open));
  });
  byId('sideScrim').addEventListener('click', closeMenu);
}

function closeMenu() {
  byId('side').classList.remove('open');
  byId('btnMenu').setAttribute('aria-expanded', 'false');
}

/** Esvazia a barra (sessão encerrada). */
export function resetNav() {
  sig = '';
  items = new Map();
  byId('navSets').replaceChildren();
  byId('crumb').replaceChildren();
  closeMenu();
  if (enterTimer !== null) clearTimeout(enterTimer);
  enterTimer = null;
}

/** Reconstrói a lista de conjuntos só se a estrutura mudou; depois sincroniza aberto/atual. */
export function renderNav() {
  const next = JSON.stringify(state.sets.map((x) => [x.id, x.name, sortedLives(x).map((l) => [l.id, liveName(l), safeColor(l.color)])]));
  if (next !== sig) {
    sig = next;
    build();
  }
  syncNav();
}

function build() {
  const box = byId('navSets');
  box.replaceChildren();
  items = new Map();
  if (!state.sets.length) {
    box.appendChild(h('p', { class: 'nav-empty', text: 'Nenhum conjunto ainda.' }));
    return;
  }
  for (const set of state.sets) {
    const lives = sortedLives(set);
    const toggle = h('button', { type: 'button', class: 'nav-item set-toggle', 'aria-expanded': 'false' },
      icon('chev', 12),
      h('span', { class: 'grow', text: set.name }),
      h('span', { class: 'count num', text: String(lives.length) }));
    toggle.firstElementChild?.classList.add('chev');
    const painel = h('button', { type: 'button', class: 'nav-item' }, h('span', { class: 'dot dot-set' }), h('span', { class: 'grow', text: 'Painel' }));
    const links = h('button', { type: 'button', class: 'nav-item' }, h('span', { class: 'dot dot-set' }), h('span', { class: 'grow', text: 'Links do conjunto' }));
    /** @type {Map<number, HTMLElement>} */
    const liveBtns = new Map();
    const sub = h('div', { class: 'sub' }, painel);
    for (const l of lives) {
      const dot = h('span', { class: 'dot' });
      setStyle(dot, { background: safeColor(l.color) });
      const b = h('button', { type: 'button', class: 'nav-item nav-live', title: liveName(l) }, dot, h('span', { class: 'grow', text: liveName(l) }));
      b.addEventListener('click', () => onNavigate({ screen: 'conjunto', setId: set.id, focus: { live: l.id } }));
      liveBtns.set(l.id, b);
      sub.appendChild(b);
    }
    sub.appendChild(links);
    const col = collapse(sub);
    const sid = set.id;
    toggle.addEventListener('click', () => {
      if (state.navOpen.has(sid)) state.navOpen.delete(sid);
      else state.navOpen.add(sid);
      syncNav();
    });
    painel.addEventListener('click', () => onNavigate({ screen: 'conjunto', setId: sid, focus: null }));
    links.addEventListener('click', () => onNavigate({ screen: 'conjunto', setId: sid, focus: { links: true } }));
    box.append(toggle, col.root);
    items.set(sid, { toggle, col, painel, links, lives: liveBtns });
  }
}

/** Aberto/fechado de cada conjunto e a marca "página atual". */
export function syncNav() {
  const r = state.route;
  for (const b of document.querySelectorAll('#side [aria-current]')) b.removeAttribute('aria-current');
  if (r.screen !== 'conjunto') {
    const fixed = document.getElementById(`nav-${r.screen}`);
    if (fixed) fixed.setAttribute('aria-current', 'page');
  }
  for (const [id, it] of items) {
    const open = state.navOpen.has(id);
    it.toggle.setAttribute('aria-expanded', String(open));
    it.col.set(open);
    if (r.screen === 'conjunto' && r.setId === id) {
      const f = r.focus;
      if (f && f.live !== undefined && it.lives.has(f.live)) it.lives.get(f.live)?.setAttribute('aria-current', 'page');
      else if (f && f.links) it.links.setAttribute('aria-current', 'page');
      else it.painel.setAttribute('aria-current', 'page');
    }
  }
}

/** Migalhas no topo. */
export function renderCrumb() {
  const r = state.route;
  const set = setById(r.setId);
  /** @type {[string, string]} */
  let pair = ['LiveStats', 'Visão geral'];
  if (r.screen === 'conjunto') pair = ['Conjuntos', set ? set.name : '—'];
  else if (r.screen === 'inscritos') pair = ['Inscritos', 'Contador e cookies'];
  else if (r.screen === 'saude') pair = ['Sistema', 'Saúde'];
  byId('crumb').replaceChildren(h('span', { text: pair[0] }), h('span', { text: '/' }), h('b', { text: pair[1] }));
}

/**
 * Mostra só a tela pedida, com entrada suave (as animações vivem sob a classe `enter`).
 * @param {Route} route
 */
export function showScreen(route) {
  state.route = route;
  if (route.screen === 'conjunto' && route.setId !== null) state.navOpen.add(route.setId);
  for (const name of SCREENS) {
    const scr = byId(`scr-${name}`);
    const on = name === route.screen;
    if (on && scr.hidden) {
      scr.hidden = false;
    }
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
  closeMenu();
  renderCrumb();
  syncNav();
}

/** Texto e estado da faixa "Tudo funcionando" da barra lateral. */
export function renderChip() {
  const list = services();
  const bad = list.filter((x) => x.level === 'bad');
  const warn = list.filter((x) => x.level === 'warn' || x.level === 'off');
  const okN = list.filter((x) => x.level === 'ok').length;
  const chip = byId('healthChip');
  const level = bad.length ? 'bad' : warn.length ? 'warn' : 'ok';
  chip.dataset.level = level;
  byId('chipTitle').textContent = bad.length ? 'Atenção: há falhas' : warn.length ? 'Funcionando com avisos' : 'Tudo funcionando';
  const beat = state.status && state.status.workers && state.status.workers.lives
    ? toEpoch(state.status.workers.lives.last_heartbeat_at) : null;
  byId('chipSub').textContent = `${okN} de ${list.length} serviços ok${beat !== null ? ` · leitura ${relTime(beat)}` : ''}`;
}
