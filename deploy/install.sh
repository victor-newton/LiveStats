#!/bin/sh
# LiveStats - instalacao no servidor (ZimaOS). Idempotente: pode rodar de novo sem perder dados nem segredos.
# NUNCA imprime valores secretos e nunca recebe segredos como argumento (ficariam visiveis no `ps`).
#
# Uso (normalmente chamado por deploy/enviar.sh, que manda o codigo e os segredos por um unico ssh):
#   sh deploy/install.sh --lan-ip 192.168.0.10 [opcoes]
#
# Segredos (so se ainda nao existirem em secrets/):
#   --secrets-stdin           le da entrada padrao: linha 1 = senha do admin
#   (sem isso)                pergunta no terminal, sem eco
# O canal do YouTube nao e informado: o worker-inscritos o descobre pelos cookies (D38).
#
# Opcoes:
#   --dir DIR                 pasta de instalacao (padrao /DATA/AppData/livestats)
#   --lan-ip IP               IP da LAN do servidor, vai no SAN do certificado (obrigatorio)
#   --extra-san IP            outro IP no SAN (ex.: o do Tailscale); pode repetir
#   --admin-user NOME         usuario do admin (padrao: o do .env, senao admin)
#   --public-base-url URL     URL publica exibida no painel (padrao https://live.exemplo.com)
#   --admin-bind IP           onde a 8081 escuta no host (padrao 0.0.0.0; a lista de IPs filtra)
#   --admin-allow "CIDRS"     lista da 8081 (padrao "192.168.0.0/16 100.64.0.0/10")
#   --admin-externo           liga o acesso externo ao painel (D49): a lista de IPs deixa de valer e o login fica em 5/min por IP;
#                             exige o IP fixo do NPM (--admin-trusted-proxy ou o ja gravado no .env)
#   --admin-interno           desliga o acesso externo (volta a lista ADMIN_ALLOW_CIDRS)
#   --admin-trusted-proxy IP  IP fixo (/32) do NPM na rede livestats-edge (ADMIN_TRUSTED_PROXY_IP)
#   --public-admin-url URL    endereco externo do painel, exibido na tela Conta (ex.: https://painel.exemplo.com)
#   --admin-real-ip-header H  cabecalho do IP do cliente no painel externo: X-Forwarded-For (padrao), CF-Connecting-IP (tunel Cloudflare) ou X-Real-IP
#   --master-allow "CIDRS"    IPv4/CIDR de onde o mestre pode entrar (MASTER_ALLOW_CIDRS; vazio = mantem o do .env)
#   --renew-tls               gera um certificado TLS novo
#   --reset-admin-password    troca o hash da senha do admin
#   --skip-build              nao reconstroi as imagens
#   --connect-npm [NOME]      ao final, roda connect_npm.sh (opcao A do README)
set -eu

AQUI=$(cd "$(dirname "$0")" && pwd)
ORIGEM=$(dirname "$AQUI")
# shellcheck source=lib.sh
. "$AQUI/lib.sh"

DIR=$DIR_PADRAO
LAN_IP=""
EXTRA_SAN=""
ADMIN_USER=""
PUBLIC_URL=""
ADMIN_BIND=0.0.0.0
ADMIN_ALLOW=""   # vazio: mantem o do .env; sem .env, 192.168.0.0/16 100.64.0.0/10
SECRETS_STDIN=0
RENOVAR_TLS=0
TROCAR_SENHA=0
PULAR_BUILD=0
CONECTAR_NPM=0
NPM_NOME=""
ADMIN_EXTERNO=""        # vazio = mantem o do .env
ADMIN_PROXY=""
PUBLIC_ADMIN=""
MASTER_ALLOW=""
REAL_IP_HEADER=""

