// @ts-check
// Mestre (D52/D53): só o dono. Três partes como abas internas, em linguagem simples: Usuários (um cartão legível por
// conta: nome, ativo ou suspenso, último acesso e o uso de conjuntos, lives e contador com barra e "2 de 5"; as ações do
// dia a dia à vista e as perigosas agrupadas em "Mais ações", sempre confirmadas), Atividade (terminal com o registro
// de auditoria, filtrável por usuário) e Uso do servidor (totais e anéis). Mais "Dados do usuário": os conjuntos e o
// contador de um usuário, com os mesmos componentes de conjunto/live (o mestre edita pelo id). A explicação de cada
// parte mora num infoTip. A senha provisória só vive no texto do pop-up: nada de storage, URL ou log; some ao fechar.

import { api } from './api.js';
import { byId, inputById, buttonById, showMessage, clearMessage } from './dom.js';
import { h, icon, setStyle } from './h.js';
import { ring } from './charts.js';
import { crumbs } from './nav.js';
import { write, requestRefresh } from './net.js';
import { armedButton, levelPill, toast } from './ui.js';
import { collapse } from './collapse.js';
import { dropdown, setDropdownOptions } from './dropdown.js';
import { infoTip, setInfoTip } from './infotip.js';
import { openDanger, initDanger, resetDanger, clearMyData } from './danger.js';
import { mountActivity, terminalCard, resetLog } from './parts.js';
import { setList, fetchSparks } from './setcard.js';
import { state, subsNow } from './store.js';
import {
  DASH, fmt, inheritNumbers, relTime, setText, toEpoch,
} from './util.js';

/** @typedef {import('./store.js').Route} Route */

const SEGMENTS = /** @type {const} */ ([['usuarios', 'Usuários'], ['atividade', 'Atividade'], ['uso', 'Uso do servidor']]);
/** Explicação de cada parte (o texto fixo saiu da tela: fica no infoTip). */
const TIPS = /** @type {Record<string, string>} */ ({
  usuarios: 'Só você cria contas: não há cadastro aberto. Cada cartão mostra quanto o usuário já usa do que ele pode usar.',
  atividade: 'Quem fez o quê: acessos, senhas, contas, conjuntos, lives e cookies. As últimas 100 linhas.',
  uso: 'Totais da instalação e a ocupação do teto geral e do pool de contadores.',
});
const USERNAME_RE = /^[a-z0-9._-]{3,32}$/;
const MAX_SETS_LIMIT = 1000; // teto do schema da api (MasterUserUpdate)
const MAX_LIVES_LIMIT = 10000;
const RING_OK = '#34D399';
const RING_WARN = '#FBBF24';
const RING_BAD = '#FB7185';

/** @type {Record<string, any>} */
const R = {};
/** @type {(route: Route) => void} */
let go = () => {};
let usersSig = '';
let usageSig = '';
let actSig = '';
let dataSig = '';
/** @type {ReturnType<typeof setList> | null} */
let dataList = null;
/** Usuários cujo grupo "Mais ações" está aberto (sobrevive ao refazer o cartão a cada atualização). @type {Set<number>} */
const moreOpen = new Set();
/** @type {{id: number} | null} */
let editingUser = null;

/** @param {(route: Route) => void} navigate */
export function setMestreNavigator(navigate) {
  go = navigate;
}

/** @param {number} n @returns {string} */
function bytes(n) {
  if (typeof n !== 'number') return DASH;
  if (n < 1024) return `${fmt(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${(n / 1024 / 1024).toFixed(1).replace('.', ',')} MB`;
}

/** @param {number} pct @returns {string} */
const ringColor = (pct) => (pct >= 100 ? RING_BAD : pct >= 80 ? RING_WARN : RING_OK);

/**
 * Anel de uso: usado em relação ao limite (sem limite: anel vazio com o número usado).
 * @param {number} used
 * @param {number | null} max
 * @param {string} label
 * @param {boolean} animate
 * @returns {HTMLElement}
 */
