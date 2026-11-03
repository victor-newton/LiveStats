#!/bin/sh
# LiveStats - instalacao no servidor (ZimaOS). Idempotente: pode rodar de novo sem perder dados nem segredos.
# NUNCA imprime valores secretos e nunca recebe segredos como argumento (ficariam visiveis no `ps`).
#
# Uso (normalmente chamado por deploy/enviar.sh, que manda o codigo e os segredos por um unico ssh):
#   sh deploy/install.sh --lan-ip 192.168.15.62 --channel-id UCxxxxxxxxxxxxxxxxxxxxxx [opcoes]
#
# Segredos (so se ainda nao existirem em secrets/):
#   --secrets-stdin           le da entrada padrao: linha 1 = chave da YouTube API, linha 2 = senha do admin
#   --youtube-key-file ARQ    le a chave da YouTube de um arquivo (apague o arquivo depois)
#   (sem nada disso)          pergunta no terminal, sem eco
#
# Opcoes:
#   --dir DIR                 pasta de instalacao (padrao /DATA/AppData/livestats)
#   --lan-ip IP               IP da LAN do servidor, vai no SAN do certificado (obrigatorio)
#   --extra-san IP            outro IP no SAN (ex.: o do Tailscale); pode repetir
#   --admin-user NOME         usuario do admin (padrao: o do .env, senao Vitex)
#   --channel-id UC...        ID do canal do YouTube (obrigatorio na primeira vez)
#   --public-base-url URL     URL publica exibida no painel (padrao https://lives.vitex.xyz.br)
#   --admin-bind IP           onde a 8081 escuta no host (padrao 0.0.0.0; a lista de IPs filtra)
#   --admin-allow "CIDRS"     lista da 8081 (padrao "192.168.15.0/24 100.64.0.0/10")
#   --renew-tls               gera um certificado TLS novo
#   --reset-admin-password    troca o hash da senha do admin
#   --skip-build              nao reconstroi as imagens
#   --connect-npm [NOME]      ao final, roda connect_npm.sh (opcao A do docs/DEPLOY.md)
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
ORIGEM=$(dirname "$AQUI")
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
LAN_IP=""
EXTRA_SAN=""
ADMIN_USER=""
CHANNEL_ID=""
PUBLIC_URL=""
ADMIN_BIND=0.0.0.0
ADMIN_ALLOW="192.168.15.0/24 100.64.0.0/10"
SECRETS_STDIN=0
YT_ARQUIVO=""
RENOVAR_TLS=0
TROCAR_SENHA=0
PULAR_BUILD=0
CONECTAR_NPM=0
NPM_NOME=""

while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --lan-ip) LAN_IP=$2; shift 2 ;;
    --extra-san) EXTRA_SAN="$EXTRA_SAN $2"; shift 2 ;;
    --admin-user) ADMIN_USER=$2; shift 2 ;;
    --channel-id) CHANNEL_ID=$2; shift 2 ;;
    --public-base-url) PUBLIC_URL=$2; shift 2 ;;
    --admin-bind) ADMIN_BIND=$2; shift 2 ;;
    --admin-allow) ADMIN_ALLOW=$2; shift 2 ;;
    --secrets-stdin) SECRETS_STDIN=1; shift ;;
    --youtube-key-file) YT_ARQUIVO=$2; shift 2 ;;
    --renew-tls) RENOVAR_TLS=1; shift ;;
    --reset-admin-password) TROCAR_SENHA=1; shift ;;
    --skip-build) PULAR_BUILD=1; shift ;;
    --connect-npm)
      CONECTAR_NPM=1; shift
      if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then NPM_NOME=$1; shift; fi ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1 (use --help)." ;;
  esac
done

ipv4() { printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; }
[ -n "$LAN_IP" ] || die "Informe --lan-ip (IP da LAN do servidor)."
ipv4 "$LAN_IP" || die "--lan-ip invalido: $LAN_IP"
for ip in $EXTRA_SAN; do ipv4 "$ip" || die "--extra-san invalido: $ip"; done

