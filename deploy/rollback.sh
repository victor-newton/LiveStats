#!/bin/sh
# LiveStats - rollback: desliga o LiveStats SEM apagar volumes (dados e sessoes ficam), solta o NPM/cloudflared
# da rede livestats-edge (senao a rede nao pode ser removida) e explica como religar os apps antigos.
# Os apps antigos (Live Stats e YT Inscritos) continuam instalados no ZimaOS; esta rotina nao mexe neles.
#
# Uso: sh deploy/rollback.sh [--dir DIR] [--yes]
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
SIM=0
while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --yes) SIM=1; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1" ;;
  esac
done

escolher_docker || die "Sem acesso ao Docker."
# Registrado no painel do ZimaOS: o down usa o arquivo registrado (root, sudo com senha). Confere o terminal ANTES de
# mexer em qualquer coisa.
if zimaos_registrado && ! zimaos_sudo_ok; then
  die "LiveStats registrado no painel do ZimaOS: preciso de terminal para o sudo. Rode com ssh -t (enviar.sh rollback). Nada foi alterado."
fi
cd "$DIR" || die "Pasta $DIR nao existe."

if [ "$SIM" = 0 ]; then
  [ -t 0 ] || die "Sem terminal para confirmar. Use --yes."
  printf 'Desligar o LiveStats (volumes ficam)? Digite "sim": ' >&2
  read -r R
  [ "$R" = sim ] || die "Cancelado."
fi

titulo "Soltando containers externos da rede $REDE_EDGE"
if docker_ network inspect "$REDE_EDGE" >/dev/null 2>&1; then
  for c in $(docker_ network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$REDE_EDGE"); do
    case $c in
      livestats-*) ;;
      *) docker_ network disconnect "$REDE_EDGE" "$c" && ok "$c desconectado de $REDE_EDGE." ;;
    esac
  done
else
  info "Rede $REDE_EDGE nao existe (nada a soltar)."
fi

titulo "docker compose down (sem -v: volumes preservados)"
if zimaos_registrado; then
  compose_registrado down
  info "O registro do painel do ZimaOS continua ($ZIMAOS_ARQ): o cartao fica como app parado. Para tirar o cartao sem religar nada: sudo rm -rf $ZIMAOS_PASTA"
  info "Para religar depois: bash deploy/enviar.sh registrar-zimaos (recria pelo arquivo registrado)."
else
  docker_ compose down
fi
docker_ volume ls --format '{{.Name}}' | grep '^livestats-' | sed 's/^/[ok]    volume preservado: /' || true
env_set "$DIR/.env" TRUSTED_PROXY_CIDR ""
ok "TRUSTED_PROXY_CIDR zerado no .env."

titulo "Agora, por voce"
msg "1. No painel do Nginx Proxy Manager:"
msg "     - lives.vitex.xyz.br: volte o destino para http://192.168.15.62:8001"
msg "     - canaldobenja.vitex.xyz.br: reative o host (destino http://192.168.15.62:8000)"
msg "   (Faca ANTES de qualquer reinicio do NPM: um destino 'livestats-web' que nao resolve impede o NPM de recarregar.)"
msg "2. No painel do ZimaOS, ligue os apps antigos 'Live Stats' e 'YT Inscritos'."
msg "3. No vMix/OBS volte as URLs antigas (tabela em docs/DEPLOY.md)."
msg "Para voltar ao LiveStats depois: sh $DIR/deploy/install.sh --lan-ip <IP> --skip-build ... (dados e segredos continuam)."
msg "Para APAGAR os dados de vez (irreversivel, so se tiver certeza): docker volume rm livestats-data livestats-admin"
