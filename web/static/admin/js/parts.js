// @ts-check
// Peças compartilhadas por telas: os 3 cartões do contador de inscritos (Visão geral e Inscritos) e o registro de eventos.

import { h, s, setStyle } from './h.js';
import { sparkline } from './charts.js';
import { state, subsNow, services } from './store.js';
import { levelPill } from './ui.js';
import {
  fmt, hhmm, relTime, setText, toEpoch, DASH,
} from './util.js';

const SOURCE_NAME = { lives: 'Leitura das lives', inscritos: 'Contador', api: 'API' };
/** @type {Record<string, string>} */
const LEVEL_COLOR = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)', info: 'var(--ink-3)' };

/**
 * Registro de eventos (mais recentes primeiro). Texto sempre como textContent.
 * @param {any[]} events
 * @param {number} limit
 * @returns {HTMLElement}
 */
export function eventLog(events, limit) {
  const box = h('div', { class: 'log' });
  const list = (Array.isArray(events) ? events : []).slice(0, limit);
  if (!list.length) {
    box.appendChild(h('p', { class: 'empty', text: 'Nenhum evento ainda.' }));
    return box;
  }
  for (const ev of list) {
    const at = toEpoch(ev.at);
    const dot = h('span', { class: 'dot' });
    setStyle(dot, { background: LEVEL_COLOR[ev.level] || LEVEL_COLOR.info });
    box.appendChild(h('div', { class: `log-item ${typeof ev.level === 'string' ? ev.level : ''}`.trim() },
      h('time', { text: at === null ? DASH : hhmm(at) }),
      dot,
      h('span', null, h('b', { text: `${/** @type {Record<string, string>} */ (SOURCE_NAME)[ev.source] || ev.source}: ` }), String(ev.message))));
  }
  return box;
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
  const ch = state.settings && state.settings.channel;
  const sub = state.subHist;
  const subs = subsNow();
  const ins = services().find((x) => x.key === 'inscritos');
  const ses = services().find((x) => x.key === 'sessao');
  const upd = state.status && state.status.subscribers ? toEpoch(state.status.subscribers.atualizado_em) : null;
  const todayGain = sub && typeof sub.today_gain === 'number' ? sub.today_gain : null;
  const hourGain = sub && typeof sub.last_hour_gain === 'number' ? sub.last_hour_gain : null;
  const sign = (/** @type {number | null} */ n) => (n === null ? DASH : `${n >= 0 ? '+' : '−'}${fmt(Math.abs(n))}`);

  const big = h('div', { class: 'subs-big num', 'data-subs': '' });
  setText(big, subs === null ? DASH : fmt(subs), animate);
  const hero = h('div', { class: 'card subs-card' },
    h('div', { class: 'channel' },
      h('span', { class: 'avatar', text: ch && ch.name ? String(ch.name).trim().slice(0, 1).toUpperCase() : 'YT' }),
      h('div', { class: 'who' },
        h('strong', { class: 'channel-name', text: ch && ch.name ? String(ch.name) : 'Canal detectado pelos cookies' }),
        h('div', { class: 'count mono channel-id', text: ch && ch.id ? String(ch.id) : 'ainda não detectado' })),
      ins ? levelPill(ins.level, ins.level === 'ok' ? 'lendo' : ins.label) : null),
    big,
    h('div', { class: 'count', text: `${upd !== null ? `atualizado ${relTime(upd)}` : 'aguardando a primeira leitura'}${todayGain !== null ? ` · ${sign(todayGain)} hoje` : ''}` }));

  const days = sub && Array.isArray(sub.days) ? sub.days : [];
  const svg = s('svg', { class: 'spark', viewBox: '0 0 300 70', role: 'img', 'aria-label': 'Inscritos nos últimos 7 dias' });
  sparkline(svg, days.map((/** @type {any} */ d) => d.value).filter((/** @type {unknown} */ v) => typeof v === 'number'), '#FF6B6B', animate);
  const total = days.length > 1 && typeof days[0].value === 'number' && typeof days[days.length - 1].value === 'number' ? days[days.length - 1].value - days[0].value : null;
  const gain = h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Ganho de inscritos'), h('p', { text: 'Últimos 7 dias.' })),
      total !== null ? h('span', { class: `trend${total < 0 ? ' down' : ''}`, text: `${total < 0 ? '↓' : '↑'} ${fmt(Math.abs(total))}` }) : null),
    svg,
    h('div', { class: 'days num' }, ...days.map((/** @type {any} */ d, /** @type {number} */ i) => h('span', { text: i === days.length - 1 ? 'hoje' : weekday(String(d.day)) }))));

  const stat = (/** @type {string} */ v, /** @type {string} */ label) => {
    const b = h('b', { class: 'num' });
    setText(b, v, animate);
    return h('div', null, b, h('span', { text: label }));
  };
  const cookies = state.status && state.status.cookies ? state.status.cookies : null;
  const today = h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Hoje'))),
    h('div', { class: 'set-stats two' },
      stat(sign(todayGain), 'inscritos desde 00:00'),
      stat(sign(hourGain), 'na última hora'),
      stat(cookies ? String(cookies.count) : DASH, 'cookies na sessão'),
      h('div', null, h('b', { class: 'num', 'data-static': '', text: ses ? ses.label : DASH }), h('span', { text: 'sessão do Studio' }))));
  return [hero, gain, today];
}
