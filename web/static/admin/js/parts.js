// @ts-check
// Peças compartilhadas por telas: os 3 cartões do contador de inscritos (Visão geral e Inscritos) e o registro de eventos.

import { h, s, setStyle } from './h.js';
import { sparkline } from './charts.js';
import { state, subsNow, counterService } from './store.js';
import { levelPill } from './ui.js';
import {
  fmt, hhmm, relTime, setText, toEpoch, DASH,
} from './util.js';

const SOURCE_NAME = { lives: 'Leitura das lives', inscritos: 'Contador', api: 'API' };
/** Nível (cor da marca) de cada ação do registro de atividade do mestre (D52); o que não está na lista é informativo. */
const ACTION_LEVEL = /** @type {Record<string, string>} */ ({
  login_ok: 'ok', login_failed: 'bad', user_created: 'ok', user_updated: 'info', user_reactivated: 'ok', user_suspended: 'warn',
  user_deleted: 'bad', user_purged: 'warn', password_changed: 'info', password_reset: 'warn',
  set_created: 'info', set_deleted: 'warn', live_created: 'info', live_deleted: 'warn', cookies_sent: 'info',
});
/** Marca de cada nível, como num terminal: ✓ ok, › info, ! aviso, ✕ erro. */
const MARK = /** @type {Record<string, string>} */ ({ ok: '✓', info: '›', warn: '!', bad: '✕' });
/** Linhas já mostradas: só as NOVAS deslizam ao entrar (as de antes não se mexem a cada atualização). */
const shown = new Set();

/**
 * Corpo do terminal de eventos: mais antigos em cima, mais recentes embaixo (rolagem interna), a última linha
 * com cursor piscando. Texto sempre como textContent.
 * @param {any[]} events do mais recente para o mais antigo (como vem da api)
 * @param {number} limit
 * @param {boolean} animate todas as linhas deslizam (entrada da tela)
 * @returns {HTMLElement}
 */
export function eventLog(events, limit, animate = false) {
  const body = h('div', { class: 'term-body log', role: 'log', 'aria-label': 'Eventos' });
  const list = (Array.isArray(events) ? events : []).slice(0, limit).reverse();
  if (!list.length) {
    body.appendChild(h('p', { class: 'empty', text: 'Nenhum evento ainda.' }));
    return body;
  }
  list.forEach((ev, i) => {
    const at = toEpoch(ev.at);
    const level = typeof ev.level === 'string' && MARK[ev.level] ? ev.level : 'info';
    const key = `${ev.at}|${ev.source}|${ev.message}`;
    const fresh = animate || !shown.has(key);
    shown.add(key);
    const msg = h('span', { class: 'msg' }, h('b', { text: `${/** @type {Record<string, string>} */ (SOURCE_NAME)[ev.source] || ev.source}: ` }), String(ev.message));
    const line = h('div', { class: `term-line log-item ${level}${fresh ? ' slide' : ''}` },
      h('time', { text: at === null ? DASH : hhmm(at) }),
      h('span', { class: 'mark', 'aria-label': level, text: MARK[level] }),
      msg);
    if (fresh && animate) setStyle(line, { 'animation-delay': `${Math.min(i, 12) * 40}ms` });
    if (i === list.length - 1) msg.appendChild(h('i', { class: 'cursor', 'aria-hidden': 'true' }));
    body.appendChild(line);
  });
  return body;
}

/**
 * Troca o corpo do terminal mantendo a rolagem (embaixo, se o usuário já estava embaixo).
 * @param {HTMLElement} box contêiner do terminal
 * @param {any[]} events
 * @param {number} limit
 * @param {boolean} animate
 */
export function mountLog(box, events, limit, animate) {
  const old = box.firstElementChild;
  const atEnd = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 24;
  const keep = old ? old.scrollTop : 0;
  const body = eventLog(events, limit, animate);
  box.replaceChildren(body);
  body.scrollTop = atEnd ? body.scrollHeight : keep;
}

/** Descarta as linhas já mostradas (sessão encerrada). */
export function resetLog() {
  shown.clear();
}

/**
 * Cartão "terminal": barra com 3 bolinhas e o título, e o corpo de eventos.
 * @param {string} title
 * @param {HTMLElement} box contêiner do corpo (preenchido por `mountLog`)
 * @param {string} [cls]
 * @returns {HTMLElement}
 */
