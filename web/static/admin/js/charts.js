// @ts-check
// Gráficos em SVG puro (atributos SVG e classes; nenhum atributo `style`, nenhum HTML vindo de dado):
// linha suave com dois eixos, crosshair + ponto + dica, rosca, anéis de progresso, tempo de resposta da saúde e
// minigráfico. As dicas ao passar o mouse são montadas com nós de texto. As animações são classes CSS; com
// prefers-reduced-motion ficam desligadas. Os gráficos grandes se ajustam ao tamanho real do cartão (viewBox em px).

import { h, s, setStyle } from './h.js';
import { fmt, fmtDec, hhmm, inkOn, niceMax, safeColor } from './util.js';

const VIEWS_COLOR = '#A5B4FC';
const LIKES_COLOR = '#67E8F9';
const LIVES_MS_COLOR = '#A5B4FC';
const SUBS_MS_COLOR = '#E879F9';
const GRID = 'rgba(255,255,255,.07)';
const FACE = '#0B0D15';

/** @type {WeakMap<Element, () => void>} */
const redraws = new WeakMap();
/** @type {WeakMap<Element, string>} */
const sizes = new WeakMap();
/** @type {ResizeObserver | null} */
const observer = typeof ResizeObserver === 'function'
  ? new ResizeObserver((entries) => {
    for (const e of entries) {
      const svg = e.target.querySelector('svg.fit');
      if (!svg) continue;
      requestAnimationFrame(() => {
        const box = svg.parentElement;
        const key = box ? `${box.clientWidth}x${box.clientHeight}` : '';
        if (key === sizes.get(svg)) return;
        const fn = redraws.get(svg);
        if (fn) fn();
      });
    }
  })
  : null;

/**
 * Tamanho real (px) da caixa do gráfico, para o viewBox ficar 1:1 com a tela (texto sempre nítido).
 * @param {SVGElement} svg
 * @param {number} defW
 * @param {number} defH
 * @param {() => void} redraw refaz o gráfico (sem animação) quando a caixa mudar de tamanho
 * @returns {{W: number, H: number}}
 */
function fit(svg, defW, defH, redraw) {
  const box = svg.parentElement;
  const w = box ? Math.round(box.clientWidth) : 0;
  const hh = box ? Math.round(box.clientHeight) : 0;
  const W = w >= 200 ? w : defW;
  const H = hh >= 120 ? hh : defH;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.classList.add('fit');
  sizes.set(svg, `${w}x${hh}`);
  redraws.set(svg, redraw);
  if (observer && box && !box.hasAttribute('data-fit')) {
    box.setAttribute('data-fit', '');
    observer.observe(box);
  }
  return { W, H };
}

/**
 * Gráfico sem seleção de texto nem menu de contexto (segurar o dedo no celular não pode copiar texto): o toque vira
 * clique. Vale para o próprio elemento e para os seus descendentes.
 * @param {...Element} els
 */
function noSelect(...els) {
  for (const el of els) {
    el.addEventListener('contextmenu', (ev) => ev.preventDefault());
    el.addEventListener('selectstart', (ev) => ev.preventDefault());
  }
}

/** Fixações ativas (modo clique) por gráfico: sobrevivem ao redesenho de 5 s enquanto o índice existir. @type {WeakMap<Element, number>} */
const pinnedAt = new WeakMap();
/** Ouvinte de "tocar fora" de cada gráfico, para não acumular quando ele é redesenhado. @type {WeakMap<Element, (ev: Event) => void>} */
const outsideOf = new WeakMap();

/**
 * Desfaz a fixação quando o toque/clique cai fora das áreas dadas.
 * @param {Element} key
 * @param {Element[]} inside
 * @param {() => void} unpin
 */
function unpinOutside(key, inside, unpin) {
  const old = outsideOf.get(key);
  if (old) document.removeEventListener('pointerdown', old, true);
  /** @param {Event} ev */
  const fn = (ev) => {
    const t = ev.target;
    if (!(t instanceof Node) || !key.isConnected) { document.removeEventListener('pointerdown', fn, true); return; }
    if (inside.some((e) => e.contains(t))) return;
    unpin();
  };
  outsideOf.set(key, fn);
  document.addEventListener('pointerdown', fn, true);
}

