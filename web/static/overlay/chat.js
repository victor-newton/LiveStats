// @ts-check
// Overlay do chat unificado (D57): /overlay/chat.html?conjunto={id}[&tema=<id>]
// Lê a aparência em /api/public/chat/{id} e as mensagens por SSE em /chat/{id}/eventos (eventos msg, del,
// clear e config). Nada é guardado: a mensagem vive só no DOM e é REMOVIDA quando sai da área visível (ou
// após `lifetime_s`). Fundo transparente. Todo dado do chat entra por textContent/createElement; a URL do
// emote é montada aqui (Twitch/Kick pelo id; YouTube só nos dois hosts permitidos), nunca vem pronta da rede.
// Estilo dinâmico só por CSSOM (style.setProperty), que a CSP permite.

export const TEMAS = ['cartoon', 'adesivo', 'pixel', 'postit', 'chiclete', 'classico', 'balao', 'vidro', 'minimal', 'terminal', 'rodape'];
const LOGOS = ['yt', 'tw', 'kk'];
const NOMES = { yt: 'YouTube', tw: 'Twitch', kk: 'Kick' };
const SIGLAS = { yt: 'YT', tw: 'TW', kk: 'KICK' };
const CORES = { yt: '#FF0033', tw: '#9146FF', kk: '#53FC18' };
const ROTULOS = { owner: 'dono', mod: 'mod', vip: 'vip', sub: 'sub', member: 'membro', verified: 'verificado' };
const PRIORIDADE = ['owner', 'mod', 'vip', 'sub', 'member', 'verified'];
const HOSTS_YT = ['yt3.ggpht.com', 'www.youtube.com'];
const ID_EMOTE = /^[A-Za-z0-9_-]{1,64}$/;
const COR_VALIDA = /^#[0-9A-Fa-f]{6}$/;
const UID_VALIDO = /^[a-z0-9_.@-]{1,64}$/; // login/slug/id do canal do autor, em minúsculas (o `clear` de um usuário usa o mesmo)
const NOVA_TENTATIVA_MS = 30000;
const SAIDA_MS = 380; // duração da animação de saída (.msg.out)
const FILA_MAX = 25; // Rodapé: mensagens esperando para entrar no letreiro
const ESPACO_LETREIRO = 34; // igual ao gap do .track
const TEXTO_MAX = 500;
const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * @typedef {{t: 'text', v: string} | {t: 'emote', id: string, alt: string, src: string}} Parte
 * @typedef {{id: string, p: 'yt' | 'tw' | 'kk', name: string, uid: string, color: string | null, role: string | null, parts: Parte[]}} Msg
 * @typedef {{m: Msg, el: HTMLElement | null, vida: number, saida: number}} Entrada
 * @typedef {{theme: string, font_size: number, logo_style: string, name_color: string, lifetime_s: number, show_roles: boolean}} Aparencia
 */

/**
 * Lê ?conjunto=N (uma vez só, só dígitos) e ?tema= (opcional, lista fechada; valor inválido é ignorado).
 * @param {string} search
 * @returns {{id: string, tema: string | null} | null}
 */
export function lerParametros(search) {
  const params = new URLSearchParams(search);
  const conjuntos = params.getAll('conjunto');
  if (conjuntos.length !== 1 || !/^[1-9][0-9]{0,9}$/.test(conjuntos[0])) return null;
  const temas = params.getAll('tema');
  const tema = temas.length === 1 && TEMAS.includes(temas[0]) ? temas[0] : null;
  return { id: conjuntos[0], tema };
}

/**
 * URL da imagem de um emote, montada aqui. null = não mostra imagem (vira o texto `alt`).
 * @param {'yt' | 'tw' | 'kk'} p
 * @param {{id: string, src: string}} emote
 * @returns {string | null}
 */