# ---- 1. segredos que chegam por stdin (lidos primeiro; depois a entrada padrao fecha) ----------------
YT_KEY=""
ADMIN_PW=""
if [ "$SECRETS_STDIN" = 1 ]; then
  IFS= read -r YT_KEY || true
  IFS= read -r ADMIN_PW || true
  exec </dev/null
fi

# ---- 2. verificacoes previas -------------------------------------------------------------------------
titulo "Verificacoes previas"
if ! sh "$AQUI/preflight.sh" --dir "$DIR"; then
  die "O preflight encontrou bloqueios (acima). Nada foi alterado."
fi
escolher_docker || die "Sem acesso ao Docker."

# ---- 3. codigo na pasta de instalacao ----------------------------------------------------------------
titulo "Pasta de instalacao"
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
if [ "$ORIGEM" != "$DIR" ]; then
  info "Copiando o codigo de $ORIGEM para $DIR ..."
  (cd "$ORIGEM" && tar -cf - --exclude=./secrets --exclude=./.env .) | tar -xf - -C "$DIR"
fi
cd "$DIR"
[ -f docker-compose.yml ] || die "docker-compose.yml nao esta em $DIR (codigo nao foi enviado?)."
ok "Codigo em $DIR"

# ---- 4. .env de producao (sem segredos) --------------------------------------------------------------
titulo ".env"
ENV_ARQ="$DIR/.env"
[ -n "$ADMIN_USER" ] || ADMIN_USER=$(env_get "$ENV_ARQ" ADMIN_USERNAME)
[ -n "$ADMIN_USER" ] || ADMIN_USER=Vitex
[ -n "$CHANNEL_ID" ] || CHANNEL_ID=$(env_get "$ENV_ARQ" YOUTUBE_CHANNEL_ID)
[ -n "$CHANNEL_ID" ] || die "Informe --channel-id (formato UC + 22 caracteres)."
printf '%s' "$CHANNEL_ID" | grep -Eq '^UC[0-9A-Za-z_-]{22}$' || die "--channel-id fora do formato UC + 22 caracteres."
printf '%s' "$ADMIN_USER" | grep -Eq '^[A-Za-z0-9._@-]{1,64}$' || die "--admin-user invalido."
[ -n "$PUBLIC_URL" ] || PUBLIC_URL=$(env_get "$ENV_ARQ" PUBLIC_BASE_URL)
[ -n "$PUBLIC_URL" ] || PUBLIC_URL=https://lives.vitex.xyz.br
printf '%s' "$ADMIN_ALLOW" | grep -Eq '^[0-9./ ]+$' || die "--admin-allow so aceita IPv4/CIDR separados por espaco."
case $ADMIN_ALLOW in *0.0.0.0/0*) die "--admin-allow nao pode conter 0.0.0.0/0." ;; esac
ipv4 "$ADMIN_BIND" || die "--admin-bind invalido."

( umask 077
  env_set "$ENV_ARQ" ADMIN_USERNAME "$ADMIN_USER"
  env_set "$ENV_ARQ" YOUTUBE_CHANNEL_ID "$CHANNEL_ID"
  env_set "$ENV_ARQ" PUBLIC_BASE_URL "$PUBLIC_URL"
  env_set "$ENV_ARQ" ADMIN_BIND "$ADMIN_BIND"
  env_set "$ENV_ARQ" ADMIN_ALLOW_CIDRS "$ADMIN_ALLOW"
  # Preserva o que ja existir (por exemplo o TRUSTED_PROXY_CIDR gravado por connect_npm.sh).
  env_set "$ENV_ARQ" TRUSTED_PROXY_CIDR "$(env_get "$ENV_ARQ" TRUSTED_PROXY_CIDR)"
)
chmod 600 "$ENV_ARQ"
ok ".env gravado (usuario $ADMIN_USER, admin em $ADMIN_BIND:8081, lista: $ADMIN_ALLOW, TRUSTED_PROXY_CIDR='$(env_get "$ENV_ARQ" TRUSTED_PROXY_CIDR)')."

