// @ts-check
// Tela do conjunto: 4 números, gráfico de linha (espectadores + likes, períodos 5/30/60/120 min), rosca por live,
// lista de lives que expande (links + configurações + prévia do overlay) e links do conjunto.
// Dados do servidor só entram por textContent/valores de campo. A atualização de 5 s só TROCA valores.

import { api } from './api.js';
import { byId } from './dom.js';
import { h, icon, setStyle } from './h.js';
import { lineChart, donutChart } from './charts.js';
import { registerFontSelect, setFontValue } from './fonts.js';
import { write, readGoal, requestRefresh, sessionLost } from './net.js';
import { liveEndpoints, setEndpoints } from './endpoints.js';
import {
  colorField, overlayPreview, collapse, linkItem, statusPill, setPill, platBadge, armedButton, toast,
} from './ui.js';
import {
  state, setById, liveStats, liveName, sortedLives, setTotals,
} from './store.js';
import { showMessage, clearMessage } from './dom.js';
import {
  fmt, fmtDec, hhmm, reduced, safeColor, setText, toEpoch, PLATFORM_NAME,
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

/** Monta a estrutura fixa da tela (uma vez). */
export function initConjunto() {
  const scr = byId('scr-conjunto');
  R.title = h('h1', { id: 'setTitle' });
  R.sub = h('p', { id: 'setSub' });
  R.btnConfig = h('button', { type: 'button', class: 'btn', id: 'btnConfigSet' }, icon('gear', 14), 'Configurar conjunto');
  R.btnAdd = h('button', { type: 'button', class: 'btn btn-primary', id: 'btnAddLive' }, icon('plus', 14), 'Adicionar live');
  R.btnConfig.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openConfigSet(s); });
  R.btnAdd.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openAddLive(s); });

  // números
  R.kViewers = h('span', { class: 'num v', id: 'kViewers' });
  R.tViewers = h('span', { class: 'trend', hidden: true });
  R.kLikes = h('span', { class: 'num v', id: 'kLikes' });
  R.kGoal = h('small', { class: 'num', id: 'kGoal' });
  R.meterFill = h('i');
  R.meter = h('div', { class: 'meter' }, R.meterFill);
  R.kOn = h('span', { class: 'num v', id: 'kOn' });
  R.kOnOf = h('small', { class: 'num' });
  R.pills = h('div', { class: 'foot pills' });
  R.kPeak = h('span', { class: 'num v', id: 'kPeak' });
  R.peakFoot = h('div', { class: 'foot' });
  const kpi = (/** @type {string} */ label, /** @type {Node[]} */ value, /** @type {Node[]} */ foot) => h('div', { class: 'card kpi' },
    h('div', { class: 'label', text: label }), h('div', { class: 'value' }, ...value), ...foot);
  const kpis = h('div', { class: 'kpis' },
    kpi('Espectadores agora', [R.kViewers], [h('div', { class: 'foot' }, R.tViewers, ' nos últimos 15 min')]),
    kpi('Likes (só YouTube)', [R.kLikes, ' ', R.kGoal], [R.meter]),
    kpi('Lives ao vivo', [R.kOn, ' ', R.kOnOf], [R.pills]),
    kpi('Pico de hoje', [R.kPeak], [R.peakFoot]));

  // gráfico de linha
  R.range = h('select', { class: 'select', id: 'range', 'aria-label': 'Período do gráfico' });
  for (const [v, t] of RANGES) R.range.appendChild(h('option', { value: v, text: t }));
  /** @type {HTMLSelectElement} */ (R.range).value = String(state.range);
  R.tChart = h('span', { class: 'trend', hidden: true });
  R.lineSub = h('p', { id: 'lineSub', text: 'Soma das lives do conjunto.' });
  R.svg = /** @type {SVGElement} */ (document.createElementNS('http://www.w3.org/2000/svg', 'svg'));
  R.svg.setAttribute('class', 'chart');
  R.svg.setAttribute('id', 'lineChart');
  R.svg.setAttribute('viewBox', '0 0 760 290');
  R.svg.setAttribute('role', 'img');
  R.svg.setAttribute('aria-label', 'Gráfico de linha de espectadores e likes');
  R.tip = h('div', { class: 'tip', hidden: true });
  R.range.addEventListener('change', () => void changeRange());
  const chartCard = h('div', { class: 'card' },
    h('div', { class: 'card-h' },
      h('div', null, h('h2', null, 'Espectadores e likes ', R.tChart), R.lineSub),
      h('div', { class: 'card-tools' },
        h('div', { class: 'legend' },
          h('span', null, h('i', { class: 'lg views' }), 'Espectadores'),
          h('span', null, h('i', { class: 'lg likes' }), 'Likes')),
        R.range)),
    h('div', { class: 'chart-box' }, R.svg, R.tip));

  // rosca
  R.donut = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  R.donut.setAttribute('id', 'donut');
  R.donut.setAttribute('viewBox', '0 0 220 220');
  R.donut.setAttribute('role', 'img');
  R.donut.setAttribute('aria-label', 'Gráfico de rosca da participação de cada live');
  R.shareList = h('div', { class: 'share-list', id: 'shareList' });
  const donutCard = h('div', { class: 'card' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Participação por live'), h('p', { text: 'Quanto cada live soma nos espectadores agora.' }))),
    h('div', { class: 'donut-wrap' }, R.donut), R.shareList);

  // lives
  R.list = h('div', { class: 'lives', id: 'livesList' });
  R.empty = h('p', { class: 'empty', text: 'Este conjunto ainda não tem lives.', hidden: true });
  R.btnAddRow = h('button', { type: 'button', class: 'add-row', id: 'btnAddRow' }, icon('plus', 16), 'Adicionar live: cole o link do YouTube, da Twitch ou do Kick');
  R.btnAddRow.addEventListener('click', () => { const s = setById(state.route.setId); if (s) openAddLive(s); });
  R.cardLives = h('div', { class: 'card', id: 'cardLives' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Lives'), h('p', { text: 'Clique numa live para abrir os links e as configurações dela.' }))),
    h('div', { class: 'live-row hdr' }, h('span'), h('span', { text: 'Live' }), h('span', { class: 'c-status', text: 'Estado' }), h('span', { class: 'cell-num', text: 'Espectadores' }), h('span', { class: 'cell-num', text: 'Likes' }), h('span', { class: 'c-share', text: 'Parte do total' }), h('span')),
    R.list, R.empty, R.btnAddRow);

  // links
  R.links = h('div', { class: 'links-grid', id: 'setLinks' });
  R.cardLinks = h('div', { class: 'card', id: 'cardLinks' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', null, 'Links do conjunto'), h('p', { text: 'Somam todas as lives acima. Use no OBS, no vMix ou em artes próprias.' }))),
    R.links);

  scr.replaceChildren(
    h('div', { class: 'head' }, h('div', null, R.title, R.sub), h('div', { class: 'actions' }, R.btnConfig, R.btnAdd)),
    kpis,
    h('div', { class: 'grid-main' }, chartCard, donutCard),
    R.cardLives, R.cardLinks);
}

/** Descarta o conteúdo (sessão encerrada). */
export function resetConjunto() {
  items = new Map();
  builtFor = null;
  chartKey = '';
  donutKey = '';
  linksKey = '';
  focusKey = '';
  if (R.list) R.list.replaceChildren();
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
  const v = Number(/** @type {HTMLSelectElement} */ (R.range).value);
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
  /** @type {HTMLSelectElement} */ (R.range).value = String(state.range);
  const step = data && typeof data.step_s === 'number' ? data.step_s : 60;
  setTxt(R.lineSub, `Soma das lives do conjunto, um ponto a cada ${STEP_TEXT[/** @type {10 | 30 | 60} */ (step)] || `${step} s`}.`);
  setTrend(R.tChart, data ? data.change_15m_pct : null);
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
  const expand = h('button', { type: 'button', class: 'expand', 'aria-expanded': 'false', 'aria-controls': `det-${id}` }, icon('down', 12));
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
  const fontSel = /** @type {HTMLSelectElement} */ (h('select', { id: `lv${id}-font` }));
  registerFontSelect(fontSel);
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
  const detail = h('div', { class: 'live-detail', id: `det-${id}` }, linksBox, cfgBox);
  const col = collapse(detail);

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

  const toggle = () => {
    const open = !col.isOpen();
    col.set(open);
    expand.setAttribute('aria-expanded', String(open));
    row.classList.toggle('open', open);
  };
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
    root: h('div', { class: 'live-item', 'data-live': id }, row, col.root),
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
    chartKey = '';
    donutKey = '';
    linksKey = '';
  }
  const animate = first || changedSet;
  const lives = sortedLives(set);
  const tot = setTotals(set);
  const platforms = new Set(lives.map((l) => l.platform));
  setTxt(R.title, set.name);
  setTxt(R.sub, `${lives.length} ${lives.length === 1 ? 'live' : 'lives'} em ${platforms.size} ${platforms.size === 1 ? 'plataforma' : 'plataformas'}${typeof set.goal === 'number' ? ` · meta de ${fmt(set.goal)} likes` : ''} · leitura a cada 10 s`);

  // números
  const hs = state.hist.get(set.id);
  const hist = hs && hs.range === state.range ? hs.data : (hs ? hs.data : null);
  setText(R.kViewers, fmt(tot.viewers), animate);
  setTrend(R.tViewers, hist ? hist.change_15m_pct : null);
  setText(R.kLikes, tot.likes === null ? '—' : fmt(tot.likes), animate);
  setText(R.kGoal, typeof set.goal === 'number' ? `/ ${fmt(set.goal)}` : '', animate);
  const pct = typeof set.goal === 'number' && tot.likes !== null ? Math.min(100, (tot.likes / set.goal) * 100) : 0;
  R.meterFill.style.width = `${pct.toFixed(1)}%`;
  R.meter.hidden = typeof set.goal !== 'number';
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
    if (!seen.has(id)) { it.root.remove(); items.delete(id); }
  }
  const want = lives.map((l) => /** @type {LiveItem} */ (items.get(l.id)).root);
  const have = [...R.list.children];
  if (want.length !== have.length || want.some((n, i) => n !== have[i])) R.list.replaceChildren(...want);
  R.empty.hidden = lives.length > 0;

  // links do conjunto
  const lk = `${set.id}|${state.publicBase}`;
  if (lk !== linksKey) {
    linksKey = lk;
    R.links.replaceChildren(...setEndpoints(set.id).map((ep) => linkItem(ep.label, `${state.publicBase}${ep.path}`, 'link-card')));
  }

  applyFocus();
}

/** Rola até a live/links pedidos pelo menu lateral (uma vez por pedido). */
function applyFocus() {
  const r = state.route;
  const key = JSON.stringify([r.setId, r.focus]);
  if (key === focusKey) return;
  focusKey = key;
  const behavior = reduced() ? 'auto' : 'smooth';
  if (r.focus && r.focus.live !== undefined) {
    const it = items.get(r.focus.live);
    if (it) { it.open(); it.row.scrollIntoView({ behavior, block: 'center' }); }
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
