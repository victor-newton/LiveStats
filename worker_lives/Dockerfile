# LiveStats - imagem do worker-lives (YouTube Data API v3). Contexto de build: raiz do repositorio (D22).
#
# Base: python:3.12-slim (Debian 13 "trixie"), fixada por digest do indice multi-arquitetura (o mesmo da api).
# Nenhum ARG/ENV de segredo: a chave da API e lida de /run/secrets/youtube_api_key em tempo de execucao.

# ---------------------------------------------------------------- estagio 1: dependencias
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
# requirements.txt tem versoes exatas e hashes (T4); --require-hashes recusa qualquer pacote sem hash.
COPY worker_lives/requirements.txt /build/requirements.txt
RUN /opt/venv/bin/pip install --require-hashes -r /build/requirements.txt

# ---------------------------------------------------------------- estagio final
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

# Usuario sem privilegios 10001:10001 e /data root:10001 modo 2770 (D1). Este container NAO tem /admin-state.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir /data \
 && chown root:10001 /data \
 && chmod 2770 /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/livestats
COPY worker_lives ./worker_lives

USER 10001:10001

# Mesmo teste do healthcheck do docker-compose.yml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "worker_lives.healthcheck"]

# Nenhuma porta: o worker so sai para a internet e escreve no SQLite de /data.
CMD ["python", "-m", "worker_lives"]