# ---- 5. imagens --------------------------------------------------------------------------------------
if [ "$PULAR_BUILD" = 1 ]; then
  info "Build pulado (--skip-build)."
else
  titulo "Construindo as imagens (a primeira vez demora varios minutos)"
  docker_ compose build
fi
docker_ image inspect livestats/api:local >/dev/null 2>&1 || die "Imagem livestats/api:local nao existe; rode sem --skip-build."

# ---- 6. segredos -------------------------------------------------------------------------------------
titulo "Segredos"
SEG="$DIR/secrets"
( umask 077; mkdir -p "$SEG" )
chmod 700 "$SEG"

ler_oculto() { # $1 = texto do prompt; resultado em SEGREDO_LIDO
  [ -t 0 ] || die "Sem terminal para perguntar '$1'. Use --secrets-stdin ou --youtube-key-file."
  printf '%s' "$1" >&2
  stty -echo
  IFS= read -r SEGREDO_LIDO || true
  stty echo
  printf '\n' >&2
}

# 6a. chave da YouTube
if [ -s "$SEG/youtube_api_key.txt" ]; then
  ok "youtube_api_key.txt ja existe (mantido)."
else
  if [ -z "$YT_KEY" ] && [ -n "$YT_ARQUIVO" ]; then
    [ -r "$YT_ARQUIVO" ] || die "Nao consigo ler $YT_ARQUIVO."
    YT_KEY=$(head -n 1 "$YT_ARQUIVO")
  fi
  if [ -z "$YT_KEY" ]; then ler_oculto "Chave da YouTube Data API: "; YT_KEY=$SEGREDO_LIDO; fi
  YT_KEY=$(printf '%s' "$YT_KEY" | tr -d '\r\n ')
  [ "${#YT_KEY}" -ge 20 ] || die "A chave da YouTube parece vazia ou curta demais."
  ( umask 077; printf '%s\n' "$YT_KEY" > "$SEG/youtube_api_key.txt" )
  chmod 644 "$SEG/youtube_api_key.txt"
  ok "youtube_api_key.txt gravado."
fi
YT_KEY=""

# 6b. par de chaves dos cookies (gerado na imagem da api; a saida fica so em variaveis do shell)
if [ -s "$SEG/cookie_private_key.txt" ] && [ -s "$SEG/cookie_public_key.txt" ]; then
  ok "Par de chaves dos cookies ja existe (mantido, para nao invalidar cookies ja salvos)."
elif [ -e "$SEG/cookie_private_key.txt" ] || [ -e "$SEG/cookie_public_key.txt" ]; then
  die "So um dos arquivos do par de chaves existe em $SEG. Corrija a mao (apague os dois para gerar de novo)."
else
  PAR=$(docker_ run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
        --user 10001:10001 livestats/api:local python -m tools.gen_cookie_keys)
  PRIV=$(printf '%s\n' "$PAR" | sed -n 's/^cookie_private_key=//p')
  PUB=$(printf '%s\n' "$PAR" | sed -n 's/^cookie_public_key=//p')
  PAR=""
  if [ -z "$PRIV" ] || [ -z "$PUB" ]; then die "A ferramenta gen_cookie_keys nao devolveu o par de chaves."; fi
  # 644: o worker-inscritos roda como uid 10001 e precisa LER a chave privada (achado V2). A pasta e 700.
  ( umask 077; printf '%s\n' "$PRIV" > "$SEG/cookie_private_key.txt"; printf '%s\n' "$PUB" > "$SEG/cookie_public_key.txt" )
  chmod 644 "$SEG/cookie_private_key.txt" "$SEG/cookie_public_key.txt"
  PRIV=""; PUB=""
  ok "Par de chaves dos cookies gerado."
fi

# 6c. hash da senha do admin
if [ -s "$SEG/admin_password_hash.txt" ] && [ "$TROCAR_SENHA" = 0 ]; then
  ok "admin_password_hash.txt ja existe (mantido)."
