#!/usr/bin/env bash
# LiveStats - envio e execucao do deploy a partir do SEU PC (Git Bash no Windows). Roda LOCALMENTE.
#
# QUANTAS VEZES A SENHA DO SSH E PEDIDA (o servidor usa senha, sem chave SSH):
#   instalar      1 vez  - um unico ssh recebe, pela entrada padrao, o codigo (git archive do commit atual)
#                          e o segredo (senha do admin), extrai, instala e sobe tudo.
#                          Preflight, build, geracao de chaves/certificado e checagens rodam dentro dessa sessao.
#   preflight     1 vez  - so leitura; opcional (o "instalar" ja roda o preflight antes de alterar qualquer coisa).
#   status        1 vez
#   conectar-npm  1 vez  - so se voce escolher a opcao A do README (ou use: instalar --conectar-npm, 0 extras).
#   rollback      1 vez
#   registrar-zimaos  1 senha do SSH + a senha do sudo (ssh -t): grava o compose em /var/lib/casaos/apps (pasta do
#                     root) para o LiveStats aparecer na tela inicial do ZimaOS. Roda o codigo que o "instalar" ja
#                     deixou no servidor. Com --remover desfaz. Depois do registro, conectar-npm e rollback tambem
#                     usam ssh -t (pedem a senha do sudo) e o "instalar" para antes de recriar os containers,
#                     mandando rodar o registrar-zimaos de novo. Nenhum segredo passa por este modo.
#   Caminho recomendado (opcao B, sem mexer no container do NPM): UMA senha no total ("instalar").
#   Cada comando e uma conexao ssh separada; nao ha como reaproveitar a senha (o ssh do Windows nao tem multiplexacao).
#
# SEGREDOS: a senha do admin e digitada AQUI (sem eco), nunca vai em argumento de linha de comando (nem
# local nem remoto) e nunca e gravada em disco no servidor: chega pela entrada padrao do instalador, que
# grava so o HASH da senha. Nao ha mais chave da YouTube nem ID de canal (D38/D39).
#
# Uso:
#   deploy/enviar.sh instalar --host IP-DO-SERVIDOR --user USUARIO-SSH
#        [--admin-user NOME] [--admin-allow "192.168.0.0/16 100.64.0.0/10"] [--public-base-url https://live.exemplo.com] [--extra-san IP-do-Tailscale]
#        [--sem-segredos] [--conectar-npm [NOME]] [--renew-tls] [--skip-build]
#        [--admin-externo [--admin-trusted-proxy IP-do-NPM]] [--admin-interno] [--admin-real-ip-header CF-Connecting-IP]
#        [--public-admin-url https://painel.exemplo.com] [--master-allow "CIDRS"]
#      (acesso externo ao painel, D49: o IP fixo do NPM e obrigatorio; veja o README, "Acessar o painel de fora da rede")
#   deploy/enviar.sh preflight|status|conectar-npm|rollback --host H --user U
#   deploy/enviar.sh registrar-zimaos [--remover] [--recriar-web] --host H --user U
#   (--host e --user tambem podem vir das variaveis LIVESTATS_HOST e LIVESTATS_USER)
set -euo pipefail

HOST=${LIVESTATS_HOST:-}
USUARIO=${LIVESTATS_USER:-}
DEST=/DATA/AppData/livestats
SSH=${ENVIAR_SSH:-ssh}   # sobrescrevivel para ensaios locais; em producao e o ssh de verdade

MODO=${1:-}
[ -n "$MODO" ] || { sed -n '2,32p' "$0"; exit 1; }
shift

