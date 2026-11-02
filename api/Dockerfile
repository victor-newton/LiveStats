# LiveStats - imagem da api (FastAPI). Contexto de build: raiz do repositorio (docker-compose.yml, D22).
#
# Base: python:3.12-slim (Debian 13 "trixie"), fixada por digest do indice multi-arquitetura.
# Para atualizar: `docker buildx imagetools inspect python:3.12-slim` e trocar o digest nos DOIS `FROM`.
# Nenhum ARG/ENV de segredo: a api le /run/secrets/* em tempo de execucao (arquivos montados pelo compose).

# ---------------------------------------------------------------- estagio 1: dependencias
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
# requirements.txt tem versoes exatas e hashes (T3); --require-hashes recusa qualquer pacote sem hash.
COPY api/requirements.txt /build/requirements.txt
RUN /opt/venv/bin/pip install --require-hashes -r /build/requirements.txt

# ---------------------------------------------------------------- estagio final
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}"

# Usuario sem privilegios 10001:10001 (mesmo UID/GID das tres imagens Python, D1).
#  - /data: root:10001 modo 2770 (api e workers escrevem no mesmo SQLite; um volume nomeado novo herda
#    dono e modo deste diretorio).
#  - /admin-state: 10001 modo 0700, SOMENTE nesta imagem (volume livestats-admin, D2).
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir /data /admin-state \
 && chown root:10001 /data \
 && chmod 2770 /data \
 && chown 10001:10001 /admin-state \
 && chmod 0700 /admin-state

COPY --from=builder /opt/venv /opt/venv

# Mesma disposicao relativa do repositorio (api/ e db/ lado a lado), para o codigo achar os esquemas SQL.
WORKDIR /srv/livestats/api
COPY api/app ./app
COPY api/tools ./tools
COPY db/schema.sql db/admin_schema.sql /srv/livestats/db/

USER 10001:10001
EXPOSE 8000

# Mesmo teste do healthcheck do docker-compose.yml.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-m", "app.healthcheck"]

# Porta 8000 so na rede interna (nao publicada). Um unico processo: o SQLite e compartilhado com os workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
