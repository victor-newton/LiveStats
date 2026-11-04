// @ts-check
// Overlay do contador de inscritos (carrossel de dígitos). Porte fiel do
// contador.html original: mesmos tempos, mesmas curvas, mesma estrutura de DOM.
// Diferenças (decisão D12): o JS é um arquivo da mesma origem, busca o caminho
// absoluto da api e monta o carrossel sem HTML em texto.

import { aplicarAparencia } from './aparencia.js';

const URL_JSON = '/api/public/subscribers';
const INTERVALO_MS = 3000;
const CURVA = 'cubic-bezier(0.25, 0.46, 0.45, 0.94)';

/** @type {string | null} */
let valorAtual = null;

/** @param {string} c */
function ehSeparador(c) {
  return c === '.' || c === ',';
}

/**
 * @param {HTMLElement} container
 * @param {string} texto
 */
function construirCarrossel(container, texto) {
  container.replaceChildren();
  for (let i = 0; i < texto.length; i++) {
    const char = texto[i];
    if (ehSeparador(char)) {
      const sep = document.createElement('span');
      sep.className = 'separador';
      sep.textContent = char;
      container.appendChild(sep);
    } else {
      const wrap = document.createElement('div');
      wrap.className = 'digito-wrap';
      const inner = document.createElement('div');
      inner.className = 'digito-inner';
      for (let d = 0; d <= 9; d++) {
        const span = document.createElement('span');
        span.textContent = String(d);
        inner.appendChild(span);
      }
      wrap.appendChild(inner);
      container.appendChild(wrap);
      const digito = parseInt(char);
      inner.style.transform = `translateY(-${digito}em)`;
      inner.style.transition = 'none';
    }
  }
}

/**
 * @param {HTMLElement} container
 * @param {string} novoTexto
 */
function animarParaValor(container, novoTexto) {
  const chars = [...novoTexto].filter((c) => !ehSeparador(c));
  const inners = /** @type {NodeListOf<HTMLElement>} */ (container.querySelectorAll('.digito-inner'));
  const textoAtual = valorAtual || '';
  const estruturaIgual =
    textoAtual.length === novoTexto.length &&
    [...textoAtual].every((c, i) => ehSeparador(c) === ehSeparador(novoTexto[i]));

  if (!estruturaIgual) {
    construirCarrossel(container, novoTexto);
    const novosInners = /** @type {NodeListOf<HTMLElement>} */ (container.querySelectorAll('.digito-inner'));
    novosInners.forEach((inner, idx) => {
      const char = chars[idx];
      if (char === undefined) return;
      setTimeout(() => {
        inner.style.transition = `transform 0.5s ${CURVA}`;
        inner.style.transform = `translateY(-${parseInt(char)}em)`;
      }, idx * 40);
    });
    return;
  }

  let idxChar = 0;
  for (let i = 0; i < novoTexto.length; i++) {
    const c = novoTexto[i];
    if (ehSeparador(c)) continue;
    const inner = inners[idxChar];
    if (inner) {
      inner.style.transition = `transform 0.45s ${CURVA}`;
      inner.style.transform = `translateY(-${parseInt(c)}em)`;
    }
    idxChar++;
  }
}

async function atualizar() {
  try {
    const res = await fetch(`${URL_JSON}?t=${Date.now()}`);
    if (!res.ok) return;
    const data = await res.json();
    const container = document.getElementById('contador');
    if (!container) return;
    aplicarAparencia(container, data); // fonte e cor (D35): a cada leitura, mesmo sem mudar o número
    const novo = data.inscritos;
    if (!novo || novo === valorAtual) return;
    if (valorAtual === null) {
      construirCarrossel(container, novo);
      const inners = /** @type {NodeListOf<HTMLElement>} */ (container.querySelectorAll('.digito-inner'));
      const chars = [...novo].filter((c) => !ehSeparador(c));
      inners.forEach((inner, idx) => {
        const char = chars[idx];
        if (!char) return;
        inner.style.transform = 'translateY(0)';
        inner.style.transition = 'none';
        setTimeout(() => {
          inner.style.transition = `transform 0.5s ${CURVA}`;
          inner.style.transform = `translateY(-${parseInt(char)}em)`;
        }, 50 + idx * 60);
      });
    } else {
      animarParaValor(container, novo);
    }
    valorAtual = novo;
  } catch (e) {
    console.warn('Erro ao buscar inscritos:', e);
  }
}

atualizar();
setInterval(atualizar, INTERVALO_MS);
