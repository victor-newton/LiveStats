// @ts-check
// Construtores de elementos. Nada aqui interpreta HTML: texto entra por nós de texto e valores por atributos.
// Cor e tamanho dinâmicos só pelo CSSOM (`setStyle`) ou por atributos SVG; o atributo `style` é proibido (CSP).

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * @param {Element} e
 * @param {Record<string, any>} props
 */
function applyProps(e, props) {
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.setAttribute('class', String(v));
    else if (k === 'text') e.textContent = String(v);
    else if (k === 'style') throw new Error('Atributo style é proibido (CSP): use setStyle');
    else e.setAttribute(k, v === true ? '' : String(v));
  }
}

/**
 * @param {Element} e
 * @param {(Node | string | null | undefined | false)[]} kids
 */
function appendKids(e, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  }
}

/**
 * Cria um elemento HTML. `props`: class, text (textContent) e qualquer outro par vira atributo.
 * @param {string} tag
 * @param {Record<string, any> | null} [props]
 * @param {...(Node | string | null | undefined | false)} kids
 * @returns {HTMLElement}
 */
export function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  applyProps(e, props || {});
  appendKids(e, kids);
  return e;
}

/**
 * Cria um elemento SVG com atributos (fill, stroke, width...) e filhos.
 * @param {string} tag
 * @param {Record<string, any> | null} [attrs]
 * @param {...(Node | string | null | undefined | false)} kids
 * @returns {SVGElement}
 */
export function s(tag, attrs, ...kids) {
  const e = document.createElementNS(SVG_NS, tag);
  applyProps(e, attrs || {});
  appendKids(e, kids);
  return e;
}

/**
 * Estilo dinâmico pelo CSSOM (a CSP bloqueia o atributo `style`, não o CSSOM).
 * @param {HTMLElement | SVGElement} e
 * @param {Record<string, string | number>} props propriedades CSS (inclui variáveis --x)
 */
export function setStyle(e, props) {
  for (const [k, v] of Object.entries(props)) e.style.setProperty(k, String(v));
}

/** Traçados dos ícones (viewBox 16x16, só linha). */
const ICONS = {
  grid: 'M3 2.7h3.3a.7.7 0 0 1 .7.7v3.3a.7.7 0 0 1-.7.7H3a.7.7 0 0 1-.7-.7V3.4a.7.7 0 0 1 .7-.7ZM9.7 2.7H13a.7.7 0 0 1 .7.7v3.3a.7.7 0 0 1-.7.7H9.7a.7.7 0 0 1-.7-.7V3.4a.7.7 0 0 1 .7-.7ZM3 9.3h3.3a.7.7 0 0 1 .7.7v3.3a.7.7 0 0 1-.7.7H3a.7.7 0 0 1-.7-.7V10a.7.7 0 0 1 .7-.7ZM9.7 9.3H13a.7.7 0 0 1 .7.7v3.3a.7.7 0 0 1-.7.7H9.7a.7.7 0 0 1-.7-.7V10a.7.7 0 0 1 .7-.7Z',
  chev: 'M6 3.5 10.5 8 6 12.5',
  down: 'M3.5 6 8 10.5 12.5 6',
  plus: 'M8 3v10M3 8h10',
  user: 'M8 8.2a2.7 2.7 0 1 0 0-5.4 2.7 2.7 0 0 0 0 5.4ZM3 13.6c.7-2.4 2.7-3.6 5-3.6s4.3 1.2 5 3.6',
  pulse: 'M1.5 8.5h3l1.8-4 3 7.5 2-5h3.2',
  out: 'M6 3H3.5v10H6M10 5l3 3-3 3M13 8H6',
  menu: 'M2.5 4h11M2.5 8h11M2.5 12h11',
  gear: 'M8 10.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4ZM8 1.8v1.6M8 12.6v1.6M14.2 8h-1.6M3.4 8H1.8M12.4 3.6l-1.1 1.1M4.7 11.3l-1.1 1.1M12.4 12.4l-1.1-1.1M4.7 4.7 3.6 3.6',
  close: 'M4 4l8 8M12 4l-8 8',
};

/**
 * Ícone de linha 16x16 (só atributos SVG).
 * @param {keyof typeof ICONS} name
 * @param {number} [size]
 * @returns {SVGElement}
 */
export function icon(name, size = 16) {
  return s('svg', {
    width: size, height: size, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', class: `ico ico-${name}`,
  }, s('path', {
    d: ICONS[name], stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
}
