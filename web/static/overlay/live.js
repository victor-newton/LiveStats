// @ts-check
// Overlay único de lives e conjuntos (D41):
//   /overlay/live.html?live={id}&metrica=likes|views
//   /overlay/live.html?conjunto={id}&metrica=likes|views
// Só com metrica=views: &icone=1 mostra o ícone da plataforma (de uma live) ou os das plataformas ao vivo
// (de um conjunto) à esquerda do número; &plataforma=youtube|twitch|kick (só conjunto) soma só aquela plataforma (D58).
// "likes" mostra likes/meta (só "likes" sem meta) com o carrossel de dígitos do contador de inscritos e uma
// barra fina colada embaixo, de largura min(likes/meta, 1) na cor da live ou do conjunto. "views" mostra o
// número de espectadores (de uma live, ou a soma do conjunto), sem barra. Fonte, cor do texto e cor da barra
// vêm da própria live ou do conjunto. Os dados vêm de /api/public/lives/{id} ou /api/public/sets/{id} a cada 3 s.
// Nada vindo do servidor vira HTML: só textContent e CSSOM (style.setProperty).

import { criarCarrossel } from './carousel.js';
import { aplicarAparencia } from './aparencia.js';

const INTERVALO_MS = 3000;
const COR_PADRAO = '#FF0033';
const COR_VALIDA = /^#[0-9A-Fa-f]{6}$/;
/** Arquivo do ícone de cada plataforma; a ordem é a dos ícones do conjunto sem `plataforma`. */
const ICONES = { kick: 'kk', twitch: 'tw', youtube: 'yt' };
const NOMES = { kick: 'Kick', twitch: 'Twitch', youtube: 'YouTube' };

/**
 * Lê ?live=N ou ?conjunto=N (exatamente um dos dois, uma vez só, só dígitos) e ?metrica=likes|views.
 * Qualquer outra coisa (ausente, repetido, "1abc", id 0, métrica desconhecida) = inválido.
 * @param {string} search
 * @returns {{caminho: string, metrica: 'likes' | 'views', conjunto: boolean, icone: boolean, plataforma: string | null} | null}
 */