function usageRing(used, max, label, animate) {
  const pct = max ? Math.min(100, (used / max) * 100) : null;
  const rg = ring({
    pct, color: pct === null ? RING_OK : ringColor(pct), center: max ? `${used}/${max}` : String(used), size: 62, label, animate,
  });
  return h('div', { class: 'u-ring' }, rg.root, h('small', { text: label }));
}

/** Barra de uso: preenche em % do limite, com a cor do nível (verde, amarelo a partir de 80 %, vermelho no limite). */
function useBar(/** @type {number} */ used, /** @type {number | null} */ max) {
  const fill = h('i');
  const pct = max ? Math.min(100, (used / max) * 100) : 0;
  fill.style.width = `${pct.toFixed(1)}%`;
  setStyle(fill, { background: max ? ringColor((used / max) * 100) : RING_OK });
  return h('span', { class: 'use-bar', 'aria-hidden': 'true' }, fill);
}

/**
 * Linha de uso: nome, barra e "2 de 5" (sem limite, só o número usado).
 * @param {string} label
 * @param {number} used
 * @param {number | null} max
 * @param {boolean} animate
 * @returns {HTMLElement}
 */
function useRow(label, used, max, animate) {
  const val = h('b', { class: 'num use-val' });
  setText(val, max ? `${fmt(used)} de ${fmt(max)}` : `${fmt(used)} · sem limite`, animate);
  return h('div', { class: 'use-row', role: 'group', 'aria-label': `${label}: ${max ? `${used} de ${max}` : used}` }, h('span', { class: 'use-label', text: label }), useBar(used, max), val);
}

/** Monta a estrutura fixa da tela e os pop-ups. */
export function initMestre() {
  R.head = h('div', { class: 'head' });
  R.tabs = h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Partes do painel mestre' });
  for (const [key, text] of SEGMENTS) {
    const b = h('button', { type: 'button', class: 'seg-btn', role: 'tab', id: `seg-${key}`, 'data-seg': key, text });
    b.addEventListener('click', () => go({ screen: 'mestre', setId: null, focus: null, seg: key }));
    R.tabs.appendChild(b);
  }
  R.crumbsBox = h('div', { class: 'crumbs-box', id: 'mestreCrumbs' });
  R.btnNew = h('button', { type: 'button', class: 'btn btn-primary btn-lg', id: 'btnNovoUsuario' }, icon('plus', 16), 'Novo usuário');
  R.btnNew.addEventListener('click', () => openNewUser());
  R.btnConta = h('button', { type: 'button', class: 'btn', id: 'btnMinhaConta', text: 'Minha conta' });
  R.btnConta.addEventListener('click', () => go({ screen: 'conta', setId: null, focus: null }));
  R.title = h('h1', { text: 'Mestre' });
  R.tip = infoTip(TIPS.usuarios, { label: 'Sobre esta parte' });
  R.actions = h('div', { class: 'actions' }, R.btnConta, R.btnNew);
  R.head.append(h('div', null, R.crumbsBox, h('div', { class: 'title-row' }, R.title, R.tip)), R.actions);

  R.count = h('span', { class: 'count', id: 'usersCount' });
  R.users = h('div', { class: 'user-cards', id: 'userCards' });
  R.actFilter = dropdown({ id: 'actFilter', label: 'Filtrar por usuário', className: 'select', options: [{ value: '', text: 'Todos os usuários' }], value: '' }).el;
  R.actFilter.addEventListener('change', () => { state.master.filter = /** @type {HTMLButtonElement} */ (R.actFilter).value; actSig = ''; renderActivity(false); });
  R.actBox = h('div', { class: 'term-box tall', id: 'logAtividade' });
  R.actEmpty = h('p', { class: 'empty m-empty', hidden: true, text: 'Nenhuma atividade deste usuário ainda.' });
  R.act = h('div', { class: 'seg-body', id: 'segAtividade' },
    h('div', { class: 'toolbar' }, h('label', { for: 'actFilter', text: 'Usuário' }), R.actFilter),
    R.actEmpty,
    h('div', { class: 'bento' }, terminalCard('atividade.log', R.actBox, 'b-wide')));
  R.usage = h('div', { class: 'bento usage-grid', id: 'usageGrid' });
  R.data = h('div', { class: 'seg-body', id: 'segDados' });
  R.usersEmpty = h('p', { class: 'empty m-empty', hidden: true, text: 'Nenhum usuário ainda. Use "Novo usuário" para criar o primeiro.' });
  R.segUsers = h('div', { class: 'seg-body', id: 'segUsuarios' },
    h('div', { class: 'm-sec-h' }, h('h2', { text: 'Usuários' }), R.count, h('span', { class: 'rule' })),
    R.usersEmpty, R.users);
  R.segUso = h('div', { class: 'seg-body', id: 'segUso' }, R.usage);
  byId('scr-mestre').replaceChildren(R.head, R.tabs, R.segUsers, R.act, R.segUso, R.data);
  initDialogs();
}

