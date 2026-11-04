#!/bin/sh
# LiveStats - registra o app no painel (tela inicial) do ZimaOS. Roda NO servidor; use deploy/enviar.sh registrar-zimaos.
#
# POR QUE: o ZimaOS so lista os apps cujo compose fica em /var/lib/casaos/apps/<projeto>/docker-compose.yml. O
# LiveStats e instalado em /DATA/AppData/livestats e funciona, mas nao aparece. Este script gera uma copia
# "resolvida" do compose (docker compose config: caminhos absolutos, .env interpolado, x-casaos mantido), grava em
# /var/lib/casaos/apps/livestats/docker-compose.yml (dono root, modo 600) e recria os containers a partir dela.
#
# SEGURANCA: antes de instalar, confere que o conteudo de nenhum arquivo de secrets/ aparece no YAML gerado (o .env
# nao tem segredos por desenho; isto e a rede de seguranca) e aborta se aparecer. Volumes nunca sao removidos.
# O arquivo e 600 do root porque carrega valores de configuracao do .env.
#
# SUDO: a pasta e do root. O script usa `sudo` INTERATIVO (a senha e pedida no terminal): rode com `ssh -t`
# (enviar.sh registrar-zimaos ja faz isso). Para testes, LIVESTATS_ZIMAOS_APPS troca a pasta de destino e
# LIVESTATS_SUDO= (vazio) tira o sudo; em producao os padroes sao /var/lib/casaos/apps e sudo.
#
# COMO OS OUTROS SCRIPTS RESPEITAM O REGISTRO (decisao de design): nenhum recria container "por fora" do arquivo
# registrado, senao o label config_files voltaria para /DATA/AppData/livestats e o cartao sumiria.
#   - install.sh (sem terminal para o sudo) faz tudo ate o build/segredos e PARA antes de subir (saida 2), mandando
#     rodar `enviar.sh registrar-zimaos` (que regera o arquivo com o codigo/.env novos e recria so o que mudou).
#   - connect_npm.sh (com terminal) chama este script; sem terminal para o sudo, aborta ANTES de mudar qualquer coisa.
#   - rollback.sh usa `docker compose -f <registrado> down` (e exige terminal para o sudo antes de tocar em algo).
#   - status.sh so le (docker ps/inspect), nao precisa do arquivo.
#
# Uso: sh deploy/registrar_zimaos.sh [--dir DIR] [--recriar-web] [--remover]
#   --recriar-web   forca a recriacao do web (certificado TLS novo, por exemplo)
#   --remover       apaga /var/lib/casaos/apps/livestats e recria pelo compose de DIR (volta ao estado anterior)
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
REMOVER=0
RECRIAR_WEB=0
while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --recriar-web) RECRIAR_WEB=1; shift ;;
    --remover) REMOVER=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1 (use --help)." ;;
  esac
done

escolher_docker || die "Sem acesso ao Docker."
[ -d "$DIR" ] || die "Pasta $DIR nao existe."
DIR=$(cd "$DIR" && pwd)
[ -f "$DIR/docker-compose.yml" ] || die "$DIR/docker-compose.yml nao existe."
[ -f "$DIR/.env" ] || die "$DIR/.env nao existe: instale primeiro (deploy/install.sh)."
zimaos_sudo_ok || die "Preciso de sudo e nao ha terminal para a senha. Rode com ssh -t (enviar.sh registrar-zimaos)."

# Espera os 4 servicos ficarem healthy ou aborta mostrando o estado.
aguardar() {
  info "Esperando os 4 servicos ficarem 'healthy' (ate 6 minutos) ..."
  if ! esperar_saudaveis 360; then
    erro "Nem todos os servicos ficaram healthy:"
    for c in $SERVICOS; do printf '  %s: %s\n' "$c" "$(saude "$c")"; done
    exit 1
  fi
  for c in $SERVICOS; do ok "$c: healthy"; done
}

# ---- --remover ---------------------------------------------------------------------------------------
if [ "$REMOVER" = 1 ]; then
  titulo "Removendo o registro do painel do ZimaOS"
  case $ZIMAOS_PASTA in
    ""|/|/var|/var/lib|/var/lib/casaos|/var/lib/casaos/apps) die "Pasta de registro suspeita: $ZIMAOS_PASTA" ;;
  esac
  if [ -e "$ZIMAOS_ARQ" ] || [ -d "$ZIMAOS_PASTA" ]; then
    $ZIMAOS_SUDO rm -rf "$ZIMAOS_PASTA"
    ok "$ZIMAOS_PASTA apagado."
  else
    info "Nao estava registrado."
  fi
  ATUAL="$DIR/docker-compose.yml"
  if [ "$(config_do_container livestats-web)" != "$ATUAL" ]; then
    docker_ compose --project-directory "$DIR" -f "$ATUAL" --env-file "$DIR/.env" up -d --force-recreate
  else
    docker_ compose --project-directory "$DIR" -f "$ATUAL" --env-file "$DIR/.env" up -d
  fi
  aguardar
  [ "$(config_do_container livestats-web)" = "$ATUAL" ] || die "O label config_files de livestats-web nao voltou para $ATUAL."
  ok "De volta ao compose de $DIR (volumes preservados). O cartao some do painel do ZimaOS."
  exit 0
