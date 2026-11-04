// @ts-check
// Formatação (pt-BR), cores seguras, movimento reduzido, relógio do servidor e contagem animada de números.

export const COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
export const DASH = '—';

/** Sugestões (no máximo 8): tons das paletas D44 de YouTube, Twitch e Kick, mais branco. */
export const SUGGESTED_COLORS = [
  '#FF4141', '#FFA48F', '#B3123A',
  '#A970FF', '#D6B8FF',
  '#53FC18', '#B4FF8A',
  '#FFFFFF',
];
/** @type {Record<string, string>} */
export const PLATFORM_COLOR = { youtube: '#FF4141', twitch: '#A970FF', kick: '#53FC18' };
/** @type {Record<string, string>} */
export const PLATFORM_NAME = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick' };
/** @type {Record<string, string>} */
export const PLATFORM_LETTER = { youtube: 'Y', twitch: 'T', kick: 'K' };
/** @type {Record<string, string>} */
export const PLATFORM_CLASS = { youtube: 'yt', twitch: 'tw', kick: 'kick' };

/**
 * Cor vinda do servidor: só `#RRGGBB`; senão o reserva.
 * @param {unknown} v
 * @param {string} [fallback]
 * @returns {string}
 */
export function safeColor(v, fallback = '#8F939C') {
  return typeof v === 'string' && COLOR_RE.test(v) ? v.toUpperCase() : fallback;
}

/** @returns {boolean} */
export function reduced() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** @param {unknown} n @returns {string} */
export function fmt(n) {
  return typeof n === 'number' && Number.isFinite(n) ? Math.round(n).toLocaleString('pt-BR') : DASH;
}