// ------------------------------------------------------------------ pop-ups

function initDialogs() {
  byId('userForm').addEventListener('submit', (ev) => { ev.preventDefault(); void saveUser(); });
  byId('tempCopy').addEventListener('click', async () => {
    const btn = byId('tempCopy');
    let msg = 'Copiado';
    try {
      await navigator.clipboard.writeText(byId('tempPass').textContent || '');
    } catch (e) {
      const sel = window.getSelection();
      if (sel) sel.selectAllChildren(byId('tempPass'));
      msg = 'Selecionado (Ctrl+C)';
    }
    btn.textContent = msg;
    window.setTimeout(() => { btn.textContent = 'Copiar'; }, 1500);
  });
  const pass = /** @type {HTMLDialogElement} */ (byId('dlgPass'));
  byId('passDone').addEventListener('click', () => pass.close());
  pass.addEventListener('close', () => { byId('tempPass').textContent = ''; byId('passWho').textContent = ''; byId('tempCopy').textContent = 'Copiar'; });
  initDanger();
}

/** Limpa os pop-ups do mestre (sessão encerrada): a senha provisória some do DOM. */
export function resetMestreDialogs() {
  byId('tempPass').textContent = '';
  byId('passWho').textContent = '';
  resetDanger();
  editingUser = null;
  moreOpen.clear();
}

/**
 * @param {string} who texto que diz de quem é a senha
 * @param {string} password
 */
function showTempPassword(who, password) {
  byId('passWho').textContent = who;
  byId('tempPass').textContent = password;
  byId('tempCopy').textContent = 'Copiar';
  /** @type {HTMLDialogElement} */ (byId('dlgPass')).showModal();
}

/** @param {any | null} user null = novo usuário */
function openUserForm(user) {
  editingUser = user ? { id: user.id } : null;
  byId('dlgUserT').textContent = user ? `Editar @${user.username}` : 'Novo usuário';
  byId('uNameField').hidden = !!user;
  byId('uHint').hidden = !!user;
  byId('uSave').textContent = user ? 'Salvar' : 'Criar usuário';
  inputById('uName').value = '';
  inputById('uDisplay').value = user ? user.display_name || '' : '';
  inputById('uSets').value = String(user ? user.max_sets : 5);
  inputById('uLives').value = String(user ? user.max_lives : 20);
  inputById('uCounter').checked = user ? !!user.has_counter : true;
  clearMessage(byId('uErr'));
  /** @type {HTMLDialogElement} */ (byId('dlgUser')).showModal();
  (user ? inputById('uDisplay') : inputById('uName')).focus();
}

export function openNewUser() {
  openUserForm(null);
}

/**
 * @param {string} raw
 * @param {string} what
 * @param {number} max
 * @returns {number | string}
 */
function readLimit(raw, what, max) {
  const t = raw.trim();
  const n = Number(t);
  if (!/^\d+$/.test(t) || !Number.isInteger(n) || n < 0 || n > max) return `${what}: informe um número inteiro de 0 a ${fmt(max)}.`;
  return n;
}