else
  if [ -z "$ADMIN_PW" ]; then
    ler_oculto "Senha do admin (minimo 12 caracteres): "; ADMIN_PW=$SEGREDO_LIDO
    ler_oculto "Repita a senha: "
    [ "$SEGREDO_LIDO" = "$ADMIN_PW" ] || die "As senhas nao conferem."
  fi
  HASH=$(printf '%s\n' "$ADMIN_PW" | docker_ run --rm -i --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --user 10001:10001 livestats/api:local python -m tools.hash_password) \
    || die "Falha ao gerar o hash (a senha precisa ter pelo menos 12 caracteres)."
  ADMIN_PW=""
  # shellcheck disable=SC2016  # $ literal: prefixo do hash PHC
  case $HASH in '$argon2id$'*) ;; *) die "A ferramenta hash_password nao devolveu um hash argon2id." ;; esac
  ( umask 077; printf '%s\n' "$HASH" > "$SEG/admin_password_hash.txt" )
  chmod 644 "$SEG/admin_password_hash.txt"
  HASH=""
  ok "admin_password_hash.txt gravado (so o hash; a senha nao e guardada)."
fi

# 6d. certificado TLS autoassinado da 8081 (IP no SAN)
SAN="IP:$LAN_IP,IP:127.0.0.1"
for ip in $EXTRA_SAN; do SAN="$SAN,IP:$ip"; done
if [ -s "$SEG/admin_tls_cert.pem" ] && [ -s "$SEG/admin_tls_key.pem" ] && [ "$RENOVAR_TLS" = 0 ]; then
  ok "Certificado TLS ja existe (mantido)."
  if command -v openssl >/dev/null 2>&1 && ! openssl x509 -in "$SEG/admin_tls_cert.pem" -noout -text | grep -q "IP Address:$LAN_IP"; then
    aviso "O certificado atual NAO tem $LAN_IP no SAN. Rode de novo com --renew-tls."
  fi
