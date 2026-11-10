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

# Acesso externo ao painel (D49) e limites do mestre (D48). Valida e grava no .env, PRESERVANDO o que ja existe
# quando o argumento vem vazio. Uso: aplicar_acesso_externo ARQ_ENV EXTERNO PROXY_IP PUBLIC_ADMIN_URL MASTER_ALLOW
#   EXTERNO: vazio = manter o do .env (padrao 0); 1 = ligar; 0 = desligar.
# Com acesso externo ligado, ADMIN_TRUSTED_PROXY_IP (argumento ou .env) e obrigatorio: sem o IP fixo do NPM o IP real
# do cliente nao existe e o limite de tentativas valeria para o proxy inteiro. Nada disso e segredo.
aplicar_acesso_externo() {
  _arq=$1; _ext=$2; _prx=$3; _pub=$4; _mst=$5; _hdr=${6:-}
  [ -n "$_hdr" ] || _hdr=$(env_get "$_arq" ADMIN_REAL_IP_HEADER)
  [ -n "$_hdr" ] || _hdr=X-Forwarded-For
  case $_hdr in X-Forwarded-For|CF-Connecting-IP|X-Real-IP) ;; *) die "--admin-real-ip-header deve ser X-Forwarded-For, CF-Connecting-IP ou X-Real-IP." ;; esac
  [ -n "$_ext" ] || _ext=$(env_get "$_arq" ADMIN_EXTERNAL_ACCESS)
  [ -n "$_ext" ] || _ext=0
  case $_ext in 0|1) ;; *) die "ADMIN_EXTERNAL_ACCESS deve ser 0 ou 1." ;; esac
  [ -n "$_prx" ] || _prx=$(env_get "$_arq" ADMIN_TRUSTED_PROXY_IP)
  [ -n "$_pub" ] || _pub=$(env_get "$_arq" PUBLIC_ADMIN_URL)
  [ -n "$_mst" ] || _mst=$(env_get "$_arq" MASTER_ALLOW_CIDRS)
  if [ -n "$_prx" ]; then
    printf '%s' "$_prx" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || die "--admin-trusted-proxy invalido: $_prx (use o IP fixo do NPM, ex.: 172.29.80.11)."
    case $_prx in *.0|*.1|*.255|0.*|127.*) die "ADMIN_TRUSTED_PROXY_IP=$_prx nao pode ser rede, gateway, broadcast nem loopback: use o IP fixo do NPM (ex.: 172.29.80.11)." ;; esac
  fi
  _sub=$(env_get "$_arq" EDGE_SUBNET); _web=$(env_get "$_arq" WEB_EDGE_IP)
  if [ -n "$_sub" ] && [ "$_sub" != "172.29.80.0/24" ] && [ -z "$_web" ]; then
    die "EDGE_SUBNET foi alterado: defina tambem WEB_EDGE_IP (um IP livre da nova sub-rede, fora de .0/.1/.255; o padrao 172.29.80.2 so vale na sub-rede padrao)."
  fi
  if [ "$_ext" = 1 ] && [ -z "$_prx" ]; then
    die "--admin-externo exige o IP fixo do Nginx Proxy Manager (ADMIN_TRUSTED_PROXY_IP): informe --admin-trusted-proxy IP (ex.: 172.29.80.11) ou rode antes deploy/connect_npm.sh. Sem proxy confiavel o painel nao pode ser aberto de fora. Nada foi alterado."
  fi
  if [ -n "$_pub" ]; then
    printf '%s' "$_pub" | grep -Eq '^https?://[A-Za-z0-9.:/_-]+$' || die "--public-admin-url invalida: $_pub"
  fi
  if [ -n "$_mst" ]; then
    printf '%s' "$_mst" | grep -Eq '^[0-9./ ]+$' || die "--master-allow so aceita IPv4/CIDR separados por espaco."
  fi
  env_set "$_arq" ADMIN_EXTERNAL_ACCESS "$_ext"
  env_set "$_arq" ADMIN_TRUSTED_PROXY_IP "$_prx"
  env_set "$_arq" ADMIN_REAL_IP_HEADER "$_hdr"
  env_set "$_arq" PUBLIC_ADMIN_URL "$_pub"
  env_set "$_arq" MASTER_ALLOW_CIDRS "$_mst"
}

