// @ts-check
// Gráficos em SVG puro (atributos SVG e classes; nenhum atributo `style`, nenhum HTML vindo de dado):
// linha com dois eixos, rosca, tempo de resposta da saúde e minigráfico. As dicas ao passar o mouse
// são montadas com nós de texto. As animações são classes CSS; com prefers-reduced-motion ficam desligadas.

import { h, s, setStyle } from './h.js';
import { fmt, fmtDec, hhmm, inkOn, niceMax, safeColor } from './util.js';

const VIEWS_COLOR = '#8F939C';
const LIKES_COLOR = '#F2F3F5';
const LIVES_MS_COLOR = '#A3A6AE';
const SUBS_MS_COLOR = '#FF6B6B';
const GRID = '#24262D';

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
  hit.addEventListener('pointermove', (ev) => {
    const r = svg.getBoundingClientRect();
    if (!r.width || !xs.length) return;
    const k = width / r.width;
    const vx = ((/** @type {PointerEvent} */ (ev)).clientX - r.left) * k;
    let i = 0;
    let best = Infinity;
    xs.forEach((x, j) => {
      const d = Math.abs(x - vx);
      if (d < best) { best = d; i = j; }
    });
    const info = render(i);
    g.replaceChildren(s('line', { x1: xs[i], x2: xs[i], y1: info.top, y2: info.bottom, class: 'hover-line' }));
    for (const p of info.points) {
      g.appendChild(s('circle', { cx: xs[i], cy: p.y, r: 4.5, fill: p.color, stroke: '#131418', 'stroke-width': 2 }));
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
    const left = Math.min(Math.max(xs[i] / k, 90), Math.max(90, r.width - 90));
    setStyle(tip, { left: `${left}px`, top: `${Math.min(...info.points.map((p) => p.y)) / k}px` });
  });
  hit.addEventListener('pointerleave', () => {
    g.setAttribute('opacity', '0');
    tip.hidden = true;
  });
}

/**
 * Caminho "M x,y L x,y ..." a partir de pontos (null quebra a linha).
 * @param {(number | null)[]} vals
 * @param {(i: number) => number} x
 * @param {(v: number) => number} y
 * @returns {string}
 */
