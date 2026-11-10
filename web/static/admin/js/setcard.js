// @ts-check
// Cartão bento de um conjunto (nome, plataformas, espectadores agora, meta, mini-gráfico, lives) e a lista que o
// mantém: usado por Início, Conjuntos e Mestre > Dados do usuário. Dados do servidor só entram por textContent.

import { api } from './api.js';
import { h, s, setStyle } from './h.js';
import { sparkline } from './charts.js';
import { statusPill, platBadge } from './ui.js';
import { setAccent } from './glow.js';
import {
  state, liveStats, liveName, sortedLives, setTotals,
} from './store.js';
import {
  fmt, fmtDec, inheritNumbers, safeColor, setText,
} from './util.js';

const SPARK_TTL_MS = 30000;

/**
 * Busca o minigráfico (última hora) dos conjuntos cujo cache envelheceu.
 * @param {any[]} sets
 * @returns {Promise<import('./api.js').ApiResponse[]>}
 */
export async function fetchSparks(sets) {
  const now = Date.now();
  const stale = sets.filter((x) => {
    const sp = state.sparks.get(x.id);
    return !sp || now - sp.at > SPARK_TTL_MS;
  });
  const out = await Promise.all(stale.map((x) => api('GET', `/api/admin/sets/${x.id}/history?range=60`)));
  stale.forEach((x, i) => {
    const r = out[i];
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
export function setCard(set, animate) {
  const lives = sortedLives(set);
  const t = setTotals(set);
  const goal = typeof set.goal === 'number' ? set.goal : null;
  const pct = goal && t.likes !== null ? Math.min(100, (t.likes / goal) * 100) : 0;
  const num = (/** @type {string} */ v, /** @type {string} */ label) => {
    const b = h('b', { class: 'num' });
    setText(b, v, animate);
    return h('div', null, b, h('span', { text: label }));
  };
  const open = h('button', { type: 'button', class: 'btn open-set', 'data-set': set.id, 'aria-label': `Abrir o conjunto ${set.name}`, text: 'Abrir' });
  const meterFill = h('i');
  meterFill.style.width = `${pct.toFixed(1)}%`;
  const spark = s('svg', { class: 'spark', viewBox: '0 0 300 70', role: 'img', 'aria-label': 'Espectadores na última hora' });
  const sp = state.sparks.get(set.id);
  sparkline(spark, sp ? sp.values : [], '#A5B4FC', animate);
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
  const platforms = [...new Set(lives.map((l) => l.platform))];
  const el = h('div', { class: 'card glow-card set-card b-2', 'data-set': set.id },
    h('div', { class: 'row' },
      h('div', { class: 'set-id' },
        h('h3', { text: set.name }),
        h('div', { class: 'set-meta' },
          h('span', { class: 'plats' }, ...platforms.map((p) => platBadge(p, 'sm'))),
          h('span', { class: 'count', text: `${lives.length} ${lives.length === 1 ? 'live' : 'lives'} · ${t.on} ao vivo` }))),
      open),
    h('div', { class: 'set-stats' },
      num(fmt(t.viewers), 'espectadores agora'),
      num(t.likes === null ? '—' : fmt(t.likes), goal ? `likes de ${fmt(goal)}` : 'likes'),
      num(goal ? (pct >= 100 ? 'meta batida' : `${fmtDec(pct)}%`) : '—', 'da meta')),
    h('div', { class: 'meter' }, meterFill),
    spark, mix, mini);
  setAccent(el, lives.length ? safeColor(lives[0].color) : '#818CF8');
  return el;
}

/**
 * Lista de cartões que só reconstrói o que mudou.
 * @param {HTMLElement} box contêiner
 * @param {{empty: string, onOpen: (setId: number) => void}} opts
 */
export function setList(box, opts) {
  /** @type {Map<number, {el: HTMLElement, sig: string}>} */
  let cards = new Map();
  box.addEventListener('click', (ev) => {
    const b = /** @type {HTMLElement} */ (ev.target).closest('button.open-set');
    if (b) opts.onOpen(Number(/** @type {HTMLElement} */ (b).dataset.set));
  });
  return {
    reset() {
      cards = new Map();
      box.replaceChildren();
    },
    /** @param {any[]} sets @param {boolean} first */
    render(sets, first) {
      if (first) { cards = new Map(); box.replaceChildren(); }
      const seen = new Set();
      for (const set of sets) {
        seen.add(set.id);
        const sp = state.sparks.get(set.id);
        const sig = JSON.stringify([set, sp ? sp.values.length : 0, sp ? sp.values[sp.values.length - 1] : 0]);
        const prev = cards.get(set.id);
        if (prev && prev.sig === sig) continue;
        const el = setCard(set, first || !prev);
        if (prev) { inheritNumbers(prev.el, el); prev.el.replaceWith(el); } else box.appendChild(el);
        cards.set(set.id, { el, sig });
      }
      for (const [id, c] of cards) {
        if (!seen.has(id)) { c.el.remove(); cards.delete(id); }
      }
      if (!sets.length) {
        if (!box.querySelector('.empty')) box.appendChild(h('p', { class: 'empty', text: opts.empty }));
      } else {
        box.querySelector('.empty')?.remove();
      }
      const want = sets.map((x) => cards.get(x.id)?.el).filter(Boolean);
      const have = [...box.children].filter((n) => n.classList.contains('set-card'));
      if (want.length === have.length && want.some((n, i) => n !== have[i])) box.replaceChildren(.../** @type {HTMLElement[]} */ (want));
    },
  };
}
