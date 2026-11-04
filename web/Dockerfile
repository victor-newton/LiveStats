# LiveStats - imagem do Nginx (web). Contexto de build: raiz do repositorio (docker-compose.yml, D22).
#
# Base: nginxinc/nginx-unprivileged (Nginx 1.30.6, Alpine), fixada por digest do indice multi-arquitetura.
# Para atualizar: `docker buildx imagetools inspect nginxinc/nginx-unprivileged:<tag>` e trocar o digest.
# O front (web/static, T6) e assado na imagem: nenhum volume, nenhum bind-mount.

# ---------------------------------------------------------------- estagio 1: estaticos
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS assets
USER root
# Somente as duas pastas do contrato de URLs (repo-layout.md, T6): overlay/ e admin/. O painel e servido
# em /admin-assets/ (e index.html em / e /admin); o diretorio da imagem tem o mesmo nome da URL.
COPY web/static/overlay /assets/overlay
COPY web/static/admin /assets/admin-assets
# Versao no endereco dos .js/.css do overlay (?v=12 primeiros hex do SHA-256 do conteudo): o Cloudflare guarda
# .js/.css em cache mesmo com "no-cache" na origem; o HTML
# nao fica em cache, entao cada build novo pede arquivos novos. Primeiro os modulos importados (folhas), depois os
# que importam, por ultimo o HTML. Referencia sem versao quebra o BUILD.
RUN set -eu; cd /assets/overlay; \
    v() { sha256sum "$1" | cut -c1-12; }; \
    for f in aparencia.js carousel.js; do h=$(v "$f"); sed -i "s#from '\./$f'#from './$f?v=$h'#g" ./*.js; done; \
    for f in ./*.js ./*.css; do f=${f#./}; h=$(v "$f"); sed -i "s#\"/overlay/$f\"#\"/overlay/$f?v=$h\"#g" ./*.html; done; \
    if grep -n "from '\./[^'?]*'" ./*.js; then echo "import sem ?v= no overlay" >&2; exit 1; fi; \
    if grep -nE '"/overlay/[^"?]+\.(js|css)"' ./*.html; then echo "referencia sem ?v= no overlay" >&2; exit 1; fi
# Somente leitura para todos, nada executavel, nada gravavel.
RUN find /assets -type d -exec chmod 0755 {} + \
 && find /assets -type f -exec chmod 0644 {} +

# ---------------------------------------------------------------- estagio 2: valida a configuracao
# Renderiza os templates (com IPs de exemplo validos) e roda `nginx -t`: configuracao invalida quebra o
# BUILD, nao a subida em producao. Somente os ARQUIVOS DE ORIGEM seguem para a imagem final.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS confcheck
USER root
COPY web/nginx/nginx.conf /etc/nginx/nginx.conf
COPY web/nginx/snippets/ /etc/nginx/snippets/
COPY web/nginx/templates/ /etc/nginx/templates/
COPY --chmod=0755 web/nginx/docker-entrypoint.d/15-livestats-realip.envsh /docker-entrypoint.d/15-livestats-realip.envsh
# Par TLS DESCARTAVEL so para o `nginx -t` deste estagio (o estagio final nao o recebe; em producao os dois
# secrets admin_tls_cert/admin_tls_key vem do compose). O openssl existe so neste estagio.
RUN apk add --no-cache openssl \
 && mkdir -p /run/secrets \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=confcheck" \
      -keyout /run/secrets/admin_tls_key -out /run/secrets/admin_tls_cert \
 && chmod 0644 /run/secrets/admin_tls_key /run/secrets/admin_tls_cert
RUN sed -i 's/\r$//' /docker-entrypoint.d/15-livestats-realip.envsh /etc/nginx/nginx.conf \
 && find /etc/nginx/snippets /etc/nginx/templates -type f -exec sed -i 's/\r$//' {} + \
 && rm -f /etc/nginx/conf.d/default.conf \
 && TRUSTED_PROXY_CIDR=172.29.80.10/32 ADMIN_ALLOW_CIDRS='192.168.0.0/16 100.64.0.0/10' NGINX_ENTRYPOINT_QUIET_LOGS=1 \
    sh -c '. /docker-entrypoint.d/15-livestats-realip.envsh && /docker-entrypoint.d/20-envsubst-on-templates.sh' \
 && nginx -t \
 && rm -f /etc/nginx/conf.d/*.conf /run/secrets/admin_tls_key /run/secrets/admin_tls_cert

# ---------------------------------------------------------------- estagio final
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime
USER root
# O default.conf da imagem (listen 8080) conflitaria com o nosso; em producao o compose monta um tmpfs
# em /etc/nginx/conf.d, onde o envsubst escreve a configuracao renderizada.
RUN rm -f /etc/nginx/conf.d/default.conf /usr/share/nginx/html/index.html /usr/share/nginx/html/50x.html
# Correcoes de seguranca do Alpine publicadas depois da imagem base; o curl nao e usado (o healthcheck usa wget).
RUN apk upgrade --no-cache && apk del --no-cache curl
COPY --from=confcheck --chown=0:0 /etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=confcheck --chown=0:0 /etc/nginx/snippets/ /etc/nginx/snippets/
COPY --from=confcheck --chown=0:0 /etc/nginx/templates/ /etc/nginx/templates/
COPY --from=confcheck --chown=0:0 /docker-entrypoint.d/15-livestats-realip.envsh /docker-entrypoint.d/15-livestats-realip.envsh
COPY --from=assets --chown=0:0 /assets/ /usr/share/nginx/html/

USER 101:101
EXPOSE 8080 8081

# Mesmo teste do healthcheck do docker-compose.yml (passa pela api: /health e da api).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health"]