function pathOf(vals, x, y) {
  let d = '';
  let pen = false;
  vals.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
    pen = true;
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
 * Gráfico de linha: espectadores (eixo esquerdo) e likes (eixo direito).
 * @param {SVGElement} svg
 * @param {{at: number, viewers: number | null, likes: number | null}[]} points
 * @param {{animate: boolean, stepS: number}} opts
 */
export function lineChart(svg, points, opts) {
  const W = 760;
  const H = 290;
  const L = 56;
  const R = 56;
  const T = 12;
  const B = 30;
  const iw = W - L - R;
  const ih = H - T - B;
  svg.replaceChildren();
  const n = points.length;
  if (n < 2) {
    emptyChart(svg, 'O gráfico começa na primeira leitura.', W, H);
    return;
  }
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
  const vMax = niceMax(Math.max(...v) * 1.05);
  const lMax = niceMax(Math.max(...l.map((q) => q || 0), 1) * 1.05);
  const x = (/** @type {number} */ i) => L + (i / (n - 1)) * iw;
  const yv = (/** @type {number} */ q) => T + ih - (q / vMax) * ih;
  const yl = (/** @type {number} */ q) => T + ih - (q / lMax) * ih;
  const anim = opts.animate;

  const grad = s('linearGradient', { id: 'gv', x1: 0, y1: 0, x2: 0, y2: 1 },
    s('stop', { offset: '0', 'stop-color': VIEWS_COLOR, 'stop-opacity': 0.35 }),
    s('stop', { offset: '1', 'stop-color': VIEWS_COLOR, 'stop-opacity': 0 }));
  svg.appendChild(s('defs', null, grad));
  for (let k = 0; k <= 4; k++) {
    const yy = T + ih - (k / 4) * ih;
    svg.appendChild(s('line', { x1: L, x2: W - R, y1: yy, y2: yy, stroke: GRID, 'stroke-width': 1 }));
    svg.appendChild(s('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', text: fmt((vMax * k) / 4) }));
    if (hasLikes) svg.appendChild(s('text', { x: W - R + 8, y: yy + 4, 'text-anchor': 'start', text: fmt((lMax * k) / 4) }));
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
    d: pv, fill: 'none', stroke: VIEWS_COLOR, 'stroke-width': 1.8, 'stroke-linejoin': 'round', pathLength: 1, class: `line-views${anim ? ' draw' : ''}`,
  }));
  if (hasLikes) {
    const lp = s('path', {
      d: pathOf(l, x, yl), fill: 'none', stroke: LIKES_COLOR, 'stroke-width': 2, 'stroke-linejoin': 'round', pathLength: 1, class: `line-likes${anim ? ' draw d2' : ''}`,
    });
    svg.appendChild(lp);
  }
  svg.appendChild(s('circle', { cx: x(n - 1), cy: yv(v[n - 1]), r: 4, fill: VIEWS_COLOR, stroke: '#131418', 'stroke-width': 2, class: anim ? 'fade-up d3' : '' }));
  const lastLike = l[n - 1];
  if (hasLikes && lastLike !== null) {
    svg.appendChild(s('circle', { cx: x(n - 1), cy: yl(lastLike), r: 4, fill: LIKES_COLOR, stroke: '#131418', 'stroke-width': 2, class: anim ? 'fade-up d4' : '' }));
  }
  attachHover(svg, W, H, v.map((_, i) => x(i)), (i) => {
    /** @type {HoverInfo} */
    const info = {
      top: T,
      bottom: T + ih,
      points: [{ y: yv(v[i]), color: VIEWS_COLOR }],
      title: hhmm(points[i].at, true),
      rows: [{ color: VIEWS_COLOR, label: 'Espectadores', value: fmt(v[i]) }],
    };
    const li = l[i];
    if (hasLikes && li !== null) {
      info.points.push({ y: yl(li), color: LIKES_COLOR });
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
    for (const [j, row] of rows) row.classList.toggle('hl', j === idx);
    const lv = idx === null ? null : lives[idx];
    num.textContent = lv ? fmt(lv.viewers) : fmt(total);
    lab.textContent = lv ? `${lv.name.split(' · ')[0]} · ${Math.round((lv.viewers / total) * 100)}%` : 'espectadores';
  };
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
    row.addEventListener('pointerenter', () => focus(idx));
    row.addEventListener('pointerleave', () => focus(null));
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
    seg.addEventListener('pointerenter', () => focus(idx));
    seg.addEventListener('pointerleave', () => focus(null));
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
}

/**
 * Tempo de resposta (ms) por minuto, na última hora, com faixas nos incidentes.
 * @param {SVGElement} svg
 * @param {{minute: number, lives_avg_ms: number | null, inscritos_avg_ms: number | null, incident: boolean, note: string | null}[]} minutes
 * @param {boolean} animate
 */
export function latencyChart(svg, minutes, animate) {
  const W = 720;
  const H = 250;
  const L = 50;
  const R = 10;
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
    svg.appendChild(s('line', { x1: L, x2: W - R, y1: yy, y2: yy, stroke: GRID }));
    svg.appendChild(s('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', text: q >= 1000 ? `${fmtDec(q / 1000, 2)} s` : `${Math.round(q)} ms` }));
  }
  [['−60 min', 0, 'start'], ['−45', Math.round((n - 1) * 0.25), 'middle'], ['−30', Math.round((n - 1) * 0.5), 'middle'], ['−15', Math.round((n - 1) * 0.75), 'middle'], ['agora', n - 1, 'end']]
    .forEach(([t, i, a]) => svg.appendChild(s('text', { x: x(/** @type {number} */ (i)), y: H - 6, 'text-anchor': a, text: t })));
  svg.appendChild(s('path', { d: pathOf(lives, x, y), fill: 'none', stroke: LIVES_MS_COLOR, 'stroke-width': 1.8, 'stroke-linejoin': 'round', pathLength: 1, class: animate ? 'draw' : '' }));
  svg.appendChild(s('path', { d: pathOf(subs, x, y), fill: 'none', stroke: SUBS_MS_COLOR, 'stroke-width': 1.6, 'stroke-linejoin': 'round', pathLength: 1, class: animate ? 'draw d2' : '' }));
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
      info.points.push({ y: y(/** @type {number} */ (lives[i])), color: LIVES_MS_COLOR });
      info.rows.push({ color: LIVES_MS_COLOR, label: 'Lives', value: `${fmt(lives[i])} ms` });
    }
    if (subs[i] !== null) {
      info.points.push({ y: y(/** @type {number} */ (subs[i])), color: SUBS_MS_COLOR });
      info.rows.push({ color: SUBS_MS_COLOR, label: 'Inscritos', value: `${fmt(subs[i])} ms` });
    }
    if (!info.rows.length) info.rows.push({ color: 'transparent', label: 'Sem leitura neste minuto', value: '' });
    if (!info.points.length) info.points.push({ y: T + ih, color: GRID });
    if (m.incident) info.note = `Incidente${m.note ? `: ${m.note}` : ''}`;
    return info;
  });
}

/**
 * Minigráfico de linha com área.
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
  svg.appendChild(s('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.8, pathLength: 1, class: animate ? 'draw' : '' }));
}