while [ $# -gt 0 ]; do
  case $1 in
    --dir) DIR=$2; shift 2 ;;
    --lan-ip) LAN_IP=$2; shift 2 ;;
    --extra-san) EXTRA_SAN="$EXTRA_SAN $2"; shift 2 ;;
    --admin-user) ADMIN_USER=$2; shift 2 ;;
    --public-base-url) PUBLIC_URL=$2; shift 2 ;;
    --admin-bind) ADMIN_BIND=$2; shift 2 ;;
    --admin-allow) ADMIN_ALLOW=$2; shift 2 ;;
    --admin-externo) ADMIN_EXTERNO=1; shift ;;
    --admin-interno) ADMIN_EXTERNO=0; shift ;;
    --admin-trusted-proxy) ADMIN_PROXY=$2; shift 2 ;;
    --public-admin-url) PUBLIC_ADMIN=$2; shift 2 ;;
    --master-allow) MASTER_ALLOW=$2; shift 2 ;;
    --admin-real-ip-header) REAL_IP_HEADER=$2; shift 2 ;;
    --secrets-stdin) SECRETS_STDIN=1; shift ;;
    --renew-tls) RENOVAR_TLS=1; shift ;;
    --reset-admin-password) TROCAR_SENHA=1; shift ;;
    --skip-build) PULAR_BUILD=1; shift ;;
    --connect-npm)
      CONECTAR_NPM=1; shift
      if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then NPM_NOME=$1; shift; fi ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
    *) die "Argumento desconhecido: $1 (use --help)." ;;
  esac
done

ipv4() { printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; }
[ -n "$LAN_IP" ] || die "Informe --lan-ip (IP da LAN do servidor)."
ipv4 "$LAN_IP" || die "--lan-ip invalido: $LAN_IP"
for ip in $EXTRA_SAN; do ipv4 "$ip" || die "--extra-san invalido: $ip"; done

# Acesso externo (D49): recusa ANTES de qualquer mudanca se faltar o proxy confiavel (valida so; grava na secao do .env).
if [ "$ADMIN_EXTERNO" = 1 ] || [ -n "$ADMIN_PROXY" ]; then
  _prx=$ADMIN_PROXY
  [ -n "$_prx" ] || _prx=$(env_get "$DIR/.env" ADMIN_TRUSTED_PROXY_IP)
  [ -n "$_prx" ] || die "--admin-externo exige o IP fixo do Nginx Proxy Manager (ADMIN_TRUSTED_PROXY_IP): informe --admin-trusted-proxy IP (ex.: 172.29.80.11) ou rode antes deploy/connect_npm.sh. Sem proxy confiavel o painel nao pode ser aberto de fora. Nada foi alterado."
fi

# ---- 1. segredos que chegam por stdin (lidos primeiro; depois a entrada padrao fecha) ----------------
ADMIN_PW=""
if [ "$SECRETS_STDIN" = 1 ]; then
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
[ -n "$ADMIN_USER" ] || ADMIN_USER=admin
printf '%s' "$ADMIN_USER" | grep -Eq '^[A-Za-z0-9._@-]{1,64}$' || die "--admin-user invalido."
[ -n "$PUBLIC_URL" ] || PUBLIC_URL=$(env_get "$ENV_ARQ" PUBLIC_BASE_URL)
[ -n "$PUBLIC_URL" ] || PUBLIC_URL=https://live.exemplo.com
[ -n "$ADMIN_ALLOW" ] || ADMIN_ALLOW=$(env_get "$ENV_ARQ" ADMIN_ALLOW_CIDRS)
[ -n "$ADMIN_ALLOW" ] || ADMIN_ALLOW="192.168.0.0/16 100.64.0.0/10"
printf '%s' "$ADMIN_ALLOW" | grep -Eq '^[0-9./ ]+$' || die "--admin-allow so aceita IPv4/CIDR separados por espaco."
case $ADMIN_ALLOW in *0.0.0.0/0*) die "--admin-allow nao pode conter 0.0.0.0/0." ;; esac
ipv4 "$ADMIN_BIND" || die "--admin-bind invalido."

( umask 077
  env_set "$ENV_ARQ" ADMIN_USERNAME "$ADMIN_USER"
  env_set "$ENV_ARQ" PUBLIC_BASE_URL "$PUBLIC_URL"
  env_set "$ENV_ARQ" ADMIN_BIND "$ADMIN_BIND"
  env_set "$ENV_ARQ" ADMIN_ALLOW_CIDRS "$ADMIN_ALLOW"
  # Preserva o que ja existir (por exemplo o TRUSTED_PROXY_CIDR gravado por connect_npm.sh).
  env_set "$ENV_ARQ" TRUSTED_PROXY_CIDR "$(env_get "$ENV_ARQ" TRUSTED_PROXY_CIDR)"
  # D42: um LIVE_INTERVAL=30 herdado de um .env.example de versao anterior derrubaria o worker-lives (maximo 15 s).
  ajustar_live_interval "$ENV_ARQ"
  # D49/D48: acesso externo, endereco externo do painel e filtro do mestre; vazio preserva o valor do .env.
  aplicar_acesso_externo "$ENV_ARQ" "$ADMIN_EXTERNO" "$ADMIN_PROXY" "$PUBLIC_ADMIN" "$MASTER_ALLOW" "$REAL_IP_HEADER"
)
chmod 600 "$ENV_ARQ"
ok ".env gravado (usuario $ADMIN_USER, admin em $ADMIN_BIND:8081, lista: $ADMIN_ALLOW, TRUSTED_PROXY_CIDR='$(env_get "$ENV_ARQ" TRUSTED_PROXY_CIDR)', acesso externo: $(env_get "$ENV_ARQ" ADMIN_EXTERNAL_ACCESS))."

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
  [ -t 0 ] || die "Sem terminal para perguntar '$1'. Use --secrets-stdin."
  printf '%s' "$1" >&2
  stty -echo
  IFS= read -r SEGREDO_LIDO || true
  stty echo
  printf '\n' >&2
}

