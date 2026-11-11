// @ts-check
// Cartão "Chat do conjunto" (D57), no fim da página do conjunto. Só aparece com a chave ligada (Configurar conjunto).
// Mostra: lives que entram no chat (um interruptor por live, gravado na hora), galeria dos 11 temas, aparência e
// filtros, endereço do overlay com Copiar e "Salvar chat". Nenhuma mensagem de chat passa pelo painel: as miniaturas
// usam textos de exemplo. Dado do servidor só entra por textContent/atributos; cor e rotação por CSSOM (CSP).

import { api } from './api.js';
import { h, s, setStyle } from './h.js';
import { dropdown } from './dropdown.js';
import { infoTip } from './infotip.js';
import { write, requestRefresh, sessionLost } from './net.js';
import { linkItem, toast } from './ui.js';
import { state, liveName, sortedLives } from './store.js';

const PLAT = /** @type {Record<string, {nome: string, sigla: string, cor: string, shapes: [string, Record<string, string>][]}>} */ ({
  yt: {
    nome: 'YouTube', sigla: 'YT', cor: '#FF0033',
    shapes: [['rect', { class: 'cm-b', x: '1', y: '5', width: '22', height: '14', rx: '4', fill: '#FF0033' }], ['path', { class: 'cm-f', d: 'M10 9v6l5.2-3z', fill: '#fff' }]],
  },
  tw: {
    nome: 'Twitch', sigla: 'TW', cor: '#9146FF',
    shapes: [
      ['path', { class: 'cm-b', d: 'M4.3 2 2 6.2V20h4.8v2.6h2.6L12 20h3.9L22 13.9V2z', fill: '#9146FF' }],
      ['path', { class: 'cm-f', d: 'M5.8 4h14.3v9.1l-3.3 3.3h-5L9.2 19v-2.6H5.8z', fill: '#fff' }],
      ['path', { class: 'cm-b', d: 'M11 7.2h1.9v5H11zM15.3 7.2h1.9v5h-1.9z', fill: '#9146FF' }],
    ],
  },
  kk: {
    nome: 'Kick', sigla: 'KICK', cor: '#53FC18',
    shapes: [['rect', { class: 'cm-b', x: '1', y: '1', width: '22', height: '22', rx: '5', fill: '#53FC18' }], ['path', { class: 'cm-f', d: 'M6.5 5.5h3.8v4.2h1.4V7.6h1.9V5.5h3.9v4.6h-1.9v3.8h1.9v4.6h-3.9v-2.1h-1.9v-2.1h-1.4v4.2H6.5z', fill: '#0b0b0b' }]],
  },
});
const PLAT_KEY = /** @type {Record<string, string>} */ ({ youtube: 'yt', twitch: 'tw', kick: 'kk' });
const THEMES = [['cartoon', 'Cartoon'], ['adesivo', 'Adesivo'], ['pixel', 'Pixel'], ['postit', 'Post-it'], ['chiclete', 'Chiclete'], ['classico', 'Clássico'], ['balao', 'Balão'], ['vidro', 'Vidro'], ['minimal', 'Minimal'], ['terminal', 'Terminal'], ['rodape', 'Rodapé']];
const SAMPLE = [
  { p: 'yt', name: 'gabi_fps', color: '#FFB020', text: 'que jogada!!', rot: -2 },
  { p: 'tw', name: 'Rafa Souza', color: '#4CC9F0', text: 'GG demais', rot: 1.6 },
];
const LIFETIMES = [[0, 'Só quando sair da tela'], [10, '10 segundos'], [30, '30 segundos'], [60, '1 minuto']];
const NAME_OK = /^[a-z0-9_.-]{1,40}$/;
const SAVED_MS = 2500;

/** @typedef {{theme: string, font_size: number, logo_style: string, name_color: string, lifetime_s: number, show_roles: boolean, hide_commands: boolean, ignored_users: string[], max_rate: number}} ChatCfg */