/**
 * @typedef {object} HoverInfo
 * @property {number} top
 * @property {number} bottom
 * @property {{y: number, color: string}[]} points
 * @property {string} title
 * @property {{color: string, label: string, value: string}[]} rows
 * @property {string} [note]
 */

/**
 * Liga a dica ao passar o mouse a um gráfico. `xs` = posição x (no viewBox) de cada amostra.
 * @param {SVGElement} svg
 * @param {number} width largura do viewBox
 * @param {number} height altura do viewBox
 * @param {number[]} xs
 * @param {(i: number) => HoverInfo} render
 */
function attachHover(svg, width, height, xs, render) {
  const box = /** @type {HTMLElement} */ (svg.parentElement);
  const tip = /** @type {HTMLElement} */ (box.querySelector('.tip'));
  const g = s('g', { 'pointer-events': 'none', opacity: 0, class: 'hover-g' });
  const hit = s('rect', { x: 0, y: 0, width, height, fill: 'transparent', class: 'hit' });
  svg.append(g, hit);
  noSelect(svg, box);
  /** @type {number | null} */
  let pinned = null;
  /** @param {number} clientX @returns {number} índice da amostra mais próxima */
  const indexAt = (clientX) => {
    const r = svg.getBoundingClientRect();
    const k = width / (r.width || 1);
    const vx = (clientX - r.left) * k;
    let i = 0;
    let best = Infinity;
    xs.forEach((x, j) => {
      const d = Math.abs(x - vx);
      if (d < best) { best = d; i = j; }
    });
    return i;
  };
  /** @param {number} i */
  const show = (i) => {
    const r = svg.getBoundingClientRect();
    if (!r.width || !xs.length) return;
    const k = width / r.width;
    const info = render(i);
    g.replaceChildren(s('line', { x1: xs[i], x2: xs[i], y1: info.top, y2: info.bottom, class: 'hover-line' }));
    for (const p of info.points) {
      g.appendChild(s('circle', {
        cx: xs[i], cy: p.y, r: 5.5, fill: p.color, stroke: '#E0E7FF', 'stroke-width': 2, class: 'hover-dot',
      }));
    }
    g.setAttribute('opacity', '1');
    tip.replaceChildren(h('div', { class: 't', text: info.title }));
    for (const row of info.rows) {
      const dot = h('i', { class: 'tip-dot' });
      setStyle(dot, { background: row.color });
      tip.appendChild(h('div', { class: 'r' }, h('span', null, dot, row.label), h('b', { text: row.value })));
    }
    if (info.note) tip.appendChild(h('div', { class: 'note', text: info.note }));
    tip.hidden = false;
    const left = Math.min(Math.max(xs[i] / k, 76), Math.max(76, r.width - 76));
    setStyle(tip, { left: `${left}px`, top: `${Math.min(...info.points.map((p) => p.y)) / k}px` });
  };
  const clear = () => {
    g.setAttribute('opacity', '0');
    tip.hidden = true;
  };
  const unpin = () => {
    if (pinned === null) return;
    pinned = null;
    pinnedAt.delete(svg);
    svg.classList.remove('pinned');
    clear();
  };
  /** @param {number} i */
  const pin = (i) => {
    pinned = i;
    pinnedAt.set(svg, i);
    svg.classList.add('pinned');
    show(i);
  };
  // desktop: o mouse acompanha; o toque NÃO move o ponto (só o clique/toque o fixa)
  hit.addEventListener('pointermove', (ev) => {
    if (pinned !== null || /** @type {PointerEvent} */ (ev).pointerType !== 'mouse') return;
    show(indexAt(/** @type {PointerEvent} */ (ev).clientX));
  });
  hit.addEventListener('pointerleave', () => { if (pinned === null) clear(); });
  // clique ou toque: fixa o ponto; tocar de novo no mesmo ponto desmarca
  hit.addEventListener('click', (ev) => {
    const i = indexAt(/** @type {MouseEvent} */ (ev).clientX);
    if (pinned === i) unpin(); else pin(i);
  });
  unpinOutside(svg, [svg, tip], unpin);
  const keep = pinnedAt.get(svg);
  if (keep !== undefined && keep < xs.length) pin(keep);
}

