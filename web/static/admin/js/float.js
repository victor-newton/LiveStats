// @ts-check
// Camada flutuante compartilhada (dropdown, seletor de cor, infoTip): leva o painel para fora de qualquer cartão com
// `overflow: hidden` (ou para dentro do <dialog> aberto, que fica na camada superior), posiciona sem estourar a tela
// (vira para cima quando falta espaço embaixo e é contido nas laterais), fecha ao tocar fora, com Esc e ao rolar a
// página. Posição e tamanho entram só pelo CSSOM (`style.setProperty`), nunca pelo atributo `style` (CSP).

const MARGIN = 8;
const GAP = 6;

/** @typedef {{anchor: HTMLElement, pop: HTMLElement, close: () => void, focusBack: boolean, align: 'start' | 'center' | 'end', matchWidth: boolean}} Entry */

/** @type {Entry[]} */
const stack = [];
let wired = false;
let raf = 0;

/**
 * Onde o painel entra no DOM: dentro do <dialog> do gatilho (a camada superior esconde o resto) ou no corpo da página.
 * @param {HTMLElement} anchor
 * @returns {HTMLElement}
 */
function hostOf(anchor) {
  return /** @type {HTMLElement} */ (anchor.closest('dialog') || document.body);
}

/** Espaço reservado embaixo: a barra de abas fixa do celular. */
function bottomUi() {
  return window.matchMedia('(max-width: 820px)').matches ? 72 : MARGIN;
}

/**
 * Posiciona `pop` junto a `anchor`, dentro da tela visível.
 * @param {HTMLElement} pop
 * @param {HTMLElement} anchor
 * @param {{align?: 'start' | 'center' | 'end', matchWidth?: boolean, maxHeight?: number}} [o]
 */
export function place(pop, anchor, o = {}) {
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  const keep = pop.scrollTop;  // medir com max-height solto zera a rolagem do painel: guarda e devolve no fim
  pop.style.setProperty('max-height', 'none');
  pop.style.setProperty('min-width', o.matchWidth ? `${Math.round(r.width)}px` : '0px');
  pop.style.setProperty('max-width', `${vw - MARGIN * 2}px`);
  const pw = pop.offsetWidth;
  const full = pop.scrollHeight;
  const below = vh - bottomUi() - r.bottom - GAP;
  const above = r.top - MARGIN - GAP;
  const cap = o.maxHeight || 360;
  const up = full > below && above > below;
  const room = Math.max(120, up ? above : below);
  const height = Math.min(full, cap, room);
  pop.style.setProperty('max-height', `${Math.floor(height)}px`);
  let left = o.align === 'center' ? r.left + r.width / 2 - pw / 2 : o.align === 'end' ? r.right - pw : r.left;
  left = Math.max(MARGIN, Math.min(left, vw - pw - MARGIN));
  const top = up ? r.top - GAP - height : r.bottom + GAP;
  pop.style.setProperty('left', `${Math.round(left)}px`);
  pop.style.setProperty('top', `${Math.round(Math.max(MARGIN, top))}px`);
  pop.dataset.side = up ? 'up' : 'down';
  if (keep) pop.scrollTop = keep;
}

function reposition() {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    raf = 0;
    for (const e of stack) place(e.pop, e.anchor, { align: e.align, matchWidth: e.matchWidth });
  });
}

function wire() {
  if (wired) return;
  wired = true;
  document.addEventListener('pointerdown', (ev) => {
    const t = ev.target;
    if (!(t instanceof Node)) return;
    for (const e of [...stack]) {
      if (!e.pop.contains(t) && !e.anchor.contains(t)) e.close();
    }
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || !stack.length) return;
    const top = stack[stack.length - 1];
    ev.preventDefault();
    ev.stopPropagation();
    const had = top.pop.contains(document.activeElement);
    top.close();
    if (had || top.focusBack) top.anchor.focus();
  }, true);
  window.addEventListener('resize', reposition);
  // Rolar DENTRO de um painel (lista de fontes, cores sugeridas) não muda a posição dele: reposicionar aqui medido de novo
  // e jogava a rolagem de volta ao topo.
  window.addEventListener('scroll', (ev) => {
    const t = ev.target;
    if (t instanceof Node && stack.some((e) => e.pop.contains(t))) return;
    reposition();
  }, true);
}

/**
 * Abre um painel flutuante junto ao gatilho. Devolve `close` (idempotente).
 * @param {{anchor: HTMLElement, pop: HTMLElement, onClose?: () => void, align?: 'start' | 'center' | 'end', matchWidth?: boolean, focusBack?: boolean}} o
 * @returns {{close: () => void, reposition: () => void}}
 */
export function openFloating(o) {
  wire();
  const { anchor, pop } = o;
  hostOf(anchor).appendChild(pop);
  pop.hidden = false;
  /** @type {Entry} */
  const entry = {
    anchor, pop, align: o.align || 'start', matchWidth: !!o.matchWidth, focusBack: !!o.focusBack,
    close() {
      const i = stack.indexOf(entry);
      if (i < 0) return;
      stack.splice(i, 1);
      pop.hidden = true;
      pop.remove();
      if (o.onClose) o.onClose();
    },
  };
  stack.push(entry);
  place(pop, anchor, { align: entry.align, matchWidth: entry.matchWidth });
  return { close: entry.close, reposition: () => place(pop, anchor, { align: entry.align, matchWidth: entry.matchWidth }) };
}

/** Fecha todos os painéis abertos (troca de tela, fim da sessão). */
export function closeAllFloating() {
  for (const e of [...stack]) e.close();
}
