# shellcheck shell=sh
# Funcoes comuns dos scripts de deploy do LiveStats (POSIX sh). Carregado com `.` pelos outros scripts.
# Regra: nenhuma funcao daqui imprime valor de segredo.

DIR_PADRAO=/DATA/AppData/livestats
REDE_EDGE=livestats-edge
SERVICOS="livestats-api livestats-worker-lives livestats-worker-inscritos livestats-web"
export DIR_PADRAO REDE_EDGE SERVICOS

# ZimaOS: a home do usuario (/DATA) tem um .docker do root (710) que o CLI nao consegue ler; assim o CLI nao
# acha nem o plugin compose do sistema ("'compose' is not a docker command"). Nesse caso usa uma pasta de config
# propria e vazia (sem credenciais); os plugins do sistema (/usr/libexec/docker/cli-plugins etc.) continuam valendo.
if [ -z "${DOCKER_CONFIG:-}" ] && [ -e "${HOME:-/nao-existe}/.docker" ] \
   && { [ ! -r "$HOME/.docker" ] || [ ! -x "$HOME/.docker" ]; }; then
  DOCKER_CONFIG=$(mktemp -d "${TMPDIR:-/tmp}/livestats-docker.XXXXXX") || { printf '[ERRO]  mktemp falhou\n' >&2; exit 1; }
  export DOCKER_CONFIG
fi

msg()    { printf '%s\n' "$*"; }
ok()     { printf '[ok]    %s\n' "$*"; }
info()   { printf '[info]  %s\n' "$*"; }
aviso()  { printf '[aviso] %s\n' "$*" >&2; }
erro()   { printf '[ERRO]  %s\n' "$*" >&2; }
die()    { erro "$*"; exit 1; }
titulo() { printf '\n== %s ==\n' "$*"; }

# Escolhe como falar com o Docker: direto, ou via sudo sem senha. Define USAR_SUDO (0/1).
escolher_docker() {
  USAR_SUDO=0
  command -v docker >/dev/null 2>&1 || return 1
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    USAR_SUDO=1
    return 0
  fi
  return 1
}

docker_() {
  if [ "$USAR_SUDO" = 1 ]; then sudo -n docker "$@"; else docker "$@"; fi
}

# Le a chave $2 do arquivo .env $1, sem aspas. Vazio se nao existir.
env_get() {
  [ -f "$1" ] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n 1 | sed 's/^"\(.*\)"$/\1/'
}

# Grava/atualiza CHAVE="valor" no .env. So para valores NAO secretos.
env_set() {
  _arq=$1; _chave=$2; _valor=$3
  _tmp="$_arq.tmp.$$"
  if [ -f "$_arq" ]; then
    grep -v "^$_chave=" "$_arq" > "$_tmp" || true
  else
    : > "$_tmp"
  fi
  printf '%s="%s"\n' "$_chave" "$_valor" >> "$_tmp"
  chmod 600 "$_tmp"
  mv "$_tmp" "$_arq"
}

# D42: LIVE_INTERVAL do .env tem de estar entre 5 e 15 s (acima disso a live ficaria mais de 15 s sem atualizar e o
# worker-lives nem sobe). Ausente, nao numerico ou fora da faixa (por exemplo o 30 de um .env.example de versao anterior) vira 10.
ajustar_live_interval() {
  _li=$(env_get "$1" LIVE_INTERVAL)
  case $_li in
    ''|*[!0-9]*) _li_ok=0 ;;
    *) if [ "$_li" -ge 5 ] && [ "$_li" -le 15 ]; then _li_ok=1; else _li_ok=0; fi ;;
  esac
  if [ "$_li_ok" = 0 ]; then
    aviso "LIVE_INTERVAL='${_li}' fora da faixa 5-15 s (D42); gravando LIVE_INTERVAL=10 em $1."
    env_set "$1" LIVE_INTERVAL 10
  fi
}

# Estado de saude de um container: healthy, unhealthy, starting, sem-healthcheck ou ausente.
saude() {
  docker_ inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-healthcheck{{end}}' "$1" 2>/dev/null || printf 'ausente\n'
}

# Espera todos os servicos ficarem healthy (maximo $1 segundos). Retorna 0 se todos ok.
esperar_saudaveis() {
  _limite=$1; _t=0
  while [ "$_t" -lt "$_limite" ]; do
    _todos=1
    for _c in $SERVICOS; do
      [ "$(saude "$_c")" = healthy ] || _todos=0
    done
    if [ "$_todos" = 1 ]; then return 0; fi
    sleep 5
    _t=$((_t + 5))
  done
  return 1
}

# Filtro que remove o que parece segredo de qualquer texto (defesa extra para logs).
# shellcheck disable=SC2016  # \$ e literal de proposito: casa o prefixo dos hashes argon2
ocultar() {
  sed -e 's/AIza[0-9A-Za-z_-]\{20,\}/[chave-youtube-oculta]/g' \
      -e 's/\$argon2[^ "]*/[hash-oculto]/g' \
      -e 's/-----BEGIN[^-]*-----/[pem-oculto]/g'
}

# Nome do primeiro container cujo "nome imagem" casa com o padrao egrep $1 (vazio se nenhum).
achar_container() {
  docker_ ps --format '{{.Names}} {{.Image}}' | grep -Ei "$1" | head -n 1 | cut -d' ' -f1 || true
}

# ---- Registro no painel do ZimaOS (C17) -----------------------------------------------------------------
# O ZimaOS so lista na tela inicial os apps cujo compose fica em /var/lib/casaos/apps/<projeto>/docker-compose.yml
# (pasta do root). deploy/registrar_zimaos.sh grava la uma copia "resolvida" do compose. Quando esse registro existe,
# os containers PRECISAM ser (re)criados a partir dele, senao o cartao some. As funcoes abaixo servem a isso.
# Em producao os padroes sao /var/lib/casaos/apps e `sudo`; as variaveis LIVESTATS_ZIMAOS_APPS e LIVESTATS_SUDO
# existem so para os testes (LIVESTATS_SUDO vazio = sem sudo).
ZIMAOS_APPS=${LIVESTATS_ZIMAOS_APPS:-/var/lib/casaos/apps}
ZIMAOS_SUDO=${LIVESTATS_SUDO-sudo}
if [ "$(id -u)" = 0 ]; then ZIMAOS_SUDO=; fi
ZIMAOS_PASTA="$ZIMAOS_APPS/livestats"
ZIMAOS_ARQ="$ZIMAOS_PASTA/docker-compose.yml"
export ZIMAOS_APPS ZIMAOS_SUDO ZIMAOS_PASTA ZIMAOS_ARQ

# 0 se o LiveStats ja esta registrado no painel do ZimaOS (basta existir; ler o arquivo exige root).
zimaos_registrado() { [ -e "$ZIMAOS_ARQ" ]; }

# 0 se da para usar sudo agora: sem sudo configurado, ja root, ou com terminal para a senha (ssh -t).
zimaos_sudo_ok() { [ -z "$ZIMAOS_SUDO" ] || [ "$(id -u)" = 0 ] || [ -t 0 ]; }

# docker compose sobre o arquivo REGISTRADO (que e do root, modo 600, por isso o sudo).
compose_registrado() {
  # shellcheck disable=SC2086  # ZIMAOS_SUDO vazio ou "sudo": a expansao sem aspas e intencional
  $ZIMAOS_SUDO docker compose -f "$ZIMAOS_ARQ" "$@"
}

# Valor do label com.docker.compose.project.config_files do container $1 (vazio se nao existir).
config_do_container() {
  docker_ inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$1" 2>/dev/null || true
}