export function lerParametros(search) {
  const params = new URLSearchParams(search);
  const lives = params.getAll('live');
  const conjuntos = params.getAll('conjunto');
  const metricas = params.getAll('metrica');
  if (metricas.length !== 1 || (metricas[0] !== 'likes' && metricas[0] !== 'views')) return null;
  const metrica = /** @type {'likes' | 'views'} */ (metricas[0]);
  if (lives.length + conjuntos.length !== 1) return null;
  const conjunto = conjuntos.length === 1;
  const id = conjunto ? conjuntos[0] : lives[0];
  if (!/^[1-9][0-9]{0,9}$/.test(id)) return null;
  // Opcionais, só de espectadores: icone=1 (uma vez) e plataforma=youtube|twitch|kick (só conjunto).
  const icones = params.getAll('icone');
  const plataformas = params.getAll('plataforma');
  if (icones.length > 1 || (icones.length === 1 && icones[0] !== '1')) return null;
  if (plataformas.length > 1 || (plataformas.length === 1 && !Object.hasOwn(ICONES, plataformas[0]))) return null;
  if ((icones.length > 0 || plataformas.length > 0) && metrica !== 'views') return null;
  if (plataformas.length > 0 && !conjunto) return null;
  return {
    caminho: `/api/public/${conjunto ? 'sets' : 'lives'}/${id}`, metrica, conjunto,
    icone: icones.length === 1, plataforma: plataformas.length === 1 ? plataformas[0] : null,
  };
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
 * @param {{caminho: string, metrica: 'likes' | 'views', conjunto: boolean, icone: boolean, plataforma: string | null}} cfg
 * @param {HTMLElement} raiz
 */
function iniciar(cfg, raiz) {
  const contagem = /** @type {HTMLElement} */ (raiz.querySelector('#contagem'));
  const barra = /** @type {HTMLElement} */ (raiz.querySelector('#barra'));
  const preenchimento = /** @type {HTMLElement} */ (raiz.querySelector('#barra-preenchimento'));
  const carrossel = criarCarrossel(contagem);
  document.title = cfg.metrica === 'likes' ? 'Likes' : 'Espectadores';

  // Com icone=1 o número ganha uma linha: [ícones][número]. Sem ele a página fica exatamente como era.
  /** @type {HTMLElement | null} */
  let icones = null;
  let iconesChave = '';
  if (cfg.icone) {
    const linha = document.createElement('div');
    linha.id = 'linha-views';
    icones = document.createElement('span');
    icones.id = 'icones';
    contagem.replaceWith(linha);
    linha.append(icones, contagem);
  }

  /** @param {string[]} plataformas chaves em ordem fixa (kick, twitch, youtube) */
  function mostrarIcones(plataformas) {
    const chave = plataformas.join(',');
    if (!icones || chave === iconesChave) return;
    iconesChave = chave;
    icones.replaceChildren(...plataformas.map((p) => {
      const img = document.createElement('img');
      img.className = 'icone-plat';
      img.src = `/overlay/plataformas/${ICONES[/** @type {'kick'} */ (p)]}.svg`;
      img.alt = NOMES[/** @type {'kick'} */ (p)];
      return img;
    }));
    icones.hidden = plataformas.length === 0;
  }

  /** @param {any} dados @returns {number | null} */
  function espectadores(dados) {
    if (!cfg.conjunto) return dados.viewers;
    if (cfg.plataforma === null) return dados.viewers_total;
    const lista = Array.isArray(dados.lives) ? dados.lives : [];
    let soma = 0;
    for (const l of lista) {
      if (l && l.platform === cfg.plataforma && l.status === 'ao_vivo' && inteiroNaoNegativo(l.viewers)) soma += l.viewers;
    }
    return soma;
  }

  /** @param {any} dados */
  function atualizarIcones(dados) {
    if (!cfg.icone) return;
    if (cfg.plataforma !== null) return mostrarIcones([cfg.plataforma]);
    if (!cfg.conjunto) {
      return mostrarIcones(typeof dados.platform === 'string' && Object.hasOwn(ICONES, dados.platform) ? [dados.platform] : []);
    }
    const lista = Array.isArray(dados.lives) ? dados.lives : [];
    mostrarIcones(Object.keys(ICONES).filter((p) => lista.some((/** @type {any} */ l) => l && l.platform === p && l.status === 'ao_vivo')));
  }

  async function atualizar() {
    try {
      const res = await fetch(`${cfg.caminho}?t=${Date.now()}`);
      if (!res.ok) return; // 503 antes do 1º ciclo do worker, 429...: mantém o que já está na tela
      const dados = await res.json();
      if (!dados) return;
      const valor = cfg.metrica === 'likes'
        ? (cfg.conjunto ? dados.likes_total : dados.likes)
        : espectadores(dados);
      if (!inteiroNaoNegativo(valor)) return; // sem valor (ex.: likes de Twitch/Kick): mantém o que já está
      const cor = typeof dados.color === 'string' && COR_VALIDA.test(dados.color) ? dados.color : COR_PADRAO;

      aplicarAparencia(contagem, dados); // fonte e cor do texto (D35)
      if (cfg.metrica === 'likes') {
        const meta = inteiroNaoNegativo(dados.goal) && dados.goal > 0 ? dados.goal : null;
        const texto = meta === null ? milhar(valor) : `${milhar(valor)}/${milhar(meta)}`;
        contagem.setAttribute('aria-label', texto);
        carrossel.mostrar(texto);
        raiz.style.setProperty('--cor-barra', cor);
        if (meta === null) {
          barra.hidden = true;
          preenchimento.style.width = '0%';
        } else {
          barra.hidden = false;
          const fracao = Math.min(valor / meta, 1);
          preenchimento.style.width = `${Math.round(fracao * 10000) / 100}%`;
        }
      } else {
        const texto = milhar(valor);
        contagem.setAttribute('aria-label', texto);
        carrossel.mostrar(texto);
        barra.hidden = true;
        atualizarIcones(dados);
      }
      raiz.hidden = false;
    } catch (e) {
      // Falha de rede ou JSON inválido: mantém o último valor exibido.
      console.warn('Erro ao buscar os dados:', e);
    }
  }

  void atualizar();
  setInterval(() => void atualizar(), INTERVALO_MS);
}

const cfg = lerParametros(window.location.search);
const raiz = document.getElementById('overlay-likes');
if (cfg !== null && raiz) {
  iniciar(cfg, raiz);
}