else
  rm -f "$SEG/admin_tls_cert.pem" "$SEG/admin_tls_key.pem"
  if command -v openssl >/dev/null 2>&1; then
    ( umask 077
      openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=livestats-admin" \
        -addext "subjectAltName=$SAN" -keyout "$SEG/admin_tls_key.pem" -out "$SEG/admin_tls_cert.pem" 2>/dev/null )
  else
    info "Gerando o certificado num container alpine temporario ..."
    docker_ run --rm -v "$SEG:/out" alpine:3.20.3 sh -c "apk add --no-cache openssl >/dev/null \
      && openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj /CN=livestats-admin \
         -addext subjectAltName=$SAN -keyout /out/admin_tls_key.pem -out /out/admin_tls_cert.pem 2>/dev/null \
      && chown $(id -u):$(id -g) /out/admin_tls_key.pem /out/admin_tls_cert.pem"
  fi
  # 644: o Nginx do web roda como uid 101 e precisa ler os dois. A pasta secrets/ e 700.
  chmod 644 "$SEG/admin_tls_cert.pem" "$SEG/admin_tls_key.pem"
  ok "Certificado TLS gerado (SAN: $SAN; validade 825 dias)."
  RECRIAR_WEB=1
fi
RECRIAR_WEB=${RECRIAR_WEB:-0}

for f in youtube_api_key.txt cookie_private_key.txt cookie_public_key.txt admin_password_hash.txt admin_tls_cert.pem admin_tls_key.pem; do
  [ -s "$SEG/$f" ] || die "Falta $SEG/$f."
done
ok "Os 6 arquivos de secrets existem e sao legiveis pelos containers (modo 644 em pasta 700)."

# ---- 7. subir ----------------------------------------------------------------------------------------
titulo "Subindo os servicos"
docker_ compose up -d
if [ "$RECRIAR_WEB" = 1 ]; then docker_ compose up -d --force-recreate web; fi
info "Esperando os 4 servicos ficarem 'healthy' (ate 6 minutos) ..."
if ! esperar_saudaveis 360; then
  erro "Nem todos os servicos ficaram healthy:"
  for c in $SERVICOS; do printf '  %s: %s\n' "$c" "$(saude "$c")"; done
  for c in $SERVICOS; do
    if [ "$(saude "$c")" != healthy ]; then
      printf -- '--- ultimas linhas de %s ---\n' "$c"
      docker_ logs --tail 15 "$c" 2>&1 | ocultar
    fi
  done
  exit 1
fi
for c in $SERVICOS; do ok "$c: healthy"; done

# ---- 8. checagens por GET ----------------------------------------------------------------------------
titulo "Checagens HTTP"
http_code() { curl -sS -m 10 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || printf '000'; }
FALHAS=0
esperar_codigo() { # $1 = descricao, $2 = codigo esperado, resto = args do curl
  _d=$1; _e=$2; shift 2
  _c=$(http_code "$@")
  if [ "$_c" = "$_e" ]; then ok "$_d -> $_c"; else erro "$_d -> $_c (esperado $_e)"; FALHAS=$((FALHAS + 1)); fi
}
esperar_codigo "GET http://127.0.0.1:8080/health" 200 http://127.0.0.1:8080/health
esperar_codigo "GET http://127.0.0.1:8080/live1.json" 200 http://127.0.0.1:8080/live1.json
# Antes de existir a primeira leitura (cookies do Studio ainda nao colados) a api responde 503 JSON: e o esperado.
CODIGO_INSC=$(http_code http://127.0.0.1:8080/inscritos.json)
case $CODIGO_INSC in
  200) ok "GET http://127.0.0.1:8080/inscritos.json -> 200" ;;
  503) ok "GET http://127.0.0.1:8080/inscritos.json -> 503 (esperado enquanto nao houver cookies/primeira leitura)" ;;
  *) erro "GET http://127.0.0.1:8080/inscritos.json -> $CODIGO_INSC (esperado 200 ou 503)"; FALHAS=$((FALHAS + 1)) ;;
esac
esperar_codigo "GET http://127.0.0.1:8080/api/admin/session (admin NAO existe na 8080)" 404 http://127.0.0.1:8080/api/admin/session
CODIGO_ADMIN=$(http_code --cacert "$SEG/admin_tls_cert.pem" "https://$LAN_IP:8081/health")
case $CODIGO_ADMIN in
  200) ok "GET https://$LAN_IP:8081/health (certificado conferido com o gerado) -> 200" ;;
  403) aviso "https://$LAN_IP:8081/health -> 403: o TLS funciona, mas a lista ADMIN_ALLOW_CIDRS nao inclui a origem que o servidor usa para falar consigo mesmo. Teste do seu PC (docs/DEPLOY.md)." ;;
  *) erro "GET https://$LAN_IP:8081/health -> $CODIGO_ADMIN (esperado 200)"; FALHAS=$((FALHAS + 1)) ;;
esac
[ "$FALHAS" -eq 0 ] || die "$FALHAS checagem(ns) falharam."

# ---- 9. NPM (opcional) -------------------------------------------------------------------------------
if [ "$CONECTAR_NPM" = 1 ]; then
  titulo "Conectando o Nginx Proxy Manager"
  if [ -n "$NPM_NOME" ]; then
    sh "$AQUI/connect_npm.sh" --dir "$DIR" --npm "$NPM_NOME"
  else
    sh "$AQUI/connect_npm.sh" --dir "$DIR"
  fi
fi

titulo "Instalacao concluida"
msg "Admin (so LAN/Tailscale): https://$LAN_IP:8081  (aceite o aviso do certificado autoassinado; usuario: $ADMIN_USER)"
msg "Publico: http://$LAN_IP:8080/live1.json , /contador  e, apos o NPM, $PUBLIC_URL/..."
msg "Proximos passos: docs/DEPLOY.md (NPM, slots, cookies do Studio, vMix/OBS)."
