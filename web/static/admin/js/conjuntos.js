// @ts-check
// Conjuntos (D53): lista de cartões bento dos conjuntos do usuário (nome, plataformas, espectadores agora, meta,
// mini-gráfico, Abrir) e o botão "Novo conjunto". Abrir leva à página do conjunto, onde moram as lives.

import { byId } from './dom.js';
import { h, icon } from './h.js';
import { setList, fetchSparks } from './setcard.js';
import { state } from './store.js';
import { openNewSet } from './dialogs.js';
import { titleTip } from './infotip.js';

/** @type {Record<string, HTMLElement>} */
const R = {};
/** @type {ReturnType<typeof setList>} */
let list;
/** @type {(setId: number) => void} */
let openSet = () => {};

/** Monta a estrutura fixa da tela. */
export function initConjuntos() {
  R.count = h('p', { id: 'setsCount' });
  R.cards = h('div', { class: 'set-cards bento', id: 'setList' });
  R.btnNew = h('button', { type: 'button', class: 'btn btn-primary', id: 'btnNovoConjunto' }, icon('plus', 14), 'Novo conjunto');
  R.btnNew.addEventListener('click', () => openNewSet());
  list = setList(R.cards, { empty: 'Nenhum conjunto ainda. Use "Novo conjunto" para criar o primeiro.', onOpen: (id) => openSet(id) });
  byId('scr-conjuntos').replaceChildren(
    h('div', { class: 'head' }, h('div', null, titleTip('h1', 'Conjuntos', 'Cada conjunto agrupa lives e tem os próprios números e links. Abra um conjunto para ver as lives dele.'), R.count), h('div', { class: 'actions' }, R.btnNew)),
    R.cards);
}

/** @param {(setId: number) => void} fn */
export function setOpenHandler(fn) {
  openSet = fn;
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetConjuntos() {
  if (R.cards) { list.reset(); R.count.textContent = ''; }
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export function fetchExtra() {
  return fetchSparks(state.sets);
}

/** @param {boolean} first */
export function render(first) {
  const nLives = state.sets.reduce((a, x) => a + x.lives.length, 0);
  R.count.textContent = `${state.sets.length} de ${state.limits.max_sets} conjuntos · ${nLives} de ${state.limits.max_lives} lives`;
  list.render(state.sets, first);
}
