// @ts-check
// Utilidades de DOM. Todo texto vindo do servidor entra na página SOMENTE por
// textContent / atributos de texto; nenhuma string vira HTML.

/**
 * @param {string} id
 * @returns {HTMLElement}
 */
export function byId(id) {
  const e = document.getElementById(id);
  if (!e) throw new Error(`Elemento #${id} não encontrado`);
  return e;
}

/**
 * @param {string} id
 * @returns {HTMLInputElement}
 */
export function inputById(id) {
  const e = byId(id);
  if (!(e instanceof HTMLInputElement)) throw new Error(`#${id} não é um input`);
  return e;
}

/**
 * @param {string} id
 * @returns {HTMLTextAreaElement}
 */
export function textareaById(id) {
  const e = byId(id);
  if (!(e instanceof HTMLTextAreaElement)) throw new Error(`#${id} não é um textarea`);
  return e;
}

/**
 * @param {string} id
 * @returns {HTMLButtonElement}
 */
export function buttonById(id) {
  const e = byId(id);
  if (!(e instanceof HTMLButtonElement)) throw new Error(`#${id} não é um botão`);
  return e;
}

/**
 * Clona o conteúdo de um <template> e devolve o primeiro elemento.
 * @param {string} id
 * @returns {HTMLElement}
 */
export function cloneTemplate(id) {
  const t = byId(id);
  if (!(t instanceof HTMLTemplateElement)) throw new Error(`#${id} não é um template`);
  const first = t.content.firstElementChild;
  if (!(first instanceof HTMLElement)) throw new Error(`Template #${id} vazio`);
  return /** @type {HTMLElement} */ (first.cloneNode(true));
}

/**
 * Busca um descendente por seletor, falhando alto se ele não existir.
 * @param {ParentNode} root
 * @param {string} selector
 * @returns {HTMLElement}
 */
export function query(root, selector) {
  const e = root.querySelector(selector);
  if (!(e instanceof HTMLElement)) throw new Error(`Seletor ${selector} não encontrado`);
  return e;
}

/**
 * Mostra ou esconde um elemento pelo atributo `hidden`.
 * @param {HTMLElement} e
 * @param {boolean} visible
 */
export function setVisible(e, visible) {
  e.hidden = !visible;
}

/**
 * Mostra uma mensagem (texto puro) num elemento de aviso.
 * @param {HTMLElement} e
 * @param {string} text
 */
export function showMessage(e, text) {
  e.textContent = text;
  e.hidden = false;
}

/**
 * @param {HTMLElement} e
 */
export function clearMessage(e) {
  e.textContent = '';
  e.hidden = true;
}
