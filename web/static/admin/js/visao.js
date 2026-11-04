// @ts-check
// Visão geral: dois assuntos separados. (1) Lives por conjunto: cada conjunto tem os próprios números, nada é somado
// entre conjuntos. (2) Inscritos do canal: só YouTube, 1 canal. Mais a atividade recente.

import { api } from './api.js';
import { byId } from './dom.js';
import { h, s, setStyle } from './h.js';
import { sparkline } from './charts.js';
import { statusPill } from './ui.js';
import { subsCards, eventLog } from './parts.js';
import {
  state, liveStats, liveName, sortedLives, setTotals,
} from './store.js';
import {
  fmt, fmtDec, inheritNumbers, safeColor, setText,
} from './util.js';

const SPARK_TTL_MS = 30000;
/** @type {Map<number, {el: HTMLElement, sig: string}>} */
let cards = new Map();
let subsSig = '';
let logSig = '';
/** @type {Record<string, HTMLElement>} */
const R = {};

/** Monta a estrutura fixa da tela. */
export function initVisao() {
  R.count = h('span', { class: 'count num' });
  R.cards = h('div', { class: 'set-cards', id: 'setCards' });
  R.subs = h('div', { class: 'yt-zone', id: 'subsZone' });
  R.log = h('div', { id: 'logVisao' });
  byId('scr-visao').replaceChildren(
    h('div', { class: 'head' }, h('div', null, h('h1', { text: 'Visão geral' }), h('p', { text: 'Dois assuntos separados: as lives de cada conjunto e o contador de inscritos de um canal do YouTube.' }))),
    h('div', { class: 'zone' },
      h('div', { class: 'zone-h' }, h('h2', { text: 'Lives por conjunto' }), h('p', { text: 'Cada conjunto tem os próprios números. Nada é somado entre conjuntos.' }), h('span', { class: 'rule' }), R.count),
      R.cards),
    h('div', { class: 'zone' },
      h('div', { class: 'zone-h' }, h('h2', { text: 'Inscritos do canal' }), h('span', { class: 'yt-badge', text: 'só YouTube · 1 canal' }), h('p', { text: 'Lido no YouTube Studio da conta dos cookies.' }), h('span', { class: 'rule' })),
      R.subs),
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Atividade recente' }))), R.log));
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetVisao() {
  cards = new Map();
  subsSig = '';
  logSig = '';
  if (R.cards) { R.cards.replaceChildren(); R.subs.replaceChildren(); R.log.replaceChildren(); R.count.textContent = ''; }
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const now = Date.now();
  const stale = state.sets.filter((x) => {
    const sp = state.sparks.get(x.id);
    return !sp || now - sp.at > SPARK_TTL_MS;
  });
  const jobs = [
    api('GET', '/api/admin/subscribers/history'),
    api('GET', '/api/admin/health/detail'),
    ...stale.map((x) => api('GET', `/api/admin/sets/${x.id}/history?range=60`)),
  ];
  const out = await Promise.all(jobs);
  if (out[0].ok && out[0].data) state.subHist = out[0].data;
  if (out[1].ok && out[1].data) state.health = out[1].data;
  stale.forEach((x, i) => {
    const r = out[2 + i];
    if (r.ok && r.data && Array.isArray(r.data.points)) {
      state.sparks.set(x.id, { at: now, values: r.data.points.map((/** @type {any} */ p) => (typeof p.viewers === 'number' ? p.viewers : 0)) });
    }
  });
  return out;
}

/**
 * @param {any} set
 * @param {boolean} animate
 * @returns {HTMLElement}
 */
function setCard(set, animate) {
  const lives = sortedLives(set);
  const t = setTotals(set);
  const goal = typeof set.goal === 'number' ? set.goal : null;
  const pct = goal && t.likes !== null ? Math.min(100, (t.likes / goal) * 100) : 0;
  const num = (/** @type {string} */ v, /** @type {string} */ label) => {
    const b = h('b', { class: 'num' });
    setText(b, v, animate);
    return h('div', null, b, h('span', { text: label }));
  };
  const open = h('button', { type: 'button', class: 'btn open-set', 'data-set': set.id, 'aria-label': `Abrir o conjunto ${set.name}`, text: 'Abrir conjunto' });
  const meterFill = h('i');
  meterFill.style.width = `${pct.toFixed(1)}%`;
  const spark = s('svg', { class: 'spark', viewBox: '0 0 300 70', role: 'img', 'aria-label': 'Espectadores na última hora' });
  const sp = state.sparks.get(set.id);
  sparkline(spark, sp ? sp.values : [], '#8F939C', animate);
  const mix = h('div', { class: 'mix' });
  for (const l of lives) {
    const v = (liveStats(l) || {}).viewers || 0;
    if (!v || !t.viewers) continue;
    const seg = h('i');
    setStyle(seg, { width: `${((v / t.viewers) * 100).toFixed(1)}%`, background: safeColor(l.color) });
    mix.appendChild(seg);
  }
  const mini = h('div', { class: 'mini-lives' }, ...lives.map((l) => {
    const st = liveStats(l);
    const dot = h('span', { class: 'dot' });
    setStyle(dot, { background: safeColor(l.color) });
    const v = h('span', { class: 'num' });
    setText(v, fmt(st ? st.viewers : null), animate);
    return h('div', { class: 'mini-live' }, dot, h('span', { class: 'mini-name', text: liveName(l) }), statusPill(st ? st.status : null), v);
  }));
  const el = h('div', { class: 'card set-card', 'data-set': set.id },
    h('div', { class: 'row' },
      h('div', null, h('h3', { text: set.name }), h('span', { class: 'count', text: `${lives.length} ${lives.length === 1 ? 'live' : 'lives'} · ${t.on} ao vivo` })),
      open),
    h('div', { class: 'set-stats' },
      num(fmt(t.viewers), 'espectadores agora'),
      num(t.likes === null ? '—' : fmt(t.likes), goal ? `likes de ${fmt(goal)}` : 'likes'),
      num(goal ? (pct >= 100 ? 'meta batida' : `${fmtDec(pct)}%`) : '—', 'da meta')),
    h('div', { class: 'meter' }, meterFill),
    spark, mix, mini);
  return el;
}

/** @param {(setId: number) => void} openSet */
export function setOpenHandler(openSet) {
  R.cards.addEventListener('click', (ev) => {
    const b = /** @type {HTMLElement} */ (ev.target).closest('button.open-set');
    if (b) openSet(Number(/** @type {HTMLElement} */ (b).dataset.set));
  });
}

/**
 * @param {boolean} first
 */
export function render(first) {
  const nLives = state.sets.reduce((a, x) => a + x.lives.length, 0);
  R.count.textContent = `${state.sets.length} de ${state.limits.max_sets} conjuntos · ${nLives} de ${state.limits.max_lives} lives`;
  if (first) { cards = new Map(); R.cards.replaceChildren(); }
  const seen = new Set();
  for (const set of state.sets) {
    seen.add(set.id);
    const sp = state.sparks.get(set.id);
    const sig = JSON.stringify([set, sp ? sp.values.length : 0, sp ? sp.values[sp.values.length - 1] : 0]);
    const prev = cards.get(set.id);
    if (prev && prev.sig === sig) continue;
    const el = setCard(set, first || !prev);
    if (prev) { inheritNumbers(prev.el, el); prev.el.replaceWith(el); } else R.cards.appendChild(el);
    cards.set(set.id, { el, sig });
  }
  for (const [id, c] of cards) {
    if (!seen.has(id)) { c.el.remove(); cards.delete(id); }
  }
  if (!state.sets.length) {
    if (!R.cards.querySelector('.empty')) R.cards.appendChild(h('p', { class: 'empty', text: 'Nenhum conjunto ainda. Use "Novo conjunto" na barra lateral.' }));
  } else {
    R.cards.querySelector('.empty')?.remove();
  }
  // ordem igual à da barra lateral
  const want = state.sets.map((x) => cards.get(x.id)?.el).filter(Boolean);
  const have = [...R.cards.children].filter((n) => n.classList.contains('set-card'));
  if (want.length === have.length && want.some((n, i) => n !== have[i])) R.cards.replaceChildren(.../** @type {HTMLElement[]} */ (want));

  const ssig = JSON.stringify([state.subscribers, state.status && state.status.subscribers, state.status && state.status.cookies, state.settings && state.settings.channel, state.subHist, state.status && state.status.workers && state.status.workers.inscritos && state.status.workers.inscritos.state]);
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
  const events = state.health && Array.isArray(state.health.events) ? state.health.events : [];
  const lsig = JSON.stringify(events.slice(0, 8));
  if (first || lsig !== logSig) {
    logSig = lsig;
    R.log.replaceChildren(eventLog(events, 8));
  }
}
