// @ts-check
// Carrossel de dígitos do overlay de likes (D34). É a MESMA animação do contador de
// inscritos (contador.js): mesmos tempos, mesma curva, mesma estrutura de DOM
// (.digito-wrap > .digito-inner > 10 spans). A diferença é só o que conta como
// separador: aqui qualquer caractere que não seja dígito ("." do milhar, "/" da meta).

export const CURVA = 'cubic-bezier(0.25, 0.46, 0.45, 0.94)';

/** @param {string} c */
export function ehSeparador(c) {
  return c < '0' || c > '9';
}

/**
 * @param {HTMLElement} container
 * @param {string} texto
 */
function construirCarrossel(container, texto) {
  container.replaceChildren();
  for (const char of texto) {
    if (ehSeparador(char)) {
      const sep = document.createElement('span');
      sep.className = 'separador';
      sep.textContent = char;
      sep.setAttribute('aria-hidden', 'true');
      container.appendChild(sep);
    } else {
      const wrap = document.createElement('div');
      wrap.className = 'digito-wrap';
      wrap.setAttribute('aria-hidden', 'true');
      const inner = document.createElement('div');
      inner.className = 'digito-inner';
      for (let d = 0; d <= 9; d++) {
        const span = document.createElement('span');
        span.textContent = String(d);
        inner.appendChild(span);
      }
      wrap.appendChild(inner);
      container.appendChild(wrap);
      inner.style.transform = `translateY(-${parseInt(char, 10)}em)`;
      inner.style.transition = 'none';
    }
  }
}

/**
 * Carrossel ligado a um container. `mostrar(texto)` aceita só dígitos e separadores
 * (ex.: "1.234" ou "1.000/10.000"); texto igual ao atual não faz nada.
 * @param {HTMLElement} container
 * @returns {{mostrar: (texto: string) => void}}
 */
export function criarCarrossel(container) {
  /** @type {string | null} */
  let valorAtual = null;

  /** @param {string} novoTexto */
  function animarParaValor(novoTexto) {
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
          inner.style.transform = `translateY(-${parseInt(char, 10)}em)`;
        }, idx * 40);
      });
      return;
    }

    let idxChar = 0;
    for (const c of novoTexto) {
      if (ehSeparador(c)) continue;
      const inner = inners[idxChar];
      if (inner) {
        inner.style.transition = `transform 0.45s ${CURVA}`;
        inner.style.transform = `translateY(-${parseInt(c, 10)}em)`;
      }
      idxChar++;
    }
  }

  return {
    mostrar(texto) {
      if (!texto || texto === valorAtual) return;
      if (valorAtual === null) {
        // Primeira exibição: os dígitos "rolam" de 0 até o valor, escalonados.
        construirCarrossel(container, texto);
        const inners = /** @type {NodeListOf<HTMLElement>} */ (container.querySelectorAll('.digito-inner'));
        const chars = [...texto].filter((c) => !ehSeparador(c));
        inners.forEach((inner, idx) => {
          const char = chars[idx];
          if (!char) return;
          inner.style.transform = 'translateY(0)';
          inner.style.transition = 'none';
          setTimeout(() => {
            inner.style.transition = `transform 0.5s ${CURVA}`;
            inner.style.transform = `translateY(-${parseInt(char, 10)}em)`;
          }, 50 + idx * 60);
        });
      } else {
        animarParaValor(texto);
      }
      valorAtual = texto;
    },
  };
}
