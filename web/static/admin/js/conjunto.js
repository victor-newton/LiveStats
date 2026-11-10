// @ts-check
// Tela do conjunto: 4 números, gráfico de linha (espectadores + likes, períodos 5/30/60/120 min), rosca por live,
// lista de lives que expande (links + configurações + prévia do overlay) e links do conjunto.
// Dados do servidor só entram por textContent/valores de campo. A atualização de 5 s só TROCA valores.

import { api } from './api.js';
import { byId } from './dom.js';
import { crumbs } from './nav.js';
import { h, icon, setStyle } from './h.js';
import { lineChart, donutChart, ring } from './charts.js';
import { setAccent } from './glow.js';
import { fontDropdown, setFontValue } from './fonts.js';
import { dropdown, setDropdownOptions } from './dropdown.js';
import { infoTip, setInfoTip } from './infotip.js';
import { write, readGoal, requestRefresh, sessionLost } from './net.js';
import { liveEndpoints, setEndpoints } from './endpoints.js';
import {
  colorField, overlayPreview, collapse, linkItem, statusPill, setPill, platBadge, platLogo, armedButton, toast,
} from './ui.js';
import {
  state, setById, liveStats, liveName, sortedLives, setTotals,
} from './store.js';
import { showMessage, clearMessage } from './dom.js';
import {
  fmt, fmtDec, hhmm, reduced, safeColor, setText, toEpoch, PLATFORM_NAME, PLATFORM_CLASS,
} from './util.js';
import { openAddLive, openConfigSet } from './dialogs.js';

const ERROR_TEXTS = {
  blocked: 'bloqueado pela plataforma',
  parse_error: 'página em formato inesperado',
  not_found: 'live não encontrada',
  http_error: 'erro HTTP',
  network_error: 'falha de rede',
  rate_limited: 'limite de requisições',
  invalid_response: 'resposta inválida',
};
const RANGES = [[5, 'Últimos 5 min'], [30, 'Últimos 30 min'], [60, 'Última hora'], [120, 'Últimas 2 horas']];
const STATE_TEXT = /** @type {Record<string, string>} */ ({
  ao_vivo: 'ao vivo', encerrada: 'encerrada', agendada: 'agendada', offline: 'offline', erro: 'erro',
});
const CHIP_HINT = '';
const STEP_TEXT = { 10: '10 s', 30: '30 s', 60: 'minuto' };

/** @typedef {ReturnType<typeof buildLive>} LiveItem */

/** @type {Record<string, any>} */
const R = {};
/** @type {Map<number, LiveItem>} */
let items = new Map();
let builtFor = /** @type {number | null} */ (null);
let chartKey = '';
let donutKey = '';
let linksKey = '';
let pendingAnimate = false;
let focusKey = '';
let crumbKey = '';
/** @type {(route: import('./store.js').Route) => void} */
let navigateTo = () => {};

/** @param {(route: import('./store.js').Route) => void} fn */
export function setNavigator(fn) {
  navigateTo = fn;
}

