// @ts-check
// Tela Inscritos: canal detectado pelos cookies (SÓ LEITURA), cookies do Studio, aparência do contador
// (sempre visível, com prévia) e links. O conteúdo colado nos cookies é segredo: só existe no textarea e na requisição.

import { api, errorText } from './api.js';
import { byId, showMessage, clearMessage } from './dom.js';
import { h } from './h.js';
import { registerFontSelect, setFontValue } from './fonts.js';
import { write, requestRefresh, sessionLost } from './net.js';
import { subscribersEndpoints } from './endpoints.js';
import { colorField, overlayPreview, linkItem, toast } from './ui.js';
import { subsCards } from './parts.js';
import { state, subsNow } from './store.js';
import {
  inheritNumbers, relTime, safeColor, toEpoch,
} from './util.js';

/** @type {Record<string, any>} */
const R = {};
let sig = '';
let linksKey = '';
let dirty = false;

/**
 * @param {string | undefined} authState
 * @returns {string}
 */
export function authMessage(authState) {
  switch (authState) {
    case 'login_required':
      return 'Atenção: cookies expirados. O YouTube Studio pediu login novamente; cole cookies novos abaixo.';
    case 'decrypt_failed':
      return 'Atenção: o worker não conseguiu decifrar os cookies salvos. Cole os cookies novamente abaixo.';
    case 'no_cookies':
      return 'Atenção: nenhum cookie foi aplicado ainda; o scraper não está autenticado. Cole os cookies abaixo.';
    default:
      return '';
  }
}

/** Monta a estrutura fixa da tela. */
export function initInscritos() {
  R.cards = h('div', { class: 'subs-trio', id: 'subsCards' });
  // cookies
  R.origin = h('p', { class: 'cookies-origin', id: 'cookiesOrigin', role: 'status', hidden: true });
  R.badge = h('div', { class: 'status-badge bad', id: 'cookieBadge', text: 'Nenhum cookie: o scraper não está autenticado' });
  R.area = h('textarea', {
    id: 'cookiesInput', class: 'cookies-input', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'Cole aqui o JSON do EditThisCookie, por exemplo:\n[{"name": "SID", "value": "…", "domain": ".youtube.com"}]',
  });
  R.ckBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'cookiesBtn', text: 'Salvar cookies' });
  R.ckMsg = h('p', { class: 'msg', id: 'cookiesMsg', role: 'status', hidden: true });
  R.ckBtn.addEventListener('click', () => void saveCookies());
  const cookieCard = h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Cookies do Studio' }), h('p', { text: 'O canal é descoberto pelos cookies. Para trocar de canal, envie cookies de outra conta.' }))),
    R.origin, R.badge,
    h('details', { class: 'steps' },
      h('summary', { text: 'Como exportar pelo EditThisCookie' }),
      h('ol', null,
        h('li', { text: 'No Chrome, abra studio.youtube.com logado na conta do canal.' }),
        h('li', { text: 'Clique no ícone do EditThisCookie e em Export (seta para baixo).' }),
        h('li', { text: 'O JSON vai para a área de transferência: cole abaixo e salve.' }))),
    h('div', { class: 'field' }, h('label', { for: 'cookiesInput', text: 'JSON de cookies do EditThisCookie' }), R.area),
    h('div', { class: 'form-actions' }, R.ckBtn), R.ckMsg);

  // aparência (sempre visível, com prévia)
  R.font = h('select', { id: 'font-subs' });
  registerFontSelect(R.font);
  R.color = colorField('Cor do texto', '#FFFFFF', () => { dirty = true; preview(); });
  R.pv = overlayPreview({ counter: true });
  R.font.addEventListener('change', () => { dirty = true; preview(); });
  R.lookBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'lookBtn', text: 'Salvar aparência' });
  R.lookOk = h('span', { class: 'save-msg', id: 'lookOk', role: 'status', hidden: true, text: '✓ Aparência salva' });
  R.lookErr = h('p', { class: 'msg err', id: 'lookErr', role: 'alert', hidden: true });
  R.lookBtn.addEventListener('click', () => void saveLooks());
  const lookCard = h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Aparência do contador' }), h('p', { text: 'Vale para o overlay /contador e para o JSON com aparência. A prévia muda na hora.' }))),
    h('div', { class: 'look-grid' },
      h('div', { class: 'stack' },
        h('div', { class: 'field' }, h('label', { for: 'font-subs', text: 'Fonte' }), R.font),
        R.color.root,
        h('div', { class: 'form-actions' }, R.lookBtn, R.lookOk), R.lookErr),
      R.pv.root));

  R.links = h('div', { class: 'links-grid', id: 'subsLinks' });
  byId('scr-inscritos').replaceChildren(
    h('div', { class: 'head' }, h('div', null, h('h1', { text: 'Contador e cookies' }), h('p', { text: 'O canal é descoberto pelos cookies do YouTube Studio. Só YouTube, um canal.' }))),
    R.cards,
    h('div', { class: 'grid-2' }, cookieCard, lookCard),
    h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Links do contador' }))), R.links));
}

