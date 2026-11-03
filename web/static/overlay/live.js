// @ts-check
// Overlay de likes de uma live (D34): /overlay/live.html?slot=N (N de 1 a 4).
// Mostra os likes com o carrossel de dígitos do contador de inscritos, no formato
// "likes/meta" (só "likes" sem meta), e uma barra fina embaixo cuja largura é
// min(likes/meta, 1) na cor do slot. Os dados vêm de /api/public/lives/N a cada 5 s.
// Nada vindo do servidor vira HTML: só textContent e CSSOM (style.setProperty).

import { criarCarrossel } from './carousel.js';
import { aplicarAparencia } from './aparencia.js';

const INTERVALO_MS = 5000;
const COR_PADRAO = '#FF0033';
const COR_VALIDA = /^#[0-9A-Fa-f]{6}$/;

/**
 * Lê ?slot=N. Só "1".."4" valem; qualquer outra coisa (ausente, 0, 5, "1abc", repetido) = inválido.
 * @param {string} search
 * @returns {number | null}
 */
function lerSlot(search) {
  const valores = new URLSearchParams(search).getAll('slot');
  if (valores.length !== 1 || !/^[1-4]$/.test(valores[0])) return null;
  return Number(valores[0]);
}

/**
 * Inteiro com separador de milhar "." (ex.: 1000 -> "1.000").
 * @param {number} n
 * @returns {string}
 */
function milhar(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

/**
 * @param {unknown} v
 * @returns {v is number}
 */
function inteiroNaoNegativo(v) {
  return typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
}

/**
 * @param {number} slot
 * @param {HTMLElement} raiz
 */
function iniciar(slot, raiz) {
  const contagem = /** @type {HTMLElement} */ (raiz.querySelector('#contagem'));
  const barra = /** @type {HTMLElement} */ (raiz.querySelector('#barra'));
  const preenchimento = /** @type {HTMLElement} */ (raiz.querySelector('#barra-preenchimento'));
  const carrossel = criarCarrossel(contagem);

  async function atualizar() {
    try {
      const res = await fetch(`/api/public/lives/${slot}?t=${Date.now()}`);
      if (!res.ok) return; // 503 antes do 1º ciclo do worker, 429...: mantém o que já está na tela
      const dados = await res.json();
      if (!dados || !inteiroNaoNegativo(dados.likes)) return;
      const likes = dados.likes;
      const meta = inteiroNaoNegativo(dados.goal) && dados.goal > 0 ? dados.goal : null;
      const cor = typeof dados.color === 'string' && COR_VALIDA.test(dados.color) ? dados.color : COR_PADRAO;

      aplicarAparencia(contagem, dados); // fonte e cor do texto (D35)
      const texto = meta === null ? milhar(likes) : `${milhar(likes)}/${milhar(meta)}`;
      contagem.setAttribute('aria-label', texto);
      carrossel.mostrar(texto);

      raiz.style.setProperty('--cor-barra', cor);
      if (meta === null) {
        barra.hidden = true;
        preenchimento.style.width = '0%';
      } else {
        barra.hidden = false;
        const fracao = Math.min(likes / meta, 1);
        preenchimento.style.width = `${Math.round(fracao * 10000) / 100}%`;
      }
      raiz.hidden = false;
    } catch (e) {
      // Falha de rede ou JSON inválido: mantém o último valor exibido.
      console.warn('Erro ao buscar likes:', e);
    }
  }

  void atualizar();
  setInterval(() => void atualizar(), INTERVALO_MS);
}

const slot = lerSlot(window.location.search);
const raiz = document.getElementById('overlay-likes');
if (slot !== null && raiz) {
  iniciar(slot, raiz);
}