# 6a. par de chaves dos cookies (gerado na imagem da api; a saida fica so em variaveis do shell)
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

# 6b. hash da senha do admin
if [ -s "$SEG/admin_password_hash.txt" ] && [ "$TROCAR_SENHA" = 0 ]; then
  ok "admin_password_hash.txt ja existe (mantido)."
else
  if [ -z "$ADMIN_PW" ]; then
    ler_oculto "Senha do admin (minimo 8 caracteres): "; ADMIN_PW=$SEGREDO_LIDO
    ler_oculto "Repita a senha: "
    [ "$SEGREDO_LIDO" = "$ADMIN_PW" ] || die "As senhas nao conferem."
  fi
  HASH=$(printf '%s\n' "$ADMIN_PW" | docker_ run --rm -i --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --user 10001:10001 livestats/api:local python -m tools.hash_password) \
    || die "Falha ao gerar o hash (a senha precisa ter pelo menos 8 caracteres)."
  ADMIN_PW=""
  # shellcheck disable=SC2016  # $ literal: prefixo do hash PHC
  case $HASH in '$argon2id$'*) ;; *) die "A ferramenta hash_password nao devolveu um hash argon2id." ;; esac
  ( umask 077; printf '%s\n' "$HASH" > "$SEG/admin_password_hash.txt" )
  chmod 644 "$SEG/admin_password_hash.txt"
  HASH=""
  ok "admin_password_hash.txt gravado (so o hash; a senha nao e guardada)."
fi

# 6c. certificado TLS autoassinado da 8081 (IP no SAN)
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

for f in cookie_private_key.txt cookie_public_key.txt admin_password_hash.txt admin_tls_cert.pem admin_tls_key.pem; do
  [ -s "$SEG/$f" ] || die "Falta $SEG/$f."
done
ok "Os 5 arquivos de secrets existem e sao legiveis pelos containers (modo 644 em pasta 700)."

# ---- 7. subir ----------------------------------------------------------------------------------------
titulo "Subindo os servicos"
# Registrado no painel do ZimaOS (deploy/registrar_zimaos.sh): os containers so podem ser recriados a partir do arquivo
# registrado (que e do root; precisa de sudo com senha, e este script nao tem terminal). Para NAO deixar o projeto metade
# de um jeito e metade do outro, para aqui: codigo, imagens, .env e segredos ja estao atualizados; os containers seguem
# rodando como antes, ate voce rodar o registrar-zimaos (que regera o arquivo e recria o que mudou).
if zimaos_registrado; then
  _web=""
  if [ "$RECRIAR_WEB" = 1 ]; then _web=" --recriar-web"; fi
  aviso "O LiveStats esta registrado no painel do ZimaOS ($ZIMAOS_ARQ)."
  aviso "Codigo, imagens, .env e segredos foram atualizados, mas os containers NAO foram recriados (precisam de sudo)."
  aviso "Termine com:  bash deploy/enviar.sh registrar-zimaos$_web   (pede a senha do SSH e a do sudo)"
  exit 2
fi
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
esperar_codigo "GET http://127.0.0.1:8080/contador" 200 http://127.0.0.1:8080/contador
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
  403) aviso "https://$LAN_IP:8081/health -> 403: o TLS funciona, mas a lista ADMIN_ALLOW_CIDRS nao inclui a origem que o servidor usa para falar consigo mesmo. Teste do seu PC (README, 'Problemas comuns')." ;;
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
msg "Publico: http://$LAN_IP:8080/contador , /inscritos.json  e, apos o NPM, $PUBLIC_URL/..."
msg "Proximos passos: README.md (NPM, cookies do Studio, vMix/OBS)."
