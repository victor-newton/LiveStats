// @ts-check
// Aba "Saúde" (D35/D36): cartões por componente e diagrama de linhas finas
// YouTube → workers → banco → api → web. Os dados vêm SÓ de /api/admin/workers/status
// e /health, a cada 10 s. Nada vindo do servidor vira HTML: só textContent e atributos de dados.
// As animações (pontos nas ligações saudáveis) são CSS; prefers-reduced-motion as desliga.

import { api } from './api.js';
import { loadStatus } from './status.js';
import { byId, cloneTemplate, query } from './dom.js';

/**
 * @typedef {object} HealthHooks
 * @property {() => void} onUnauthorized
 * @property {(resp: import('./api.js').ApiResponse | null) => void} onStatus resposta de workers/status (null = sem conexão)
 */

export const HEALTH_INTERVAL_MS = 10000;
const SVG_NS = 'http://www.w3.org/2000/svg';

/** @typedef {'ok' | 'degraded' | 'error' | 'unknown'} Level */

/** @type {Record<Level, string>} */
const LEVEL_LABELS = { ok: 'ok', degraded: 'degradado', error: 'erro', unknown: 'sem dados' };
/** @type {Record<Level, number>} */
const SEVERITY = { ok: 0, unknown: 1, degraded: 2, error: 3 };

/** Componentes, na ordem dos cartões. */
const COMPONENTS = [
  ['api', 'API'],
  ['db', 'Banco de dados'],
  ['lives', 'Worker de lives'],
  ['inscritos', 'Worker de inscritos'],
  ['auth', 'Cookies e autenticação'],
];

/** Rótulos dos nós, na ordem do diagrama. */
const NODE_LABELS = {
  youtube: 'YouTube',
  lives: 'worker-lives',
  inscritos: 'worker-inscritos',
  db: 'Banco',
  api: 'API',
  web: 'Web',
};

/**
 * Dois desenhos do mesmo diagrama: horizontal (telas largas; as ligações saem da borda direita e
 * entram na esquerda) e vertical (≤ 720 px; empilhado de cima para baixo, em tamanho real, sem
 * miniaturizar). `centers` = centro de cada nó no viewBox.
 */
const LAYOUTS = {
  horizontal: {
    viewBox: '0 0 920 250',
    nodeW: 140,
    nodeH: 48,
    centers: {
      youtube: [80, 125], lives: [320, 60], inscritos: [320, 190], db: [540, 125], api: [705, 125], web: [850, 125],
    },
  },
  vertical: {
    viewBox: '0 0 300 540',
    nodeW: 136,
    nodeH: 48,
    centers: {
      youtube: [150, 30], lives: [76, 150], inscritos: [224, 150], db: [150, 270], api: [150, 390], web: [150, 510],
    },
  },
};
const MOBILE_QUERY = '(max-width: 720px)';

/** Ligações: id, de, para. */
const LINKS = [
  ['youtube-lives', 'youtube', 'lives'],
  ['youtube-inscritos', 'youtube', 'inscritos'],
  ['lives-db', 'lives', 'db'],
  ['inscritos-db', 'inscritos', 'db'],
  ['db-api', 'db', 'api'],
  ['api-web', 'api', 'web'],
];

/** @type {number | null} */
let timer = null;
let generation = 0;
let apiFailures = 0;
/** @type {HealthHooks | null} */
let lastHooks = null;

/** Atualiza já (ex.: logo depois de salvar cookies), sem esperar os 10 s. */
export function refreshHealthNow() {
  if (lastHooks) void refreshHealth(lastHooks);
}

/**
 * @param {Level} a
 * @param {Level} b
 * @returns {Level}
 */
function worst(a, b) {
  return SEVERITY[a] >= SEVERITY[b] ? a : b;
}

/**
 * @param {unknown} state
 * @returns {Level}
 */
function workerLevel(state) {
  if (state === 'ok') return 'ok';
  if (state === 'degraded') return 'degraded';
  if (state === 'error') return 'error';
  return 'unknown'; // starting, stopped, unknown
}

/**
 * "há N s" a partir de dois instantes ISO (o relógio é o do servidor, não o do navegador).
 * @param {unknown} iso
 * @param {unknown} nowIso
 * @returns {string}
 */
function ago(iso, nowIso) {
  if (typeof iso !== 'string' || !iso) return '—';
  const t = Date.parse(iso);
  const now = typeof nowIso === 'string' ? Date.parse(nowIso) : NaN;
  if (Number.isNaN(t) || Number.isNaN(now)) return iso;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `há ${s} s`;
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}

/**
 * @typedef {object} Row
 * @property {Level} level
 * @property {string} lastSignal
 * @property {string} lastSuccess
 * @property {string} failures
 * @property {string} interval
 * @property {string} message
 */

