// @ts-check
// Abas do painel (D36): Inscritos, Lives e Saúde. Uma visível por vez; a aba atual fica em
// location.hash (ex.: /#lives) e pode ser aberta direto; setas, Home e End navegam pelo teclado.

const TABS = ['inscritos', 'lives', 'saude'];
const DEFAULT_TAB = 'inscritos';

/** @returns {HTMLElement[]} */
function tabButtons() {
  return TABS.map((name) => {
    const e = document.getElementById(`tab-${name}`);
    if (!(e instanceof HTMLElement)) throw new Error(`Aba #tab-${name} não encontrada`);
    return e;
  });
}

/** @param {string} hash @returns {string | null} */
function nameFromHash(hash) {
  const name = hash.replace(/^#/, '');
  return TABS.includes(name) ? name : null;
}

/**
 * Mostra uma aba e esconde as outras.
 * @param {string} name
 * @param {boolean} [focus] move o foco para o botão da aba
 */
export function selectTab(name, focus = false) {
  const target = TABS.includes(name) ? name : DEFAULT_TAB;
  for (const tab of tabButtons()) {
    const active = tab.dataset.tab === target;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(tab.dataset.tab || '');
    if (panel) panel.hidden = !active;
    if (active && focus) tab.focus();
  }
}

/** @returns {string} aba atualmente selecionada */
export function currentTab() {
  const tab = tabButtons().find((t) => t.getAttribute('aria-selected') === 'true');
  return tab && tab.dataset.tab ? tab.dataset.tab : DEFAULT_TAB;
}

/**
 * Seleciona a aba e a reflete em location.hash (sem rolar a página).
 * @param {string} name
 * @param {boolean} [focus]
 */
function go(name, focus = false) {
  selectTab(name, focus);
  if (nameFromHash(window.location.hash) !== name) {
    history.pushState(null, '', `#${name}`);
  }
}

/** Liga cliques, teclado e o hash (uma vez). */
export function initTabs() {
  const buttons = tabButtons();
  buttons.forEach((tab, i) => {
    tab.addEventListener('click', () => go(tab.dataset.tab || DEFAULT_TAB));
    tab.addEventListener('keydown', (ev) => {
      /** @type {number | null} */
      let next = null;
      switch (ev.key) {
        case 'ArrowRight': next = (i + 1) % buttons.length; break;
        case 'ArrowLeft': next = (i - 1 + buttons.length) % buttons.length; break;
        case 'Home': next = 0; break;
        case 'End': next = buttons.length - 1; break;
        default: break;
      }
      if (next === null) return;
      ev.preventDefault();
      go(buttons[next].dataset.tab || DEFAULT_TAB, true);
    });
  });
  const fromHash = () => selectTab(nameFromHash(window.location.hash) || DEFAULT_TAB);
  window.addEventListener('hashchange', fromHash);
  window.addEventListener('popstate', fromHash);
  fromHash();
}