/** @type {Record<string, any>} */
const R = {};
/** @type {ChatCfg} */
let cur = defaults();
/** @type {ChatCfg | null} */
let loaded = null;
let loadedFor = /** @type {number | null} */ (null);
let loading = false;
let dirty = false;
let saving = false;
let linkKey = '';
let savedTimer = /** @type {number | null} */ (null);
/** @type {Map<number, {row: HTMLElement, sw: HTMLButtonElement, name: HTMLElement, pending: boolean}>} */
let rows = new Map();
/** @type {{id: string, mini: HTMLElement}[]} */
const minis = [];

/** @returns {ChatCfg} */
function defaults() {
  return {
    theme: 'classico', font_size: 18, logo_style: 'cor', name_color: 'usuario', lifetime_s: 0, show_roles: true,
    hide_commands: true, ignored_users: ['nightbot', 'streamelements', 'moobot', 'streamlabs', 'botrix'], max_rate: 8,
  };
}

/**
 * Logotipo da plataforma, desenhado por JS (cor própria ou branco).
 * @param {string} p
 * @param {boolean} mono
 */
function logoSvg(p, mono) {
  const d = PLAT[p];
  return s('svg', { viewBox: '0 0 24 24', class: `cm-logo${mono ? ' cm-mono' : ''}`, role: 'img', 'aria-label': d.nome },
    ...d.shapes.map(([tag, attrs]) => s(tag, attrs)));
}

/**
 * Interruptor acessível (botão role=switch).
 * @param {string} label
 * @param {boolean} on
 * @param {(next: boolean) => void} onToggle
 * @returns {HTMLButtonElement}
 */
function toggleSwitch(label, on, onToggle) {
  const b = /** @type {HTMLButtonElement} */ (h('button', { type: 'button', class: 'chat-switch', role: 'switch', 'aria-checked': String(on), 'aria-label': label }));
  b.addEventListener('click', () => onToggle(b.getAttribute('aria-checked') !== 'true'));
  return b;
}

/** @param {HTMLElement} sw @param {boolean} on */
function setSwitch(sw, on) {
  sw.setAttribute('aria-checked', String(on));
}

/**
 * Grupo de botões exclusivos (a escolha vai em `aria-pressed`).
 * @param {string} label
 * @param {[string, string][]} options
 * @param {(value: string) => void} onPick
 */
function segmented(label, options, onPick) {
  const root = h('div', { class: 'chat-seg', role: 'group', 'aria-label': label });
  /** @type {HTMLButtonElement[]} */
  const buttons = options.map(([value, text]) => {
    const b = /** @type {HTMLButtonElement} */ (h('button', { type: 'button', 'data-v': value, 'aria-pressed': 'false', text }));
    b.addEventListener('click', () => { set(value); onPick(value); });
    return b;
  });
  root.append(...buttons);
  /** @param {string} value */
  function set(value) {
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.v === value));
  }
  return { root, set };
}

/** @param {string} text */
function showError(text) {
  R.err.textContent = text;
  R.err.hidden = !text;
}

function markDirty() {
  dirty = true;
  if (R.saved) R.saved.hidden = true;
}

// ---------------------------------------------------------------------------------------------- miniaturas

/** @param {{p: string, name: string, color: string, text: string, rot: number}} m @param {string} theme */
function miniMsg(m, theme) {
  const nc = cur.name_color === 'usuario' ? m.color : cur.name_color === 'plataforma' ? PLAT[m.p].cor : '#ffffff';
  const msg = h('div', { class: 'cm-msg', 'data-p': m.p });
  if (theme === 'adesivo' || theme === 'postit') setStyle(msg, { '--rot': `${m.rot}deg` });
  if (theme === 'terminal') msg.append(h('span', { class: 'cm-hora', text: '21:40' }));
  const who = h('span', { class: 'cm-who' });
  if (cur.logo_style === 'sigla') who.append(h('span', { class: 'cm-sigla', 'data-p': m.p, text: PLAT[m.p].sigla }));
  else who.append(logoSvg(m.p, cur.logo_style === 'mono'));
  const name = h('span', { class: 'cm-name', text: m.name });
  setStyle(name, { color: nc, '--nc': nc });
  who.append(name);
  msg.append(who, h('span', { class: 'cm-txt', text: m.text }));
  return msg;
}

