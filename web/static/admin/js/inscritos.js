// @ts-check
// Tela Inscritos: canal lido (o detectado pelos cookies ou o escolhido pelo dono, D54), cookies do Studio, aparência do contador
// (sempre visível, com prévia) e links. O conteúdo colado nos cookies é segredo: só existe no textarea e na requisição.

import { api, errorText } from './api.js';
import { byId, showMessage, clearMessage } from './dom.js';
import { h } from './h.js';
import { fontDropdown, setFontValue } from './fonts.js';
import { titleTip } from './infotip.js';
import {
  write, requestRefresh, sessionLost, isPasswordRequired, passwordRequired,
} from './net.js';
import { subscribersEndpoints } from './endpoints.js';
import {
  armedButton, colorField, overlayPreview, linkItem, toast,
} from './ui.js';
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
let channelDirty = false;

/** Instante (s) em que o dono salvou cookies ou canal aqui; zero = nada em andamento. Vale até a 1ª leitura boa depois disso. */
let connectingSince = 0;
const CONNECT_TIMEOUT_S = 120;

/** Marca o começo de uma troca de sessão/canal: o painel mostra "Conectando" até o contador ler de novo. */
function startConnecting() {
  connectingSince = Date.now() / 1000;
}

/**
 * O contador está (re)conectando ao Studio? Verdadeiro logo depois de salvar cookies ou canal, até a primeira leitura
 * boa depois disso (ou 2 min); também quando o servidor ainda não aplicou os cookies ou o worker está iniciando.
 * @param {any} c contador (GET /api/admin/counter)
 * @returns {boolean}
 */