ADMIN_ALLOW=""; ADMIN_USER=""; PUBLIC_URL=""; ADMIN_EXTERNO=""; ADMIN_PROXY=""; PUBLIC_ADMIN=""; MASTER_ALLOW=""; REAL_IP_HEADER=""; EXTRA=(); SEM_SEGREDOS=0; EXTRAS_INSTALL=(); EXTRAS_REGISTRO=()
while [ $# -gt 0 ]; do
  case $1 in
    --host) HOST=$2; shift 2 ;;
    --user) USUARIO=$2; shift 2 ;;
    --admin-user) ADMIN_USER=$2; shift 2 ;;
    --admin-allow) ADMIN_ALLOW=$2; shift 2 ;;
    --public-base-url) PUBLIC_URL=$2; shift 2 ;;
    --admin-externo) ADMIN_EXTERNO=1; shift ;;
    --admin-interno) ADMIN_EXTERNO=0; shift ;;
    --admin-trusted-proxy) ADMIN_PROXY=$2; shift 2 ;;
    --public-admin-url) PUBLIC_ADMIN=$2; shift 2 ;;
    --master-allow) MASTER_ALLOW=$2; shift 2 ;;
    --admin-real-ip-header) REAL_IP_HEADER=$2; shift 2 ;;
    --extra-san) EXTRA+=(--extra-san "$2"); shift 2 ;;
    --sem-segredos) SEM_SEGREDOS=1; shift ;;
    --renew-tls) EXTRAS_INSTALL+=(--renew-tls); shift ;;
    --skip-build) EXTRAS_INSTALL+=(--skip-build); shift ;;
    --remover) EXTRAS_REGISTRO+=(--remover); shift ;;
    --recriar-web) EXTRAS_REGISTRO+=(--recriar-web); shift ;;
    --conectar-npm)
      EXTRAS_INSTALL+=(--connect-npm); shift
      if [ $# -gt 0 ] && [[ $1 != --* ]]; then EXTRAS_INSTALL+=("$1"); shift; fi ;;
    *) echo "Argumento desconhecido: $1" >&2; exit 1 ;;
  esac
done

valida() { [[ $2 =~ $1 ]] || { echo "Valor invalido: $2" >&2; exit 1; }; }
[ -n "$HOST" ] && [ -n "$USUARIO" ] || { echo "Informe o servidor: --host IP e --user USUARIO (ou LIVESTATS_HOST e LIVESTATS_USER)." >&2; exit 1; }
valida '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' "$HOST"
valida '^[A-Za-z0-9._-]{1,32}$' "$USUARIO"

RAIZ=$(git rev-parse --show-toplevel)
cd "$RAIZ"
SSH_OPTS=(-T -o ServerAliveInterval=30 -o ServerAliveCountMax=40)
# Com terminal (ssh -t): para o que pode precisar da senha do sudo (registro no painel do ZimaOS). Nunca leva segredos.
SSH_OPTS_TTY=(-t -o ServerAliveInterval=30 -o ServerAliveCountMax=40)
ALVO="$USUARIO@$HOST"

lerpar() { # $1 prompt, $2 variavel de saida; sem eco quando ha terminal
  local _v
  printf '%s' "$1" >&2
  if [ -t 0 ]; then read -rs _v; printf '\n' >&2; else read -r _v; fi
  printf -v "$2" '%s' "$_v"
}