async function saveUser() {
  const err = byId('uErr');
  clearMessage(err);
  const sets = readLimit(inputById('uSets').value, 'Máximo de conjuntos', MAX_SETS_LIMIT);
  const lives = readLimit(inputById('uLives').value, 'Máximo de lives', MAX_LIVES_LIMIT);
  if (typeof sets === 'string') { showMessage(err, sets); return; }
  if (typeof lives === 'string') { showMessage(err, lives); return; }
  const display = inputById('uDisplay').value.trim();
  const counter = inputById('uCounter').checked;
  const btn = buttonById('uSave');
  if (editingUser) {
    btn.disabled = true;
    const resp = await write('PUT', `/api/admin/master/users/${editingUser.id}`, {
      display_name: display, max_sets: sets, max_lives: lives, has_counter: counter,
    }, err);
    btn.disabled = false;
    if (!resp) return;
    /** @type {HTMLDialogElement} */ (byId('dlgUser')).close();
    toast('Usuário salvo');
    await requestRefresh();
    return;
  }
  const username = inputById('uName').value.trim();
  if (!USERNAME_RE.test(username)) {
    showMessage(err, 'Nome de usuário inválido: use de 3 a 32 caracteres entre letras minúsculas, números, ponto, hífen e sublinhado.');
    return;
  }
  /** @type {Record<string, unknown>} */
  const body = { username, max_sets: sets, max_lives: lives, has_counter: counter };
  if (display) body.display_name = display;
  btn.disabled = true;
  const resp = await write('POST', '/api/admin/master/users', body, err);
  btn.disabled = false;
  if (!resp) return;
  /** @type {HTMLDialogElement} */ (byId('dlgUser')).close();
  const temp = resp.data && typeof resp.data.temp_password === 'string' ? resp.data.temp_password : '';
  showTempPassword(`Usuário @${username} criado. Senha provisória:`, temp);
  await requestRefresh();
}

// ------------------------------------------------------------------ usuários

/** @param {string | undefined | null} st @param {boolean} has */
function counterPill(st, has) {
  if (!has) return levelPill('off', 'contador desativado');
  if (st === 'ok') return levelPill('ok', 'contador lendo');
  if (st === 'degraded') return levelPill('warn', 'contador degradado');
  if (st === 'error') return levelPill('bad', 'contador com erro');
  return levelPill('off', 'contador sem leitura');
}

/**
 * Cartão de um usuário: quem é, se está ativo, o último acesso, o uso de conjuntos, lives e contador (barra e "2 de 5"),
 * as ações do dia a dia à vista e as perigosas dentro de "Mais ações". O mestre vê o próprio cartão com "Limpar meus dados".
 * @param {any} u
 * @param {boolean} animate
 * @returns {HTMLElement}
 */
