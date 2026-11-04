#!/bin/sh
# LiveStats - verificacao previa (SOMENTE LEITURA). Roda no servidor (ZimaOS).
# Nao altera nada, nao le segredos. Saida: [ok] / [aviso] / [ERRO]; o codigo de saida e o numero de ERROS.
# Uso: sh deploy/preflight.sh [--dir /DATA/AppData/livestats]
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1" ;;
  esac
done

ERROS=0
DOCKER_OK=0
falha() { erro "$*"; ERROS=$((ERROS + 1)); }

titulo "Sistema"
info "Host: $(uname -n) | $(uname -sm) | usuario: $(id -un) (uid $(id -u))"
[ -r /etc/os-release ] && info "SO: $(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | tr -d '"')"
if [ -r /proc/meminfo ]; then
  info "Memoria livre aproximada: $(awk '/MemAvailable/ {printf "%d MB", $2/1024}' /proc/meminfo)"
fi

titulo "Docker"
if ! command -v docker >/dev/null 2>&1; then
  falha "docker nao encontrado no PATH."
elif ! escolher_docker; then
  falha "O usuario $(id -un) nao consegue falar com o Docker (nem direto, nem por 'sudo -n'). Peca para entrar no grupo docker ('sudo usermod -aG docker $(id -un)', depois reconecte o SSH) ou configure sudo sem senha."
else
  DOCKER_OK=1
  if [ "$USAR_SUDO" = 1 ]; then
    aviso "Docker so funciona via sudo: os scripts vao usar 'sudo -n docker'."
  else
    ok "Usuario $(id -un) fala com o Docker direto (grupo docker)."
  fi
  ok "Docker: $(docker_ version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
  if docker_ compose version >/dev/null 2>&1; then
    ok "Compose: $(docker_ compose version --short 2>/dev/null || echo '?')"
  else
    falha "Plugin 'docker compose' (v2) nao encontrado."
  fi
fi

titulo "Ferramentas"
if command -v curl >/dev/null 2>&1; then ok "curl presente."; else falha "curl ausente (usado nas checagens HTTP)."; fi
if command -v openssl >/dev/null 2>&1; then
  ok "openssl presente ($(openssl version | cut -d' ' -f1-2))."
else
  aviso "openssl ausente: o install.sh usara um container alpine temporario para gerar o certificado TLS."
fi

titulo "Disco"
ALVO=/DATA/AppData
if [ -d "$ALVO" ]; then
  ok "$ALVO existe."
  LIVRE_KB=$(df -Pk "$ALVO" | awk 'NR==2 {print $4}')
  if [ "$LIVRE_KB" -lt 5242880 ]; then
    falha "Pouco espaco livre em $ALVO: $((LIVRE_KB / 1024)) MB (recomendado: pelo menos 5120 MB para construir as imagens)."
  else
    ok "Espaco livre em $ALVO: $((LIVRE_KB / 1024)) MB."
  fi
  if [ -d "$DIR" ]; then
    if [ -w "$DIR" ]; then ok "$DIR existe e e gravavel."; else falha "$DIR existe mas o usuario nao pode gravar nele."; fi
  elif [ -w "$ALVO" ]; then
    ok "$ALVO e gravavel (o install.sh cria $DIR)."
  else
    falha "$ALVO nao e gravavel por $(id -un): o install.sh nao conseguira criar $DIR."
  fi
else
  falha "$ALVO nao existe. Este nao parece ser um ZimaOS/CasaOS (ou o caminho mudou)."
fi
if command -v docker >/dev/null 2>&1 && [ "$DOCKER_OK" = 1 ]; then
  RAIZ_DOCKER=$(docker_ info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  if [ -n "$RAIZ_DOCKER" ] && [ -d "$RAIZ_DOCKER" ]; then
    DK_KB=$(df -Pk "$RAIZ_DOCKER" | awk 'NR==2 {print $4}')
    info "Espaco livre onde o Docker guarda imagens ($RAIZ_DOCKER): $((DK_KB / 1024)) MB."
  fi
fi

titulo "Portas 8080 e 8081"
# 8080 = 1F90, 8081 = 1F91; estado 0A = LISTEN em /proc/net/tcp e tcp6.
porta_ocupada() {
  _hex=$1
  cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | grep -qi ":$_hex [0-9A-F:]* 0A "
}
JA_INSTALADO=0
if [ "$DOCKER_OK" = 1 ] && [ "$(saude livestats-web)" != ausente ]; then JA_INSTALADO=1; fi
for par in "8080 1F90" "8081 1F91"; do
  # shellcheck disable=SC2086
  set -- $par
  if porta_ocupada "$2"; then
    if [ "$JA_INSTALADO" = 1 ]; then
      ok "Porta $1 em uso, mas pelo proprio LiveStats ja instalado (reinstalar e seguro)."
    else
      falha "Porta $1 ja esta em uso por outro programa. Descubra qual: 'docker ps' ou 'ss -ltnp'."
    fi
  else
    ok "Porta $1 livre."
  fi
done

titulo "Nginx Proxy Manager e cloudflared"
if [ "$DOCKER_OK" = 1 ]; then
  # shellcheck disable=SC2016  # {{...}} e sintaxe do Go template do docker, nao do shell
  descrever() {
    docker_ inspect -f '{{range $k,$v := .NetworkSettings.Networks}}  rede {{$k}} ip {{$v.IPAddress}}{{"\n"}}{{end}}' "$1"
  }
  NPM=$(achar_container 'nginx-proxy-manager|jc21')
  if [ -n "$NPM" ]; then
    ok "Container do NPM: $NPM (imagem $(docker_ inspect -f '{{.Config.Image}}' "$NPM"))"
    descrever "$NPM"
    # shellcheck disable=SC2016
    docker_ inspect -f '{{range $p,$b := .NetworkSettings.Ports}}{{if $b}}  porta {{$p}} -> {{range $b}}{{.HostIp}}:{{.HostPort}} {{end}}{{"\n"}}{{end}}{{end}}' "$NPM"
  else
    aviso "Nenhum container do Nginx Proxy Manager encontrado (nome/imagem com 'nginx-proxy-manager' ou 'jc21')."
  fi
  CF=$(achar_container 'cloudflared')
  if [ -n "$CF" ]; then
    ok "Container do cloudflared: $CF"
    descrever "$CF"
  else
    info "Nenhum container do cloudflared neste host (esperado: o tunel pode estar em outro lugar)."
  fi
  if docker_ network inspect "$REDE_EDGE" >/dev/null 2>&1; then
    info "Rede $REDE_EDGE ja existe (subrede $(docker_ network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$REDE_EDGE"))."
  else
    info "Rede $REDE_EDGE ainda nao existe (sera criada pelo compose)."
  fi
fi

titulo "Resultado"
if [ "$ERROS" -eq 0 ]; then
  ok "Nenhum bloqueio encontrado."
else
  erro "$ERROS bloqueio(s) encontrado(s). Resolva antes de instalar."
fi
exit "$ERROS"
