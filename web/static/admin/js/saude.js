// @ts-check
// Tela Saúde: 4 serviços, gráfico da última hora (com incidentes) e eventos ao lado, e a leitura de cada live
// em lista (sem rolagem lateral). Dados: /api/admin/health/detail + /api/admin/workers/status, a cada 5 s.

import { api } from './api.js';
import { byId } from './dom.js';
import { h, s, setStyle } from './h.js';
import { latencyChart, ring } from './charts.js';
import { levelPill, statusPill, platBadge } from './ui.js';
import { mountLog, resetLog, terminalCard } from './parts.js';
import { state, services } from './store.js';
import {
  DASH, fmt, relTime, safeColor, setText, toEpoch,
} from './util.js';
import { infoTip, titleTip } from './infotip.js';

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
const RING_OK = '#34D399';
const RING_WARN = '#FBBF24';
const RING_BAD = '#FB7185';

/**
 * Percentual de minutos sem incidente entre os minutos com atividade (leitura ou incidente), de `health/detail`.
 * @param {any[]} minutes
 * @param {string} key campo de latência do serviço
 * @returns {number | null}
 */
export function okPct(minutes, key) {
  let seen = 0;
  let bad = 0;
  for (const m of minutes) {
    if (typeof m[key] !== 'number' && !m.incident) continue;
    seen += 1;
    if (m.incident) bad += 1;
  }
  return seen ? Math.round(((seen - bad) / seen) * 100) : null;
}

/** @param {number | null} pct @returns {string} */
const ringColor = (pct) => (pct === null ? RING_WARN : pct >= 95 ? RING_OK : pct >= 80 ? RING_WARN : RING_BAD);

/**
 * Bytes em MB (abaixo de 1 GB) ou GB, no formato pt-BR (vírgula decimal).
 * @param {number | null | undefined} n
 * @returns {string}
 */
export function fmtBytes(n) {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return DASH;
  const MB = 1024 * 1024;
  if (n >= 1024 * MB) return `${(n / (1024 * MB)).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} GB`;
  return `${Math.round(n / MB).toLocaleString('pt-BR')} MB`;
}

/** @type {Record<string, any>} */
const R = {};
let svcSig = '';
let memSig = '';
let chartSig = '';
let readsSig = '';
let logSig = '';

/** Monta a estrutura fixa da tela. */
export function initSaude() {
  R.overall = h('span', { class: 'head-pill' });
  R.svcs = h('div', { class: 'svc-grid', id: 'svcGrid' });
  R.svg = s('svg', { class: 'chart lat-chart', id: 'latChart', viewBox: '0 0 720 250', role: 'img', 'aria-label': 'Tempo de resposta na última hora' });
  R.tip = h('div', { class: 'tip', hidden: true });
  R.log = h('div', { class: 'term-box', id: 'logSaude' });
  R.mem = h('div', { class: 'card glow-card acc-indigo mem-card', id: 'memCard', hidden: true });
  R.reads = h('div', { class: 'reads', id: 'readList' });
  byId('scr-saude').replaceChildren(
    h('div', { class: 'head' },
      titleTip('h1', 'Saúde', 'Estado de cada serviço, de cada leitura de live e da sessão do YouTube Studio. Atualiza sozinha a cada 5 segundos.'),
      h('div', { class: 'actions' }, R.overall)),
    R.svcs,
    R.mem,
    h('div', { class: 'bento bento-210' },
      h('div', { class: 'card glow-card acc-indigo b-lat' },
        h('div', { class: 'card-title' }, h('span', { class: 'title-row' }, 'Tempo de resposta, última hora', infoTip('Tempo médio de resposta das leituras, por minuto. As faixas marcam os minutos com problema.', { label: 'Sobre o tempo de resposta' })),
          h('div', { class: 'legend' },
            h('span', null, h('i', { class: 'lg lives' }), 'Lives'),
            h('span', null, h('i', { class: 'lg subs' }), 'Inscritos'),
            h('span', null, h('i', { class: 'lg incident' }), 'Incidente'))),
        h('div', { class: 'chart-box' }, R.svg, R.tip)),
      terminalCard('eventos.log', R.log, 'b-term')),
    h('div', { class: 'card glow-card acc-amber' },
      h('div', { class: 'card-h' }, titleTip('h2', 'Leitura de cada live', 'Estado, tempo de resposta da página e quando cada live foi e será lida.')),
      R.reads));
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetSaude() {
  svcSig = '';
  memSig = '';
  chartSig = '';
  readsSig = '';
  logSig = '';
  resetLog();
  if (R.svcs) {
    R.svcs.replaceChildren();
    R.mem.replaceChildren();
    R.mem.hidden = true;
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
  const ch = state.counter && state.counter.channel;
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
  /** @type {Record<string, number | null>} */
  const ringPct = {
    lives: okPct(minutes, 'lives_avg_ms'),
    inscritos: okPct(minutes, 'inscritos_avg_ms'),
    sessao: ck.configured ? (ck.applied ? 100 : 50) : 0,
    api: state.apiOk ? 100 : 0,
  };
  /** @type {Record<string, string>} */
  const ringLabel = { lives: 'leituras ok · 1 h', inscritos: 'leituras ok · 1 h', sessao: 'sessão do Studio', api: 'no ar' };
  return list.map((sv) => {
    const i = info[sv.key];
    const big = h('div', { class: 'big num' });
    if (sv.key === 'api') big.textContent = i.big; else setText(big, i.big, animate);
    const pct = ringPct[sv.key];
    const rg = ring({
      pct, color: ringColor(pct), center: pct === null ? '—' : `${pct}%`, size: 60, label: ringLabel[sv.key], animate,
    });
    const el = h('div', { class: `card glow-card svc ${sv.level === 'bad' ? 'acc-rose' : sv.level === 'ok' ? 'acc-emerald' : 'acc-amber'}`, 'data-svc': sv.key },
      h('div', { class: 'top-line' }, h('h3', { text: sv.name }), levelPill(sv.level, sv.label)),
      h('div', { class: 'svc-main' },
        h('div', { class: 'svc-text' }, big, h('div', { class: 'count', text: i.count })),
        h('div', { class: 'svc-ring' }, rg.root, h('small', { text: ringLabel[sv.key] }))),
      h('dl', null, ...i.dl.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { class: 'num', 'data-static': '', text: v })])));
    return el;
  });
}