function userCard(u, animate) {
  const usage = u.usage || {};
  const isMaster = u.role === 'master';
  const mine = !!state.user && state.user.id === u.id;
  const suspended = u.status === 'suspended';
  const name = u.display_name || u.username;
  const lastSeen = toEpoch(u.last_seen_at);
  const btn = (/** @type {string} */ text, /** @type {string} */ cls, /** @type {string} */ id) => h('button', { type: 'button', class: `btn ${cls}`.trim(), 'data-act': id, 'aria-label': `${text}: ${u.username}`, text });
  const bData = btn('Dados do usuário', '', 'data');
  bData.addEventListener('click', () => go({ screen: 'mestre', setId: null, focus: null, seg: 'dados', userId: u.id }));
  const main = h('div', { class: 'u-actions' }, bData);
  const danger = h('div', { class: 'u-danger-list' });
  if (!isMaster) {
    const bEdit = btn('Editar limites', '', 'edit');
    bEdit.addEventListener('click', () => openUserForm(u));
    const bSus = btn(suspended ? 'Reativar' : 'Suspender', suspended ? '' : 'warn-btn', 'suspend');
    armedButton(bSus, suspended ? 'Confirmar: reativa' : 'Confirmar: derruba a sessão', async () => {
      const resp = await write('PUT', `/api/admin/master/users/${u.id}`, { status: suspended ? 'active' : 'suspended' }, null);
      if (resp) { toast(suspended ? 'Usuário reativado' : 'Usuário suspenso'); void requestRefresh(); }
    });
    main.append(bEdit, bSus);
    const bReset = btn('Redefinir senha', '', 'reset');
    armedButton(bReset, 'Confirmar: gera senha nova', async () => {
      const resp = await write('POST', `/api/admin/master/users/${u.id}/reset-password`, undefined, null);
      if (!resp) return;
      showTempPassword(`Nova senha provisória de @${u.username}:`, resp.data && typeof resp.data.temp_password === 'string' ? resp.data.temp_password : '');
      void requestRefresh();
    });
    const bPurge = btn('Limpar dados', 'danger', 'purge');
    bPurge.addEventListener('click', () => openDanger({
      title: `Limpar os dados de @${u.username}`,
      text: 'Apaga todos os conjuntos, lives, leituras e o contador deste usuário. A conta continua. Não dá para desfazer.',
      expected: u.username,
      go: 'Limpar dados',
      run: async (confirm) => {
        const resp = await write('POST', `/api/admin/master/users/${u.id}/purge`, { confirm }, byId('dangerErr'));
        if (resp) { toast('Dados apagados'); void requestRefresh(); }
        return !!resp;
      },
    }));
    const bDel = btn('Apagar conta', 'danger', 'delete');
    bDel.addEventListener('click', () => openDanger({
      title: `Apagar a conta de @${u.username}`,
      text: 'Apaga a conta e tudo o que ela criou: conjuntos, lives, leituras e contador. Não dá para desfazer.',
      expected: u.username,
      go: 'Apagar conta',
      run: async (confirm) => {
        const resp = await write('DELETE', `/api/admin/master/users/${u.id}`, { confirm }, byId('dangerErr'));
        if (resp) { toast('Conta apagada'); void requestRefresh(); }
        return !!resp;
      },
    }));
    danger.append(bReset, bPurge, bDel);
  } else if (mine) {
    const bMine = btn('Limpar meus dados', 'danger', 'clear-mine');
    bMine.addEventListener('click', () => clearMyData());
    danger.append(bMine);
  }
  /** @type {HTMLElement | null} */
  let more = null;
  if (danger.childElementCount) {
    const group = collapse(h('div', { class: 'u-danger' },
      h('p', { class: 'u-danger-h', text: 'Ações perigosas' }), danger), `more-${u.id}`, { open: moreOpen.has(u.id) });
    const toggle = h('button', { type: 'button', class: 'btn btn-ghost u-more', 'data-act': 'more', 'aria-label': `Mais ações: ${u.username}` }, h('span', { text: 'Mais ações' }), icon('down', 12));
    group.bind(toggle);
    toggle.addEventListener('click', () => {
      const open = !group.isOpen();
      group.set(open);
      if (open) moreOpen.add(u.id); else moreOpen.delete(u.id);
    });
    more = h('div', { class: 'u-more-box' }, toggle, group.root);
  }
  const el = h('div', { class: `card glow-card user-card ${suspended ? 'acc-rose' : isMaster ? 'acc-amber' : 'acc-indigo'}`, 'data-user': u.id, 'data-username': u.username },
    h('div', { class: 'u-head' },
      h('span', { class: 'avatar', text: name.trim().slice(0, 1).toUpperCase() }),
      h('div', { class: 'u-who' }, h('h3', { text: name }), h('span', { class: 'count mono', text: `@${u.username}` })),
      h('div', { class: 'u-pills' },
        levelPill(suspended ? 'bad' : 'ok', suspended ? 'suspenso' : 'ativo'),
        isMaster ? levelPill('warn', 'mestre') : null,
        u.must_change_password ? levelPill('warn', 'senha provisória') : null)),
    h('p', { class: 'count u-seen', text: lastSeen !== null ? `Último acesso: ${relTime(lastSeen)}` : 'Nunca acessou' }),
    h('div', { class: 'u-usage' },
      useRow('Conjuntos', usage.sets || 0, isMaster ? null : u.max_sets, animate),
      useRow('Lives', usage.lives || 0, isMaster ? null : u.max_lives, animate),
      h('div', { class: 'use-row use-counter' }, h('span', { class: 'use-label', text: 'Contador' }), counterPill(usage.counter_state, !!u.has_counter), h('small', { class: 'num', text: `${fmt(usage.counter_loads_per_hour || 0)} cargas/h` }))),
    h('p', { class: 'count u-mini num' },
      `${fmt(usage.reads_per_min || 0)} leituras/min · ${fmt(usage.samples || 0)} amostras · `,
      h('span', { 'data-static': '', text: `${bytes(usage.approx_bytes)} de espaço` })),
    main,
    more);
  return el;
}

