// @ts-check
// D59: "Exportar PNG" do gráfico do conjunto. Pop-up com De/Até, resumo e download; o PNG (1600x800) é desenhado no navegador
// num <canvas>, no mesmo estilo do gráfico do painel. Os dados vêm da mesma rota do gráfico (`range=sessao` e `from`/`to`).

import { api } from './api.js';
import { byId, inputById, buttonById, showMessage, clearMessage } from './dom.js';
import { toast } from './ui.js';
import { fmt, niceMax } from './util.js';

const W = 1600;
const H = 800;
const BG = '#0B0D15';
const VIEWS = '#A5B4FC';
const LIKES = '#67E8F9';
const GRID = 'rgba(255,255,255,0.07)';
const INK = '#E8EAF2';
const MUTED = '#8F96B2';
const FONT = '"DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif';

/** @typedef {{at: number, viewers: number | null, likes: number | null}} Pt */

/** @type {{set: any, token: number, retention: number, sessionFrom: number | null, sessionTo: number | null, points: Pt[], summary: any, from: number, to: number, wired: boolean}} */
const st = { set: null, token: 0, retention: 14, sessionFrom: null, sessionTo: null, points: [], summary: null, from: 0, to: 0, wired: false };

/** @param {number} n */
const two = (n) => String(n).padStart(2, '0');

/** @param {number} epoch @returns {string} AAAA-MM-DDTHH:MM no fuso do navegador (valor de datetime-local) */
export function toLocalInput(epoch) {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** @param {string} v @returns {number} epoch em segundos, NaN se vazio ou inválido */
export function fromLocalInput(v) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v)) return NaN;
  return Math.floor(new Date(v).getTime() / 1000);
}

/** @param {number} epoch */
function fullDate(epoch) {
  const d = new Date(epoch * 1000);
  return `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** @param {number} s */
function duration(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h} h ${two(m)} min` : `${m} min`;
}

/** @param {string} name */
function slug(name) {
  const s = name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return s.slice(0, 40) || 'conjunto';
}

