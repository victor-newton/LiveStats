// @ts-check
// Tela Saúde: 4 serviços, gráfico da última hora (com incidentes) e eventos ao lado, e a leitura de cada live
// em lista (sem rolagem lateral). Dados: /api/admin/health/detail + /api/admin/workers/status, a cada 5 s.

import { api } from './api.js';
import { byId } from './dom.js';
import { h, s, setStyle } from './h.js';
import { latencyChart } from './charts.js';
import { levelPill, statusPill, platBadge } from './ui.js';
import { eventLog } from './parts.js';
import { state, services } from './store.js';
import {
  DASH, fmt, relTime, safeColor, setText, toEpoch,
} from './util.js';

const ERROR_TEXTS = {
  blocked: 'bloqueado pela plataforma',
  parse_error: 'página em formato inesperado',
  not_found: 'live não encontrada',
  http_error: 'erro HTTP',
  network_error: 'falha de rede',
  rate_limited: 'limite de requisições',
  invalid_response: 'resposta inválida',
};
const SLOW_MS = 5000;

/** @type {Record<string, any>} */
const R = {};
let svcSig = '';
let chartSig = '';
let readsSig = '';
let logSig = '';

/** Monta a estrutura fixa da tela. */
export function initSaude() {
  R.overall = h('span', { class: 'head-pill' });
  R.svcs = h('div', { class: 'svc-grid', id: 'svcGrid' });
  R.svg = s('svg', { class: 'chart lat-chart', id: 'latChart', viewBox: '0 0 720 250', role: 'img', 'aria-label': 'Tempo de resposta na última hora' });
  R.tip = h('div', { class: 'tip', hidden: true });
  R.log = h('div', { id: 'logSaude' });
  R.reads = h('div', { class: 'reads', id: 'readList' });
  byId('scr-saude').replaceChildren(
    h('div', { class: 'head' },
      h('div', null, h('h1', { text: 'Saúde' }), h('p', { text: 'Estado de cada serviço, de cada leitura de live e da sessão do YouTube Studio. Atualiza sozinha a cada 5 s.' })),
      h('div', { class: 'actions' }, R.overall)),
    R.svcs,
    h('div', { class: 'health-top' },
      h('div', { class: 'card' },
        h('div', { class: 'card-h' },
          h('div', null, h('h2', { text: 'Última hora' }), h('p', { text: 'Tempo médio de resposta das leituras, por minuto. As faixas marcam os minutos com problema.' })),
          h('div', { class: 'legend' },
            h('span', null, h('i', { class: 'lg lives' }), 'Lives'),
            h('span', null, h('i', { class: 'lg subs' }), 'Inscritos'),
            h('span', null, h('i', { class: 'lg incident' }), 'Incidente'))),
        h('div', { class: 'chart-box' }, R.svg, R.tip)),
      h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Eventos' }), h('p', { text: 'Erros, renovações e mudanças de estado.' }))), R.log)),
    h('div', { class: 'card' },
      h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Leitura de cada live' }), h('p', { text: 'Estado, tempo de resposta da página e quando cada live foi e será lida.' }))),
      R.reads));
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetSaude() {
  svcSig = '';
  chartSig = '';
  readsSig = '';
  logSig = '';
  if (R.svcs) {
    R.svcs.replaceChildren();
    R.svg.replaceChildren();
    R.log.replaceChildren();
    R.reads.replaceChildren();
    R.overall.replaceChildren();
    R.tip.hidden = true;
  }
}

/** @returns {number} ritmo desta tela (ms) */
export const REFRESH_MS = SLOW_MS;

/** Detalhe da saúde. @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const t0 = performance.now();
  const resp = await api('GET', '/api/admin/health/detail');
  if (resp.ok && resp.data) {
    state.health = resp.data;
    state.apiMs = Math.round(performance.now() - t0);
  }
  return [resp];
}

/**
 * @param {string} key
 * @returns {Record<string, any>}
 */
function worker(key) {
  return state.status && state.status.workers ? state.status.workers[key] || {} : {};
}

/** @param {any} w @returns {string} */
const cycle = (w) => (typeof w.interval_seconds === 'number' ? `${w.interval_seconds} s` : DASH);
/** @param {any} w @returns {string} */
const fails = (w) => (typeof w.consecutive_failures === 'number' ? String(w.consecutive_failures) : DASH);

/**
 * Cartões dos 4 serviços.
 * @param {boolean} animate
 * @returns {HTMLElement[]}
 */
function serviceCards(animate) {
  const list = services();
  const reads = state.health && Array.isArray(state.health.reads) ? state.health.reads : [];
  const minutes = state.health && Array.isArray(state.health.minutes) ? state.health.minutes : [];
  const lastLat = [...minutes].reverse().find((m) => typeof m.lives_avg_ms === 'number');
  const okReads = reads.filter((/** @type {any} */ r) => !r.error_code && r.status !== 'erro').length;
  const lw = worker('lives');
  const iw = worker('inscritos');
  const ck = state.status && state.status.cookies ? state.status.cookies : {};
  const ch = state.settings && state.settings.channel;
  const ev = state.health && Array.isArray(state.health.events) ? state.health.events : [];
  const lastOk = toEpoch(iw.last_success_at);
  const created = toEpoch(ck.created_at);
  const created_txt = created === null ? DASH : new Date(created * 1000).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  /** @type {Record<string, {big: string, count: string, dl: [string, string][]}>} */
  const info = {
    lives: {
      big: reads.length ? `${okReads} / ${reads.length}` : DASH,
      count: 'lives lidas no último ciclo',
      dl: [['Ciclo', cycle(lw)], ['Tempo médio', lastLat ? `${fmt(lastLat.lives_avg_ms)} ms` : DASH], ['Falhas seguidas', fails(lw)]],
    },
    inscritos: {
      big: relTime(lastOk),
      count: 'última leitura no Studio',
      dl: [['Ciclo', cycle(iw)], ['Canal', ch && ch.id ? 'detectado' : 'não detectado'], ['Falhas seguidas', fails(iw)]],
    },
    sessao: {
      big: ck.configured ? `${ck.count} cookies` : 'sem cookies',
      count: ck.source === 'worker' ? 'renovados pelo worker' : ck.source === 'admin' ? 'enviados pelo painel' : 'nenhum conjunto salvo',
      dl: [['Origem', ck.source === 'worker' ? 'renovação automática' : ck.source === 'admin' ? 'painel' : DASH], ['Desde', created_txt], ['Aplicados', ck.applied ? 'sim' : 'não']],
    },
    api: {
      big: state.apiOk ? 'no ar' : 'sem resposta',
      count: 'respondendo ao painel',
      dl: [['Resposta', typeof state.apiMs === 'number' ? `${fmt(state.apiMs)} ms` : DASH], ['Eventos guardados', fmt(ev.length)], ['Lives monitoradas', fmt(reads.length)]],
    },
  };
  return list.map((sv) => {
    const i = info[sv.key];
    const big = h('div', { class: 'big num' });
    if (sv.key === 'api') big.textContent = i.big; else setText(big, i.big, animate);
    return h('div', { class: 'card svc', 'data-svc': sv.key },
      h('div', { class: 'top-line' }, h('h3', { text: sv.name }), levelPill(sv.level, sv.label)),
      big,
      h('div', { class: 'count', text: i.count }),
      h('dl', null, ...i.dl.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { class: 'num', 'data-static': '', text: v })])));
  });
}

