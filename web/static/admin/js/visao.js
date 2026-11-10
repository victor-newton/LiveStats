// @ts-check
// Início: dois assuntos separados. (1) Lives por conjunto: cada conjunto tem os próprios números, nada é somado
// entre conjuntos. (2) Inscritos do canal do próprio usuário (só YouTube, 1 canal). Para o mestre, a atividade recente.

import { api } from './api.js';
import { byId } from './dom.js';
import { h } from './h.js';
import { subsCards, mountLog, resetLog, terminalCard } from './parts.js';
import { setList, fetchSparks } from './setcard.js';
import { state, isMaster } from './store.js';
import { inheritNumbers } from './util.js';
import { titleTip } from './infotip.js';

let subsSig = '';
let logSig = '';
/** @type {Record<string, HTMLElement>} */
const R = {};
/** @type {ReturnType<typeof setList>} */
let list;
/** @type {(setId: number) => void} */
let openSet = () => {};

/** Monta a estrutura fixa da tela. */
export function initVisao() {
  R.count = h('span', { class: 'count num' });
  R.cards = h('div', { class: 'set-cards bento', id: 'setCards' });
  R.subs = h('div', { class: 'yt-zone bento', id: 'subsZone' });
  R.log = h('div', { class: 'term-box', id: 'logVisao' });
  R.logZone = h('div', { class: 'bento', id: 'logZone' }, terminalCard('atividade-recente.log', R.log, 'b-wide'));
  list = setList(R.cards, { empty: 'Nenhum conjunto ainda. Abra a aba Conjuntos e use "Novo conjunto".', onOpen: (id) => openSet(id) });
  byId('scr-visao').replaceChildren(
    h('div', { class: 'head' }, titleTip('h1', 'Início', 'Dois assuntos separados: as lives de cada conjunto e o contador de inscritos de um canal do YouTube.')),
    h('div', { class: 'zone' },
      h('div', { class: 'zone-h' }, titleTip('h2', 'Lives por conjunto', 'Cada conjunto tem os próprios números. Nada é somado entre conjuntos.'), h('span', { class: 'rule' }), R.count),
      R.cards),
    h('div', { class: 'zone', id: 'subsZoneBox' },
      h('div', { class: 'zone-h' }, titleTip('h2', 'Inscritos do canal', 'Lido no YouTube Studio da conta dos cookies. Só YouTube, um canal.'), h('span', { class: 'yt-badge', text: 'só YouTube · 1 canal' }), h('span', { class: 'rule' })),
      R.subs),
    R.logZone);
}

/** @param {(setId: number) => void} fn */
export function setOpenHandler(fn) {
  openSet = fn;
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetVisao() {
  subsSig = '';
  logSig = '';
  resetLog();
  if (R.cards) { list.reset(); R.subs.replaceChildren(); R.log.replaceChildren(); R.count.textContent = ''; }
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const jobs = [fetchSparks(state.sets)];
  const hist = state.counterOff ? null : api('GET', '/api/admin/subscribers/history');
  const health = isMaster() ? api('GET', '/api/admin/health/detail') : null;
  const [sparks, h1, h2] = await Promise.all([jobs[0], hist, health]);
  const out = [...sparks];
  if (h1) {
    if (h1.ok && h1.data) state.subHist = h1.data;
    if (!(h1.status === 403 && h1.data && h1.data.code === 'counter_disabled')) out.push(h1);
  }
  if (h2) {
    if (h2.ok && h2.data) state.health = h2.data;
    out.push(h2);
  }
  return out;
}

/**
 * @param {boolean} first
 */
export function render(first) {
  const nLives = state.sets.reduce((a, x) => a + x.lives.length, 0);
  R.count.textContent = `${state.sets.length} de ${state.limits.max_sets} conjuntos · ${nLives} de ${state.limits.max_lives} lives`;
  list.render(state.sets, first);

  byId('subsZoneBox').hidden = state.counterOff;
  const ssig = JSON.stringify([state.counter, state.subHist]);
  if (first || ssig !== subsSig) {
    subsSig = ssig;
    const fresh = subsCards(first);
    if (!first) {
      const frag = document.createDocumentFragment();
      frag.append(...fresh);
      inheritNumbers(R.subs, frag);
    }
    R.subs.replaceChildren(...fresh);
  }
  R.logZone.hidden = !isMaster();
  const events = state.health && Array.isArray(state.health.events) ? state.health.events : [];
  const lsig = JSON.stringify(events.slice(0, 8));
  if (first || lsig !== logSig) {
    logSig = lsig;
    mountLog(R.log, events, 8, first);
  }
}