/** @param {boolean} first */
function renderUsers(first) {
  const users = state.master.users;
  R.count.textContent = users ? `${users.length} ${users.length === 1 ? 'conta' : 'contas'}` : 'Carregando…';
  if (!users) return;
  R.usersEmpty.hidden = users.length > 0;
  const sig = JSON.stringify(users);
  if (!first && sig === usersSig) return;
  // Um botão de duas etapas armado (Suspender, Redefinir senha) perde a confirmação se o cartão for refeito: espera
  // o clique ou os 4 s do próprio botão e refaz na atualização seguinte (usersSig só muda ao refazer).
  if (!first && R.users.querySelector('.armed')) return;
  usersSig = sig;
  const old = R.users;
  const fresh = users.map((/** @type {any} */ u) => userCard(u, first));
  if (!first) {
    const frag = document.createDocumentFragment();
    frag.append(...fresh);
    inheritNumbers(old, frag);
  }
  R.users.replaceChildren(...fresh);
}

// ------------------------------------------------------------------ atividade e uso

/** @param {boolean} first */
function renderActivity(first) {
  const events = state.master.activity;
  if (!events) return;
  const actors = new Set(events.map((/** @type {any} */ e) => String(e.actor || 'sistema')));
  for (const u of state.master.users || []) actors.add(u.username);
  const filter = state.master.filter;
  const key = JSON.stringify([events, filter, [...actors]]);
  if (!first && key === actSig) return;
  actSig = key;
  const sel = /** @type {HTMLButtonElement} */ (R.actFilter);
  setDropdownOptions(sel, [{ value: '', text: 'Todos os usuários' }, ...[...actors].sort().map((a) => ({ value: a, text: a }))]);
  sel.value = filter;
  const shown = filter ? events.filter((/** @type {any} */ e) => String(e.actor || 'sistema') === filter) : events;
  R.actEmpty.hidden = shown.length > 0;
  mountActivity(R.actBox, shown, first);
}

/** @param {boolean} first */
function renderUsage(first) {
  const u = state.master.usage;
  if (!u) return;
  const sig = JSON.stringify(u);
  if (!first && sig === usageSig) return;
  usageSig = sig;
  const lim = u.limits || {};
  const pool = u.user_pool || { size: 0, busy: 0 };
  const tile = (/** @type {string} */ title, /** @type {string} */ value, /** @type {string} */ foot, /** @type {HTMLElement | null} */ rg, cls = 'acc-indigo') => {
    const b = h('div', { class: 'value num' });
    setText(b, value, first);
    return h('div', { class: `card glow-card kpi ${cls}`, 'data-tile': title },
      h('div', { class: 'card-title' }, h('span', { text: title })),
      h('div', { class: 'kpi-ring' }, rg, h('div', null, b, h('div', { class: 'foot', text: foot }))));
  };
  const rg = (/** @type {number} */ used, /** @type {number} */ max, /** @type {string} */ label) => usageRing(used, max || null, label, first);
  R.usage.replaceChildren(
    tile('Usuários', fmt(u.users), 'contas cadastradas', null, 'acc-cyan'),
    tile('Conjuntos', fmt(u.sets), `teto geral: ${fmt(lim.max_sets_total)}`, rg(u.sets, lim.max_sets_total, 'conjuntos'), 'acc-indigo'),
    tile('Lives', fmt(u.lives), `teto geral: ${fmt(lim.max_lives_total)}`, rg(u.lives, lim.max_lives_total, 'lives'), 'acc-fuchsia'),
    tile('Pool de contadores', `${fmt(pool.busy)}/${fmt(pool.size)}`, `${fmt(u.counters_active)} contadores ativos`, rg(pool.busy, pool.size, 'pool'), 'acc-emerald'),
    tile('Leituras por minuto', fmt(u.reads_per_min), `${fmt(u.platform_pairs)} pares de plataforma distintos`, null, 'acc-amber'),
    tile('Banco de dados', bytes(u.db_bytes), 'tamanho do arquivo compartilhado', null, 'acc-rose'));
}

