// @ts-check
// Cliente do /api/admin. O token CSRF vive SOMENTE nesta variável de módulo
// (memória): nunca vai para localStorage, sessionStorage, cookie ou DOM.

/** @type {string | null} */
let csrfToken = null;

/** @param {string} token */
export function setCsrfToken(token) {
  csrfToken = token;
}

export function clearCsrfToken() {
  csrfToken = null;
}

/**
 * @typedef {object} ApiResponse
 * @property {number} status
 * @property {boolean} ok
 * @property {any} data corpo JSON, ou null se a resposta não for JSON
 * @property {number | null} retryAfter segundos (cabeçalho Retry-After)
 */

/**
 * Chama a api. Respostas HTTP de erro NÃO lançam: voltam em `status`.
 * Só falha de rede lança (TypeError do fetch).
 * @param {string} method
 * @param {string} path
 * @param {unknown} [body]
 * @returns {Promise<ApiResponse>}
 */
export async function api(method, path, body) {
  /** @type {Record<string, string>} */
  const headers = { Accept: 'application/json' };
  /** @type {RequestInit} */
  const init = { method, headers, credentials: 'same-origin', cache: 'no-store' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (method !== 'GET' && csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const res = await fetch(path, init);
  /** @type {any} */
  let data = null;
  const texto = await res.text();
  if (texto) {
    try {
      data = JSON.parse(texto);
    } catch (e) {
      // Corpo que não é JSON (ex.: página de erro de um proxy): segue sem dados.
      data = null;
    }
  }
  const ra = res.headers.get('Retry-After');
  const retryAfter = ra !== null && /^\d+$/.test(ra) ? Number(ra) : null;
  return { status: res.status, ok: res.ok, data, retryAfter };
}

/**
 * Extrai um texto de erro de uma resposta, sem nunca interpretar HTML.
 * `detail` string -> o próprio texto; lista de validação -> "Slot N: msg".
 * @param {ApiResponse} resp
 * @param {string} fallback
 * @returns {string}
 */
export function errorText(resp, fallback) {
  const detail = resp.data && typeof resp.data === 'object' ? resp.data.detail : undefined;
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(validationLine).join('; ');
  }
  return fallback;
}

/**
 * @param {any} item
 * @returns {string}
 */
function validationLine(item) {
  const msg = item && typeof item.msg === 'string' ? item.msg : 'valor inválido';
  /** @type {unknown[]} */
  const loc = item && Array.isArray(item.loc) ? item.loc : [];
  const idx = loc.find((p) => typeof p === 'number');
  if (loc.includes('slots') && typeof idx === 'number') {
    return `Slot ${idx + 1}: ${msg}`;
  }
  return msg;
}