fi

# ---- 1. gera a versao resolvida ----------------------------------------------------------------------
titulo "Gerando o compose resolvido"
TMP=$(umask 077; mktemp "${TMPDIR:-/tmp}/livestats-zimaos.XXXXXX") || die "mktemp falhou."
FILTRADO="$TMP.pat"
trap 'rm -f "$TMP" "$FILTRADO"' EXIT INT TERM
docker_ compose --project-directory "$DIR" -f "$DIR/docker-compose.yml" --env-file "$DIR/.env" config > "$TMP" \
  || die "docker compose config falhou (veja o erro acima)."
grep -q '^name: livestats$' "$TMP" || die "O compose gerado nao tem 'name: livestats'. Nada foi instalado."
grep -q '^x-casaos:' "$TMP" || die "O compose gerado nao tem o bloco x-casaos (o ZimaOS nao mostraria o app). Nada foi instalado."
ok "Compose resolvido gerado (name: livestats, x-casaos presente)."

# ---- 2. nenhum valor de segredo no arquivo -----------------------------------------------------------
titulo "Conferindo que nenhum segredo foi parar no arquivo"
for f in cookie_private_key.txt cookie_public_key.txt admin_password_hash.txt admin_tls_cert.pem admin_tls_key.pem; do
  [ -s "$DIR/secrets/$f" ] || die "Falta $DIR/secrets/$f (instale primeiro)."
done
for seg in "$DIR"/secrets/*; do
  [ -f "$seg" ] || continue
  # padroes = linhas nao vazias do arquivo; comparacao literal (-F), sem imprimir nada do conteudo
  grep -v '^[[:space:]]*$' "$seg" > "$FILTRADO" || true
  [ -s "$FILTRADO" ] || continue
  if grep -Fq -f "$FILTRADO" "$TMP"; then
    die "O conteudo de secrets/$(basename "$seg") aparece no compose gerado. ABORTADO, nada foi instalado."
  fi
done
rm -f "$FILTRADO"
ok "Nenhum conteudo de secrets/ aparece no arquivo gerado."

# ---- 3. instala (root, 600), so se mudou -------------------------------------------------------------
titulo "Registrando em $ZIMAOS_ARQ"
if [ -e "$ZIMAOS_ARQ" ] && $ZIMAOS_SUDO cmp -s "$TMP" "$ZIMAOS_ARQ"; then
  ok "O arquivo registrado ja esta igual (nao reescrevo)."
else
  $ZIMAOS_SUDO install -d -m 755 "$ZIMAOS_PASTA"
  $ZIMAOS_SUDO install -m 600 -o root -g root "$TMP" "$ZIMAOS_ARQ.novo"
  $ZIMAOS_SUDO mv -f "$ZIMAOS_ARQ.novo" "$ZIMAOS_ARQ"
  ok "Arquivo registrado (dono root, modo 600)."
fi

# ---- 4. recria a partir do arquivo registrado --------------------------------------------------------
titulo "Subindo a partir do arquivo registrado"
# Se o compose "so" mudou de lugar, o hash de configuracao dos servicos nao muda e o `up -d` nao recriaria nada:
# o label config_files ficaria apontando para a pasta antiga. Nesse caso forca a recriacao (volumes ficam).
if [ "$(config_do_container livestats-web)" != "$ZIMAOS_ARQ" ]; then
  info "Os containers ainda apontam para outro compose; recriando (alguns segundos fora do ar)."
  compose_registrado up -d --force-recreate
else
  compose_registrado up -d
  if [ "$RECRIAR_WEB" = 1 ]; then compose_registrado up -d --force-recreate web; fi
fi
aguardar
[ "$(config_do_container livestats-web)" = "$ZIMAOS_ARQ" ] \
  || die "O label config_files de livestats-web nao aponta para $ZIMAOS_ARQ."
ok "livestats-web agora vem de $ZIMAOS_ARQ."
msg "Pronto: o LiveStats deve aparecer na tela inicial do ZimaOS (recarregue a pagina)."
msg "Mudancas de configuracao: faca pelos scripts (enviar.sh instalar / registrar-zimaos). Editar o app pela tela do ZimaOS pode reescrever o YAML e tirar protecoes."