export function urlEmote(p, emote) {
  if (p === 'tw' && ID_EMOTE.test(emote.id)) return `https://static-cdn.jtvnw.net/emoticons/v2/${emote.id}/default/dark/2.0`;
  if (p === 'kk' && ID_EMOTE.test(emote.id)) return `https://files.kick.com/emotes/${emote.id}/fullsize`;
  if (p === 'yt') {
    try {
      const u = new URL(emote.src);
      if (u.protocol === 'https:' && HOSTS_YT.includes(u.hostname) && u.port === '' && u.username === '' && u.password === '') return u.href;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Valida uma mensagem do SSE. Qualquer coisa fora do formato normalizado é descartada.
 * @param {any} d
 * @returns {Msg | null}
 */
export function validarMensagem(d) {
  if (!d || typeof d !== 'object') return null;
  if (typeof d.id !== 'string' || d.id.length === 0 || d.id.length > 100) return null;
  if (d.p !== 'yt' && d.p !== 'tw' && d.p !== 'kk') return null;
  if (typeof d.name !== 'string' || !Array.isArray(d.parts)) return null;
  /** @type {Parte[]} */
  const parts = [];
  let total = 0;
  for (const x of d.parts.slice(0, 200)) {
    if (!x || typeof x !== 'object') continue;
    if (x.t === 'text' && typeof x.v === 'string') {
      const v = x.v.slice(0, Math.max(0, TEXTO_MAX - total));
      total += v.length;
      if (v) parts.push({ t: 'text', v });
    } else if (x.t === 'emote' && typeof x.alt === 'string') {
      parts.push({ t: 'emote', id: typeof x.id === 'string' ? x.id : '', alt: x.alt.slice(0, 100), src: typeof x.src === 'string' ? x.src : '' });
    }
  }
  if (parts.length === 0) return null;
  const roles = Array.isArray(d.roles) ? d.roles.filter((r) => typeof r === 'string') : [];
  const role = PRIORIDADE.find((r) => roles.includes(r)) ?? null;
  const color = typeof d.color === 'string' && COR_VALIDA.test(d.color) ? d.color : null;
  const uid = typeof d.uid === 'string' && UID_VALIDO.test(d.uid) ? d.uid : '';
  return { id: d.id, p: d.p, name: d.name.slice(0, 50), uid, color, role, parts };
}

/**
 * Normaliza a resposta de /api/public/chat/{id}; valores fora do contrato caem nos padrões.
 * @param {any} d
 * @returns {(Aparencia & {enabled: boolean}) | null}
 */
export function normalizarConfig(d) {
  if (!d || typeof d !== 'object') return null;
  const inteiro = (v, min, max, padrao) => (Number.isInteger(v) && v >= min && v <= max ? v : padrao);
  return {
    enabled: d.enabled === true,
    theme: TEMAS.includes(d.theme) ? d.theme : 'classico',
    font_size: inteiro(d.font_size, 12, 40, 18),
    logo_style: d.logo_style === 'mono' || d.logo_style === 'sigla' ? d.logo_style : 'cor',
    name_color: d.name_color === 'plataforma' || d.name_color === 'branco' ? d.name_color : 'usuario',
    lifetime_s: inteiro(d.lifetime_s, 0, 300, 0),
    show_roles: d.show_roles !== false && d.show_roles !== 0,
  };
}

/**
 * @param {string} tag
 * @param {string} [cls]
 * @param {string} [texto]
 * @returns {HTMLElement}
 */
function el(tag, cls, texto) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

/**
 * @param {{id: string}} cfg
 * @param {string | null} temaUrl
 */
function iniciar(cfg, temaUrl) {
  const chat = /** @type {HTMLElement} */ (document.getElementById('chat'));
  const lista = /** @type {HTMLElement} */ (document.getElementById('lista'));
  const faixa = /** @type {HTMLElement} */ (document.getElementById('faixa'));
  const vista = /** @type {HTMLElement} */ (faixa.querySelector('.band-view'));
  const trilha = /** @type {HTMLElement} */ (document.getElementById('trilha'));
  const reduzido = window.matchMedia('(prefers-reduced-motion: reduce)');

  /** @type {Aparencia} */
  let ap = { theme: 'classico', font_size: 18, logo_style: 'cor', name_color: 'usuario', lifetime_s: 0, show_roles: true };
  let tema = ''; // só vale depois da 1ª aplicação da aparência
  /** @type {Set<Entrada>} em ordem de chegada */
  const entradas = new Set();
  /** @type {Map<string, Entrada>} */
  const porId = new Map();
  /** @type {WeakMap<Element, Entrada>} */
  const deElemento = new WeakMap();
  /** @type {Map<string, Element>} logos já lidos (svg do próprio overlay) */
  const logos = new Map();
  /** @type {EventSource | null} */
  let fonte = null;
  let novaTentativa = 0;
  let animando = false;
  let x = 0; // deslocamento do letreiro
  let ultimo = 0;

  // ---------- Montagem de uma mensagem ----------

  /** @param {'yt' | 'tw' | 'kk'} p */
  function icone(p) {
    const base = logos.get(p);
    if (ap.logo_style === 'sigla' || !base) {
      const s = el('span', 'sigla', SIGLAS[p]);
      s.dataset.p = p;
      s.title = NOMES[p];
      return s;
    }
    const svg = /** @type {SVGElement} */ (document.importNode(base, true));
    svg.setAttribute('class', ap.logo_style === 'mono' ? 'logo mono' : 'logo');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', NOMES[p]);
    return svg;
  }

  /** @param {Msg} m */
  function corDoNome(m) {
    if (ap.name_color === 'branco') return '#ffffff';
    if (ap.name_color === 'plataforma') return CORES[m.p];
    return m.color ?? CORES[m.p];
  }

  /**
   * @param {Msg} m
   * @param {string} t tema
   */
  function construir(m, t) {
    const msg = el('div', 'msg');
    msg.dataset.p = m.p;
    if (t === 'adesivo' || t === 'postit') {
      const rot = (Math.random() * 5 - 2.5) * (t === 'postit' ? 1.4 : 1);
      msg.style.setProperty('--rot', `${rot.toFixed(2)}deg`);
    }
    if (t === 'terminal') msg.append(el('span', 'hora', new Date().toTimeString().slice(0, 5)));
    const who = el('span', 'who');
    who.append(icone(m.p));
    if (ap.show_roles && m.role) {
      const r = el('span', 'role', ROTULOS[/** @type {keyof typeof ROTULOS} */ (m.role)]);
      r.dataset.r = m.role;
      who.append(r);
    }
    const nome = el('span', 'name', m.name);
    const cor = corDoNome(m);
    nome.style.setProperty('color', cor);
    nome.style.setProperty('--nc', cor);
    who.append(nome);
    const txt = el('span', 'txt');
    for (const parte of m.parts) {
      if (parte.t === 'text') {
        txt.append(document.createTextNode(parte.v));
        continue;
      }
      const url = urlEmote(m.p, parte);
      if (url === null) {
        if (parte.alt) txt.append(document.createTextNode(parte.alt));
        continue;
      }
      const img = document.createElement('img');
      img.className = 'emote';
      img.alt = parte.alt;
      img.referrerPolicy = 'no-referrer';
      img.decoding = 'async';
      img.src = url;
      img.addEventListener('error', () => img.replaceWith(document.createTextNode(parte.alt)), { once: true });
      txt.append(img);
    }
    msg.append(who, txt);
    return msg;
  }

  // ---------- Entradas e remoção ----------

  /** @param {Entrada} e */
  function remover(e) {
    clearTimeout(e.vida);
    clearTimeout(e.saida);
    if (e.el) {
      deElemento.delete(e.el);
      e.el.remove();
      e.el = null;
    }
    entradas.delete(e);
    if (porId.get(e.m.id) === e) porId.delete(e.m.id);
  }

  /** @param {Entrada} e */
  function sair(e) {
    if (!e.el) return;
    e.el.classList.add('out');
    e.saida = window.setTimeout(() => remover(e), SAIDA_MS);
  }

  function aparar() {
    if (tema === 'rodape') return;
    const max = chat.clientHeight - (tema === 'terminal' ? 16 : 0);
    while (lista.children.length > 1 && lista.offsetHeight > max) {
      const primeiro = /** @type {HTMLElement} */ (lista.firstElementChild);
      const e = deElemento.get(primeiro);
      if (e) remover(e);
      else primeiro.remove();
    }
  }

  function pendentes() {
    let n = 0;
    for (const e of entradas) if (e.el === null) n++;
    return n;
  }

  /** @param {Msg} m */
  function receber(m) {
    if (porId.has(m.id)) return;
    /** @type {Entrada} */
    const e = { m, el: null, vida: 0, saida: 0 };
    entradas.add(e);
    porId.set(m.id, e);
    if (tema === 'rodape') {
      // letreiro: fila curta; a mais antiga que ainda não entrou sai primeiro
      while (pendentes() > FILA_MAX) {
        for (const o of entradas) {
          if (o.el === null) {
            remover(o);
            break;
          }
        }
      }
      return;
    }
    e.el = construir(m, tema);
    deElemento.set(e.el, e);
    lista.append(e.el);
    aparar();
    if (ap.lifetime_s > 0 && e.el) e.vida = window.setTimeout(() => sair(e), ap.lifetime_s * 1000);
  }

  /** @param {string[]} ids */
  function apagar(ids) {
    for (const id of ids) {
      const e = porId.get(id);
      if (e) remover(e);
    }
  }

  /**
   * Limpa o chat de uma plataforma (`usuario` null) ou só as mensagens de uma conta: o `usuario` do evento é o `uid` da
   * mensagem (login/slug/id do canal), não o nome exibido, que pode ser outro. Usuário fora do padrão não limpa nada.
   * @param {string} p
   * @param {string | null} usuario
   */
  function limparChat(p, usuario) {
    const alvo = usuario === null ? null : usuario.toLowerCase();
    if (alvo !== null && !UID_VALIDO.test(alvo)) return;
    for (const e of [...entradas]) {
      if (e.m.p === p && (alvo === null || e.m.uid === alvo)) remover(e);
    }
  }

  function esvaziar() {
    for (const e of [...entradas]) remover(e);
    lista.textContent = '';
    trilha.textContent = '';
  }

  // ---------- Letreiro (Rodapé) ----------

  /** @param {number} agora */
  function quadro(agora) {
    animando = false;
    if (tema !== 'rodape' || faixa.hidden) return;
    const dt = Math.min(agora - ultimo, 100);
    ultimo = agora;
    const parado = reduzido.matches;
    if (parado) {
      x = 0;
    } else {
      x -= ((70 + pendentes() * 8) * dt) / 1000;
      const primeiro = /** @type {HTMLElement | null} */ (trilha.firstElementChild);
      if (primeiro && x + primeiro.offsetWidth + ESPACO_LETREIRO < 0) {
        x += primeiro.offsetWidth + ESPACO_LETREIRO;
        const e = deElemento.get(primeiro);
        if (e) remover(e);
        else primeiro.remove();
      }
    }
    if (pendentes() > 0 && (parado || trilha.scrollWidth + x < vista.clientWidth)) {
      let prox = null;
      for (const e of entradas) {
        if (e.el === null) {
          prox = e;
          break;
        }
      }
      if (prox) {
        prox.el = construir(prox.m, 'rodape');
        deElemento.set(prox.el, prox);
        trilha.append(prox.el);
        // sem movimento: o letreiro vira uma faixa estática que descarta as mais antigas
        while (parado && trilha.children.length > 1 && trilha.scrollWidth > vista.clientWidth) {
          const primeiro = /** @type {HTMLElement} */ (trilha.firstElementChild);
          const e = deElemento.get(primeiro);
          if (e) remover(e);
          else primeiro.remove();
        }
      }
    }
    trilha.style.setProperty('transform', `translateX(${x.toFixed(1)}px)`);
    animar();
  }

  function animar() {
    if (animando || tema !== 'rodape' || faixa.hidden) return;
    animando = true;
    requestAnimationFrame(quadro);
  }

  // ---------- Aparência ----------

  function reconstruir() {
    lista.textContent = '';
    trilha.textContent = '';
    for (const e of entradas) {
      clearTimeout(e.saida);
      if (e.el) deElemento.delete(e.el);
      e.el = null;
    }
    if (tema === 'rodape') {
      x = vista.clientWidth;
      return;
    }
    for (const e of entradas) {
      e.el = construir(e.m, tema);
      deElemento.set(e.el, e);
      lista.append(e.el);
    }
    aparar();
  }

  /** @param {Aparencia} nova */
  function aplicar(nova) {
    const novoTema = temaUrl ?? nova.theme;
    const mudou = novoTema !== tema || nova.logo_style !== ap.logo_style || nova.name_color !== ap.name_color || nova.show_roles !== ap.show_roles;
    ap = nova;
    tema = novoTema;
    document.body.style.setProperty('--fs', `${nova.font_size}px`);
    chat.className = `chat t-${tema}`;
    chat.hidden = tema === 'rodape';
    faixa.hidden = tema !== 'rodape';
    if (mudou) reconstruir();
    else aparar();
    ultimo = performance.now();
    animar();
  }

  // ---------- Rede ----------

  async function carregarLogos() {
    await Promise.all(LOGOS.map(async (p) => {
      try {
        const res = await fetch(`/overlay/plataformas/${p}.svg`);
        if (!res.ok) return;
        const doc = new DOMParser().parseFromString(await res.text(), 'image/svg+xml');
        const raiz = doc.documentElement;
        if (raiz && raiz.namespaceURI === SVG_NS && raiz.localName === 'svg') {
          raiz.removeAttribute('width');
          raiz.removeAttribute('height');
          logos.set(p, raiz);
        }
      } catch (e) {
        console.warn('Logo indisponível, usando a sigla:', p);
      }
    }));
  }

  async function buscarConfig() {
    try {
      const res = await fetch(`/api/public/chat/${cfg.id}?t=${Date.now()}`);
      if (!res.ok) return null; // 404: conjunto inexistente
      return normalizarConfig(await res.json());
    } catch (e) {
      return null;
    }
  }

  function agendar() {
    if (novaTentativa === 0) {
      novaTentativa = window.setTimeout(() => {
        novaTentativa = 0;
        void ciclo();
      }, NOVA_TENTATIVA_MS);
    }
  }

  function desligar() {
    if (fonte) fonte.close();
    fonte = null;
    esvaziar();
    chat.hidden = true;
    faixa.hidden = true;
  }

  async function ciclo() {
    const c = await buscarConfig();
    if (c === null || !c.enabled) {
      desligar();
      agendar();
      return;
    }
    aplicar(c);
    if (fonte === null) abrir();
  }

  /** @param {string} nome @param {(dados: any) => void} tratar */
  function ouvir(nome, tratar) {
    /** @type {EventSource} */ (fonte).addEventListener(nome, (ev) => {
      let dados;
      try {
        dados = JSON.parse(/** @type {MessageEvent} */ (ev).data);
      } catch (e) {
        return;
      }
      tratar(dados);
    });
  }

  function abrir() {
    const es = new EventSource(`/chat/${cfg.id}/eventos`);
    fonte = es;
    ouvir('msg', (d) => {
      const m = validarMensagem(d);
      if (m) receber(m);
    });
    ouvir('del', (d) => {
      if (d && Array.isArray(d.ids)) apagar(d.ids.filter((/** @type {unknown} */ i) => typeof i === 'string'));
    });
    ouvir('clear', (d) => {
      if (!d || (d.p !== 'yt' && d.p !== 'tw' && d.p !== 'kk')) return;
      if (d.user === null || d.user === undefined) limparChat(d.p, null);
      else if (typeof d.user === 'string') limparChat(d.p, d.user);
    });
    ouvir('config', () => void ciclo());
    es.addEventListener('error', () => {
      // 404/503 do servidor fecha o EventSource de vez; queda de rede o navegador reconecta sozinho
      if (es.readyState === EventSource.CLOSED && fonte === es) {
        desligar();
        agendar();
      }
    });
  }

  window.addEventListener('resize', () => {
    aparar();
    if (tema === 'rodape') animar();
  });
  void carregarLogos().then(() => ciclo());
}

const cfg = lerParametros(window.location.search);
if (cfg !== null && document.getElementById('chat')) {
  iniciar(cfg, cfg.tema);
}