export function terminalCard(title, box, cls = '') {
  return h('div', { class: `card glow-card acc-fuchsia term ${cls}`.trim() },
    h('div', { class: 'term-bar' },
      h('span', { class: 'term-dots', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('span', { class: 'term-title', text: title })),
    box);
}

/**
 * Nome dos dias da semana ("qua", "qui"...), o último vira "hoje".
 * @param {string} day YYYY-MM-DD
 * @returns {string}
 */
function weekday(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return day;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '');
}

/**
 * Os 3 cartões do contador: canal + número, ganho em 7 dias, hoje. Só YouTube, um canal.
 * @param {boolean} animate
 * @returns {HTMLElement[]}
 */
export function subsCards(animate) {
  const c = state.counter;
  const ch = c && c.channel;
  const sub = state.subHist;
  const subs = subsNow();
  const ins = counterService();
  const ck = c && c.cookies ? c.cookies : null;
  const sesLabel = !ck || !ck.configured ? 'sem cookies'
    : c.auth_state === 'login_required' ? 'pediu login'
      : c.auth_state === 'decrypt_failed' ? 'não decifra'
        : c.auth_state === 'ok' && ck.applied ? 'válida' : 'aguardando';
  const upd = c && c.reading ? toEpoch(c.reading.read_at) : null;
  const todayGain = sub && typeof sub.today_gain === 'number' ? sub.today_gain : null;
  const hourGain = sub && typeof sub.last_hour_gain === 'number' ? sub.last_hour_gain : null;
  const sign = (/** @type {number | null} */ n) => (n === null ? DASH : `${n >= 0 ? '+' : '−'}${fmt(Math.abs(n))}`);

  const big = h('div', { class: 'subs-big num', 'data-subs': '' });
  setText(big, subs === null ? DASH : fmt(subs), animate);
  const hero = h('div', { class: 'card glow-card acc-yt subs-card b-hero' },
    h('div', { class: 'channel' },
      h('span', { class: 'avatar', text: ch && ch.name ? String(ch.name).trim().slice(0, 1).toUpperCase() : 'YT' }),
      h('div', { class: 'who' },
        h('strong', { class: 'channel-name', text: ch && ch.name ? String(ch.name) : 'Canal detectado pelos cookies' }),
        h('div', { class: 'count mono channel-id', text: ch && ch.id ? String(ch.id) : 'ainda não detectado' }),
        c && c.channel_override ? h('span', { class: 'status-badge ok chosen-badge', text: 'canal escolhido' }) : null),
      levelPill(ins.level, ins.label)),
    big,
    h('div', { class: 'count', text: `${upd !== null ? `atualizado ${relTime(upd)}` : 'aguardando a primeira leitura'}${todayGain !== null ? ` · ${sign(todayGain)} hoje` : ''}` }));

  const days = sub && Array.isArray(sub.days) ? sub.days : [];
  const svg = s('svg', { class: 'spark', viewBox: '0 0 300 70', role: 'img', 'aria-label': 'Inscritos nos últimos 7 dias' });
  sparkline(svg, days.map((/** @type {any} */ d) => d.value).filter((/** @type {unknown} */ v) => typeof v === 'number'), '#34D399', animate);
  const total = days.length > 1 && typeof days[0].value === 'number' && typeof days[days.length - 1].value === 'number' ? days[days.length - 1].value - days[0].value : null;
  const gain = h('div', { class: 'card glow-card acc-emerald' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Ganho de inscritos'), h('p', { text: 'Últimos 7 dias.' })),
      total !== null ? h('span', { class: `trend${total < 0 ? ' down' : ''}`, text: `${total < 0 ? '↓' : '↑'} ${fmt(Math.abs(total))}` }) : null),
    svg,
    h('div', { class: 'days num' }, ...days.map((/** @type {any} */ d, /** @type {number} */ i) => h('span', { text: i === days.length - 1 ? 'hoje' : weekday(String(d.day)) }))));

  const stat = (/** @type {string} */ v, /** @type {string} */ label) => {
    const b = h('b', { class: 'num' });
    setText(b, v, animate);
    return h('div', null, b, h('span', { text: label }));
  };
  const cookies = ck;
  const today = h('div', { class: 'card glow-card acc-cyan' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Hoje'))),
    h('div', { class: 'set-stats two' },
      stat(sign(todayGain), 'inscritos desde 00:00'),
      stat(sign(hourGain), 'na última hora'),
      stat(cookies ? String(cookies.count) : DASH, 'cookies na sessão'),
      h('div', null, h('b', { class: 'num', 'data-static': '', text: sesLabel }), h('span', { text: 'sessão do Studio' }))));
  return [hero, gain, today];
}

/**
 * Terminal da atividade do mestre (D52): quem fez o quê, em quem, com que detalhe. Do mais antigo (em cima) ao mais
 * recente (embaixo). Texto sempre como textContent.
 * @param {any[]} events do mais recente para o mais antigo (como vem da api)
 * @param {boolean} animate
 * @returns {HTMLElement}
 */
export function activityLog(events, animate = false) {
  const body = h('div', { class: 'term-body log', role: 'log', 'aria-label': 'Atividade' });
  const list = (Array.isArray(events) ? events : []).slice().reverse();
  if (!list.length) {
    body.appendChild(h('p', { class: 'empty', text: 'Nenhuma atividade registrada.' }));
    return body;
  }
  list.forEach((ev, i) => {
    const at = toEpoch(ev.at);
    const action = String(ev.action);
    const level = ACTION_LEVEL[action] || 'info';
    const key = `a|${ev.at}|${ev.actor}|${action}|${ev.target}`;
    const fresh = animate || !shown.has(key);
    shown.add(key);
    const msg = h('span', { class: 'msg' },
      h('b', { text: `${ev.actor || 'sistema'} ` }),
      action,
      ev.target ? ` ${ev.target}` : '',
      ev.detail ? h('span', { class: 'detail', text: ` · ${ev.detail}` }) : null);
    const line = h('div', { class: `term-line log-item ${level}${fresh ? ' slide' : ''}` },
      h('time', { text: at === null ? DASH : `${new Date(at * 1000).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })} ${hhmm(at, true)}` }),
      h('span', { class: 'mark', 'aria-label': level, text: MARK[level] }),
      msg);
    if (i === list.length - 1) msg.appendChild(h('i', { class: 'cursor', 'aria-hidden': 'true' }));
    body.appendChild(line);
  });
  return body;
}

/**
 * Troca o corpo do terminal de atividade mantendo a rolagem.
 * @param {HTMLElement} box
 * @param {any[]} events
 * @param {boolean} animate
 */
export function mountActivity(box, events, animate) {
  const old = box.firstElementChild;
  const atEnd = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 24;
  const keep = old ? old.scrollTop : 0;
  const body = activityLog(events, animate);
  box.replaceChildren(body);
  body.scrollTop = atEnd ? body.scrollHeight : keep;
}
