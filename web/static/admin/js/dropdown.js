// @ts-check
// Dropdown próprio do painel: UM componente para todos os seletores (fonte, período do gráfico, conjunto, usuário).
// O gatilho é um <button role="combobox"> que mantém o mesmo `id`, a propriedade `.value` e o evento `change` do
// <select> que substitui; a lista é um `role="listbox"` flutuante (float.js) com `aria-activedescendant`. Teclado:
// setas, Home/End, Enter/Espaço, Esc, Tab e digitar a inicial; toque e clique funcionam; fecha ao tocar fora.

import { h, icon } from './h.js';
import { openFloating } from './float.js';

/** @typedef {{value: string, text: string, font?: string}} DdOption */
/**
 * @typedef {object} DdApi
 * @property {HTMLButtonElement} el o gatilho (leva o id)
 * @property {(options: DdOption[]) => void} setOptions
 * @property {(value: string) => void} setValue muda o valor sem disparar `change`
 * @property {() => string} get
 */

/** @type {WeakMap<HTMLElement, DdApi>} */
const apis = new WeakMap();
let seq = 0;

/**
 * Cria (ou transforma, com `el`) um dropdown.
 * @param {{id?: string, label?: string, options?: DdOption[], value?: string, className?: string, el?: HTMLButtonElement}} [o]
 * @returns {DdApi}
 */
export function dropdown(o = {}) {
  const n = ++seq;
  const btn = o.el || /** @type {HTMLButtonElement} */ (h('button', { type: 'button' }));
  if (o.id) btn.id = o.id;
  btn.classList.add('dd');
  if (o.className) for (const c of o.className.split(' ')) btn.classList.add(c);
  const text = h('span', { class: 'dd-text' });
  btn.replaceChildren(text, icon('down', 12));
  const listId = `dd-list-${n}`;
  btn.setAttribute('role', 'combobox');
  btn.setAttribute('aria-haspopup', 'listbox');
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('aria-controls', listId);
  if (o.label) btn.setAttribute('aria-label', o.label);

  /** @type {DdOption[]} */
  let options = o.options || [];
  let value = o.value !== undefined ? o.value : (options[0] ? options[0].value : '');
  let active = -1;
  /** @type {null | {close: () => void}} */
  let floating = null;
  const list = h('ul', { class: 'dd-list float-pop', role: 'listbox', id: listId, tabindex: '-1' });
  if (o.label) list.setAttribute('aria-label', o.label);
  /** @type {HTMLElement[]} */
  let items = [];
  let typed = '';
  /** @type {number | null} */
  let typedTimer = null;

  const current = () => options.find((x) => x.value === value) || null;
  const paintButton = () => {
    const cur = current();
    text.textContent = cur ? cur.text : (options.length ? '' : '—');
    if (cur && cur.font) btn.style.setProperty('font-family', cur.font);
    else btn.style.removeProperty('font-family');
  };
  const setActive = (/** @type {number} */ i) => {
    active = i;
    items.forEach((li, j) => li.classList.toggle('active', j === i));
    if (i >= 0 && items[i]) {
      btn.setAttribute('aria-activedescendant', items[i].id);
      items[i].scrollIntoView({ block: 'nearest' });
    } else btn.removeAttribute('aria-activedescendant');
  };
  const build = () => {
    items = options.map((op, i) => {
      const li = h('li', { class: 'dd-opt', role: 'option', id: `${listId}-${i}`, 'data-value': op.value, 'aria-selected': String(op.value === value) },
        h('span', { class: 'dd-opt-text', text: op.text }), h('span', { class: 'dd-check', 'aria-hidden': 'true', text: '✓' }));
      if (op.font) li.style.setProperty('font-family', op.font);
      li.addEventListener('pointermove', () => { if (active !== i) setActive(i); });
      li.addEventListener('click', () => choose(i));
      return li;
    });
    list.replaceChildren(...items);
  };
  const close = () => {
    if (floating) floating.close();
  };
  const closed = () => {
    floating = null;
    btn.setAttribute('aria-expanded', 'false');
    btn.removeAttribute('aria-activedescendant');
    btn.classList.remove('open');
  };
  const open = () => {
    if (floating || !options.length) return;
    build();
    btn.setAttribute('aria-expanded', 'true');
    btn.classList.add('open');
    floating = openFloating({ anchor: btn, pop: list, matchWidth: true, onClose: closed });
    const i = Math.max(0, options.findIndex((x) => x.value === value));
    setActive(i);
    floating.reposition();
  };
  /** @param {number} i */
  function choose(i) {
    const op = options[i];
    close();
    if (!op) return;
    const changed = op.value !== value;
    value = op.value;
    paintButton();
    if (changed) {
      btn.dispatchEvent(new Event('input', { bubbles: true }));
      btn.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  btn.addEventListener('click', () => { if (floating) close(); else open(); });
  list.addEventListener('pointerdown', (ev) => ev.preventDefault()); // o foco fica no gatilho
  btn.addEventListener('keydown', (ev) => {
    const k = ev.key;
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      ev.preventDefault();
      if (!floating) { open(); return; }
      const next = k === 'ArrowDown' ? Math.min(items.length - 1, active + 1) : Math.max(0, active - 1);
      setActive(next);
    } else if (k === 'Home' || k === 'End') {
      if (!floating) return;
      ev.preventDefault();
      setActive(k === 'Home' ? 0 : items.length - 1);
    } else if (k === 'Enter' || k === ' ') {
      ev.preventDefault();
      if (!floating) { open(); return; }
      choose(active);
    } else if (k === 'Tab') {
      close();
    } else if (k.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      typed += k.toLowerCase();
      if (typedTimer !== null) clearTimeout(typedTimer);
      typedTimer = window.setTimeout(() => { typed = ''; }, 700);
      if (!floating) open();
      const i = options.findIndex((x) => x.text.toLowerCase().startsWith(typed));
      if (i >= 0) setActive(i);
    }
  });
  btn.addEventListener('blur', () => {
    // clicar num item não tira o foco (pointerdown prevenido); sair por Tab/clique fora fecha pelo float.js
    if (floating && !list.matches(':hover')) close();
  });

  Object.defineProperty(btn, 'value', {
    configurable: true,
    get: () => value,
    set: (v) => { value = String(v); paintButton(); },
  });
  /** @type {DdApi} */
  const api = {
    el: btn,
    setOptions(next) {
      options = next;
      if (!options.some((x) => x.value === value)) value = options[0] ? options[0].value : '';
      paintButton();
      if (floating) { build(); floating.close(); }
    },
    setValue(v) { value = v; paintButton(); },
    get: () => value,
  };
  apis.set(btn, api);
  paintButton();
  return api;
}

/**
 * Troca as opções de um dropdown já criado.
 * @param {HTMLElement} el
 * @param {DdOption[]} options
 */
export function setDropdownOptions(el, options) {
  const a = apis.get(el);
  if (a) a.setOptions(options);
}

/**
 * Dropdown já existente por elemento (ou undefined).
 * @param {HTMLElement} el
 * @returns {DdApi | undefined}
 */
export function dropdownOf(el) {
  return apis.get(el);
}