/**
 * Lista de leituras por live (cada linha cabe na largura: nada de tabela larga).
 * @param {any[]} reads
 * @param {boolean} animate
 * @returns {HTMLElement[]}
 */
function readRows(reads, animate) {
  if (!reads.length) return [h('p', { class: 'empty', text: 'Nenhuma live sendo lida.' })];
  return reads.map((r) => {
    const ms = typeof r.latency_ms === 'number' ? r.latency_ms : null;
    const err = typeof r.error_code === 'string' && r.error_code ? r.error_code : null;
    const fill = h('i');
    fill.style.width = `${ms === null ? 0 : Math.min(100, ms / 50)}%`;
    setStyle(fill, { background: err || (ms !== null && ms >= 5000) ? 'var(--warn)' : 'var(--ink-2)' });
    const msText = err ? (/** @type {Record<string, string>} */ (ERROR_TEXTS)[err] || err) : ms === null ? DASH : ms >= 5000 ? 'sem resposta em 5 s' : `${fmt(ms)} ms`;
    const last = toEpoch(r.last_read_at);
    const next = toEpoch(r.next_read_at);
    const dot = h('span', { class: 'dot' });
    setStyle(dot, { background: safeColor(r.color) });
    const msNode = h('span', { class: 'num ms' });
    if (ms !== null && !err && ms < 5000) setText(msNode, msText, animate); else msNode.textContent = msText;
    return h('div', { class: 'read-row', 'data-live': r.live_id },
      platBadge(r.platform),
      h('div', { class: 'who' }, h('strong', null, dot, h('span', { class: 'who-name', text: String(r.display_name) })), h('small', { text: String(r.set_name) })),
      statusPill(err ? 'erro' : r.status),
      h('div', { class: 'lat-cell' }, h('span', { class: 'bar' }, fill), msNode),
      h('div', { class: 'when num' }, h('b', { text: relTime(last) }), `próxima ${relTime(next, 'future')}`));
  });
}

/** @param {boolean} first */
export function render(first) {
  const list = services();
  const worst = list.some((x) => x.level === 'bad') ? 'bad' : list.some((x) => x.level !== 'ok') ? 'warn' : 'ok';
  R.overall.replaceChildren(levelPill(worst, worst === 'ok' ? 'Tudo funcionando' : worst === 'warn' ? 'Funcionando com avisos' : 'Há falhas'));

  const health = state.health || { minutes: [], reads: [], events: [] };
  const ssig = JSON.stringify([list, health.reads, state.status && state.status.workers, state.status && state.status.cookies, state.apiMs && Math.floor(state.apiMs / 50)]);
  if (first || ssig !== svcSig) {
    svcSig = ssig;
    R.svcs.replaceChildren(...serviceCards(first));
  }
  const csig = JSON.stringify(health.minutes);
  if (first || csig !== chartSig) {
    chartSig = csig;
    latencyChart(R.svg, Array.isArray(health.minutes) ? health.minutes : [], first);
  }
  const lsig = JSON.stringify(health.events);
  if (first || lsig !== logSig) {
    logSig = lsig;
    R.log.replaceChildren(eventLog(health.events, 30));
  }
  const rsig = JSON.stringify(health.reads) + Math.floor(Date.now() / 5000);
  if (first || rsig !== readsSig) {
    readsSig = rsig;
    R.reads.replaceChildren(...readRows(Array.isArray(health.reads) ? health.reads : [], first));
  }
}