export function isConnecting(c = state.counter) {
  if (!c || !c.cookies || !c.cookies.configured) return false;
  const now = Date.now() / 1000;
  if (connectingSince) {
    const ok = toEpoch(c.last_success_at);
    if ((ok !== null && ok > connectingSince) || now - connectingSince > CONNECT_TIMEOUT_S) connectingSince = 0;
    else return true;
  }
  return c.state === 'starting' || !c.cookies.applied;
}

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
  R.cards = h('div', { class: 'subs-trio bento', id: 'subsCards' });
  // conectando: aparece logo depois de salvar cookies/canal, até o contador ler de novo
  R.connect = h('div', { class: 'connecting card', id: 'connecting', role: 'status', 'aria-live': 'polite', hidden: true },
    h('span', { class: 'spinner', 'aria-hidden': 'true' }),
    h('div', { class: 'connecting-text' },
      h('strong', { text: 'Conectando ao YouTube Studio…' }),
      h('span', { text: 'Abrindo a sessão, achando o canal e lendo o número. Costuma levar menos de 1 minuto.' })),
    h('span', { class: 'connecting-bar', 'aria-hidden': 'true' }));
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
  const cookieCard = h('div', { class: 'card glow-card acc-amber' },
    h('div', { class: 'card-h' }, titleTip('h2', 'Cookies do Studio', 'O canal padrão é o descoberto pelos cookies; para ler outro canal da conta, use "Canal para ler". Para trocar de conta, envie cookies de outra conta.')),
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
  R.font = fontDropdown('font-subs');
  R.color = colorField('Cor do texto', '#FFFFFF', () => { dirty = true; preview(); });
  R.pv = overlayPreview({ counter: true });
  R.font.addEventListener('change', () => { dirty = true; preview(); });
  R.lookBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'lookBtn', text: 'Salvar aparência' });
  R.lookOk = h('span', { class: 'save-msg', id: 'lookOk', role: 'status', hidden: true, text: '✓ Aparência salva' });
  R.lookErr = h('p', { class: 'msg err', id: 'lookErr', role: 'alert', hidden: true });
  R.lookBtn.addEventListener('click', () => void saveLooks());
  const lookCard = h('div', { class: 'card glow-card acc-fuchsia' },
    h('div', { class: 'card-h' }, titleTip('h2', 'Aparência do contador', 'Vale para o overlay /contador e para o JSON com aparência. A prévia muda na hora.')),
    h('div', { class: 'look-grid' },
      h('div', { class: 'stack' },
        h('div', { class: 'field' }, h('label', { for: 'font-subs', text: 'Fonte' }), R.font),
        R.color.root,
        h('div', { class: 'form-actions' }, R.lookBtn, R.lookOk), R.lookErr),
      R.pv.root));

  // canal para ler (D54): vazio = o canal padrão dos cookies; o contador de outro usuário nunca aparece aqui
  R.chNow = h('p', { class: 'channel-now', id: 'channelNow', role: 'status' });
  R.chBadge = h('span', { class: 'status-badge ok chosen-badge', id: 'channelChosen', text: 'canal escolhido', hidden: true });
  R.chState = h('p', { class: 'msg err', id: 'channelState', role: 'status', hidden: true });
  R.chInput = h('input', {
    type: 'text', id: 'channelInput', autocomplete: 'off', spellcheck: 'false', maxlength: '2048',
    placeholder: 'studio.youtube.com/channel/UC… ou UC…',
  });
  R.chInput.addEventListener('input', () => { channelDirty = true; });
  R.chBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'channelSave', text: 'Salvar canal' });
  R.chAuto = h('button', { type: 'button', class: 'btn', id: 'channelAuto', text: 'Usar o canal automático' });
  R.chOk = h('span', { class: 'save-msg', id: 'channelOk', role: 'status', hidden: true });
  R.chErr = h('p', { class: 'msg err', id: 'channelErr', role: 'alert', hidden: true });
  R.chBtn.addEventListener('click', () => void saveChannel(R.chInput.value.trim() || null));
  armedButton(R.chAuto, 'Confirmar: usar o automático', () => void saveChannel(null));
  const channelCard = h('div', { class: 'card glow-card acc-cyan' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Canal para ler' }), h('p', { text: 'Escolha qual canal da conta dos cookies o contador acompanha.' }))),
    h('div', { class: 'stack' },
      R.chNow, R.chBadge, R.chState,
      h('div', { class: 'field' },
        h('label', { for: 'channelInput', text: 'Canal para ler (opcional)' }), R.chInput,
        h('p', { class: 'hint', text: 'Cole o link do Studio do canal (ex.: studio.youtube.com/channel/UC...) ou o ID. Vazio = o canal padrão dos cookies.' })),
      h('div', { class: 'form-actions' }, R.chBtn, R.chAuto, R.chOk), R.chErr));

  R.links = h('div', { class: 'links-grid', id: 'subsLinks' });
  R.off = h('div', { class: 'card glow-card acc-amber', id: 'counterOff', hidden: true },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Contador desativado' }), h('p', { text: 'O contador de inscritos não está liberado para a sua conta. Fale com o administrador do painel.' }))));
  R.body = h('div', { class: 'screen-body', id: 'counterBody' },
    R.connect, R.cards,
    h('div', { class: 'grid-2' }, cookieCard, channelCard),
    lookCard,
    h('div', { class: 'card glow-card acc-indigo' }, h('div', { class: 'card-h' }, h('div', null, h('h2', { text: 'Links do contador' }))), R.links));
  byId('scr-inscritos').replaceChildren(
    h('div', { class: 'head' }, titleTip('h1', 'Contador', 'O canal é o descoberto pelos cookies do YouTube Studio, ou o que você escolher. Só YouTube, um canal.')),
    R.off, R.body);
}

/** Descarta o conteúdo e o texto dos cookies (sessão encerrada). */
export function resetInscritos() {
  sig = '';
  linksKey = '';
  dirty = false;
  channelDirty = false;
  connectingSince = 0;
  if (!R.area) return;
  R.area.value = '';
  clearMessage(R.ckMsg);
  clearMessage(R.lookErr);
  clearMessage(R.chErr);
  clearMessage(R.chState);
  R.chInput.value = '';
  R.chNow.textContent = '';
  R.chBadge.hidden = true;
  R.chOk.hidden = true;
  R.lookOk.hidden = true;
  R.origin.hidden = true;
  R.origin.textContent = '';
  R.cards.replaceChildren();
  R.links.replaceChildren();
  R.color.set('#FFFFFF');
  R.off.hidden = true;
}

/**
 * Lê o contador do usuário logado (D51). `counter_disabled` (403) não é erro: a tela mostra o aviso.
 * @returns {Promise<import('./api.js').ApiResponse>}
 */
export async function fetchCounter() {
  const resp = await api('GET', '/api/admin/counter');
  if (resp.ok && resp.data) {
    state.counter = resp.data;
    state.counterOff = false;
  } else if (resp.status === 403 && resp.data && resp.data.code === 'counter_disabled') {
    state.counter = null;
    state.counterOff = true;
    return { ...resp, ok: true, status: 200 };
  }
  return resp;
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  if (state.counterOff) return [];
  const resp = await api('GET', '/api/admin/subscribers/history');
  if (resp.ok && resp.data) state.subHist = resp.data;
  return [resp];
}

function preview() {
  const c = state.counter;
  R.pv.update({
    likes: null, goal: null, viewers: subsNow(),
    bar: '#FFFFFF', text: R.color.get() || R.color.last(), font: R.font.value,
  });
  void c;
}

function renderOrigin() {
  const c = state.counter && state.counter.cookies ? state.counter.cookies : null;
  const created = c && typeof c.uploaded_at === 'string' ? c.uploaded_at : null;
  if (!c || !c.configured || !created) { R.origin.textContent = ''; R.origin.hidden = true; return; }
  const d = new Date(created);
  R.origin.textContent = `Enviados em ${Number.isNaN(d.getTime()) ? created : d.toLocaleString('pt-BR')}${c.applied ? '' : ' · aguardando o contador aplicar'}`;
  R.origin.hidden = false;
}

/**
 * Endereço público completo: caminho relativo ganha a origem pública; endereço completo fica como veio.
 * @param {unknown} v
 * @returns {string | null}
 */
function publicUrl(v) {
  if (typeof v !== 'string' || !v) return null;
  return /^https?:\/\//i.test(v) ? v : `${state.publicBase}${v.startsWith('/') ? '' : '/'}${v}`;
}

/** Links do contador do usuário (vêm do servidor em `public`; sem eles, os caminhos do contador 1). */
function counterLinks() {
  const pub = state.counter && state.counter.public ? state.counter.public : null;
  const labelled = [['Overlay do contador', pub && pub.overlay], ['Inscritos (JSON)', pub && pub.json], ['JSON com aparência', pub && pub.subscribers_api]];
  const have = labelled.map(([label, v]) => [label, publicUrl(v)]).filter(([, u]) => u);
  if (have.length) return have;
  return subscribersEndpoints().map((ep) => [ep.label, `${state.publicBase}${ep.path}`]);
}

/** @param {boolean} first */
export function render(first) {
  R.off.hidden = !state.counterOff;
  R.body.hidden = state.counterOff;
  if (state.counterOff) return;
  const c = state.counter;
  const ssig = JSON.stringify([c, state.subHist]);
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
  const conn = isConnecting(c);
  R.connect.hidden = !conn;
  R.cards.classList.toggle('is-connecting', conn);
  R.cards.setAttribute('aria-busy', String(conn));
  renderOrigin();
  const ck = c && c.cookies ? c.cookies : { configured: false, count: 0 };
  const loaded = !!ck.configured && ck.count > 0;
  R.badge.textContent = loaded ? `${ck.count} cookies carregados` : 'Nenhum cookie: o scraper não está autenticado';
  R.badge.className = `status-badge ${loaded ? 'ok' : 'bad'}`;
  if (!dirty && c) {
    setFontValue(R.font, c.font);
    R.color.set(safeColor(c.text_color, '#FFFFFF'));
  }
  renderChannel(c);
  preview();
  const links = counterLinks();
  const lk = JSON.stringify(links);
  if (first || lk !== linksKey) {
    linksKey = lk;
    R.links.replaceChildren(...links.map(([label, url]) => linkItem(label, url, 'link-card')));
  }
}

/**
 * Canal efetivo (nome/ID), selo do canal escolhido e erro de acesso. O campo só acompanha o servidor enquanto o
 * usuário não digitou nada.
 * @param {any} c
 */
function renderChannel(c) {
  const chosen = c && typeof c.channel_override === 'string' && c.channel_override ? c.channel_override : null;
  const ch = c && c.channel ? c.channel : null;
  const reading = !!ch && (!chosen || ch.id === chosen);
  if (chosen) {
    R.chNow.textContent = reading
      ? `Lendo ${ch.name ? `${ch.name} · ` : ''}${ch.id}`
      : `Canal escolhido: ${chosen} (o contador troca na próxima leitura)`;
  } else {
    R.chNow.textContent = ch ? `Canal detectado pelos cookies: ${ch.name ? `${ch.name} · ` : ''}${ch.id}` : 'Canal ainda não detectado pelos cookies.';
  }
  R.chBadge.hidden = !chosen;
  if (c && c.error_code === 'channel_no_access') showMessage(R.chState, 'A conta dos cookies não tem acesso a este canal. Escolha outro canal ou volte ao automático.');
  else clearMessage(R.chState);
  if (!channelDirty) R.chInput.value = chosen || '';
}

/** @param {string | null} channelId ID, link do Studio/YouTube ou null (volta ao canal automático) */
async function saveChannel(channelId) {
  clearMessage(R.chErr);
  R.chOk.hidden = true;
  R.chBtn.disabled = true;
  R.chAuto.disabled = true;
  const resp = await write('PUT', '/api/admin/counter', { channel_id: channelId }, R.chErr);
  R.chBtn.disabled = false;
  R.chAuto.disabled = false;
  if (!resp) return;
  channelDirty = false;
  const chosen = resp.data && typeof resp.data.channel_override === 'string' ? resp.data.channel_override : '';
  R.chInput.value = chosen;
  R.chOk.textContent = chosen ? '✓ Canal salvo' : '✓ Canal automático';
  R.chOk.hidden = false;
  window.setTimeout(() => { R.chOk.hidden = true; }, 2500);
  toast(chosen ? 'Canal salvo' : 'Voltou ao canal automático');
  startConnecting();
  void requestRefresh();
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
    if (isPasswordRequired(resp)) { passwordRequired(); return; }
    if (!resp.ok) {
      say(false, `Erro: ${errorText(resp, 'desconhecido')}`);
      return;
    }
    R.area.value = '';
    say(true, resp.data && typeof resp.data.message === 'string' ? resp.data.message : 'Cookies salvos.');
    startConnecting();
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
  const resp = await write('PUT', '/api/admin/counter', { font: R.font.value, text_color: txt }, R.lookErr);
  R.lookBtn.disabled = false;
  if (!resp) return;
  dirty = false;
  R.lookOk.hidden = false;
  window.setTimeout(() => { R.lookOk.hidden = true; }, 2500);
  toast('Aparência salva');
  void requestRefresh();
}