function renderMinis() {
  for (const { id, mini } of minis) {
    if (id === 'rodape') {
      const track = h('div', { class: 'cm-track' }, ...SAMPLE.map((m) => miniMsg(m, id)));
      mini.replaceChildren(h('div', { class: 'cm-band' }, h('div', { class: 'cm-band-tag', text: 'Chat' }), h('div', { class: 'cm-band-view' }, track)));
    } else {
      mini.replaceChildren(h('div', { class: `cm-chat cm-t-${id}` }, h('div', { class: 'cm-list' }, ...SAMPLE.map((m) => miniMsg(m, id)))));
    }
  }
}

/** @param {string} id */
function paintTheme(id) {
  for (const t of R.gallery.children) t.setAttribute('aria-checked', String(/** @type {HTMLElement} */ (t).dataset.id === id));
  const found = THEMES.find(([k]) => k === id);
  R.themeName.textContent = found ? found[1] : '';
}

// ---------------------------------------------------------------------------------------------- tags de ignorados

function renderTags() {
  R.tags.replaceChildren(...cur.ignored_users.map((u) => {
    const x = h('button', { type: 'button', 'aria-label': `Tirar ${u}`, text: '×' });
    x.addEventListener('click', () => {
      cur.ignored_users = cur.ignored_users.filter((v) => v !== u);
      markDirty();
      renderTags();
    });
    return h('span', { class: 'chat-tag' }, u, x);
  }));
}

/** Adiciona nomes (separados por espaço ou vírgula); se algum for inválido, não adiciona nenhum e avisa. @param {string} raw */
function addIgnored(raw) {
  /** @type {string[]} */
  const names = [];
  for (const piece of raw.split(/[,\s]+/)) {
    const name = piece.trim().toLowerCase();
    if (!name) continue;
    if (!NAME_OK.test(name)) {
      showError("Nome inválido: use de 1 a 40 caracteres entre letras, números, '.', '_' e '-'.");
      return;
    }
    if (!cur.ignored_users.includes(name) && !names.includes(name)) names.push(name);
  }
  showError('');
  if (names.length === 0) return;
  cur.ignored_users.push(...names);
  markDirty();
  renderTags();
}

// ---------------------------------------------------------------------------------------------- campos

/** Reflete `cur` nos controles. */
function paint() {
  paintTheme(cur.theme);
  R.fs.value = String(cur.font_size);
  R.fsOut.textContent = `${cur.font_size} px`;
  if (!LIFETIMES.some(([v]) => v === cur.lifetime_s)) {
    R.life.setOptions([...LIFETIMES, [cur.lifetime_s, `${cur.lifetime_s} segundos`]].map(([v, t]) => ({ value: String(v), text: String(t) })));
  }
  R.life.setValue(String(cur.lifetime_s));
  R.logo.set(cur.logo_style);
  R.cor.set(cur.name_color);
  setSwitch(R.roles, cur.show_roles);
  setSwitch(R.cmd, cur.hide_commands);
  R.rate.value = String(cur.max_rate);
  R.rateOut.textContent = `${cur.max_rate}/s`;
  renderTags();
  renderMinis();
}

/** @param {ChatCfg} c */
function fromServer(c) {
  return {
    theme: c.theme, font_size: c.font_size, logo_style: c.logo_style, name_color: c.name_color, lifetime_s: c.lifetime_s,
    show_roles: c.show_roles, hide_commands: c.hide_commands, ignored_users: [...c.ignored_users], max_rate: c.max_rate,
  };
}

