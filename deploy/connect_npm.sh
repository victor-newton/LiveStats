#!/bin/sh
# LiveStats - opcao A: conecta o Nginx Proxy Manager (NPM) a rede livestats-edge com IP fixo e grava esse IP (/32)
# em TRUSTED_PROXY_CIDR, para o web aceitar o CF-Connecting-IP vindo dele na 8080 (D29/D33).
# Depois disso o proxy host live.exemplo.com pode apontar para livestats-web:8080.
#
# ATENCAO: o `docker network connect` feito a mao NAO sobrevive se o ZimaOS RECRIAR o container do NPM
# (atualizacao do app, reinstalacao). Nesse caso rode este script de novo. Veja o README.
#
# Uso: sh deploy/connect_npm.sh [--npm NOME_DO_CONTAINER] [--ip 172.29.80.11] [--dir DIR]
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
NPM=""
IP=""
while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --npm) NPM=$2; shift 2 ;;
    --ip) IP=$2; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1" ;;
  esac
done

escolher_docker || die "Sem acesso ao Docker."
# Registrado no painel do ZimaOS: recriar o web exige o arquivo registrado (root, sudo com senha). Sem terminal para a
# senha, aborta ANTES de mudar qualquer coisa (rede, .env), para nao deixar o projeto pela metade.
if zimaos_registrado && ! zimaos_sudo_ok; then
  die "LiveStats registrado no painel do ZimaOS: preciso de terminal para o sudo. Rode com ssh -t (enviar.sh conectar-npm). Nada foi alterado."
fi
cd "$DIR" || die "Pasta $DIR nao existe."
[ -f .env ] || die "$DIR/.env nao existe: instale primeiro (deploy/install.sh)."
docker_ network inspect "$REDE_EDGE" >/dev/null 2>&1 || die "Rede $REDE_EDGE nao existe: o LiveStats esta de pe? (docker compose up -d)"

if [ -z "$NPM" ]; then
  NPM=$(achar_container 'nginx-proxy-manager|jc21')
  [ -n "$NPM" ] || die "Nao achei o container do NPM. Informe com --npm NOME (veja 'docker ps')."
  info "Container do NPM detectado: $NPM"
fi
docker_ inspect "$NPM" >/dev/null 2>&1 || die "Container '$NPM' nao existe."

SUBREDE=$(docker_ network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$REDE_EDGE")
if [ -z "$IP" ]; then
  case $SUBREDE in
    *.0/24) IP="$(printf '%s' "$SUBREDE" | sed 's/\.0\/24$//').11" ;;
    *) die "Subrede $SUBREDE nao e um /24 terminado em .0: informe --ip." ;;
  esac
fi
printf '%s' "$IP" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || die "IP invalido: $IP"
GATEWAY=$(docker_ network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' "$REDE_EDGE")
[ "$IP" != "$GATEWAY" ] || die "O IP do NPM nao pode ser o do gateway da bridge ($GATEWAY): o web ignora esse IP de proposito (D29)."

IP_ATUAL=$(docker_ inspect -f '{{with index .NetworkSettings.Networks "livestats-edge"}}{{.IPAddress}}{{end}}' "$NPM")
if [ "$IP_ATUAL" = "$IP" ]; then
  ok "$NPM ja esta em $REDE_EDGE com o IP $IP."
else
  if [ -n "$IP_ATUAL" ]; then
    info "$NPM estava em $REDE_EDGE com $IP_ATUAL; reconectando com $IP."
    docker_ network disconnect "$REDE_EDGE" "$NPM"
  fi
  docker_ network connect --ip "$IP" "$REDE_EDGE" "$NPM"
  ok "$NPM conectado a $REDE_EDGE com o IP fixo $IP."
fi

env_set "$DIR/.env" TRUSTED_PROXY_CIDR "$IP/32"
ok "TRUSTED_PROXY_CIDR=$IP/32 gravado no .env."

if zimaos_registrado; then
  info "Registrado no painel do ZimaOS: regerando o arquivo registrado e recriando o web a partir dele ..."
  sh "$AQUI/registrar_zimaos.sh" --dir "$DIR" --recriar-web
else
  info "Recriando so o servico web para ler o novo valor ..."
  docker_ compose up -d web
fi
_t=0
while [ "$_t" -lt 90 ] && [ "$(saude livestats-web)" != healthy ]; do sleep 3; _t=$((_t + 3)); done
[ "$(saude livestats-web)" = healthy ] || die "livestats-web nao ficou healthy (veja: docker logs livestats-web)."
ok "livestats-web healthy."

if docker_ exec "$NPM" sh -c 'wget -q -T 5 -O /dev/null http://livestats-web:8080/health' >/dev/null 2>&1; then
  ok "De dentro do NPM, http://livestats-web:8080/health responde."
else
  aviso "Nao consegui confirmar de dentro do NPM (a imagem pode nao ter wget). Teste pelo proxy host."
fi

msg ""
msg "No painel do NPM, edite o proxy host live.exemplo.com:"
msg "  Forward Hostname/IP: livestats-web   Forward Port: 8080   (esquema http)"
msg "Lembrete: se o ZimaOS recriar o container do NPM, rode este script de novo."