/** @param {number} epoch */
function stamp(epoch) {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}`;
}

const dlg = () => /** @type {HTMLDialogElement} */ (byId('dlgExport'));

/** @param {string} text */
function setSummary(text) {
  byId('expSummary').textContent = text;
}

/** @param {string} msg */
function fail(msg) {
  st.points = [];
  st.summary = null;
  buttonById('expSave').disabled = true;
  setSummary('');
  showMessage(byId('expErr'), msg);
}

async function refresh() {
  const token = ++st.token;
  clearMessage(byId('expErr'));
  const a = fromLocalInput(inputById('expFrom').value);
  const b = fromLocalInput(inputById('expTo').value);
  if (Number.isNaN(a) || Number.isNaN(b)) { fail('Informe a data e a hora de início e de fim.'); return; }
  if (b <= a) { fail('O fim precisa ser depois do início.'); return; }
  if (b - a > st.retention * 86400) { fail(`O período máximo é de ${st.retention} dias (retenção do histórico).`); return; }
  buttonById('expSave').disabled = true;
  setSummary('Carregando…');
  const resp = await api('GET', `/api/admin/sets/${st.set.id}/history?from=${a}&to=${b}`);
  if (token !== st.token) return;
  if (!resp.ok || !resp.data || !Array.isArray(resp.data.points)) {
    fail(resp.status === 422 ? 'Período inválido para o histórico.' : 'Não foi possível carregar o histórico. Tente de novo.');
    return;
  }
  const pts = resp.data.points;
  if (pts.length < 2 || !resp.data.summary) { fail('Não há dados gravados neste período.'); return; }
  st.points = pts;
  st.summary = resp.data.summary;
  st.from = a;
  st.to = b;
  setSummary(`${fmt(st.summary.samples)} pontos · duração ${duration(st.summary.duration_s)} · pico de ${fmt(st.summary.peak_viewers)} espectadores`);
  buttonById('expSave').disabled = false;
}

/** @param {number} a @param {number} b */
function setPeriod(a, b) {
  inputById('expFrom').value = toLocalInput(a);
  inputById('expTo').value = toLocalInput(b);
  void refresh();
}

function wire() {
  if (st.wired) return;
  st.wired = true;
  inputById('expFrom').addEventListener('change', () => void refresh());
  inputById('expTo').addEventListener('change', () => void refresh());
  byId('expSession').addEventListener('click', () => {
    if (st.sessionFrom !== null && st.sessionTo !== null) setPeriod(st.sessionFrom, st.sessionTo);
  });
  byId('expHour').addEventListener('click', () => {
    const end = st.sessionTo ?? Math.floor(Date.now() / 1000);
    setPeriod(end - 3600, end);
  });
  byId('exportForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    void download();
  });
}

/** @param {any} set o conjunto cujo gráfico será exportado */
export async function openExportPng(set) {
  wire();
  st.set = set;
  st.points = [];
  st.summary = null;
  st.sessionFrom = null;
  st.sessionTo = null;
  clearMessage(byId('expErr'));
  buttonById('expSave').disabled = true;
  inputById('expFrom').value = '';
  inputById('expTo').value = '';
  setSummary('Carregando…');
  byId('dlgExportT').textContent = `Exportar gráfico de ${set.name}`;
  dlg().showModal();
  const token = ++st.token;
  const resp = await api('GET', `/api/admin/sets/${set.id}/history?range=sessao`);
  if (token !== st.token) return;
  if (!resp.ok || !resp.data) { fail('Não foi possível carregar o histórico. Tente de novo.'); return; }
  st.retention = typeof resp.data.retention_days === 'number' ? resp.data.retention_days : 14;
  if (!resp.data.points.length || resp.data.from === null) { fail('Ainda não há dados gravados deste conjunto.'); return; }
  st.sessionFrom = Math.floor(resp.data.from / 60) * 60;
  st.sessionTo = Math.ceil(resp.data.to / 60) * 60;
  setPeriod(st.sessionFrom, st.sessionTo);
}

/**
 * Desenha o gráfico (mesmo estilo do painel) num canvas de 1600x800.
 * @param {HTMLCanvasElement} canvas
 * @param {Pt[]} points
 * @param {{title: string, period: string, peak: number}} info
 */
export function drawChart(canvas, points, info) {
  canvas.width = W;
  canvas.height = H;
  const c = /** @type {CanvasRenderingContext2D} */ (canvas.getContext('2d'));
  c.fillStyle = BG;
  c.fillRect(0, 0, W, H);
  const L = 110;
  const R = 110;
  const T = 150;
  const B = 100;
  const iw = W - L - R;
  const ih = H - T - B;
  c.textBaseline = 'alphabetic';
  c.fillStyle = INK;
  c.font = `700 40px ${FONT}`;
  c.textAlign = 'left';
  c.fillText(info.title, L - 50, 62);
  c.fillStyle = MUTED;
  c.font = `400 24px ${FONT}`;
  c.fillText(info.period, L - 50, 102);
  // legenda
  c.textAlign = 'right';
  c.font = `500 24px ${FONT}`;
  c.fillStyle = INK;
  c.fillText('Likes', W - 60, 62);
  const wl = c.measureText('Likes').width;
  c.fillStyle = LIKES;
  c.beginPath(); c.arc(W - 60 - wl - 18, 54, 8, 0, Math.PI * 2); c.fill();
  c.fillStyle = INK;
  c.fillText('Espectadores', W - 60 - wl - 52, 62);
  const wv = c.measureText('Espectadores').width;
  c.fillStyle = VIEWS;
  c.beginPath(); c.arc(W - 60 - wl - 52 - wv - 18, 54, 8, 0, Math.PI * 2); c.fill();

  /** @type {number[]} */
  const v = [];
  /** @type {(number | null)[]} */
  const l = [];
  let lastV = 0;
  /** @type {number | null} */
  let lastL = null;
  for (const p of points) {
    lastV = typeof p.viewers === 'number' ? p.viewers : lastV;
    v.push(lastV);
    lastL = typeof p.likes === 'number' ? p.likes : lastL;
    l.push(lastL);
  }
  const hasLikes = l.some((q) => q !== null);
  const n = points.length;
  const vMax = niceMax(Math.max(...v) * 1.05);
  const lMax = niceMax(Math.max(...l.map((q) => q || 0), 1) * 1.05);
  const x = (/** @type {number} */ i) => L + (i / (n - 1)) * iw;
  const yv = (/** @type {number} */ q) => T + ih - (q / vMax) * ih;
  const yl = (/** @type {number} */ q) => T + ih - (q / lMax) * ih;

  c.font = `400 21px ${FONT}`;
  for (let k = 0; k <= 4; k++) {
    const yy = T + ih - (k / 4) * ih;
    c.strokeStyle = GRID;
    c.lineWidth = 1.5;
    c.setLineDash(k === 0 ? [] : [6, 8]);
    c.beginPath(); c.moveTo(L, yy); c.lineTo(W - R, yy); c.stroke();
    c.setLineDash([]);
    c.fillStyle = MUTED;
    c.textAlign = 'right';
    c.fillText(fmt((vMax * k) / 4), L - 14, yy + 7);
    if (hasLikes) {
      c.fillStyle = LIKES;
      c.textAlign = 'left';
      c.fillText(fmt((lMax * k) / 4), W - R + 14, yy + 7);
    }
  }
  const span = points[n - 1].at - points[0].at;
  c.fillStyle = MUTED;
  for (let k = 0; k <= 5; k++) {
    const i = Math.round((k / 5) * (n - 1));
    const d = new Date(points[i].at * 1000);
    const hm = `${two(d.getHours())}:${two(d.getMinutes())}`;
    c.textAlign = k === 0 ? 'left' : k === 5 ? 'right' : 'center';
    c.fillText(span > 20 * 3600 ? `${two(d.getDate())}/${two(d.getMonth() + 1)} ${hm}` : hm, x(i), H - B + 42);
  }

  /** @param {(number | null)[]} vals @param {(q: number) => number} y */
  const trace = (vals, y) => {
    let prev = /** @type {[number, number] | null} */ (null);
    c.beginPath();
    vals.forEach((q, i) => {
      if (q === null) { prev = null; return; }
      const px = x(i);
      const py = y(q);
      if (prev === null) c.moveTo(px, py);
      else {
        const mid = (prev[0] + px) / 2;
        c.bezierCurveTo(mid, prev[1], mid, py, px, py);
      }
      prev = [px, py];
    });
  };
  trace(v, yv);
  c.save();
  c.lineTo(x(n - 1), T + ih);
  c.lineTo(L, T + ih);
  c.closePath();
  const g = c.createLinearGradient(0, T, 0, T + ih);
  g.addColorStop(0, 'rgba(129,140,248,0.35)');
  g.addColorStop(1, 'rgba(129,140,248,0)');
  c.fillStyle = g;
  c.fill();
  c.restore();
  c.lineJoin = 'round';
  c.lineCap = 'round';
  c.strokeStyle = VIEWS;
  c.lineWidth = 5;
  trace(v, yv);
  c.stroke();
  if (hasLikes) {
    c.strokeStyle = LIKES;
    c.lineWidth = 4;
    trace(l, yl);
    c.stroke();
  }
  // rodapé
  c.textAlign = 'left';
  c.fillStyle = MUTED;
  c.font = `700 24px ${FONT}`;
  c.fillText('LiveStats', L - 50, H - 28);
  c.textAlign = 'right';
  c.font = `400 22px ${FONT}`;
  c.fillText(`Pico: ${fmt(info.peak)} espectadores`, W - 60, H - 28);
}

async function download() {
  if (!st.points.length || !st.summary) return;
  buttonById('expSave').disabled = true;
  try {
    try { await document.fonts.load('700 24px "DM Sans"'); await document.fonts.load('400 24px "DM Sans"'); } catch { /* usa a fonte do sistema */ }
    const canvas = document.createElement('canvas');
    drawChart(canvas, st.points, {
      title: `Conjunto ${st.set.name}`,
      period: `${fullDate(st.from)} até ${fullDate(st.to)}`,
      peak: st.summary.peak_viewers,
    });
    const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
    if (!blob) { showMessage(byId('expErr'), 'Não foi possível gerar a imagem neste navegador.'); return; }
    const url = URL.createObjectURL(/** @type {Blob} */ (blob));
    const a = document.createElement('a');
    a.href = url;
    a.download = `conjunto-${slug(st.set.name)}-${stamp(st.from)}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast('PNG gerado');
  } finally {
    buttonById('expSave').disabled = false;
  }
}