/** Monta a estrutura fixa (uma vez) e devolve o cartão (começa escondido). @returns {HTMLElement} */
export function initChat() {
  R.count = h('em');
  R.picks = h('div', { class: 'chat-picks' });
  R.noLives = h('span', { class: 'hint', text: 'Este conjunto ainda não tem lives.', hidden: true });
  R.themeName = h('em');
  R.gallery = h('div', { class: 'chat-gallery', role: 'radiogroup', 'aria-label': 'Tema do chat' });
  for (const [id, nome] of THEMES) {
    const mini = h('div', { class: 'chat-mini' });
    const tile = h('button', { type: 'button', class: 'chat-tile', role: 'radio', 'aria-checked': 'false', 'data-id': id },
      mini, h('div', { class: 'chat-lab' }, h('b', { text: nome }), h('i', { 'aria-hidden': 'true', text: '✓' })));
    tile.addEventListener('click', () => { cur.theme = id; paintTheme(id); markDirty(); });
    minis.push({ id, mini });
    R.gallery.append(tile);
  }

  R.fs = h('input', { type: 'range', id: 'chatFs', min: 12, max: 40, step: 1, value: 18 });
  R.fsOut = h('output', { for: 'chatFs', text: '18 px' });
  R.fs.addEventListener('input', () => { cur.font_size = Number(R.fs.value); R.fsOut.textContent = `${cur.font_size} px`; markDirty(); });
  const life = dropdown({ id: 'chatLife', label: 'Sumir depois de', className: 'select', options: LIFETIMES.map(([v, t]) => ({ value: String(v), text: String(t) })), value: '0' });
  R.life = life;
  life.el.addEventListener('change', () => { cur.lifetime_s = Number(life.el.value); markDirty(); });
  R.logo = segmented('Ícone da plataforma', [['cor', 'Colorido'], ['mono', 'Branco'], ['sigla', 'Sigla']], (v) => { cur.logo_style = v; markDirty(); renderMinis(); });
  R.cor = segmented('Cor do nome', [['usuario', 'Do usuário'], ['plataforma', 'Plataforma'], ['branco', 'Branco']], (v) => { cur.name_color = v; markDirty(); renderMinis(); });
  R.roles = toggleSwitch('Mostrar cargos', true, (on) => { cur.show_roles = on; setSwitch(R.roles, on); markDirty(); });
  R.cmd = toggleSwitch('Esconder comandos', true, (on) => { cur.hide_commands = on; setSwitch(R.cmd, on); markDirty(); });
  R.rate = h('input', { type: 'range', id: 'chatRate', min: 1, max: 50, step: 1, value: 8 });
  R.rateOut = h('output', { for: 'chatRate', text: '8/s' });
  R.rate.addEventListener('input', () => { cur.max_rate = Number(R.rate.value); R.rateOut.textContent = `${cur.max_rate}/s`; markDirty(); });
  R.tags = h('div', { class: 'chat-tags', id: 'chatTags' });
  R.ign = h('input', { type: 'text', id: 'chatIgn', placeholder: 'Digite um nome e aperte Enter', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', maxlength: 40 });
  R.ign.addEventListener('keydown', (/** @type {KeyboardEvent} */ ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    addIgnored(R.ign.value);
    if (!R.err.textContent) R.ign.value = ''; // com erro, o texto fica para corrigir
  });

  R.linkHost = h('div', { class: 'chat-link-host' });
  R.err = h('p', { class: 'msg err', role: 'alert', hidden: true });
  R.saved = h('span', { class: 'save-msg', role: 'status', hidden: true, text: 'Salvo' });
  R.save = h('button', { type: 'button', class: 'btn btn-primary', id: 'chatSave', text: 'Salvar chat' });
  R.save.addEventListener('click', () => void saveChat());

  const field = (/** @type {string} */ label, /** @type {HTMLElement} */ control, /** @type {string} */ extra = '') => h('div', { class: `chat-f ${extra}`.trim() }, h('span', { class: 'chat-fl', text: label }), control);
  const switchRow = (/** @type {string} */ title, /** @type {string} */ sub, /** @type {HTMLElement} */ sw) => h('div', { class: 'chat-f' }, h('div', { class: 'chat-togg' }, h('span', null, title, h('small', { text: sub })), sw));

  R.root = h('div', { class: 'card glow-card acc-indigo b-wide chat-card', id: 'cardChat', hidden: true },
    h('div', { class: 'card-title' }, 'Chat do conjunto',
      infoTip('O overlay junta, num lugar só, o chat das lives marcadas abaixo, com o ícone de cada plataforma ao lado do nome. As mensagens não ficam guardadas.', { label: 'Sobre o chat do conjunto' })),
    h('p', { class: 'card-note', text: 'Junta num overlay só o chat das lives escolhidas abaixo. As mensagens passam e somem; nada fica guardado. Para desligar, use Configurar conjunto.' }),
    h('div', { class: 'chat-sec' }, h('div', { class: 'chat-sec-t' }, 'Lives que entram no chat', R.count), R.picks, R.noLives),
    h('hr', { class: 'chat-hr' }),
    h('div', { class: 'chat-sec' }, h('div', { class: 'chat-sec-t' }, 'Tema', R.themeName), R.gallery),
    h('hr', { class: 'chat-hr' }),
    h('div', { class: 'chat-sec' },
      h('div', { class: 'chat-sec-t', text: 'Aparência e filtros' }),
      h('div', { class: 'chat-opts' },
        h('div', { class: 'chat-f' }, h('label', { for: 'chatFs' }, 'Tamanho do texto ', R.fsOut), R.fs),
        h('div', { class: 'chat-f' }, h('label', { for: 'chatLife', text: 'Sumir depois de' }), life.el),
        field('Ícone da plataforma', R.logo.root),
        field('Cor do nome', R.cor.root),
        switchRow('Mostrar cargos', 'mod, vip, inscrito, membro', R.roles),
        switchRow('Esconder comandos', 'mensagens que começam com !', R.cmd),
        h('div', { class: 'chat-f w2' },
          h('label', { for: 'chatRate' }, 'Limite de mensagens por segundo ', R.rateOut), R.rate,
          h('span', { class: 'hint', text: 'Em chat muito rápido, moderador, VIP, inscrito e membro passam na frente; o excesso é descartado.' })),
        h('div', { class: 'chat-f w2' }, h('label', { for: 'chatIgn', text: 'Ignorar usuários (bots)' }), R.tags, R.ign))),
    h('hr', { class: 'chat-hr' }),
    h('div', { class: 'chat-foot' }, R.linkHost, R.saved, R.save),
    R.err,
    h('span', { class: 'hint' }, 'No OBS: fonte Navegador, 400 × 800. Para outro tema só numa cena, acrescente ', h('code', { text: '&tema=pixel' }), ' no fim do endereço.'));
  paint();
  return R.root;
}

// ---------------------------------------------------------------------------------------------- dados

/** @param {number} setId */
async function loadConfig(setId) {
  loading = true;
  try {
    const resp = await api('GET', `/api/admin/sets/${setId}/chat`);
    if (resp.status === 401) { sessionLost(); return; }
    if (loadedFor !== setId) return; // trocou de conjunto no meio
    if (!resp.ok || !resp.data) { showError('Não foi possível carregar a configuração do chat. Tentando de novo...'); loadedFor = null; return; }
    showError('');
    loaded = fromServer(resp.data);
    if (!dirty) { cur = fromServer(resp.data); paint(); }
  } catch (e) {
    showError('Não foi possível carregar a configuração do chat. Tentando de novo...');
    loadedFor = null;
  } finally {
    loading = false;
  }
}

async function saveChat() {
  const setId = loadedFor;
  if (setId === null || loaded === null || saving) return; // sem a configuração do servidor, salvar sobrescreveria com padrões
  const body = {
    theme: cur.theme, font_size: cur.font_size, logo_style: cur.logo_style, name_color: cur.name_color, lifetime_s: cur.lifetime_s,
    show_roles: cur.show_roles, hide_commands: cur.hide_commands, ignored_users: cur.ignored_users, max_rate: cur.max_rate,
  };
  saving = true;
  R.save.disabled = true;
  R.saved.hidden = true;
  const resp = await write('PUT', `/api/admin/sets/${setId}/chat`, body, R.err);
  saving = false;
  R.save.disabled = false;
  if (!resp || loadedFor !== setId) return;
  dirty = false;
  loaded = fromServer(resp.data);
  cur = fromServer(resp.data);
  paint();
  R.saved.hidden = false;
  if (savedTimer !== null) clearTimeout(savedTimer);
  savedTimer = window.setTimeout(() => { R.saved.hidden = true; }, SAVED_MS);
}

/**
 * Interruptor de uma live: grava `chat_enabled` na hora; se falhar, volta e avisa.
 * @param {number} liveId
 * @param {boolean} want
 */
async function toggleLive(liveId, want) {
  const row = rows.get(liveId);
  if (!row) return;
  row.pending = true;
  row.sw.disabled = true;
  setSwitch(row.sw, want);
  row.row.classList.toggle('on', want);
  const resp = await write('PUT', `/api/admin/lives/${liveId}`, { chat_enabled: want }, R.err);
  row.pending = false;
  row.sw.disabled = false;
  if (!resp) {
    setSwitch(row.sw, !want);
    row.row.classList.toggle('on', !want);
    toast('Não foi possível mudar a live no chat');
    return;
  }
  await requestRefresh();
}

/**
 * Atualiza o cartão com os dados do conjunto (chamado a cada atualização da tela).
 * @param {any} set
 */
export function renderChat(set) {
  const on = !!(set.chat && set.chat.enabled);
  R.root.hidden = !on;
  if (!on) return;
  if (loadedFor !== set.id) {
    loadedFor = set.id;
    loaded = null;
    dirty = false;
    cur = defaults();
    showError('');
    paint();
  }
  if (loaded === null && !loading) void loadConfig(set.id);
  R.save.disabled = loaded === null || saving;

  const lives = sortedLives(set);
  const seen = new Set();
  let enabled = 0;
  for (const live of lives) {
    seen.add(live.id);
    const want = !!live.chat_enabled;
    if (want) enabled += 1;
    let row = rows.get(live.id);
    if (!row) {
      const key = PLAT_KEY[live.platform] || 'yt';
      const sw = toggleSwitch('', want, (next) => void toggleLive(live.id, next));
      const name = h('span', { class: 'chat-nm' });
      const el = h('div', { class: 'chat-pl', 'data-live': live.id }, h('div', { class: 'chat-ic', 'aria-hidden': 'true' }, logoSvg(key, false)), name, sw);
      row = { row: el, sw, name, pending: false };
      rows.set(live.id, row);
    }
    const label = liveName(live);
    if (row.name.textContent !== label) row.name.textContent = label;
    row.name.title = label;
    row.sw.setAttribute('aria-label', `Colocar ${label} no chat`);
    if (!row.pending) { setSwitch(row.sw, want); row.row.classList.toggle('on', want); }
  }
  for (const [id, row] of rows) {
    if (!seen.has(id)) { row.row.remove(); rows.delete(id); }
  }
  const want = lives.map((l) => /** @type {HTMLElement} */ (rows.get(l.id)).row);
  const have = [...R.picks.children];
  if (want.length !== have.length || want.some((n, i) => n !== have[i])) R.picks.replaceChildren(...want);
  R.noLives.hidden = lives.length > 0;
  R.count.textContent = `${enabled} de ${lives.length}`;

  const path = set.chat.overlay;
  const key = `${set.id}|${state.publicBase}|${path}`;
  if (key !== linkKey) {
    linkKey = key;
    R.linkHost.replaceChildren(linkItem('Overlay do chat', `${state.publicBase}${path}`, 'link-card'));
  }
}

/** Descarta o estado (sessão encerrada ou troca de conjunto). */
export function resetChat() {
  loadedFor = null;
  loaded = null;
  loading = false;
  saving = false;
  dirty = false;
  linkKey = '';
  cur = defaults();
  rows = new Map();
  if (savedTimer !== null) clearTimeout(savedTimer);
  savedTimer = null;
  if (R.root) {
    R.root.hidden = true;
    R.picks.replaceChildren();
    R.linkHost.replaceChildren();
    showError('');
    R.saved.hidden = true;
    R.ign.value = '';
    paint();
  }
}