/** @param {HTMLElement} node @param {string} text */
function setTxt(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

/**
 * Tendência em porcentagem (15 min).
 * @param {HTMLElement} chip
 * @param {number | null | undefined} pct
 */
function setTrend(chip, pct) {
  if (typeof pct !== 'number') { chip.hidden = true; return; }
  chip.hidden = false;
  chip.classList.toggle('down', pct < 0);
  setTxt(chip, `${pct < 0 ? '↓' : '↑'} ${fmtDec(Math.abs(pct))}%`);
}

/** Monta a estrutura fixa da tela (uma vez): grade bento. */
export function initConjunto() {
  const scr = byId('scr-conjunto');
  R.title = h('h1', { id: 'setTitle' });
  R.sub = h('p', { id: 'setSub' });
  R.subTip = infoTip('A leitura de cada live acontece a cada 10 segundos. Os números do conjunto somam todas as lives dele.', { label: 'Sobre a leitura das lives' });
  R.btnConfig = h('button', { type: 'button', class: 'btn', id: 'btnConfigSet' }, icon('gear', 14), 'Configurar conjunto');
  R.btnAdd = h('button', { type: 'button', class: 'btn btn-primary', id: 'btnAddLive' }, icon('plus', 14), 'Adicionar live');
  R.btnConfig.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openConfigSet(s); });
  R.btnAdd.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openAddLive(s); });

  // números
  R.kViewers = h('span', { class: 'num v big', id: 'kViewers' });
  R.tViewers = h('span', { class: 'trend', hidden: true });
  R.kAvg = h('b', { class: 'num' });
  R.kMax = h('b', { class: 'num' });
  R.kLikes = h('span', { class: 'num v', id: 'kLikes' });
  R.kGoal = h('small', { class: 'num', id: 'kGoal' });
  R.ring = ring({ pct: null, color: '#34D399', center: '—', size: 58, label: 'Likes em relação à meta', animate: true });
  R.ringFoot = h('div', { class: 'foot', text: 'sem meta definida' });
  R.kOn = h('span', { class: 'num v', id: 'kOn' });
  R.kOnOf = h('small', { class: 'num' });
  R.pills = h('div', { class: 'foot pills' });
  R.kPeak = h('span', { class: 'num v', id: 'kPeak' });
  R.peakFoot = h('div', { class: 'foot' });
  const live = (/** @type {string} */ t) => h('span', { class: 'badge-live' }, h('i'), t);

  // cartão grande: gráfico de linha suave
  R.range = dropdown({
    id: 'range', label: 'Período do gráfico', className: 'select', value: String(state.range), options: RANGES.map(([v, t]) => ({ value: String(v), text: String(t) })),
  }).el;
  R.tChart = h('span', { class: 'trend', hidden: true });
  R.lineTip = infoTip('Soma das lives do conjunto.', { label: 'Sobre o gráfico' });
  R.lineSub = h('span', { id: 'lineSub', class: 'sr-only', text: 'Soma das lives do conjunto.' });
  R.svg = /** @type {SVGElement} */ (document.createElementNS('http://www.w3.org/2000/svg', 'svg'));
  R.svg.setAttribute('class', 'chart');
  R.svg.setAttribute('id', 'lineChart');
  R.svg.setAttribute('viewBox', '0 0 760 290');
  R.svg.setAttribute('role', 'img');
  R.svg.setAttribute('aria-label', 'Gráfico de linha de espectadores e likes');
  R.tip = h('div', { class: 'tip', hidden: true });
  R.range.addEventListener('change', () => void changeRange());
  const chartCard = h('div', { class: 'card glow-card acc-indigo b-chart', id: 'cardChart' },
    h('div', { class: 'card-title' }, h('span', null, 'Espectadores e likes'), live('ao vivo')),
    h('div', { class: 'stat-row' },
      h('div', { class: 'stat-main' }, h('div', { class: 'value' }, R.kViewers), h('div', { class: 'stat-sub' }, 'espectadores agora ', R.tViewers)),
      h('div', { class: 'stat-side' }, h('span', null, 'média ', R.kAvg), h('span', null, 'pico ', R.kMax)),
      h('div', { class: 'card-tools' },
        h('div', { class: 'legend' },
          h('span', null, h('i', { class: 'lg views' }), 'Espectadores'),
          h('span', null, h('i', { class: 'lg likes' }), 'Likes')),
        R.range)),
    h('div', { class: 'chart-sub' }, R.lineTip, R.lineSub, R.tChart),
    h('div', { class: 'chart-box' }, R.svg, R.tip));

  // cartão alto: rosca
  R.donut = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  R.donut.setAttribute('id', 'donut');
  R.donut.setAttribute('viewBox', '0 0 220 220');
  R.donut.setAttribute('role', 'img');
  R.donut.setAttribute('aria-label', 'Gráfico de rosca da participação de cada live');
  R.shareList = h('div', { class: 'share-list', id: 'shareList' });
  const donutCard = h('div', { class: 'card glow-card acc-fuchsia b-donut' },
    h('div', { class: 'card-title' }, h('span', { class: 'title-row' }, 'Participação por live', infoTip('Quanto cada live soma nos espectadores agora. Toque numa fatia ou numa linha da lista para fixar o destaque.', { label: 'Sobre a participação por live' }))),
    h('div', { class: 'donut-wrap' }, R.donut), R.shareList);

  // cartões pequenos
  const goalCard = h('div', { class: 'card glow-card acc-emerald kpi', id: 'cardGoal' },
    h('div', { class: 'card-title' }, h('span', null, 'Likes e meta (só YouTube)')),
    h('div', { class: 'kpi-ring' }, R.ring.root,
      h('div', null, h('div', { class: 'value' }, R.kLikes, ' ', R.kGoal), R.ringFoot)));
  const onCard = h('div', { class: 'card glow-card acc-cyan kpi' },
    h('div', { class: 'card-title' }, h('span', null, 'Lives ao vivo')),
    h('div', { class: 'value' }, R.kOn, ' ', R.kOnOf), R.pills);
  const peakCard = h('div', { class: 'card glow-card acc-amber kpi' },
    h('div', { class: 'card-title' }, h('span', null, 'Pico de hoje')),
    h('div', { class: 'value' }, R.kPeak), R.peakFoot);

  // cartão largo: lives como chips (estilo "Tech stack")
  R.chips = h('div', { class: 'chips', id: 'liveChips', role: 'group', 'aria-label': 'Lives do conjunto' });
  R.chipDesc = h('p', { class: 'chip-desc', id: 'chipDesc', 'aria-live': 'polite', text: CHIP_HINT });
  R.list = h('div', { class: 'lives', id: 'livesList' });
  R.empty = h('p', { class: 'empty', text: 'Este conjunto ainda não tem lives.', hidden: true });
  R.btnAddRow = h('button', { type: 'button', class: 'add-row', id: 'btnAddRow' }, icon('plus', 16), 'Adicionar live: cole o link do YouTube, da Twitch ou do Kick');
  R.btnAddRow.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openAddLive(s); });
  R.cardLives = h('div', { class: 'card glow-card acc-amber b-wide', id: 'cardLives' },
    h('div', { class: 'card-title' }, h('span', { class: 'title-row' }, 'Lives', infoTip('Toque ou clique numa live para abrir os links e as configurações dela. Passe o mouse ou use Tab para ver o título, os espectadores e os likes.', { label: 'Sobre as lives' }))),
    R.chips, R.chipDesc, R.list, R.empty, R.btnAddRow);

  // links
  R.links = h('div', { class: 'links-grid', id: 'setLinks' });
  R.cardLinks = h('div', { class: 'card glow-card acc-indigo b-wide', id: 'cardLinks' },
    h('div', { class: 'card-title' }, h('span', { class: 'title-row' }, 'Links do conjunto', infoTip('Somam todas as lives acima. Use no OBS, no vMix ou em artes próprias.', { label: 'Sobre os links do conjunto' }))),
    R.links);

  R.crumbs = h('div', { class: 'crumbs-box', id: 'setCrumbs' });
  R.picker = dropdown({ id: 'setPicker', label: 'Ir para outro conjunto', className: 'select picker' }).el;
  R.picker.addEventListener('change', () => {
    const id = Number(/** @type {HTMLButtonElement} */ (R.picker).value);
    const owner = state.route.userId ?? null;
    if (Number.isInteger(id) && id !== state.route.setId) navigateTo({ screen: 'conjunto', setId: id, focus: null, userId: owner });
  });
  scr.replaceChildren(
    h('div', { class: 'head' }, h('div', null, R.crumbs, R.title, h('div', { class: 'sub-row' }, R.sub, R.subTip)), h('div', { class: 'actions' }, R.btnConfig, R.btnAdd)),
    h('div', { class: 'bento bento-210' }, chartCard, donutCard, h('div', { class: 'b-side' }, goalCard, onCard, peakCard), R.cardLives, R.cardLinks));
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetConjunto() {
  items = new Map();
  builtFor = null;
  chartKey = '';
  donutKey = '';
  linksKey = '';
  focusKey = '';
  crumbKey = '';
  if (R.crumbs) R.crumbs.replaceChildren();
  if (R.list) R.list.replaceChildren();
  if (R.chips) R.chips.replaceChildren();
  if (R.chipDesc) R.chipDesc.textContent = CHIP_HINT;
  if (R.svg) R.svg.replaceChildren();
  if (R.donut) R.donut.replaceChildren();
  if (R.shareList) R.shareList.replaceChildren();
  if (R.links) R.links.replaceChildren();
  if (R.title) { R.title.textContent = ''; R.sub.textContent = ''; }
  if (R.tip) R.tip.hidden = true;
}

