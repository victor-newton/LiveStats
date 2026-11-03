# shellcheck shell=sh
# Funcoes comuns dos scripts de deploy do LiveStats (POSIX sh). Carregado com `.` pelos outros scripts.
# Regra: nenhuma funcao daqui imprime valor de segredo.

DIR_PADRAO=/DATA/AppData/livestats
REDE_EDGE=livestats-edge
SERVICOS="livestats-api livestats-worker-lives livestats-worker-inscritos livestats-web"
export DIR_PADRAO REDE_EDGE SERVICOS

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
