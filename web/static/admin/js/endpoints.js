// @ts-check
// Endereços públicos de cada assunto (rótulo e caminho). Nenhum é de admin. A tela os mostra completos
// (origem pública + caminho) com o botão Copiar (ui.js: linkItem).

/**
 * @typedef {object} Endpoint
 * @property {string} label
 * @property {string} path
 * @property {boolean} [icone] mostra o interruptor "Icone" (acrescenta &icone=1 ao endereço; só estado da tela)
 */

/** Endereços do assunto "inscritos". @returns {Endpoint[]} */
export function subscribersEndpoints() {
  return [
    { label: 'Overlay do contador', path: '/contador' },
    { label: 'Inscritos (JSON)', path: '/inscritos.json' },
    { label: 'JSON com aparência', path: '/api/public/subscribers' },
  ];
}

/** Plataformas na ordem em que o conjunto lista o overlay de espectadores de cada uma (D58). */
const PLATAFORMAS = [['youtube', 'do YouTube'], ['twitch', 'da Twitch'], ['kick', 'do Kick']];

/**
 * Endereços de uma live: JSON e os 2 overlays (likes e espectadores).
 * @param {number} id
 * @returns {Endpoint[]}
 */
export function liveEndpoints(id) {
  return [
    { label: 'JSON da live', path: `/lives/${id}.json` },
    { label: 'Overlay de likes / meta', path: `/overlay/live.html?live=${id}&metrica=likes` },
    { label: 'Overlay de espectadores', path: `/overlay/live.html?live=${id}&metrica=views`, icone: true },
  ];
}

/**
 * Endereços de um conjunto: JSON (com as somas), os 2 overlays e, para cada plataforma que o conjunto tem,
 * o overlay de espectadores só dela.
 * @param {number} id
 * @param {string[]} [plataformas] plataformas das lives do conjunto
 * @returns {Endpoint[]}
 */
export function setEndpoints(id, plataformas = []) {
  return [
    { label: 'JSON do conjunto', path: `/conjuntos/${id}.json` },
    { label: 'Overlay de likes / meta', path: `/overlay/live.html?conjunto=${id}&metrica=likes` },
    { label: 'Overlay de espectadores', path: `/overlay/live.html?conjunto=${id}&metrica=views`, icone: true },
    ...PLATAFORMAS.filter(([p]) => plataformas.includes(p)).map(([p, nome]) => (
      { label: `Overlay de espectadores ${nome}`, path: `/overlay/live.html?conjunto=${id}&metrica=views&plataforma=${p}`, icone: true }
    )),
  ];
}