/**
 * Caminho suave "M x,y C ..." (curvas de Bézier com o ponto médio como controle, como na referência);
 * `null` quebra a linha.
 * @param {(number | null)[]} vals
 * @param {(i: number) => number} x
 * @param {(v: number) => number} y
 * @returns {string}
 */
function pathOf(vals, x, y) {
  let d = '';
  /** @type {[number, number] | null} */
  let prev = null;
  vals.forEach((v, i) => {
    if (v === null) { prev = null; return; }
    const px = x(i);
    const py = y(v);
    if (prev === null) d += `M${px.toFixed(1)},${py.toFixed(1)} `;
    else {
      const mid = (prev[0] + px) / 2;
      d += `C${mid.toFixed(1)},${prev[1].toFixed(1)} ${mid.toFixed(1)},${py.toFixed(1)} ${px.toFixed(1)},${py.toFixed(1)} `;
    }
    prev = [px, py];
  });
  return d.trim();
}

/**
 * @param {SVGElement} svg
 * @param {string} text
 * @param {number} w
 * @param {number} hgt
 */
function emptyChart(svg, text, w, hgt) {
  svg.replaceChildren(s('text', { x: w / 2, y: hgt / 2, 'text-anchor': 'middle', class: 'chart-empty', text }));
}

/**
 * Gráfico de linha suave: espectadores (eixo esquerdo) e likes (eixo direito).
 * @param {SVGElement} svg
 * @param {{at: number, viewers: number | null, likes: number | null}[]} points
 * @param {{animate: boolean, stepS: number}} opts
 */
export function lineChart(svg, points, opts) {
  const { W, H } = fit(svg, 760, 290, () => lineChart(svg, points, { ...opts, animate: false }));
  const L = 52;
  const T = 12;
  const B = 28;
  /** @type {number[]} */
  const v = [];
  /** @type {(number | null)[]} */
  const l = [];
  let lastV = 0;
  let lastL = null;
  for (const p of points) {
    lastV = typeof p.viewers === 'number' ? p.viewers : lastV;
    v.push(lastV);
    lastL = typeof p.likes === 'number' ? p.likes : lastL;
    l.push(lastL);
  }
  const hasLikes = l.some((q) => q !== null);
  const R = hasLikes ? 52 : 14;
  const iw = W - L - R;
  const ih = H - T - B;
  svg.replaceChildren();
  const n = points.length;
  if (n < 2) {
    emptyChart(svg, 'O gráfico começa na primeira leitura.', W, H);
    return;
  }
  const vMax = niceMax(Math.max(...v) * 1.05);
  const lMax = niceMax(Math.max(...l.map((q) => q || 0), 1) * 1.05);
  const x = (/** @type {number} */ i) => L + (i / (n - 1)) * iw;
  const yv = (/** @type {number} */ q) => T + ih - (q / vMax) * ih;
  const yl = (/** @type {number} */ q) => T + ih - (q / lMax) * ih;
  const anim = opts.animate;

  const grad = s('linearGradient', { id: 'gv', x1: 0, y1: 0, x2: 0, y2: 1 },
    s('stop', { offset: '0', 'stop-color': '#818CF8', 'stop-opacity': 0.35 }),
    s('stop', { offset: '1', 'stop-color': '#818CF8', 'stop-opacity': 0 }));
  svg.appendChild(s('defs', null, grad));
  for (let k = 0; k <= 4; k++) {
    const yy = T + ih - (k / 4) * ih;
    svg.appendChild(s('line', { x1: L, x2: W - R, y1: yy, y2: yy, stroke: GRID, 'stroke-width': 1, 'stroke-dasharray': k === 0 ? '' : '3 4' }));
    svg.appendChild(s('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', text: fmt((vMax * k) / 4) }));
    if (hasLikes) svg.appendChild(s('text', { x: W - R + 8, y: yy + 4, 'text-anchor': 'start', class: 'ax-likes', text: fmt((lMax * k) / 4) }));
  }
  const withSec = opts.stepS < 60;
  for (let k = 0; k <= 5; k++) {
    const i = Math.round((k / 5) * (n - 1));
    svg.appendChild(s('text', {
      x: x(i), y: H - 8, 'text-anchor': k === 0 ? 'start' : k === 5 ? 'end' : 'middle', text: hhmm(points[i].at, withSec),
    }));
  }
  const pv = pathOf(v, x, yv);
  svg.appendChild(s('path', { d: `M${L},${T + ih} L${pv.slice(1)} L${x(n - 1).toFixed(1)},${T + ih} Z`, fill: 'url(#gv)', class: anim ? 'fade-up' : '' }));
  svg.appendChild(s('path', {
    d: pv, fill: 'none', stroke: VIEWS_COLOR, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', pathLength: 1, class: `line-views glow-line${anim ? ' draw' : ''}`,
  }));
  if (hasLikes) {
    svg.appendChild(s('path', {
      d: pathOf(l, x, yl), fill: 'none', stroke: LIKES_COLOR, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', pathLength: 1, class: `line-likes${anim ? ' draw d2' : ''}`,
    }));
  }
  svg.appendChild(s('circle', { cx: x(n - 1), cy: yv(v[n - 1]), r: 4, fill: VIEWS_COLOR, stroke: FACE, 'stroke-width': 2, class: anim ? 'fade-up d3' : '' }));
  const lastLike = l[n - 1];
  if (hasLikes && lastLike !== null) {
    svg.appendChild(s('circle', { cx: x(n - 1), cy: yl(lastLike), r: 4, fill: LIKES_COLOR, stroke: FACE, 'stroke-width': 2, class: anim ? 'fade-up d4' : '' }));
  }
  attachHover(svg, W, H, v.map((_, i) => x(i)), (i) => {
    /** @type {HoverInfo} */
    const info = {
      top: T,
      bottom: T + ih,
      points: [{ y: yv(v[i]), color: '#6366F1' }],
      title: hhmm(points[i].at, true),
      rows: [{ color: VIEWS_COLOR, label: 'Espectadores', value: fmt(v[i]) }],
    };
    const li = l[i];
    if (hasLikes && li !== null) {
      info.points.push({ y: yl(li), color: '#06B6D4' });
      info.rows.push({ color: LIKES_COLOR, label: 'Likes', value: fmt(li) });
    }
    if (i > 0) {
      const d = v[i] - v[i - 1];
      info.rows.push({ color: 'transparent', label: 'Variação', value: `${d >= 0 ? '+' : '−'}${fmt(Math.abs(d))}` });
    }
    return info;
  });
}