/** @returns {Promise<import('./api.js').ApiResponse[]>} */
export async function fetchExtra() {
  const id = state.route.setId;
  if (id === null || !setById(id)) return [];
  const range = state.range;
  const resp = await api('GET', `/api/admin/sets/${id}/history?range=${range}`);
  if (resp.ok && resp.data && Array.isArray(resp.data.points) && state.range === range) {
    state.hist.set(id, { range, data: resp.data });
  }
  return [resp];
}

async function changeRange() {
  const v = Number(/** @type {HTMLButtonElement} */ (R.range).value);
  if (![5, 30, 60, 120].includes(v)) return;
  state.range = v;
  pendingAnimate = true;
  if (!reduced()) R.svg.classList.add('out');
  try {
    const resp = (await fetchExtra())[0];
    if (resp && resp.status === 401) { sessionLost(); return; }
  } finally {
    R.svg.classList.remove('out');
  }
  renderChart(false);
}

/** @param {boolean} first */
function renderChart(first) {
  const set = setById(state.route.setId);
  const hs = set ? state.hist.get(set.id) : null;
  const data = hs && hs.range === state.range ? hs.data : null;
  const pts = data ? data.points : [];
  const last = pts.length ? pts[pts.length - 1] : null;
  const key = `${set ? set.id : 0}|${state.range}|${pts.length}|${last ? `${last.at}:${last.viewers}:${last.likes}` : ''}`;
  if (key === chartKey && !first && !pendingAnimate) return;
  const animate = first || pendingAnimate;
  pendingAnimate = false;
  chartKey = key;
  /** @type {HTMLButtonElement} */ (R.range).value = String(state.range);
  const step = data && typeof data.step_s === 'number' ? data.step_s : 60;
  const stepText = `Soma das lives do conjunto, um ponto a cada ${STEP_TEXT[/** @type {10 | 30 | 60} */ (step)] || `${step} s`}.`;
  setTxt(R.lineSub, stepText);
  setInfoTip(R.lineTip, stepText);
  setTrend(R.tChart, data ? data.change_15m_pct : null);
  const vs = pts.map((/** @type {any} */ p) => (typeof p.viewers === 'number' ? p.viewers : 0));
  setTxt(R.kAvg, vs.length ? fmt(vs.reduce((a, b) => a + b, 0) / vs.length) : '—');
  setTxt(R.kMax, vs.length ? fmt(Math.max(...vs)) : '—');
  lineChart(R.svg, pts, { animate, stepS: step });
}