case $MODO in
  preflight)
    echo ">> Preflight em $ALVO (1 senha). Somente leitura."
    # shellcheck disable=SC2016  # o script remoto vai entre aspas simples de proposito
    git archive --format=tar HEAD deploy | "$SSH" "${SSH_OPTS[@]}" "$ALVO" \
      'set -eu; d=$(mktemp -d); tar -xm -C "$d"; r=0; sh "$d/deploy/preflight.sh" || r=$?; rm -rf "$d"; exit $r'
    ;;
  status)
    echo ">> Status em $ALVO (1 senha)."
    "$SSH" "${SSH_OPTS[@]}" "$ALVO" "sh $DEST/deploy/status.sh --dir $DEST"
    ;;
  conectar-npm)
    echo ">> Conectando o NPM em $ALVO (1 senha)."
    "$SSH" "${SSH_OPTS_TTY[@]}" "$ALVO" "sh $DEST/deploy/connect_npm.sh --dir $DEST"
    ;;
  rollback)
    printf 'Desligar o LiveStats em %s (volumes ficam)? Digite "sim": ' "$HOST" >&2
    read -r R
    [ "$R" = sim ] || { echo "Cancelado." >&2; exit 1; }
    echo ">> Rollback em $ALVO (1 senha)."
    "$SSH" "${SSH_OPTS_TTY[@]}" "$ALVO" "sh $DEST/deploy/rollback.sh --dir $DEST --yes"
    ;;
  registrar-zimaos)
    for a in ${EXTRAS_REGISTRO[@]+"${EXTRAS_REGISTRO[@]}"}; do valida '^--[a-z-]+$' "$a"; done
    echo ">> Registrando o LiveStats no painel do ZimaOS em $ALVO (senha do SSH + senha do sudo)."
    "$SSH" "${SSH_OPTS_TTY[@]}" "$ALVO" "sh $DEST/deploy/registrar_zimaos.sh --dir $DEST ${EXTRAS_REGISTRO[*]-}"
    ;;
  instalar)
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo "AVISO: ha alteracoes nao commitadas; o envio usa SOMENTE o commit atual ($(git rev-parse --short HEAD))." >&2
    fi
    ADMIN_PW=""
    if [ "$SEM_SEGREDOS" = 0 ]; then
      lerpar "Senha do admin do painel (minimo 8 caracteres): " ADMIN_PW
      lerpar "Repita a senha do admin: " REPETE
      [ "$ADMIN_PW" = "$REPETE" ] || { echo "As senhas nao conferem." >&2; exit 1; }
      [ "${#ADMIN_PW}" -ge 8 ] || { echo "A senha do admin precisa de pelo menos 8 caracteres." >&2; exit 1; }
    fi

    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    git archive --format=tar HEAD > "$TMP/codigo.tar"
    TAM=$(wc -c < "$TMP/codigo.tar" | tr -d ' ')

    ARGS="--dir \"\$d\" --secrets-stdin --lan-ip $HOST"
    if [ -n "$ADMIN_USER" ]; then valida '^[A-Za-z0-9._@-]{1,64}$' "$ADMIN_USER"; ARGS="$ARGS --admin-user $ADMIN_USER"; fi
    if [ -n "$ADMIN_ALLOW" ]; then valida '^[0-9./ ]+$' "$ADMIN_ALLOW"; ARGS="$ARGS --admin-allow \"$ADMIN_ALLOW\""; fi
    if [ -n "$PUBLIC_URL" ]; then valida '^https?://[A-Za-z0-9.:/_-]+$' "$PUBLIC_URL"; ARGS="$ARGS --public-base-url $PUBLIC_URL"; fi
    if [ "$ADMIN_EXTERNO" = 1 ]; then ARGS="$ARGS --admin-externo"; elif [ "$ADMIN_EXTERNO" = 0 ]; then ARGS="$ARGS --admin-interno"; fi
    if [ -n "$ADMIN_PROXY" ]; then valida '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' "$ADMIN_PROXY"; ARGS="$ARGS --admin-trusted-proxy $ADMIN_PROXY"; fi
    if [ -n "$PUBLIC_ADMIN" ]; then valida '^https?://[A-Za-z0-9.:/_-]+$' "$PUBLIC_ADMIN"; ARGS="$ARGS --public-admin-url $PUBLIC_ADMIN"; fi
    if [ -n "$REAL_IP_HEADER" ]; then valida '^(X-Forwarded-For|CF-Connecting-IP|X-Real-IP)$' "$REAL_IP_HEADER"; ARGS="$ARGS --admin-real-ip-header $REAL_IP_HEADER"; fi
    if [ -n "$MASTER_ALLOW" ]; then valida '^[0-9./ ]+$' "$MASTER_ALLOW"; ARGS="$ARGS --master-allow \"$MASTER_ALLOW\""; fi
    if [ "${#EXTRA[@]}" -gt 0 ]; then
      for ((i = 1; i < ${#EXTRA[@]}; i += 2)); do
        valida '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' "${EXTRA[$i]}"; ARGS="$ARGS --extra-san ${EXTRA[$i]}"
      done
    fi
    if [ "${#EXTRAS_INSTALL[@]}" -gt 0 ]; then
      for a in "${EXTRAS_INSTALL[@]}"; do valida '^[A-Za-z0-9._-]+$' "$a"; ARGS="$ARGS $a"; done
    fi
    REMOTO="sh -c 'set -eu; umask 022; d=$DEST; read -r n; mkdir -p \"\$d\"; head -c \"\$n\" | tar -xm -C \"\$d\"; exec sh \"\$d/deploy/install.sh\" $ARGS'"

    echo ">> Instalando em $ALVO (commit $(git rev-parse --short HEAD), pacote de $TAM bytes). A senha do SSH sera pedida 1 vez."
    {
      printf '%s\n' "$TAM"
      cat "$TMP/codigo.tar"
      printf '%s\n' "$ADMIN_PW"
    } | "$SSH" "${SSH_OPTS[@]}" "$ALVO" "$REMOTO"
    ;;
  *)
    sed -n '2,32p' "$0"; exit 1 ;;
esac