/** Descarta o conteúdo e o texto dos cookies (sessão encerrada). */
export function resetInscritos() {
  sig = '';
  linksKey = '';
  dirty = false;
  if (!R.area) return;
  R.area.value = '';
  clearMessage(R.ckMsg);
  clearMessage(R.lookErr);
  R.lookOk.hidden = true;
  R.origin.hidden = true;
  R.origin.textContent = '';
  R.cards.replaceChildren();
  R.links.replaceChildren();
  R.color.set('#FFFFFF');
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const resp = await api('GET', '/api/admin/subscribers/history');
  if (resp.ok && resp.data) state.subHist = resp.data;
  return [resp];
}

function preview() {
  R.pv.update({
    likes: null, goal: null, viewers: subsNow(),
    bar: '#FFFFFF', text: R.color.get() || R.color.last(), font: R.font.value,
  });
}

function renderOrigin() {
  const c = state.status && state.status.cookies ? state.status.cookies : null;
  const created = c && typeof c.created_at === 'string' ? c.created_at : null;
  if (!c || !c.configured || !created) { R.origin.textContent = ''; R.origin.hidden = true; return; }
  const t = toEpoch(created);
  if (c.source === 'worker') R.origin.textContent = `Renovado automaticamente ${relTime(t)}`;
  else {
    const d = new Date(created);
    R.origin.textContent = `Colado no painel em ${Number.isNaN(d.getTime()) ? created : d.toLocaleString('pt-BR')}`;
  }
  R.origin.hidden = false;
}

/** @param {boolean} first */
export function render(first) {
  const ssig = JSON.stringify([state.subscribers, state.status && state.status.subscribers, state.status && state.status.cookies, state.settings && state.settings.channel, state.subHist, state.status && state.status.workers && state.status.workers.inscritos]);
  if (first || ssig !== sig) {
    sig = ssig;
    const fresh = subsCards(first);
    if (!first) {
      const frag = document.createDocumentFragment();
      frag.append(...fresh);
      inheritNumbers(R.cards, frag);
    }
    R.cards.replaceChildren(...fresh);
  }
  renderOrigin();
  const ck = state.status && state.status.cookies ? state.status.cookies : { configured: false, count: 0 };
  const loaded = !!ck.configured && ck.count > 0;
  R.badge.textContent = loaded ? `${ck.count} cookies carregados` : 'Nenhum cookie: o scraper não está autenticado';
  R.badge.className = `status-badge ${loaded ? 'ok' : 'bad'}`;
  if (!dirty && state.settings) {
    setFontValue(R.font, state.settings.subs_font);
    R.color.set(safeColor(state.settings.subs_text_color, '#FFFFFF'));
  }
  preview();
  const lk = state.publicBase;
  if (first || lk !== linksKey) {
    linksKey = lk;
    R.links.replaceChildren(...subscribersEndpoints().map((ep) => linkItem(ep.label, `${state.publicBase}${ep.path}`, 'link-card')));
  }
}

async function saveCookies() {
  clearMessage(R.ckMsg);
  /** @param {boolean} ok @param {string} text */
  const say = (ok, text) => { R.ckMsg.textContent = text; R.ckMsg.className = `msg ${ok ? 'ok' : 'err'}`; R.ckMsg.hidden = false; };
  const raw = R.area.value.trim();
  if (!raw) { say(false, 'Cole o JSON dos cookies antes de salvar.'); return; }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('Deve ser um array JSON');
  } catch (e) {
    say(false, `JSON inválido: ${e instanceof Error ? e.message : 'formato inesperado'}`);
    return;
  }
  R.ckBtn.disabled = true;
  try {
    const resp = await api('POST', '/api/admin/cookies', parsed);
    if (resp.status === 401) { sessionLost(); return; }
    if (!resp.ok) {
      say(false, `Erro: ${errorText(resp, 'desconhecido')}`);
      return;
    }
    R.area.value = '';
    say(true, resp.data && typeof resp.data.message === 'string' ? resp.data.message : 'Cookies salvos.');
    void requestRefresh();
  } catch (e) {
    say(false, 'Falha na requisição. Verifique a conexão e tente de novo.');
  } finally {
    R.ckBtn.disabled = false;
  }
}

async function saveLooks() {
  clearMessage(R.lookErr);
  R.lookOk.hidden = true;
  const txt = R.color.get();
  if (!txt) { showMessage(R.lookErr, 'Cor inválida: use o formato #RRGGBB.'); return; }
  R.lookBtn.disabled = true;
  const resp = await write('PUT', '/api/admin/settings', { subs_font: R.font.value, subs_text_color: txt }, R.lookErr);
  R.lookBtn.disabled = false;
  if (!resp) return;
  dirty = false;
  R.lookOk.hidden = false;
  window.setTimeout(() => { R.lookOk.hidden = true; }, 2500);
  toast('Aparência salva');
  void requestRefresh();
}