/**
 * @param {any} live
 * @param {any} set
 */
function buildLive(live, set) {
  const id = live.id;
  let dirty = false;
  /** @type {any} */
  let cur = live;
  const heading = h('strong', { class: 'live-heading' });
  const title = h('small', { class: 'live-title' });
  const plat = platBadge(live.platform);
  const pill = statusPill(null);
  const vV = h('span', { class: 'num v' });
  const lV = h('span', { class: 'num v' });
  const lSmall = h('small');
  const fill = h('i');
  const expand = h('button', { type: 'button', class: 'expand' }, icon('down', 12));
  // chip da live (estilo "Tech stack"): ícone da plataforma, nome e estado
  const chipIco = h('span', { class: `chip-ico ${PLATFORM_CLASS[live.platform] || ''}`.trim(), 'aria-hidden': 'true' }, platLogo(live.platform));
  const chipName = h('span', { class: 'chip-name' });
  const chipState = h('span', { class: 'chip-state' });
  const chip = h('button', { type: 'button', class: 'chip', 'data-live': id }, chipIco, chipName, chipState);
  let desc = '';
  const showDesc = () => { R.chipDesc.textContent = desc; R.chipDesc.classList.add('on'); };
  const hideDesc = () => { R.chipDesc.textContent = CHIP_HINT; R.chipDesc.classList.remove('on'); };
  chip.addEventListener('pointerenter', showDesc);
  chip.addEventListener('pointerleave', hideDesc);
  chip.addEventListener('focus', showDesc);
  chip.addEventListener('blur', hideDesc);
  const row = h('div', { class: 'live-row', 'data-live': id },
    plat,
    h('div', { class: 'live-name' }, heading, title),
    h('span', { class: 'c-status' }, pill),
    h('span', { class: 'cell-num' }, vV, h('small', { text: 'espectadores' })),
    h('span', { class: 'cell-num' }, lV, lSmall),
    h('span', { class: 'c-share' }, h('span', { class: 'bar' }, fill)),
    expand);

  // ---- detalhe: links + configurações
  const base = state.publicBase;
  const linksBox = h('div', { class: 'detail-box links-box' }, h('h4', { text: `Links desta live (#${id})` }));
  for (const ep of liveEndpoints(id)) linksBox.appendChild(linkItem(ep.label, `${base}${ep.path}`));
  const urlIn = /** @type {HTMLInputElement} */ (h('input', { id: `lv${id}-url`, type: 'text', inputmode: 'url', maxlength: 2048, autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false' }));
  const nameIn = /** @type {HTMLInputElement} */ (h('input', { id: `lv${id}-name`, type: 'text', maxlength: 60, autocomplete: 'off' }));
  const goalIn = /** @type {HTMLInputElement} */ (h('input', { id: `lv${id}-goal`, type: 'text', inputmode: 'numeric', autocomplete: 'off', class: 'num' }));
  const fontSel = fontDropdown(`lv${id}-font`);
  const barField = colorField('Cor da live (barra e gráficos)', live.color, () => preview());
  const textField = colorField('Cor do texto', live.text_color, () => preview());
  const pv = overlayPreview();
  const okEl = h('span', { class: 'save-msg live-ok', role: 'status', hidden: true, text: '✓ Salvo com sucesso' });
  const errEl = h('p', { class: 'msg err live-err', role: 'alert', hidden: true });
  const field = (/** @type {string} */ label, /** @type {HTMLElement} */ input) => h('div', { class: 'field' }, h('label', { for: input.id, text: label }), input);
  const saveBtn = h('button', { type: 'button', class: 'btn btn-primary live-save', 'aria-label': `Salvar a live ${id}`, text: 'Salvar' });
  const delBtn = h('button', { type: 'button', class: 'btn btn-ghost danger live-delete', 'aria-label': `Remover a live ${id}`, text: 'Remover live' });
  linksBox.append(h('h4', { class: 'sp', text: 'Link de origem' }), field('Link de origem da live', urlIn));
  const cfgBox = h('div', { class: 'detail-box cfg-box' },
    h('h4', { text: 'Configurações' }),
    h('div', { class: 'row2' }, field('Nome no painel', nameIn), field('Meta de likes', goalIn)),
    h('div', { class: 'row2 colors' }, barField.root, textField.root),
    field('Fonte do overlay', fontSel),
    pv.root,
    h('div', { class: 'detail-actions' }, delBtn, okEl, saveBtn),
    errEl);
  const detail = h('div', { class: 'live-detail' }, linksBox, cfgBox);
  const col = collapse(h('div', { class: 'live-pane' }, row, detail), `det-${id}`);
  col.bind(chip);
  col.bind(expand);

  function preview() {
    const st = liveStats(cur);
    const g = readGoal(goalIn.value);
    pv.update({
      likes: st ? st.likes : null,
      goal: typeof g === 'string' ? null : g.goal,
      viewers: st ? st.viewers : null,
      bar: barField.get() || barField.last(),
      text: textField.get() || textField.last(),
      font: fontSel.value,
    });
  }
  cfgBox.addEventListener('input', () => { dirty = true; preview(); });
  cfgBox.addEventListener('change', () => { dirty = true; preview(); });
  linksBox.addEventListener('input', () => { dirty = true; });

  const item = h('div', { class: 'live-item', 'data-live': id }, col.root);
  const toggle = () => {
    const open = !col.isOpen();
    col.set(open); // a altura anima de 0 até o conteúdo (e volta); aria-expanded dos gatilhos vem do collapse
    chip.classList.toggle('open', open);
    row.classList.toggle('open', open);
  };
  chip.addEventListener('click', toggle);
  expand.addEventListener('click', (ev) => { ev.stopPropagation(); toggle(); });
  row.addEventListener('click', (ev) => { if (!(/** @type {HTMLElement} */ (ev.target)).closest('button')) toggle(); });

  saveBtn.addEventListener('click', async () => {
    const g = readGoal(goalIn.value);
    if (typeof g === 'string') { showMessage(errEl, g); return; }
    const bar = barField.get();
    const txt = textField.get();
    if (!bar || !txt) { showMessage(errEl, 'Cor inválida: use o formato #RRGGBB.'); return; }
    /** @type {HTMLButtonElement} */ (saveBtn).disabled = true;
    const resp = await write('PUT', `/api/admin/lives/${id}`, {
      url: urlIn.value,
      label: nameIn.value,
      goal: goalIn.disabled ? cur.goal : g.goal,
      color: bar,
      font: fontSel.value,
      text_color: txt,
    }, errEl);
    /** @type {HTMLButtonElement} */ (saveBtn).disabled = false;
    if (!resp) return;
    dirty = false;
    okEl.hidden = false;
    window.setTimeout(() => { okEl.hidden = true; }, 2500);
    toast('Alterações salvas');
    void requestRefresh();
  });
  armedButton(delBtn, 'Confirmar remoção', async () => {
    const resp = await write('DELETE', `/api/admin/lives/${id}`, undefined, errEl);
    if (resp) { toast('Live removida'); void requestRefresh(); }
  });

  return {
    root: item,
    chip,
    row,
    expand,
    open: () => { if (!col.isOpen()) toggle(); },
    /**
     * @param {any} l
     * @param {number} index
     * @param {number} totalViewers
     * @param {boolean} animate
     */
    update(l, index, totalViewers, animate) {
      cur = l;
      const st = liveStats(l);
      const color = safeColor(l.color);
      setTxt(heading, `${index + 1}. ${liveName(l)}`);
      setTxt(chipName, liveName(l));
      chipName.title = liveName(l);
      setTxt(chipState, st ? (STATE_TEXT[st.status] || st.status) : 'aguardando');
      chip.dataset.state = st ? String(st.status) : 'aguardando';
      setAccent(chip, color);
      setStyle(chip, { '--c': color });
      desc = `${liveName(l)}: ${(st && !st.error_code && st.title) || (st && st.error_code ? 'com erro de leitura' : 'aguardando dados')} · ${st ? fmt(st.viewers) : '—'} espectadores · ${st && st.likes !== null ? `${fmt(st.likes)} likes` : 'sem likes'}`;
      if (chip.matches(':hover, :focus')) showDesc();
      const detailText = st ? (st.error_code ? `Erro: ${/** @type {Record<string, string>} */ (ERROR_TEXTS)[st.error_code] || st.error_code}` : st.title) : 'Aguardando dados...';
      setTxt(title, detailText || '');
      title.title = detailText || '';
      setPill(pill, st ? st.status : null, st ? undefined : 'aguardando');
      setText(vV, st ? fmt(st.viewers) : '—', animate);
      setText(lV, st && st.likes !== null ? fmt(st.likes) : '—', animate);
      setTxt(lSmall, st && st.likes !== null ? 'likes' : 'sem likes');
      const pct = totalViewers && st && st.viewers ? (st.viewers / totalViewers) * 100 : 0;
      fill.style.width = `${pct.toFixed(1)}%`;
      setStyle(fill, { background: color });
      expand.setAttribute('aria-label', `Abrir links e configurações de ${liveName(l)}`);
      goalIn.disabled = l.platform !== 'youtube';
      goalIn.placeholder = l.platform === 'youtube' ? 'Sem meta' : 'sem likes nesta plataforma';
      nameIn.placeholder = liveName(l);
      if (!dirty && !detail.contains(document.activeElement)) {
        urlIn.value = l.url;
        nameIn.value = l.label || '';
        goalIn.value = typeof l.goal === 'number' ? String(l.goal) : '';
        barField.set(color);
        textField.set(safeColor(l.text_color, '#FFFFFF'));
        setFontValue(fontSel, l.font);
      }
      preview();
    },
  };
}

/**
 * Atualiza a tela. `first` = acabou de entrar (animações de entrada); senão só troca valores.
 * @param {boolean} first
 */
export function render(first) {
  const set = setById(state.route.setId);
  if (!set) return;
  const changedSet = builtFor !== set.id;
  if (changedSet) {
    builtFor = set.id;
    items = new Map();
    R.list.replaceChildren();
    R.chips.replaceChildren();
    chartKey = '';
    donutKey = '';
    linksKey = '';
  }
  const animate = first || changedSet;
  renderCrumbs(set);
  const lives = sortedLives(set);
  const tot = setTotals(set);
  const platforms = new Set(lives.map((l) => l.platform));
  setTxt(R.title, set.name);
  setTxt(R.sub, `${lives.length} ${lives.length === 1 ? 'live' : 'lives'} em ${platforms.size} ${platforms.size === 1 ? 'plataforma' : 'plataformas'}${typeof set.goal === 'number' ? ` · meta de ${fmt(set.goal)} likes` : ''}`);

  // números
  const hs = state.hist.get(set.id);
  const hist = hs && hs.range === state.range ? hs.data : (hs ? hs.data : null);
  setText(R.kViewers, fmt(tot.viewers), animate);
  setTrend(R.tViewers, hist ? hist.change_15m_pct : null);
  setText(R.kLikes, tot.likes === null ? '—' : fmt(tot.likes), animate);
  setText(R.kGoal, typeof set.goal === 'number' ? `/ ${fmt(set.goal)}` : '', animate);
  const pct = typeof set.goal === 'number' && tot.likes !== null ? Math.min(100, (tot.likes / set.goal) * 100) : 0;
  const hasGoal = typeof set.goal === 'number';
  R.ring.set(hasGoal ? pct : null, '#34D399', hasGoal ? `${Math.round(pct)}%` : '—');
  setTxt(R.ringFoot, hasGoal ? (pct >= 100 ? 'meta batida' : `${fmtDec(pct)}% da meta`) : 'sem meta definida');
  setText(R.kOn, String(tot.on), animate);
  setText(R.kOnOf, `de ${tot.total}`, animate);
  R.pills.replaceChildren(...lives.slice(0, 6).map((l) => {
    const st = liveStats(l);
    const p = statusPill(st ? st.status : null, PLATFORM_NAME[l.platform] || l.platform);
    p.title = `${liveName(l)}: ${st ? st.status : 'aguardando'}`;
    return p;
  }));
  const peak = hist ? hist.peak_today : null;
  setText(R.kPeak, peak ? fmt(peak.viewers) : '—', animate);
  const peakAt = peak ? toEpoch(peak.at) : null;
  setTxt(R.peakFoot, peak && peakAt !== null ? `às ${hhmm(peakAt)} · espectadores somados` : 'sem dados hoje');

  renderChart(animate);

  // rosca
  const dl = lives.map((l) => ({ name: liveName(l), color: safeColor(l.color), viewers: (liveStats(l) || {}).viewers || 0 }));
  const dkey = JSON.stringify(dl);
  if (dkey !== donutKey) {
    donutKey = dkey;
    donutChart(R.donut, R.shareList, dl, animate);
  }

  // lista de lives
  const seen = new Set();
  lives.forEach((l, i) => {
    seen.add(l.id);
    let it = items.get(l.id);
    if (!it) {
      it = buildLive(l, set);
      items.set(l.id, it);
    }
    it.update(l, i, tot.viewers, animate);
  });
  for (const [id, it] of items) {
    if (!seen.has(id)) { it.root.remove(); it.chip.remove(); items.delete(id); }
  }
  const want = lives.map((l) => /** @type {LiveItem} */ (items.get(l.id)).root);
  const have = [...R.list.children];
  if (want.length !== have.length || want.some((n, i) => n !== have[i])) R.list.replaceChildren(...want);
  const wantChips = lives.map((l) => /** @type {LiveItem} */ (items.get(l.id)).chip);
  const haveChips = [...R.chips.children];
  if (wantChips.length !== haveChips.length || wantChips.some((n, i) => n !== haveChips[i])) R.chips.replaceChildren(...wantChips);
  R.empty.hidden = lives.length > 0;

  // links do conjunto
  const lk = `${set.id}|${state.publicBase}`;
  if (lk !== linksKey) {
    linksKey = lk;
    R.links.replaceChildren(...setEndpoints(set.id).map((ep) => linkItem(ep.label, `${state.publicBase}${ep.path}`, 'link-card')));
  }

  applyFocus();
}

/** Conjuntos que o seletor oferece: os do próprio usuário, ou os do usuário cujos dados o mestre está vendo. */
function pickerSets() {
  const uid = state.route.userId;
  if (uid !== undefined && uid !== null && state.master.data && state.master.data.user && state.master.data.user.id === uid) {
    return Array.isArray(state.master.data.sets) ? state.master.data.sets : [];
  }
  return state.sets;
}

/**
 * Caminho `Conjuntos › Nome` (o nome é um seletor suspenso para pular para outro conjunto).
 * @param {any} set
 */
function renderCrumbs(set) {
  const uid = state.route.userId ?? null;
  const list = pickerSets();
  const owner = uid !== null && state.master.data && state.master.data.user ? state.master.data.user : null;
  const key = JSON.stringify([set.id, uid, owner && owner.username, list.map((x) => [x.id, x.name])]);
  if (key === crumbKey) return;
  crumbKey = key;
  const sel = /** @type {HTMLButtonElement} */ (R.picker);
  setDropdownOptions(sel, list.map((x) => ({ value: String(x.id), text: String(x.name) })));
  sel.value = String(set.id);
  /** @type {(string | {text: string, go: () => void})[]} */
  const items = uid === null
    ? [{ text: 'Conjuntos', go: () => navigateTo({ screen: 'conjuntos', setId: null, focus: null }) }]
    : [
      { text: 'Mestre', go: () => navigateTo({ screen: 'mestre', setId: null, focus: null, seg: 'usuarios' }) },
      { text: owner ? (owner.display_name || owner.username) : 'Usuário', go: () => navigateTo({ screen: 'mestre', setId: null, focus: null, seg: 'dados', userId: uid }) },
    ];
  const nav = crumbs(items);
  nav.append(h('span', { class: 'crumb-sep', 'aria-hidden': 'true', text: '›' }), sel);
  R.crumbs.replaceChildren(nav);
}

/** Rola até a live/links pedidos pela navegação (uma vez por pedido). */
function applyFocus() {
  const r = state.route;
  const key = JSON.stringify([r.setId, r.focus]);
  if (key === focusKey) return;
  focusKey = key;
  const behavior = reduced() ? 'auto' : 'smooth';
  if (r.focus && r.focus.live !== undefined) {
    const it = items.get(r.focus.live);
    if (it) { it.open(); it.root.scrollIntoView({ behavior, block: 'center' }); }
  } else if (r.focus && r.focus.links) {
    R.cardLinks.scrollIntoView({ behavior, block: 'start' });
  } else {
    window.scrollTo({ top: 0 });
  }
}

/** Para a navegação poder repetir o foco (o mesmo item clicado de novo). */
export function resetFocus() {
  focusKey = '';
}
