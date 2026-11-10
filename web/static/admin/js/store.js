// @ts-check
// Estado do painel em memória (nada vai para storage) e leituras derivadas dos dados do servidor.

import { PLATFORM_NAME } from './util.js';

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
 * @property {'visao' | 'conjuntos' | 'conjunto' | 'inscritos' | 'conta' | 'saude' | 'mestre'} screen
 * @property {number | null} setId
 * @property {{live?: number, links?: boolean} | null} focus
 * @property {'usuarios' | 'atividade' | 'uso' | 'dados'} [seg] segmento da aba Mestre
 * @property {number | null} [userId] usuário em "Dados do usuário" (também o dono do conjunto aberto pelo mestre)
 */

export const state = {
  /** @type {any[]} */
  sets: [],
  limits: { max_sets: 50, max_lives: 200 },
  /** usuário da sessão (D48): {id, username, display_name, role, must_change_password, has_counter, max_sets, max_lives} */
  /** @type {any} */
  user: null,
  /** o servidor negou (404) uma rota do mestre: a interface esconde Saúde e Mestre */
  masterDenied: false,
  /** contador do usuário logado (GET /api/admin/counter, D51) */
  /** @type {any} */
  counter: null,
  /** o contador está desativado para a conta (403 counter_disabled) */
  counterOff: false,
  /** @type {any} */
  status: null,
  /** @type {any} */
  subHist: null,
  /** dados do painel mestre (D52) */
  /** @type {{users: any[] | null, usage: any, activity: any[] | null, data: any, filter: string}} */
  master: { users: null, usage: null, activity: null, data: null, filter: '' },
  /** conjuntos de OUTRO usuário abertos pelo mestre (id -> conjunto) */
  /** @type {Map<number, any>} */
  foreign: new Map(),
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
  /** endereço do painel mostrado na Conta (PUBLIC_ADMIN_URL, se o servidor o informar; senão a origem atual) */
  adminUrl: '',
  apiOk: true,
  /** @type {number | null} */
  apiMs: null,
  /** @type {Route} */
  route: { screen: 'visao', setId: null, focus: null },
};

/** Descarta TODO dado de sessão. */
export function resetState() {
  state.sets = [];
  state.user = null;
  state.masterDenied = false;
  state.counter = null;
  state.counterOff = false;
  state.status = null;
  state.subHist = null;
  state.master = { users: null, usage: null, activity: null, data: null, filter: '' };
  state.foreign.clear();
  state.health = null;
  state.hist.clear();
  state.sparks.clear();
  state.range = 60;
  state.publicBase = '';
  state.adminUrl = '';
  state.apiOk = true;
  state.route = { screen: 'visao', setId: null, focus: null };
}

/** O usuário logado é o mestre (e o servidor não negou as rotas dele). */
export function isMaster() {
  return !!state.user && state.user.role === 'master' && !state.masterDenied;
}

/** @param {number | null} id @returns {any} */
export function setById(id) {
  return state.sets.find((x) => x.id === id) || (id !== null ? state.foreign.get(id) : null) || null;
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

/**
 * Inscritos atuais (número), da leitura do contador do usuário (D51). A api manda `reading.value` como TEXTO do
 * com separador de milhar ("460600" ou "460.600"): só os dígitos valem.
 * @param {{value?: unknown} | null | undefined} [reading] leitura (padrão: a do contador do usuário logado)
 * @returns {number | null}
 */
export function subsNow(reading = state.counter && state.counter.reading) {
  const digits = reading && typeof reading.value === 'string' ? reading.value.replace(/\D/g, '') : '';
  return digits ? Number(digits) : null;
}

/**
 * Estado do contador do usuário (para o chip do cabeçalho e para o selo do cartão).
 * @returns {{level: Level, label: string}}
 */
export function counterService() {
  const c = state.counter;
  if (state.counterOff) return { level: 'off', label: 'desativado' };
  if (!c) return { level: 'off', label: 'sem dados' };
  if (c.auth_state === 'login_required') return { level: 'bad', label: 'pediu login' };
  if (c.auth_state === 'decrypt_failed') return { level: 'bad', label: 'não decifra' };
  if (!c.cookies || !c.cookies.configured) return { level: 'bad', label: 'sem cookies' };
  if (c.state === 'ok') return { level: 'ok', label: 'lendo' };
  if (c.state === 'degraded') return { level: 'warn', label: 'degradado' };
  if (c.error_code === 'channel_no_access') return { level: 'bad', label: 'sem acesso ao canal' };
  if (c.state === 'error') return { level: 'bad', label: 'erro' };
  return { level: 'warn', label: 'aguardando' };
}