/**
 * @typedef {object} DonutLive
 * @property {string} name
 * @property {string} color
 * @property {number} viewers
 */

/**
 * Rosca de participação por live (na cor da live) e a lista ao lado, ligadas pelo destaque.
 * Todos os textos desenhados sobre a rosca têm pointer-events desligado, para o destaque não piscar.
 * @param {SVGElement} svg
 * @param {HTMLElement} list
 * @param {DonutLive[]} lives
 * @param {boolean} animate
 */
export function donutChart(svg, list, lives, animate) {
  svg.replaceChildren();
  list.replaceChildren();
  const total = lives.reduce((a, b) => a + (b.viewers || 0), 0);
  const cx = 110;
  const cy = 110;
  const r = 80;
  const w = 30;
  svg.appendChild(s('circle', { cx, cy, r, fill: 'none', stroke: GRID, 'stroke-width': w }));
  const num = s('text', { x: cx, y: cy - 2, 'text-anchor': 'middle', class: 'donut-center big', text: fmt(total) });
  const lab = s('text', { x: cx, y: cy + 18, 'text-anchor': 'middle', class: 'donut-center small', text: 'espectadores' });
  /** @type {Map<number, SVGElement>} */
  const segs = new Map();
  /** @type {Map<number, HTMLElement>} */
  const rows = new Map();
  /** @param {number | null} idx */
  const focus = (idx) => {
    svg.classList.toggle('focus', idx !== null);
    for (const [j, p] of segs) {
      const on = j === idx;
      p.classList.toggle('on', on);
      p.setAttribute('stroke-width', String(on ? w + 8 : w));
    }
    for (const [j, row] of rows) {
      row.classList.toggle('hl', j === idx);
      if (row.hasAttribute('aria-pressed')) row.setAttribute('aria-pressed', String(j === idx && pinned === idx));
    }
    const lv = idx === null ? null : lives[idx];
    num.textContent = lv ? fmt(lv.viewers) : fmt(total);
    lab.textContent = lv ? `${lv.name.split(' · ')[0]} · ${Math.round((lv.viewers / total) * 100)}%` : 'espectadores';
  };
  /** Fatia fixada pelo toque/clique (null = só o hover do mouse manda). @type {number | null} */
  let pinned = null;
  /** @param {number | null} idx */
  const pin = (idx) => {
    pinned = idx;
    if (idx === null) pinnedAt.delete(svg); else pinnedAt.set(svg, idx);
    svg.classList.toggle('pinned', idx !== null);
    focus(idx);
  };
  /** @param {number} idx @param {PointerEvent} ev hover só do mouse e só sem fixação */
  const hover = (idx, ev) => { if (pinned === null && ev.pointerType === 'mouse') focus(idx); };
  /** @param {PointerEvent} ev */
  const unhover = (ev) => { if (pinned === null && ev.pointerType === 'mouse') focus(null); };
  /** @param {number} idx */
  const toggle = (idx) => pin(pinned === idx ? null : idx);
  noSelect(svg, svg.parentElement || svg, list);
  let a0 = -Math.PI / 2;
  const labels = [];
  lives.forEach((lv, idx) => {
    const frac = total ? (lv.viewers || 0) / total : 0;
    const pct = frac * 100;
    const color = safeColor(lv.color);
    const row = h('div', { class: 'share-row', 'data-live': idx }, h('span', { class: 'dot' }), h('span', { class: 'grow', text: lv.name }), h('span', { class: 'n num', text: `${fmtDec(pct)}%` }));
    setStyle(/** @type {HTMLElement} */ (row.firstElementChild), { background: color });
    list.appendChild(row);
    rows.set(idx, row);
    if (!lv.viewers) return;
    row.setAttribute('role', 'button');
    row.setAttribute('tabindex', '0');
    row.setAttribute('aria-pressed', 'false');
    row.addEventListener('pointerenter', (ev) => hover(idx, /** @type {PointerEvent} */ (ev)));
    row.addEventListener('pointerleave', (ev) => unhover(/** @type {PointerEvent} */ (ev)));
    row.addEventListener('click', () => toggle(idx));
    row.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(idx); }
    });
    const a1 = a0 + frac * Math.PI * 2;
    /** @type {SVGElement} */
    let seg;
    const delay = `${(((a0 + Math.PI / 2) / (Math.PI * 2)) * 0.5).toFixed(2)}s`;
    if (frac > 0.999) {
      seg = s('circle', { cx, cy, r, fill: 'none', stroke: color, 'stroke-width': w, pathLength: 1, class: `seg${animate ? ' draw' : ''}`, 'data-i': idx });
    } else {
      const gap = 0.03;
      const st = a0 + gap / 2;
      const en = a1 - gap / 2;
      seg = s('path', {
        d: `M${cx + r * Math.cos(st)},${cy + r * Math.sin(st)} A${r},${r} 0 ${en - st > Math.PI ? 1 : 0} 1 ${cx + r * Math.cos(en)},${cy + r * Math.sin(en)}`,
        fill: 'none', stroke: color, 'stroke-width': w, pathLength: 1, class: `seg${animate ? ' draw' : ''}`, 'data-i': idx,
      });
    }
    if (animate) setStyle(seg, { 'animation-delay': delay, 'animation-duration': `${Math.max(0.35, frac * 0.9)}s` });
    seg.addEventListener('pointerenter', (ev) => hover(idx, /** @type {PointerEvent} */ (ev)));
    seg.addEventListener('pointerleave', (ev) => unhover(/** @type {PointerEvent} */ (ev)));
    seg.addEventListener('click', () => toggle(idx));
    svg.appendChild(seg);
    segs.set(idx, seg);
    if (frac > 0.07 && frac <= 0.999) {
      const am = (a0 + a1) / 2;
      labels.push(s('text', {
        x: cx + r * Math.cos(am), y: cy + r * Math.sin(am) + 4, 'text-anchor': 'middle', fill: inkOn(color), class: 'donut-label', text: `${Math.round(pct)}%`,
      }));
    }
    a0 = a1;
  });
  // Rótulos por cima das fatias, mas sem capturar o mouse (CSS: .donut-label, .donut-center).
  svg.append(...labels, num, lab);
  unpinOutside(svg, [svg, list], () => pin(null));
  const keep = pinnedAt.get(svg);
  if (keep !== undefined && segs.has(keep)) pin(keep);
}