/**
 * @param {any} w linha do worker em /api/admin/workers/status
 * @param {string} now server_time
 * @returns {Row}
 */
function workerRow(w, now) {
  const level = workerLevel(w && w.state);
  const message = w && (w.last_error_message || w.last_error_code)
    ? String(w.last_error_message || w.last_error_code)
    : (level === 'ok' ? 'Funcionando normalmente' : (level === 'unknown' ? 'Ainda sem sinal do worker' : ''));
  return {
    level,
    lastSignal: ago(w && w.last_heartbeat_at, now),
    lastSuccess: ago(w && w.last_success_at, now),
    failures: w && typeof w.consecutive_failures === 'number' ? String(w.consecutive_failures) : '—',
    interval: w && typeof w.interval_seconds === 'number' ? `${w.interval_seconds} s` : '—',
    message,
  };
}

/**
 * Cookies e autenticação do worker de inscritos.
 * @param {any} ws
 * @param {string} now
 * @returns {Row}
 */
function authRow(ws, now) {
  const cookies = ws && ws.cookies ? ws.cookies : { configured: false, count: 0, applied: false, uploaded_at: null };
  const w = ws && ws.workers ? ws.workers.inscritos : null;
  const authState = w && typeof w.auth_state === 'string' ? w.auth_state : 'unknown';
  /** @type {Level} */
  let level;
  let message;
  if (!cookies.configured) {
    level = 'error';
    message = 'Nenhum cookie configurado: cole os cookies na seção Cookies.';
  } else if (authState === 'login_required') {
    level = 'error';
    message = 'O YouTube Studio pediu login: cole cookies novos.';
  } else if (authState === 'decrypt_failed') {
    level = 'error';
    message = 'Os cookies salvos não puderam ser decifrados: cole-os de novo.';
  } else if (authState === 'no_cookies') {
    level = 'error';
    message = 'O worker ainda não aplicou nenhum cookie.';
  } else if (authState === 'ok' && cookies.applied) {
    level = 'ok';
    message = `${cookies.count} cookies aplicados e sessão válida`;
  } else {
    level = authState === 'ok' ? 'degraded' : 'unknown';
    message = 'Cookies salvos; aguardando o worker aplicá-los.';
  }
  return {
    level,
    lastSignal: ago(cookies.uploaded_at, now),
    lastSuccess: level === 'ok' ? ago(w && w.last_success_at, now) : '—',
    failures: w && typeof w.consecutive_failures === 'number' ? String(w.consecutive_failures) : '—',
    interval: w && typeof w.interval_seconds === 'number' ? `${w.interval_seconds} s` : '—',
    message,
  };
}

/**
 * @param {any} ws corpo de workers/status (ou null se falhou)
 * @param {any} health corpo de /health (ou null se falhou)
 * @param {string | null} problem texto curto da falha de rede/HTTP, se houve
 * @returns {Record<string, Row>}
 */
export function buildRows(ws, health, problem) {
  const now = ws && typeof ws.server_time === 'string' ? ws.server_time : (health && health.server_time) || '';
  const apiOk = !!health && health.status === 'ok';
  const statusOk = !!ws;
  const pollInterval = `${HEALTH_INTERVAL_MS / 1000} s`;
  const failures = String(apiFailures);

  /** @type {Row} */
  const apiRow = {
    level: apiOk ? 'ok' : 'error',
    lastSignal: apiOk ? ago(health.server_time, now || health.server_time) : '—',
    lastSuccess: apiOk ? ago(health.server_time, now || health.server_time) : '—',
    failures,
    interval: pollInterval,
    message: apiOk ? 'Respondendo em /health' : (problem || 'Sem resposta de /health'),
  };
  /** @type {Row} */
  const dbRow = {
    level: apiOk && statusOk ? 'ok' : (apiOk ? 'degraded' : 'unknown'),
    lastSignal: statusOk ? ago(ws.server_time, now) : '—',
    lastSuccess: statusOk ? ago(ws.server_time, now) : '—',
    failures,
    interval: pollInterval,
    message: apiOk && statusOk
      ? 'Leituras e gravações normais'
      : (apiOk ? 'A API respondeu, mas não entregou o status dos workers.' : 'Sem como verificar sem a API.'),
  };
  const workers = ws && ws.workers ? ws.workers : {};
  return {
    api: apiRow,
    db: dbRow,
    lives: workerRow(statusOk ? workers.lives : null, now),
    inscritos: workerRow(statusOk ? workers.inscritos : null, now),
    auth: statusOk
      ? authRow(ws, now)
      : { level: 'unknown', lastSignal: '—', lastSuccess: '—', failures: '—', interval: '—', message: 'Sem dados do status.' },
  };
}

