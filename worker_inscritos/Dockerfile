# LiveStats - imagem do worker-inscritos (Playwright + Chromium). Contexto de build: raiz do repositorio (D22).
#
# Base: python:3.12-slim (Debian 13 "trixie"), fixada por digest do indice multi-arquitetura (o mesmo da api).
# Versao do Chromium: fixada pela BIBLIOTECA. `playwright install chromium` baixa exatamente a revisao do
# Chromium que o Playwright de worker_inscritos/requirements.txt (versao exata + hashes, T5) espera; trocar
# a versao do Playwright troca o Chromium, e so assim. Nao existe "Chrome current" nesta imagem.
# Nenhum ARG/ENV de segredo: a chave privada dos cookies e lida de /run/secrets/cookie_private_key.

# ---------------------------------------------------------------- estagio 1: dependencias Python
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY worker_inscritos/requirements.txt /build/requirements.txt
# Falha o build se o Playwright nao estiver com versao exata (==) no arquivo de requisitos.
RUN grep -Eiq '^playwright==[0-9]' /build/requirements.txt \
 && /opt/venv/bin/pip install --require-hashes -r /build/requirements.txt

# ---------------------------------------------------------------- estagio final
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# Usuario sem privilegios 10001:10001 (home /home/app: o compose monta um tmpfs la) e /data root:10001 2770.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
 && mkdir /data \
 && chown root:10001 /data \
 && chmod 2770 /data

COPY --from=builder /opt/venv /opt/venv

# Chromium + bibliotecas de sistema (apt) na versao pedida pela biblioteca; instalado como root, somente
# leitura para o usuario do worker. Sem cache do apt na imagem.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && python -m playwright install --with-deps chromium \
 && apt-get purge -y --auto-remove xvfb xserver-common \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /opt/ms-playwright

WORKDIR /srv/livestats
COPY worker_inscritos ./worker_inscritos

USER 10001:10001

# Mesmo teste do healthcheck do docker-compose.yml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-m", "worker_inscritos.healthcheck"]

# Nenhuma porta (nem a de debug do Chrome): o Playwright controla o navegador por pipe.
CMD ["python", "-m", "worker_inscritos"]