/**
 * Tempo de resposta (ms) por minuto, na última hora, com faixas nos incidentes.
 * @param {SVGElement} svg
 * @param {{minute: number, lives_avg_ms: number | null, inscritos_avg_ms: number | null, incident: boolean, note: string | null}[]} minutes
 * @param {boolean} animate
 */
export function latencyChart(svg, minutes, animate) {
  const { W, H } = fit(svg, 720, 250, () => latencyChart(svg, minutes, false));
  const L = 50;
  const R = 12;
  const T = 10;
  const B = 26;
  const iw = W - L - R;
  const ih = H - T - B;
  svg.replaceChildren();
  const n = minutes.length;
  if (n < 2) {
    emptyChart(svg, 'Sem leituras na última hora.', W, H);
    return;
  }
  const lives = minutes.map((m) => (typeof m.lives_avg_ms === 'number' ? m.lives_avg_ms : null));
  const subs = minutes.map((m) => (typeof m.inscritos_avg_ms === 'number' ? m.inscritos_avg_ms : null));
  const peak = Math.max(500, ...lives.map((q) => q || 0), ...subs.map((q) => q || 0));
  const max = niceMax(peak * 1.05);
  const x = (/** @type {number} */ i) => L + (i / (n - 1)) * iw;
  const y = (/** @type {number} */ q) => T + ih - (Math.min(q, max) / max) * ih;
  const bw = Math.max(6, iw / n);
  minutes.forEach((m, i) => {
    if (m.incident) svg.appendChild(s('rect', { x: x(i) - bw / 2, y: T, width: bw, height: ih, class: 'incident-band' }));
  });
  for (let k = 0; k <= 4; k++) {
    const q = (max * k) / 4;
    const yy = y(q);
    svg.appendChild(s('line', { x1: L, x2: W - R, y1: yy, y2: yy, stroke: GRID, 'stroke-dasharray': k === 0 ? '' : '3 4' }));
    svg.appendChild(s('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', text: q >= 1000 ? `${fmtDec(q / 1000, 2)} s` : `${Math.round(q)} ms` }));
  }
  [['−60 min', 0, 'start'], ['−45', Math.round((n - 1) * 0.25), 'middle'], ['−30', Math.round((n - 1) * 0.5), 'middle'], ['−15', Math.round((n - 1) * 0.75), 'middle'], ['agora', n - 1, 'end']]
    .forEach(([t, i, a]) => svg.appendChild(s('text', { x: x(/** @type {number} */ (i)), y: H - 6, 'text-anchor': a, text: t })));
  const pl = pathOf(lives, x, y);
  if (pl && !lives.some((q) => q === null)) {
    svg.appendChild(s('defs', null, s('linearGradient', { id: 'gl', x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: '0', 'stop-color': '#818CF8', 'stop-opacity': 0.3 }),
      s('stop', { offset: '1', 'stop-color': '#818CF8', 'stop-opacity': 0 }))));
    svg.appendChild(s('path', { d: `M${x(0).toFixed(1)},${T + ih} L${pl.slice(1)} L${x(n - 1).toFixed(1)},${T + ih} Z`, fill: 'url(#gl)', class: animate ? 'fade-up' : '' }));
  }
  svg.appendChild(s('path', { d: pl, fill: 'none', stroke: LIVES_MS_COLOR, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', pathLength: 1, class: `glow-line${animate ? ' draw' : ''}` }));
  svg.appendChild(s('path', { d: pathOf(subs, x, y), fill: 'none', stroke: SUBS_MS_COLOR, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', pathLength: 1, class: animate ? 'draw d2' : '' }));
  attachHover(svg, W, H, minutes.map((_, i) => x(i)), (i) => {
    const m = minutes[i];
    /** @type {HoverInfo} */
    const info = {
      top: T,
      bottom: T + ih,
      points: [],
      title: `${hhmm(m.minute)} · −${n - 1 - i} min`.replace(' · −0 min', ' · agora'),
      rows: [],
    };
    if (lives[i] !== null) {
      info.points.push({ y: y(/** @type {number} */ (lives[i])), color: '#6366F1' });
      info.rows.push({ color: LIVES_MS_COLOR, label: 'Lives', value: `${fmt(lives[i])} ms` });
    }
    if (subs[i] !== null) {
      info.points.push({ y: y(/** @type {number} */ (subs[i])), color: '#C026D3' });
      info.rows.push({ color: SUBS_MS_COLOR, label: 'Inscritos', value: `${fmt(subs[i])} ms` });
    }
    if (!info.rows.length) info.rows.push({ color: 'transparent', label: 'Sem leitura neste minuto', value: '' });
    if (!info.points.length) info.points.push({ y: T + ih, color: '#475569' });
    if (m.incident) info.note = `Incidente${m.note ? `: ${m.note}` : ''}`;
    return info;
  });
}

/**
 * Minigráfico de linha suave com área.
 * @param {SVGElement} svg viewBox 300x70
 * @param {number[]} vals
 * @param {string} color
 * @param {boolean} animate
 */
export function sparkline(svg, vals, color, animate) {
  const W = 300;
  const H = 70;
  svg.replaceChildren();
  if (vals.length < 2) return;
  const max = Math.max(...vals);
  const min = Math.min(...vals) * 0.96;
  const x = (/** @type {number} */ i) => (i / (vals.length - 1)) * W;
  const y = (/** @type {number} */ q) => H - 4 - ((q - min) / (max - min || 1)) * (H - 10);
  const d = pathOf(vals, x, y);
  svg.appendChild(s('path', { d: `M0,${H} L${d.slice(1)} L${W},${H} Z`, fill: color, opacity: 0.16, class: animate ? 'fade-up' : '' }));
  svg.appendChild(s('path', {
    d, fill: 'none', stroke: color, 'stroke-width': 2.2, 'stroke-linecap': 'round', pathLength: 1, class: `glow-line${animate ? ' draw' : ''}`,
  }));
}

/** Raio e circunferência do anel (viewBox 50): stroke-dasharray = 2πr. */
const RING_R = 20;
export const RING_C = 2 * Math.PI * RING_R;

/**
 * Anel de progresso (estilo da referência): trilho + arco com stroke-dasharray = 2πr e stroke-dashoffset indo de
 * 2πr até 2πr·(1 − pct/100) em 1,2 s. O número (texto puro) fica no centro.
 * @param {{pct: number | null, color: string, center: string, size?: number, label?: string, animate: boolean}} o
 * @returns {{root: HTMLElement, set: (pct: number | null, color: string, center: string) => void}}
 */
export function ring(o) {
  const prog = s('circle', {
    class: 'ring-prog', cx: 25, cy: 25, r: RING_R, fill: 'none', 'stroke-width': 4, 'stroke-linecap': 'round', transform: 'rotate(-90 25 25)',
    'stroke-dasharray': RING_C.toFixed(3),
  });
  const svg = s('svg', { viewBox: '0 0 50 50', 'aria-hidden': 'true', class: 'ring-svg' },
    s('circle', { class: 'ring-track', cx: 25, cy: 25, r: RING_R, fill: 'none', 'stroke-width': 4 }), prog);
  const num = h('span', { class: 'ring-n num', 'data-static': '' });
  const root = h('div', { class: 'ring', role: 'img' }, svg, num);
  if (o.size) setStyle(root, { '--ring': `${o.size}px` });
  let first = o.animate;
  /** @param {number | null} pct @param {string} color @param {string} center */
  const set = (pct, color, center) => {
    const p = pct === null ? 0 : Math.max(0, Math.min(100, pct));
    const off = RING_C * (1 - p / 100);
    prog.setAttribute('stroke', color);
    prog.setAttribute('stroke-dashoffset', off.toFixed(3));
    setStyle(prog, { '--c': RING_C.toFixed(3), 'stroke-dashoffset': off.toFixed(3) });
    if (first) { prog.classList.add('ring-in'); first = false; }
    num.textContent = center;
    root.setAttribute('aria-label', `${o.label || 'Progresso'}: ${center}`);
    root.dataset.pct = p.toFixed(1);
  };
  set(o.pct, o.color, o.center);
  return { root, set };
}