/** @param {any} mem bloco `memory` de health/detail @returns {boolean} */
function validMemory(mem) {
  return !!mem && typeof mem === 'object' && Array.isArray(mem.services) && typeof mem.total_bytes === 'number';
}

/**
 * Cartão "Memória": total em uso (soma dos serviços medidos) contra a memória do servidor, e uma barra por serviço.
 * @param {any} mem
 * @param {boolean} animate
 * @returns {HTMLElement[]}
 */
function memoryCard(mem, animate) {
  const total = mem.total_bytes;
  const hostTotal = typeof mem.host_total_bytes === 'number' && mem.host_total_bytes > 0 ? mem.host_total_bytes : null;
  const hostFree = typeof mem.host_available_bytes === 'number' ? mem.host_available_bytes : null;
  const pct = hostTotal === null ? null : Math.min(100, Math.round((total / hostTotal) * 100));
  const color = pct === null ? RING_WARN : pct < 70 ? RING_OK : pct < 85 ? RING_WARN : RING_BAD;
  const rg = ring({
    pct, color, center: pct === null ? '—' : `${pct}%`, size: 72, label: 'Memória do servidor em uso pelos serviços', animate,
  });
  const detail = hostTotal === null ? 'memória do servidor indisponível'
    : `de ${fmtBytes(hostTotal)} do servidor${hostFree !== null ? ` · ${fmtBytes(hostFree)} livres` : ''}`;
  const rows = mem.services.map((/** @type {any} */ sv) => {
    const measured = !!sv.measured && typeof sv.bytes === 'number';
    const fill = h('i');
    fill.style.width = `${measured && total > 0 ? Math.max(2, Math.min(100, (sv.bytes / total) * 100)) : 0}%`;
    return h('div', { class: `mem-row${measured ? '' : ' unmeasured'}`, 'data-svc': String(sv.name) },
      h('span', { class: 'mem-name', text: String(sv.name) }),
      h('span', { class: 'bar' }, fill),
      measured
        ? h('span', { class: 'num mem-val', text: fmtBytes(sv.bytes) })
        : h('span', { class: 'mem-val' }, 'não medido', infoTip(
          typeof mem.note === 'string' && mem.note ? mem.note : 'Este serviço não é medido; a memória dele fica fora da soma.',
          { label: `Por que ${String(sv.name)} não é medido` },
        )));
  });
  return [
    h('div', { class: 'card-h' }, titleTip('h2', 'Memória', 'Quanto cada serviço usa, contra a memória do servidor. O que não é medido fica fora da soma.')),
    h('div', { class: 'mem-main' },
      h('div', { class: 'svc-ring' }, rg.root),
      h('div', { class: 'svc-text' },
        h('div', { class: 'big num', 'data-mem': 'total', text: fmtBytes(total) }),
        h('div', { class: 'count', 'data-mem': 'host', text: detail }))),
    h('div', { class: 'mem-rows' }, ...rows),
  ];
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
  const mem = health.memory;
  const msig = JSON.stringify(mem || null);
  if (first || msig !== memSig) {
    memSig = msig;
    R.mem.hidden = !validMemory(mem);
    if (validMemory(mem)) R.mem.replaceChildren(...memoryCard(mem, first));
    else R.mem.replaceChildren();
  }
  const csig = JSON.stringify(health.minutes);
  if (first || csig !== chartSig) {
    chartSig = csig;
    latencyChart(R.svg, Array.isArray(health.minutes) ? health.minutes : [], first);
  }
  const lsig = JSON.stringify(health.events);
  if (first || lsig !== logSig) {
    logSig = lsig;
    mountLog(R.log, Array.isArray(health.events) ? health.events : [], 30, first);
  }
  const rsig = JSON.stringify(health.reads) + Math.floor(Date.now() / 5000);
  if (first || rsig !== readsSig) {
    readsSig = rsig;
    R.reads.replaceChildren(...readRows(Array.isArray(health.reads) ? health.reads : [], first));
  }
}
