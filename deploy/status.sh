#!/bin/sh
# LiveStats - estado dos servicos, heartbeat dos workers e resumo dos ultimos logs (SOMENTE LEITURA).
# Os logs passam por um filtro que oculta chaves da YouTube, hashes argon2 e blocos PEM.
#
# Uso: sh deploy/status.sh [--dir DIR] [--linhas N]
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
LINHAS=8
while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --linhas) LINHAS=$2; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1" ;;
  esac
done

escolher_docker || die "Sem acesso ao Docker."
cd "$DIR" || die "Pasta $DIR nao existe."

titulo "Servicos"
for c in $SERVICOS; do
  if [ "$(saude "$c")" = ausente ]; then
    printf '%-28s %s\n' "$c" "AUSENTE"
    continue
  fi
  printf '%-28s saude=%-10s estado=%s reinicios=%s desde=%s\n' "$c" "$(saude "$c")" \
    "$(docker_ inspect -f '{{.State.Status}}' "$c")" "$(docker_ inspect -f '{{.RestartCount}}' "$c")" \
    "$(docker_ inspect -f '{{.State.StartedAt}}' "$c" | cut -c1-19)"
done

titulo "Heartbeat dos workers (mesma checagem do healthcheck)"
for par in "livestats-worker-lives worker_lives.healthcheck" "livestats-worker-inscritos worker_inscritos.healthcheck" "livestats-worker-chat worker_chat.healthcheck"; do
  # shellcheck disable=SC2086
  set -- $par
  if docker_ exec "$1" python -m "$2" >/dev/null 2>&1; then
    ok "$1: heartbeat recente"
  else
    erro "$1: heartbeat ausente ou antigo (veja os logs abaixo)"
  fi
done

titulo "Rede e proxy"
if [ "$(env_get "$DIR/.env" ADMIN_EXTERNAL_ACCESS)" = 1 ]; then
  if [ -n "$(env_get "$DIR/.env" ADMIN_TRUSTED_PROXY_IP)" ]; then
    ok "Acesso externo ao painel LIGADO (ADMIN_EXTERNAL_ACCESS=1; proxy confiavel $(env_get "$DIR/.env" ADMIN_TRUSTED_PROXY_IP); a lista de IPs nao se aplica; login 5/min por IP). Endereco: '$(env_get "$DIR/.env" PUBLIC_ADMIN_URL)'"
  else
    erro "ADMIN_EXTERNAL_ACCESS=1 mas ADMIN_TRUSTED_PROXY_IP esta vazio: o web nao sobe. Rode deploy/connect_npm.sh ou defina o IP fixo do NPM."
  fi
else
  info "Acesso externo ao painel DESLIGADO (so a lista ADMIN_ALLOW_CIDRS vale). Para ligar: enviar.sh instalar --admin-externo."
fi
msg "TRUSTED_PROXY_CIDR='$(env_get "$DIR/.env" TRUSTED_PROXY_CIDR)'  ADMIN_BIND='$(env_get "$DIR/.env" ADMIN_BIND)'  ADMIN_ALLOW_CIDRS='$(env_get "$DIR/.env" ADMIN_ALLOW_CIDRS)'"
if docker_ network inspect "$REDE_EDGE" >/dev/null 2>&1; then
  docker_ network inspect -f '{{range .Containers}}  {{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}' "$REDE_EDGE"
fi

titulo "Ultimos logs (ocultando segredos)"
for c in $SERVICOS; do
  [ "$(saude "$c")" = ausente ] && continue
  printf -- '--- %s ---\n' "$c"
  docker_ logs --tail "$LINHAS" "$c" 2>&1 | cut -c1-300 | ocultar
done