// ------------------------------------------------------------------ dados do usuário

/**
 * Cartão do contador do usuário (leitura): canal, número, estado, cookies.
 * @param {any} c
 * @returns {HTMLElement}
 */
function counterCard(c) {
  const ch = c && c.channel;
  const reading = c && c.reading;
  const ck = c && c.cookies ? c.cookies : { configured: false, count: 0 };
  const level = !c ? 'off' : c.state === 'ok' ? 'ok' : c.state === 'error' ? 'bad' : c.state === 'degraded' ? 'warn' : 'off';
  const read = reading ? toEpoch(reading.read_at) : null;
  return h('div', { class: 'card glow-card acc-yt', id: 'dadosContador' },
    h('div', { class: 'card-h' }, h('div', { class: 'title-row' }, h('h2', { text: 'Contador de inscritos' }), infoTip('Só leitura: o usuário configura os cookies e a aparência.', { label: 'Sobre o contador do usuário' })), levelPill(level, c ? String(c.state || 'sem leitura') : 'sem contador')),
    h('div', { class: 'set-stats' },
      h('div', null, h('b', { class: 'num', 'data-static': '', text: subsNow(reading) !== null ? fmt(/** @type {number} */ (subsNow(reading))) : DASH }), h('span', { text: 'inscritos agora' })),
      h('div', null, h('b', { 'data-static': '', text: ch && ch.name ? String(ch.name) : 'não detectado' }), h('span', { text: 'canal' })),
      h('div', null, h('b', { class: 'num', 'data-static': '', text: ck.configured ? `${ck.count} cookies` : 'sem cookies' }), h('span', { text: read !== null ? `última leitura ${relTime(read)}` : 'sem leitura ainda' }))));
}

/** @param {boolean} first */
function renderData(first) {
  const d = state.master.data;
  const uid = state.route.userId;
  const ok = d && d.user && d.user.id === uid;
  const name = ok ? (d.user.display_name || d.user.username) : '…';
  if (!ok) return;
  const sig = JSON.stringify(d);
  if (!first && sig === dataSig) return;
  dataSig = sig;
  if (!dataList) {
    const box = h('div', { class: 'set-cards bento', id: 'dadosSets' });
    dataList = setList(box, {
      empty: 'Este usuário ainda não tem conjuntos.',
      onOpen: (id) => go({ screen: 'conjunto', setId: id, focus: null, userId: state.route.userId ?? null }),
    });
    R.dataBox = box;
  }
  dataList.render(d.sets, first);
  R.data.replaceChildren(
    h('div', { class: 'zone' }, h('div', { class: 'zone-h' }, h('h2', { text: `Conjuntos de ${name}` }), h('span', { class: 'rule' })), R.dataBox),
    h('div', { class: 'zone' }, h('div', { class: 'zone-h' }, h('h2', { text: 'Contador' }), h('span', { class: 'rule' })), h('div', { class: 'bento' }, counterCard(d.counter))));
}

