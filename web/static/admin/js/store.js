// @ts-check
// Estado do painel em memória (nada vai para storage) e leituras derivadas dos dados do servidor.

import { PLATFORM_NAME, parseSubs } from './util.js';

/**
 * @typedef {object} LiveStats
 * @property {string} status
 * @property {string} title
 * @property {number | null} viewers
 * @property {number | null} likes
 * @property {string | null} error_code
 */

/**
 * @typedef {object} Route
 * @property {'visao' | 'conjunto' | 'inscritos' | 'saude'} screen
 * @property {number | null} setId
 * @property {{live?: number, links?: boolean} | null} focus
 */

export const state = {
  /** @type {any[]} */
  sets: [],
  limits: { max_sets: 50, max_lives: 200 },
  /** @type {any} */
  settings: null,
  /** @type {any} */
  status: null,
  /** leitura atual do contador (/api/public/subscribers, a cada 2 s na tela Inscritos) */
  /** @type {any} */
  subscribers: null,
  /** @type {any} */
  subHist: null,
  /** @type {any} */
  health: null,
  /** setId -> {range, data} (histórico do conjunto no período em uso) */
  /** @type {Map<number, {range: number, data: any}>} */
  hist: new Map(),
  /** setId -> {at, values} (minigráfico da Visão geral, atualizado a cada 30 s) */
  /** @type {Map<number, {at: number, values: number[]}>} */
  sparks: new Map(),
  range: 60,
  publicBase: '',
  apiOk: true,
  /** @type {number | null} */
  apiMs: null,
  /** @type {Set<number>} */
  navOpen: new Set(),
  /** @type {Route} */
  route: { screen: 'visao', setId: null, focus: null },
};

/** Descarta TODO dado de sessão. */
export function resetState() {
  state.sets = [];
  state.settings = null;
  state.status = null;
  state.subscribers = null;
  state.subHist = null;
  state.health = null;
  state.hist.clear();
  state.sparks.clear();
  state.range = 60;
  state.publicBase = '';
  state.apiOk = true;
  state.navOpen.clear();
  state.route = { screen: 'visao', setId: null, focus: null };
}

/** @param {number | null} id @returns {any} */
export function setById(id) {
  return state.sets.find((x) => x.id === id) || null;
}

/**
 * Leitura atual da live: `stats` (objeto) ou, se a api a mandar achatada (D45), os campos na própria live.
 * @param {any} live
 * @returns {LiveStats | null}
 */
export function liveStats(live) {
  if (live && live.stats && typeof live.stats === 'object') return live.stats;
  if (live && typeof live.status === 'string') {
    return {
      status: live.status,
      title: typeof live.title === 'string' ? live.title : '',
      viewers: typeof live.viewers === 'number' ? live.viewers : null,
      likes: typeof live.likes === 'number' ? live.likes : null,
      error_code: typeof live.error_code === 'string' ? live.error_code : null,
    };
  }
  return null;
}

/**
 * Nome mostrado (D44): `display_name` da api; reserva local só se faltar.
 * @param {any} live
 * @returns {string}
 */
export function liveName(live) {
  if (typeof live.display_name === 'string' && live.display_name) return live.display_name;
  if (live.label) return live.label;
  return `${PLATFORM_NAME[live.platform] || live.platform} ${live.ref}`;
}

/** @param {any} set @returns {any[]} lives em ordem de posição */
export function sortedLives(set) {
  return [...set.lives].sort((a, b) => (a.position || 0) - (b.position || 0) || a.id - b.id);
}

/**
 * Totais do conjunto (cada conjunto é um assunto: nunca se somam conjuntos entre si).
 * @param {any} set
 * @returns {{viewers: number, likes: number | null, total: number, on: number}}
 */
export function setTotals(set) {
  let viewers = 0;
  /** @type {number | null} */
  let likes = null;
  let on = 0;
  for (const l of set.lives) {
    const st = liveStats(l);
    if (!st) continue;
    viewers += st.viewers || 0;
    if (typeof st.likes === 'number') likes = (likes || 0) + st.likes;
    if (st.status === 'ao_vivo') on += 1;
  }
  return { viewers, likes, total: set.lives.length, on };
}

/** @typedef {'ok' | 'warn' | 'bad' | 'off'} Level */

/**
 * @typedef {object} Service
 * @property {'lives' | 'inscritos' | 'sessao' | 'api'} key
 * @property {string} name
 * @property {Level} level
 * @property {string} label texto da pílula
 */

/** @param {unknown} st @returns {Level} */
function workerLevel(st) {
  if (st === 'ok') return 'ok';
  if (st === 'degraded') return 'warn';
  if (st === 'error') return 'bad';
  return 'off';
}

/**
 * Os 4 serviços da Saúde (e da faixa da barra lateral).
 * @returns {Service[]}
 */
export function services() {
  const ws = state.status;
  const lives = ws && ws.workers ? ws.workers.lives : null;
  const ins = ws && ws.workers ? ws.workers.inscritos : null;
  const ck = ws && ws.cookies ? ws.cookies : { configured: false, count: 0, applied: false };
  const authState = ins && typeof ins.auth_state === 'string' ? ins.auth_state : 'unknown';
  /** @type {Record<Level, string>} */
  const word = { ok: 'ok', warn: 'degradado', bad: 'erro', off: 'sem dados' };
  const lvl = workerLevel(lives && lives.state);
  let insLvl = workerLevel(ins && ins.state);
  if (authState === 'login_required' || authState === 'decrypt_failed') insLvl = 'bad';
  /** @type {Level} */
  let ses = 'off';
  let sesLabel = 'sem dados';
  if (!ck.configured) { ses = 'bad'; sesLabel = 'sem cookies'; }
  else if (authState === 'login_required') { ses = 'bad'; sesLabel = 'pediu login'; }
  else if (authState === 'decrypt_failed') { ses = 'bad'; sesLabel = 'não decifra'; }
  else if (authState === 'ok' && ck.applied) { ses = 'ok'; sesLabel = 'válida'; }
  else { ses = 'warn'; sesLabel = 'aguardando'; }
  /** @type {Level} */
  const apiLvl = state.apiOk ? 'ok' : 'bad';
  return [
    { key: 'lives', name: 'Leitura das lives', level: lvl, label: word[lvl] },
    { key: 'inscritos', name: 'Contador de inscritos', level: insLvl, label: word[insLvl] },
    { key: 'sessao', name: 'Sessão do Studio', level: ses, label: sesLabel },
    { key: 'api', name: 'API e banco', level: apiLvl, label: word[apiLvl] },
  ];
}

/** @returns {number | null} inscritos atuais (número) */
export function subsNow() {
  const s = state.subscribers || (state.status && state.status.subscribers);
  return s ? parseSubs(s.inscritos) : null;
}