/**
 * Estado de cada ligação do diagrama a partir dos componentes.
 * Falha do worker diante do YouTube marca a ligação YouTube→worker; só db_error marca worker→banco.
 * @param {any} ws
 * @param {Record<string, Row>} rows
 * @returns {Record<string, Level>}
 */
export function linkLevels(ws, rows) {
  const workers = ws && ws.workers ? ws.workers : {};
  /** @param {string} key @returns {boolean} */
  const dbFault = (key) => !!workers[key] && workers[key].last_error_code === 'db_error';
  /** @param {string} key @returns {Level} */
  const toYoutube = (key) => (dbFault(key) ? 'ok' : rows[key].level);
  /** @param {string} key @returns {Level} */
  const toDb = (key) => {
    const lv = rows[key].level;
    if (dbFault(key)) return 'error';
    return lv === 'unknown' ? 'unknown' : rows.db.level;
  };
  return {
    'youtube-lives': toYoutube('lives'),
    'youtube-inscritos': worst(toYoutube('inscritos'), rows.auth.level === 'error' ? 'error' : 'ok'),
    'lives-db': toDb('lives'),
    'inscritos-db': toDb('inscritos'),
    'db-api': worst(rows.db.level, rows.api.level),
    'api-web': rows.api.level,
  };
}

/**
 * @param {string} name
 * @param {Record<string, string>} attrs
 * @returns {SVGElement}
 */
function svg(name, attrs) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return /** @type {SVGElement} */ (el);
}

/** @returns {'horizontal' | 'vertical'} */
function wantedLayout() {
  return window.matchMedia(MOBILE_QUERY).matches ? 'vertical' : 'horizontal';
}

/** Layout atualmente desenhado. @type {'horizontal' | 'vertical' | null} */
let drawn = null;
/** Últimos dados desenhados (para redesenhar ao trocar de layout). @type {{rows: Record<string, Row>, links: Record<string, Level>} | null} */
let lastDraw = null;

/** Desenha o diagrama: linhas finas + pontos (CSS) + nós, no layout pedido pela largura da tela. */
function buildDiagram() {
  const root = byId('healthDiagram');
  const kind = wantedLayout();
  const layout = LAYOUTS[kind];
  for (const old of root.querySelectorAll('g.links, g.nodes')) old.remove();
  root.setAttribute('viewBox', layout.viewBox);
  root.dataset.layout = kind;
  const { nodeW, nodeH } = layout;
  /** @type {Record<string, number[]>} */
  const centers = layout.centers;

  const linkLayer = svg('g', { class: 'links' });
  for (const [id, from, to] of LINKS) {
    const [x1, y1] = centers[from];
    const [x2, y2] = centers[to];
    let d;
    if (kind === 'horizontal') {
      const sx = x1 + nodeW / 2;
      const ex = x2 - nodeW / 2;
      const mid = (sx + ex) / 2;
      d = `M ${sx} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${ex} ${y2}`;
    } else {
      const sy = y1 + nodeH / 2;
      const ey = y2 - nodeH / 2;
      const mid = (sy + ey) / 2;
      d = `M ${x1} ${sy} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${ey}`;
    }
    const g = svg('g', { class: 'link', 'data-link': id, 'data-state': 'unknown' });
    g.appendChild(svg('path', { class: 'link-line', d }));
    g.appendChild(svg('path', { class: 'link-dots', d }));
    linkLayer.appendChild(g);
  }
  root.appendChild(linkLayer);

  const nodeLayer = svg('g', { class: 'nodes' });
  for (const [id, label] of Object.entries(NODE_LABELS)) {
    const [cx, cy] = centers[id];
    const g = svg('g', { class: 'node', 'data-node': id, 'data-state': 'unknown' });
    g.appendChild(svg('rect', {
      x: String(cx - nodeW / 2), y: String(cy - nodeH / 2),
      width: String(nodeW), height: String(nodeH), rx: '14',
    }));
    const text = svg('text', { x: String(cx), y: String(cy + 5), 'text-anchor': 'middle' });
    text.textContent = label;
    g.appendChild(text);
    nodeLayer.appendChild(g);
  }
  root.appendChild(nodeLayer);
  drawn = kind;
}

// Girou o celular ou mudou a janela: redesenha no layout certo com os últimos dados.
window.matchMedia(MOBILE_QUERY).addEventListener('change', () => {
  if (lastDraw) renderDiagram(lastDraw.rows, lastDraw.links);
});