// ------------------------------------------------------------------ tela

/** Descarta o conteúdo (sessão encerrada). */
export function resetMestre() {
  usersSig = '';
  usageSig = '';
  actSig = '';
  dataSig = '';
  dataList = null;
  resetLog();
  if (R.users) {
    R.users.replaceChildren();
    R.usage.replaceChildren();
    R.actBox.replaceChildren();
    R.data.replaceChildren();
    R.crumbsBox.replaceChildren();
    R.count.textContent = '';
  }
  resetMestreDialogs();
}

/** Dados da parte aberta. @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const r = state.route;
  const seg = r.seg || 'usuarios';
  if (seg === 'usuarios') {
    const resp = await api('GET', '/api/admin/master/users');
    if (resp.ok && resp.data && Array.isArray(resp.data.users)) state.master.users = resp.data.users;
    return [resp];
  }
  if (seg === 'atividade') {
    const [resp, users] = await Promise.all([api('GET', '/api/admin/master/activity?limit=100'), api('GET', '/api/admin/master/users')]);
    if (resp.ok && resp.data && Array.isArray(resp.data.events)) state.master.activity = resp.data.events;
    if (users.ok && users.data && Array.isArray(users.data.users)) state.master.users = users.data.users;
    return [resp, users];
  }
  if (seg === 'uso') {
    const resp = await api('GET', '/api/admin/master/usage');
    if (resp.ok && resp.data) state.master.usage = resp.data;
    return [resp];
  }
  if (r.userId === undefined || r.userId === null) return [];
  const resp = await api('GET', `/api/admin/master/users/${r.userId}/data`);
  if (resp.ok && resp.data && Array.isArray(resp.data.sets)) {
    state.master.data = resp.data;
    for (const s of resp.data.sets) {
      const cur = state.foreign.get(s.id);
      state.foreign.set(s.id, cur && Array.isArray(cur.lives) && JSON.stringify(cur) === JSON.stringify(s) ? cur : s);
    }
    const sparks = await fetchSparks(resp.data.sets);
    return [resp, ...sparks];
  }
  return [resp];
}

/** @param {boolean} first */
export function render(first) {
  const r = state.route;
  const seg = r.seg || 'usuarios';
  for (const b of R.tabs.children) {
    const on = /** @type {HTMLElement} */ (b).dataset.seg === seg || (seg === 'dados' && /** @type {HTMLElement} */ (b).dataset.seg === 'usuarios');
    b.setAttribute('aria-selected', String(on));
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  }
  R.tabs.hidden = seg === 'dados';
  R.segUsers.hidden = seg !== 'usuarios';
  R.act.hidden = seg !== 'atividade';
  R.segUso.hidden = seg !== 'uso';
  R.data.hidden = seg !== 'dados';
  R.btnNew.hidden = seg !== 'usuarios';
  R.btnConta.hidden = false;
  setInfoTip(R.tip, seg === 'dados' && state.master.data && state.master.data.user && state.master.data.user.id === r.userId
    ? `@${state.master.data.user.username}. Conjuntos e contador desta conta; edite pelas páginas dos conjuntos.`
    : TIPS[seg] || TIPS.usuarios);
  if (seg === 'dados') {
    const d = state.master.data;
    const who = d && d.user && d.user.id === r.userId ? (d.user.display_name || d.user.username) : '…';
    R.title.textContent = `Dados de ${who}`;
    R.crumbsBox.replaceChildren(crumbs([{ text: 'Mestre', go: () => go({ screen: 'mestre', setId: null, focus: null, seg: 'usuarios' }) }, { text: 'Usuários', go: () => go({ screen: 'mestre', setId: null, focus: null, seg: 'usuarios' }) }, who]));
  } else {
    R.title.textContent = 'Mestre';
    R.crumbsBox.replaceChildren();
  }
  if (seg === 'usuarios') renderUsers(first);
  else if (seg === 'atividade') renderActivity(first);
  else if (seg === 'uso') renderUsage(first);
  else renderData(first);
}
