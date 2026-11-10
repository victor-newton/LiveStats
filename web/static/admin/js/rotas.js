// @ts-check
// Rotas por caminho (History API): a barra do navegador reflete a aba aberta. Só funções puras sobre o caminho e o
// histórico; quem troca de tela é o main.js. O Nginx do admin serve o painel exatamente para estes caminhos.

/** @typedef {import('./store.js').Route} Route */

/** Nome da rota (primeiro trecho do caminho) -> tela do painel. */
const NAME_TO_SCREEN = {
  inicio: 'visao',
  conjuntos: 'conjuntos',
  contador: 'inscritos',
  saude: 'saude',
  mestre: 'mestre',
  conta: 'conta',
};

/** @type {Record<string, string>} */
const SCREEN_TO_NAME = {
  visao: 'inicio',
  conjuntos: 'conjuntos',
  conjunto: 'conjuntos',
  inscritos: 'contador',
  saude: 'saude',
  mestre: 'mestre',
  conta: 'conta',
};

/** Rota de entrada quando o caminho é a raiz ou desconhecido. */
export const DEFAULT_PATH = '/inicio';

/**
 * Interpreta um caminho. Retorna null quando não é uma rota do painel.
 * @param {string} path
 * @returns {Route | null}
 */
export function parsePath(path) {
  const m = /^\/(inicio|conjuntos|contador|saude|mestre|conta)(?:\/(\d{1,15}))?\/?$/.exec(path);
  if (!m) return null;
  if (m[2] !== undefined) {
    if (m[1] !== 'conjuntos') return null;
    return { screen: 'conjunto', setId: Number(m[2]), focus: null, userId: null };
  }
  const screen = /** @type {Route['screen']} */ (NAME_TO_SCREEN[/** @type {keyof typeof NAME_TO_SCREEN} */ (m[1])]);
  return { screen, setId: null, focus: null };
}

/**
 * Caminho de uma rota. Conjunto de outro usuário (aberto pelo mestre) não tem endereço próprio: fica em /mestre.
 * @param {Route} route
 * @returns {string}
 */
export function pathOf(route) {
  if (route.screen === 'conjunto') {
    if (route.userId !== undefined && route.userId !== null) return '/mestre';
    return `/conjuntos/${route.setId}`;
  }
  return `/${SCREEN_TO_NAME[route.screen] || 'inicio'}`;
}

/**
 * Nome da rota atual: inicio, conjuntos (a lista e um conjunto aberto), contador, saude, mestre ou conta.
 * @returns {string}
 */
export function currentRoute() {
  const r = parsePath(window.location.pathname);
  return r ? SCREEN_TO_NAME[r.screen] : 'inicio';
}

/** Rota pedida pela barra de endereço (raiz e desconhecidos viram o Início). @returns {Route} */
export function routeFromLocation() {
  return parsePath(window.location.pathname) || { screen: 'visao', setId: null, focus: null };
}

/**
 * Grava o caminho no histórico (pushState/replaceState); sem mudança não empilha nada.
 * @param {string} path
 * @param {'push' | 'replace'} mode
 */
export function writePath(path, mode) {
  if (window.location.pathname === path) return;
  if (mode === 'push') window.history.pushState(null, '', path);
  else window.history.replaceState(null, '', path);
}