/** @param {number} n @param {number} [digits] @returns {string} */
export function fmtDec(n, digits = 1) {
  return n.toLocaleString('pt-BR', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/**
 * Aceita epoch em segundos, ISO 8601 ou null.
 * @param {unknown} v
 * @returns {number | null} epoch em segundos
 */
export function toEpoch(v) {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v) {
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : t / 1000;
  }
  return null;
}

/** @param {number} n @returns {string} */
const two = (n) => String(n).padStart(2, '0');

/**
 * @param {number} epoch segundos
 * @param {boolean} [seconds]
 * @returns {string} HH:MM[:SS] no fuso do navegador
 */
export function hhmm(epoch, seconds = false) {
  const d = new Date(epoch * 1000);
  return `${two(d.getHours())}:${two(d.getMinutes())}${seconds ? `:${two(d.getSeconds())}` : ''}`;
}

/** Diferença entre o relógio do servidor e o do navegador (ms), atualizada a cada /workers/status. */
let skewMs = 0;

/** @param {unknown} serverIso */
export function syncClock(serverIso) {
  const t = typeof serverIso === 'string' ? Date.parse(serverIso) : NaN;
  if (!Number.isNaN(t)) skewMs = t - Date.now();
}

/** @returns {number} agora, em epoch segundos, no relógio do servidor */
export function nowSec() {
  return (Date.now() + skewMs) / 1000;
}

/**
 * "há N s" / "em N s".
 * @param {number | null} epoch
 * @param {'past' | 'future'} [dir]
 * @returns {string}
 */
export function relTime(epoch, dir = 'past') {
  if (epoch === null) return DASH;
  const sec = Math.round(dir === 'past' ? nowSec() - epoch : epoch - nowSec());
  const abs = Math.max(0, sec);
  const pre = dir === 'past' ? 'há' : 'em';
  if (abs < 60) return `${pre} ${abs} s`;
  if (abs < 3600) return `${pre} ${Math.floor(abs / 60)} min`;
  if (abs < 86400) return `${pre} ${Math.floor(abs / 3600)} h`;
  return `${pre} ${Math.floor(abs / 86400)} d`;
}

/**
 * Teto "bonito" para um eixo.
 * @param {number} x
 * @returns {number}
 */
export function niceMax(x) {
  if (!(x > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(x));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= x) return m * p;
  return 10 * p;
}

/**
 * Cor do texto (preto/branco) legível sobre `hex`.
 * @param {string} hex
 * @returns {string}
 */
export function inkOn(hex) {
  const n = parseInt(hex.slice(1), 16);
  const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.6 ? '#0A0B0D' : '#FFFFFF';
}

/** @type {WeakMap<HTMLElement, number>} */
const counters = new WeakMap();

/**
 * Coloca o número no nó. Com `animate`, conta de 0 até o valor (~1 s, ease-out); com movimento
 * reduzido, ou sem `animate`, escreve direto o valor final.
 * @param {HTMLElement} node
 * @param {number | null} to
 * @param {boolean} animate
 */
export function setNumber(node, to, animate) {
  setText(node, fmt(to), animate && to !== null);
}

/** Números pt-BR dentro de um texto: milhares com ponto e decimais com vírgula. */
const NUM_TOKEN = /\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?/g;
/** Versões ("v2.1.0"), datas e horários ("01/11 14:32", "21:42") não contam: aparecem direto. */
const NOT_A_COUNT = /v\d+\.\d|\d{1,4}[/:-]\d{1,2}([/:-]\d{1,4})?|\b\d{1,2}h\d{2}\b/i;

/**
 * Anima TODOS os números de um texto (preservando prefixo/sufixo e o formato pt-BR). Textos que parecem
 * versão, data ou horário, e nós com `data-static`, são escritos direto. Sem `animate` (atualizações de 5 s)
 * ou com movimento reduzido, só troca o texto.
 * @param {HTMLElement} node
 * @param {string} text texto final
 * @param {boolean} animate
 */
export function setText(node, text, animate) {
  if (!animate && node.dataset.final === text) return; // nada mudou: não mexe no nó
  const running = counters.get(node);
  if (running) cancelAnimationFrame(running);
  counters.delete(node);
  const old = node.dataset.final === undefined ? node.textContent || '' : node.dataset.final;
  node.dataset.final = text;
  const parts = text.split(NUM_TOKEN);
  const nums = [...text.matchAll(NUM_TOKEN)].map((m) => m[0]);
  if (reduced() || !nums.length || NOT_A_COUNT.test(text) || node.hasAttribute('data-static')) {
    node.textContent = text;
    return;
  }
  const toValue = (/** @type {string} */ t) => Number(t.replace(/\./g, '').replace(',', '.'));
  const targets = nums.map((t) => ({ value: toValue(t), decimals: t.includes(',') ? t.split(',')[1].length : 0 }));
  /** Entrada: de 0 até o valor. Troca de valor: do número antigo para o novo (se o formato é o mesmo). */
  let from = targets.map(() => 0);
  if (!animate) {
    const oldNums = [...old.matchAll(NUM_TOKEN)].map((m) => toValue(m[0]));
    if (oldNums.length !== nums.length || NOT_A_COUNT.test(old)) {
      node.textContent = text;
      return;
    }
    from = oldNums;
  }
  const dur = animate ? 1000 : 700;
  const render = (/** @type {number} */ k) => {
    let out = parts[0];
    targets.forEach((t, i) => {
      const v = from[i] + (t.value - from[i]) * k;
      out += v.toLocaleString('pt-BR', { minimumFractionDigits: t.decimals, maximumFractionDigits: t.decimals }) + parts[i + 1];
    });
    node.textContent = out;
  };
  const t0 = performance.now();
  const tick = (/** @type {number} */ t) => {
    const k = Math.min(1, (t - t0) / dur);
    if (k < 1) {
      render(1 - (1 - k) ** 3);
      counters.set(node, requestAnimationFrame(tick));
    } else {
      counters.delete(node);
      node.textContent = text;
    }
  };
  render(0);
  counters.set(node, requestAnimationFrame(tick));
}

/**
 * Quando um bloco é refeito com valores novos, os números contam do valor antigo para o novo
 * (em vez de pular ou recomeçar de zero). Pareia os nós numéricos por posição.
 * @param {ParentNode} oldRoot
 * @param {ParentNode} newRoot
 */
export function inheritNumbers(oldRoot, newRoot) {
  const olds = [...oldRoot.querySelectorAll('[data-final]')];
  const news = [...newRoot.querySelectorAll('[data-final]')];
  news.forEach((n, i) => {
    const o = olds[i];
    if (!(o instanceof HTMLElement) || !(n instanceof HTMLElement)) return;
    const target = n.dataset.final;
    if (target === undefined || o.dataset.final === target) return;
    n.textContent = o.textContent;
    n.dataset.final = o.dataset.final;
    setText(n, target, false);
  });
}

/**
 * Número de inscritos a partir da string da leitura ("753.861", "460600").
 * @param {unknown} v
 * @returns {number | null}
 */
export function parseSubs(v) {
  if (typeof v !== 'string') return null;
  const digits = v.replace(/\D/g, '');
  return digits ? Number(digits) : null;
}
