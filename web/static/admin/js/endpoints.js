// @ts-check
// Endereços públicos de cada assunto (rótulo e caminho). Nenhum é de admin. A tela os mostra completos
// (origem pública + caminho) com o botão Copiar (ui.js: linkItem).

/**
 * @typedef {object} Endpoint
 * @property {string} label
 * @property {string} path
 */

/** Endereços do assunto "inscritos". @returns {Endpoint[]} */
export function subscribersEndpoints() {
  return [
    { label: 'Overlay do contador', path: '/contador' },
    { label: 'Inscritos (JSON)', path: '/inscritos.json' },
    { label: 'JSON com aparência', path: '/api/public/subscribers' },
  ];
}

/**
 * Endereços de uma live: JSON e os 2 overlays (likes e espectadores).
 * @param {number} id
 * @returns {Endpoint[]}
 */
export function liveEndpoints(id) {
  return [
    { label: 'JSON da live', path: `/lives/${id}.json` },
    { label: 'Overlay de likes / meta', path: `/overlay/live.html?live=${id}&metrica=likes` },
    { label: 'Overlay de espectadores', path: `/overlay/live.html?live=${id}&metrica=views` },
  ];
}

/**
 * Endereços de um conjunto: JSON (com as somas) e os 2 overlays.
 * @param {number} id
 * @returns {Endpoint[]}
 */
export function setEndpoints(id) {
  return [
    { label: 'JSON do conjunto', path: `/conjuntos/${id}.json` },
    { label: 'Overlay de likes / meta', path: `/overlay/live.html?conjunto=${id}&metrica=likes` },
    { label: 'Overlay de espectadores', path: `/overlay/live.html?conjunto=${id}&metrica=views` },
  ];
}
