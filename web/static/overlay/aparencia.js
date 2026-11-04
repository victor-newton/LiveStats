// @ts-check
// Aparência do texto do overlay (D35): fonte e cor vindas da api, aplicadas por CSSOM
// (style.setProperty), que a CSP permite sem 'unsafe-inline'. Nada vira HTML nem CSS em texto:
// só nomes de família com letras, números e espaço (entre aspas, com fallback genérico) e
// cores #RRGGBB. Qualquer outro valor vindo da rede é DESCARTADO e vale o padrão do CSS.

const FONTE_VALIDA = /^[A-Za-z0-9 ]{1,40}$/;
const COR_VALIDA = /^#[0-9A-Fa-f]{6}$/;

/**
 * @param {HTMLElement} el elemento que recebe o texto
 * @param {any} dados corpo JSON da api (campos `font` e `text_color`)
 */
export function aplicarAparencia(el, dados) {
  const fonte = dados && typeof dados.font === 'string' && FONTE_VALIDA.test(dados.font) ? dados.font : null;
  const cor = dados && typeof dados.text_color === 'string' && COR_VALIDA.test(dados.text_color) ? dados.text_color : null;
  if (fonte === null) el.style.removeProperty('font-family');
  // Roboto é o padrão antigo: mantém a pilha original do overlay ('YouTube Sans' primeiro), visual idêntico.
  else if (fonte === 'Roboto') el.style.setProperty('font-family', "'YouTube Sans', 'Roboto', sans-serif");
  else el.style.setProperty('font-family', `"${fonte}", sans-serif`);
  if (cor === null) el.style.removeProperty('color');
  else el.style.setProperty('color', cor);
}