/** @param {Record<string, Row>} rows */
function renderCards(rows) {
  const box = byId('healthCards');
  box.replaceChildren();
  for (const [key, name] of COMPONENTS) {
    const row = rows[key];
    const card = cloneTemplate('healthCardTemplate');
    card.dataset.component = key;
    card.dataset.state = row.level;
    query(card, '.hc-name').textContent = name;
    const state = query(card, '.hc-state');
    state.dataset.state = row.level;
    state.textContent = LEVEL_LABELS[row.level];
    const dl = query(card, '.hc-rows');
    const lines = [
      ['Último sinal', row.lastSignal],
      ['Último sucesso', row.lastSuccess],
      ['Falhas seguidas', row.failures],
      ['Intervalo', row.interval],
      ['Mensagem', row.message || '—'],
    ];
    for (const [label, value] of lines) {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = value;
      dl.append(dt, dd);
    }
    box.appendChild(card);
  }
}

/**
 * @param {Record<string, Row>} rows
 * @param {Record<string, Level>} links
 */
function renderDiagram(rows, links) {
  lastDraw = { rows, links };
  if (drawn !== wantedLayout()) buildDiagram();
  const root = byId('healthDiagram');
  /** @type {Record<string, Level>} */
  const nodes = {
    youtube: 'unknown',
    lives: rows.lives.level,
    inscritos: worst(rows.inscritos.level, rows.auth.level === 'error' ? 'error' : 'ok'),
    db: rows.db.level,
    api: rows.api.level,
    web: 'ok',
  };
  for (const g of root.querySelectorAll('[data-node]')) {
    const id = /** @type {SVGElement} */ (g).dataset.node || '';
    /** @type {SVGElement} */ (g).dataset.state = nodes[id] || 'unknown';
  }
  const parts = [];
  for (const g of root.querySelectorAll('[data-link]')) {
    const id = /** @type {SVGElement} */ (g).dataset.link || '';
    const level = links[id] || 'unknown';
    /** @type {SVGElement} */ (g).dataset.state = level;
    if (level === 'error' || level === 'degraded') parts.push(`${id.replace('-', ' → ')}: ${LEVEL_LABELS[level]}`);
  }
  byId('diagramLabel').textContent = parts.length
    ? `Fluxo de dados com falha em: ${parts.join('; ')}`
    : 'Fluxo de dados YouTube, workers, banco, API e web, sem falhas';
}

/**
 * Desenha tudo a partir dos dois corpos (exportado para teste de unidade do dublê, se preciso).
 * @param {any} ws
 * @param {any} health
 * @param {string | null} problem
 */
export function renderHealth(ws, health, problem) {
  const rows = buildRows(ws, health, problem);
  renderCards(rows);
  renderDiagram(rows, linkLevels(ws, rows));
}

/**
 * Uma rodada: /health e /api/admin/workers/status. 401 chama `onUnauthorized`.
 * @param {HealthHooks} hooks
 */
export async function refreshHealth(hooks) {
  const gen = generation;
  /** @type {any} */
  let health = null;
  /** @type {any} */
  let ws = null;
  /** @type {string | null} */
  let problem = null;
  try {
    const [h, s] = await Promise.all([api('GET', '/health'), loadStatus()]); // loadStatus também alimenta a seção Inscritos
    if (gen !== generation) return;
    if (s.status === 401) {
      hooks.onUnauthorized();
      return;
    }
    hooks.onStatus(s);
    if (h.ok && h.data) health = h.data;
    else problem = `/health respondeu HTTP ${h.status}`;
    if (s.ok && s.data) ws = s.data;
  } catch (e) {
    if (gen !== generation) return;
    problem = 'Sem conexão com o servidor';
    hooks.onStatus(null);
  }
  apiFailures = health ? 0 : apiFailures + 1;
  renderHealth(ws, health, problem);
  byId('healthUpdated').textContent = health
    ? `Última verificação: ${ago(health.server_time, health.server_time)}.`
    : `Falha na última verificação${problem ? `: ${problem}` : ''}.`;
}

/** @param {HealthHooks} hooks */
export function startHealth(hooks) {
  lastHooks = hooks;
  stopHealth();
  void refreshHealth(hooks);
  timer = window.setInterval(() => void refreshHealth(hooks), HEALTH_INTERVAL_MS);
}

export function stopHealth() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

/** Esvazia a seção (sessão encerrada). */
export function resetHealth() {
  stopHealth();
  generation += 1;
  apiFailures = 0;
  lastDraw = null;
  byId('healthCards').replaceChildren();
  byId('healthDiagram').querySelectorAll('[data-link], [data-node]').forEach((e) => {
    /** @type {SVGElement} */ (e).dataset.state = 'unknown';
  });
  byId('healthUpdated').textContent = 'Aguardando a primeira leitura…';
}