# Imprime os passos do Proxy Host do NPM para o painel (D49) com os valores reais.
# Uso: passos_npm_painel DOMINIO IP_DO_NPM EXTERNO(0|1) IP_CLOUDFLARED(ou vazio) URL_PUBLICA_DO_SITE
passos_npm_painel() {
  _dom=$1; _npm=$2; _ext=$3; _cf=$4; _site=$5; _hdr=${6:-}
  [ -n "$_dom" ] || _dom=painel.exemplo.com
  msg ""
  msg "== Painel de fora da rede: Proxy Host no NPM (D49) =="
  if [ "$_ext" != 1 ]; then
    aviso "O acesso externo esta DESLIGADO (ADMIN_EXTERNAL_ACCESS=0): a 8081 ainda aplica a lista ADMIN_ALLOW_CIDRS."
    msg "  Para ligar:  bash deploy/enviar.sh instalar --host IP --user USUARIO --admin-externo --admin-trusted-proxy $_npm --public-admin-url https://$_dom --skip-build"
  fi
  msg "No painel do NPM: Hosts > Proxy Hosts > Add Proxy Host."
  msg "  1. Aba Details"
  msg "     Domain Names ........... $_dom"
  msg "     Scheme ................. https        (a 8081 fala TLS; HTTP simples recebe 400)"
  msg "     Forward Hostname / IP .. ${WEB_EDGE_IP:-172.29.80.2}   (IP fixo do web; o nome livestats-web NAO resolve no nginx do NPM)"
  msg "     Forward Port ........... 8081"
  msg "     Block Common Exploits .. ligado     Websockets Support: NAO e necessario (deixe desligado)"
  msg "     O certificado interno da 8081 e autoassinado: o NPM nao valida o certificado do destino, nada a configurar."
  msg "  2. Aba SSL: escolha o certificado do dominio $_dom (ou emita um Let's Encrypt, desafio DNS no Cloudflare);"
  msg "     ligue Force SSL e HTTP/2. O NPM so alcanca o web (172.29.80.2) se estiver na rede $REDE_EDGE (IP fixo $_npm, ja feito acima)."
  msg "  3. Cabecalhos: o NPM ja envia X-Forwarded-For com o IP de quem o chamou; o Nginx do LiveStats so confia nele quando"
  msg "     a conexao vem de $_npm (ADMIN_TRUSTED_PROXY_IP) e usa o ULTIMO salto. Nao desligue o envio de X-Forwarded-For."
  msg "     Com o tunel do Cloudflare, recomendado: ADMIN_REAL_IP_HEADER=CF-Connecting-IP (o NPM repassa o cabecalho; o ultimo salto de X-Forwarded-For seria so o do tunel):"
  msg "       enviar.sh instalar ... --admin-real-ip-header CF-Connecting-IP --sem-segredos --skip-build   (valor atual: ${_hdr:-X-Forwarded-For})"
  msg "     Nao use a aba Access List para restringir o painel: quem protege e a senha (e MASTER_ALLOW_CIDRS para o mestre)."
  msg "Tunel do Cloudflare (se o painel for aberto pela internet): em Zero Trust > Networks > Tunnels > o tunel do site > Public Hostname,"
  msg "  adicione Hostname $_dom com o MESMO servico do hostname do site${_site:+ ($_site)} (o NPM; ex.: http://$_npm:80, ou https://$_npm:443 com No TLS Verify)."
  msg "  Para o NPM enxergar o IP real do visitante (e o limite de 5 logins/min valer por pessoa), no Proxy Host, aba Advanced, cole:"
  msg "     real_ip_header CF-Connecting-IP;"
  msg "     set_real_ip_from ${_cf:-IP-do-cloudflared-visto-pelo-NPM};"
  msg "  Sem isso todos os visitantes do tunel contam como um IP so no limite de tentativas."
  msg "Cuidados: senha forte e unica (12+ caracteres); so o mestre cria usuarios; use MASTER_ALLOW_CIDRS para o login do mestre so da sua rede."
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
