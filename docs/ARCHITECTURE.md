# LiveStats — Arquitetura

Contrato T1 da aplicação **LiveStats**, que unifica o *Live Stats* (estatísticas de até 4 lives do YouTube) e o *YT Inscritos* (contador de inscritos do canal) numa única aplicação segura. Repositório remoto: `git@github.com:victor-newton/LiveStats.git`.

Documentos do contrato (todos em `docs/`, `db/` e `tests/contract/`): este arquivo · [`openapi.yaml`](openapi.yaml) · [`legacy-formats.md`](legacy-formats.md) · [`data-model.md`](data-model.md) + [`../db/schema.sql`](../db/schema.sql) + [`../db/admin_schema.sql`](../db/admin_schema.sql) · [`../docker-compose.yml`](../docker-compose.yml) · [`repo-layout.md`](repo-layout.md) · [`test-plan.md`](test-plan.md) · base: [`PLANO.md`](PLANO.md) e [`MAPA-DE-SEGURANCA.md`](MAPA-DE-SEGURANCA.md). Em conflito, vale esta ordem: PLANO > ARCHITECTURE (Decisões) > demais — exceto onde uma Decisão registra escolha posterior do usuário (D10: CORS nos GET públicos; D32: acesso ao admin), que prevalece sobre o PLANO.

> Este documento é um contrato. Nada aqui foi implementado, testado ou validado ainda.

## 1. Visão geral

```
 Internet ── Cloudflare Tunnel ──┐            LAN / Tailscale ──┐
                                 ▼                              ▼
                         ┌── :8080 pública ──────────┬── :8081 admin ───────┐
                         │ overlays, /api/public,    │ painel, /api/admin,  │
                         │ aliases legados, /health. │ login. + tudo da     │
                         │ Admin NÃO existe aqui.    │ pública.             │
                         ├───────────── web (Nginx) ────────────────────────┤
                         │ CSP, cabeçalhos, limit_req, IP real              │
                         └──────────────┬───────────────────────────────────┘
                              rede livestats-internal  (internal: true, sem internet)
                         ┌──────────────▼──────────────┐
                         │ api (FastAPI)               │  só a chave PÚBLICA dos cookies
                         │ /api/public  /api/admin     │  e o hash da senha do admin
                         └──────────────┬──────────────┘
                              volume livestats-data  (/data/livestats.db, SQLite WAL)
                              volume livestats-admin (/admin-state/admin.db) - SÓ a api
                 ┌──────────────────────┴──────────────────────┐
     ┌───────────▼───────────┐                     ┌───────────▼────────────┐
     │ worker-lives          │                     │ worker-inscritos       │
     │ secret: youtube_api_  │                     │ secret: chave PRIVADA  │
     │ key. YouTube Data v3  │                     │ Playwright + Chromium  │
     └───────────────────────┘                     └────────────────────────┘
          rede livestats-egress (só internet; sem acesso a web/api)
```

Princípios (PLANO §1): cada segredo existe em um só container; segredos por Docker secrets (arquivos em `/run/secrets`), nunca em variáveis de ambiente; cookies cifrados com caixa selada (a api só tem a chave pública); front 100% estático sem script inline e sem `innerHTML`; compatibilidade total com OBS/vMix; endurecimento uniforme dos containers.

Acesso ao admin (C7): a 8081 **não** é publicada para a rede (`ADMIN_BIND=127.0.0.1`). O Nginx Proxy Manager (NPM) que já roda no host termina o HTTPS (certificado Let's Encrypt por desafio DNS no Cloudflare; o nome resolve para o IP da LAN pelo Pi-hole local), aplica a lista de acesso `192.168.15.0/24` + `100.64.0.0/10` (LAN e Tailscale) e alcança `livestats-web:8081` entrando na rede `livestats-edge` com IP fixo. Clientes Tailscale usam o mesmo nome (DNS do Pi-hole / rota de sub-rede). A configuração do NPM, do Pi-hole e do DNS é tarefa da T9 (premissas em P1).

## 2. Containers

| Serviço | Container | Imagem base | Usuário | Rede(s) | Segredos | Volumes | Portas | Função |
|---|---|---|---|---|---|---|---|---|
| `web` | `livestats-web` | `nginx-unprivileged` (digest fixo) | `101:101` | `edge`, `internal` | — | nenhum | **8080**, **8081** | Serve o front estático (baked na imagem), faz proxy para a api, aplica CSP/cabeçalhos/`limit_req`, separa pública e admin |
| `api` | `livestats-api` | `python:3.12-slim` (digest) | `10001:10001` | `internal` | `cookie_public_key`, `admin_password_hash` | `livestats-data` em `/data` (rw) **e** `livestats-admin` em `/admin-state` (rw, exclusivo) | (8000 interna) | FastAPI: endpoints públicos (leitura), admin (login, sessão, CSRF, rate limit), cifra cookies, cria o esquema |
| `worker-lives` | `livestats-worker-lives` | `python:3.12-slim` (digest) | `10001:10001` | `egress` | `youtube_api_key` | `livestats-data` em `/data` (rw) | — | Lê os slots, consulta o YouTube Data API v3 em lote, grava `live_stats` |
| `worker-inscritos` | `livestats-worker-inscritos` | `python:3.12-slim` + Playwright/Chromium (versão fixa) | `10001:10001` | `egress` | `cookie_private_key` | `livestats-data` em `/data` (rw) | — | Decifra cookies, abre o YouTube Studio, lê o contador, grava `subscriber_reading` |

O volume `livestats-admin` (sessões e tentativas de login) é montado **somente** na `api`: nenhum worker o enxerga (C1 do V0, D2).

Todos: `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `tmpfs` onde precisam escrever, `init: true`, limite de processos, log rotativo. O Chromium recebe `shm_size: 512mb`. Detalhes no [`docker-compose.yml`](../docker-compose.yml).

Entradas obrigatórias (para o compose e os testes de T8 funcionarem sem combinação adicional): `uvicorn app.main:app` (api, porta 8000 interna); `python -m worker_lives`; `python -m worker_inscritos`; healthchecks `python -m app.healthcheck`, `python -m worker_lives.healthcheck`, `python -m worker_inscritos.healthcheck`.

## 3. Redes e isolamento

| Rede | Nome | Config | Membros | Para quê |
|---|---|---|---|---|
| `internal` | `livestats-internal` | `internal: true` | `web`, `api` | web ↔ api. Sem rota para fora: **a api não tem acesso à internet**. |
| `edge` | `livestats-edge` | bridge, sub-rede fixa (`EDGE_SUBNET`), sem NAT de saída | `web` + (se rodarem neste host) `cloudflared` e **Nginx Proxy Manager, cada um com IP fixo** | Onde 8080/8081 são publicadas (rede `internal: true` não publica portas) e onde cloudflared/NPM alcançam o `web`. Os únicos IPs de confiança do Nginx são esses IPs fixos (§6). |
| `egress` | `livestats-egress` | bridge comum | `worker-lives`, `worker-inscritos` | Saída para a internet. Workers não alcançam `web` nem `api` (redes separadas) e ninguém alcança os workers (sem portas). |

Consequências: um worker comprometido não consegue falar com a api; a api comprometida não consegue exfiltrar nada para a internet; nenhum container além do `web` recebe tráfego de fora. A comunicação worker ↔ api é **só** pelo SQLite no volume `/data` (nenhum worker abre porta).

## 4. Docker secrets

Arquivos em `./secrets/` (ignorados pelo Git; `secrets/README.md` é escrito na T2). Montados em `/run/secrets/<nome>` somente no container indicado.

| Secret | Arquivo | Container | Conteúdo | Quem usa para quê |
|---|---|---|---|---|
| `youtube_api_key` | `secrets/youtube_api_key.txt` | `worker-lives` | chave da YouTube Data API v3 (nova, restrita à API) | cabeçalho `X-Goog-Api-Key` nas chamadas |
| `cookie_private_key` | `secrets/cookie_private_key.txt` | `worker-inscritos` | chave privada Curve25519 (32 bytes, base64) | decifra `cookie_sets.ciphertext` |
| `cookie_public_key` | `secrets/cookie_public_key.txt` | `api` | chave pública correspondente (32 bytes, base64) | cifra o que o admin cola |
| `admin_password_hash` | `secrets/admin_password_hash.txt` | `api` | hash argon2id (formato PHC) da senha do admin | verifica o login |

Geração (ferramentas escritas na T3, em `api/tools/`): `python -m tools.gen_cookie_keys` (par de chaves) e `python -m tools.hash_password` (hash). Regras: nenhum segredo em `environment:`, em `build args`, em `Dockerfile`, em imagem, em log ou em resposta HTTP; os serviços leem os arquivos apontados por variáveis `*_FILE` (caminho, não valor) e removem espaços/quebras de linha do conteúdo; sem valor padrão no código (arquivo ausente ⇒ o processo sai com erro explícito). Um processo que receber a variável antiga (`YOUTUBE_API_KEY`) não a usa.

Variáveis de ambiente **não secretas** (ficam no compose/`.env`): `DB_PATH`, `ADMIN_DB_PATH`, `ADMIN_USERNAME`, `PUBLIC_BASE_URL`, `LIVE_INTERVAL`, `YOUTUBE_CHANNEL_ID`, `SCRAPE_INTERVAL`, `DEBUG_SCREENSHOT`, `TRUSTED_PROXY_CIDR`, `ADMIN_TRUSTED_PROXY_IP`, `EDGE_SUBNET`, `ADMIN_BIND`. Opcionais, só para apontar os workers a outro host (testes de integração; o padrão é o endereço oficial; só aceitam `https`, ou `http` para `localhost`/`127.0.0.1`/`::1`): `YOUTUBE_API_BASE_URL` (padrão `https://www.googleapis.com/youtube/v3`), `YOUTUBE_WWW_BASE_URL` (padrão `https://www.youtube.com`), `YOUTUBE_STUDIO_BASE_URL` (padrão `https://studio.youtube.com`). Nenhuma variável liga/desliga login, CSRF ou rate limit.

## 5. Fluxos de dados

### 5.1 Admin salva os slots → worker-lives lê
1. Painel (8081) `PUT /api/admin/slots` com 4 textos (sessão + CSRF).
2. A api valida (cada texto vazio ou com ID de vídeo extraível), grava em `slots` (trim) e responde `{ok, slots, saved_at}`.
3. A cada ciclo (`LIVE_INTERVAL`, padrão 30 s) o `worker-lives` lê `slots`, extrai IDs, faz **uma** chamada `videos.list` com os IDs únicos e grava `live_stats` (4 linhas; `sem_url` reescrito a cada ciclo; em erro mantém a linha anterior), mais o heartbeat em `worker_status`.
4. `/live{n}.json`, `/api/public/lives/{n}` e o painel leem `live_stats` pela api. Latência esperada de uma troca de URL: até um ciclo.

### 5.2 Admin cola cookies → api cifra → worker-inscritos decifra
1. Painel valida o JSON no navegador e envia `POST /api/admin/cookies` (array do EditThisCookie; sessão + CSRF).
2. A api valida e normaliza (5 campos por cookie), cifra o array com `SealedBox(chave pública)` e grava **só** o ciphertext + metadados em `cookie_sets` (apaga os anteriores). O texto puro existe apenas na memória da requisição e nunca vai a log, resposta ou disco. A api não tem a chave privada e seu acesso SQL à coluna `ciphertext` é negado pelo autorizador.
3. O `worker-inscritos` verifica `cookie_sets` a cada ciclo; ao ver `id` novo, lê o ciphertext, decifra com `cookie_private_key`, reinjeta os cookies no contexto do Chromium e recarrega a página do Studio (substitui a checagem de `mtime`). Registra `cookies_applied_id` e `auth_state` em `worker_status`.
4. A cada `SCRAPE_INTERVAL` (10 s) lê o contador e grava `subscriber_reading`. Se o Studio redirecionar para login: `auth_state=login_required`, `state=error`, e o painel avisa que os cookies expiraram. Sem cookies: `auth_state=no_cookies`.

### 5.3 Leitura pública (OBS/vMix, overlays)
Cliente → Nginx 8080 → api (`/live{n}.json`, `/inscritos.json`, `/api/public/*`, `/health`) → SQLite (somente leitura). Os overlays HTML/JS/CSS são servidos pelo próprio Nginx. `/contador` e `/overlay/inscritos` fazem polling de `/api/public/subscribers` a cada 5 s.

### 5.4 Login e sessão
`POST /api/admin/login` → api conta falhas (60 s) por IP e por usuário; senha verificada com argon2 (tempo constante também para usuário inexistente); sucesso cria sessão nova: id aleatório de 32 bytes vai ao cookie `__Host-livestats_session` (`HttpOnly; Secure; SameSite=Strict; Path=/`, `Max-Age=28800`), `sha256(id)` e o token CSRF vão a `admin_sessions` (em `/admin-state/admin.db`, banco que só a api enxerga). O front guarda o CSRF só em memória (recuperável por `GET /api/admin/session`). Expira em 8 h (absoluto) ou 30 min sem uso; logout apaga a linha.

## 6. Portas, rotas e limites

| Rota | 8080 | 8081 | Auth |
|---|---|---|---|
| `/health`, `/api/public/*`, `/live{n}.json`, `/inscritos.json` | sim | sim | nenhuma |
| `/contador`, `/overlay/*` (HTML estático) | sim | sim | nenhuma |
| `/` e `/admin` (painel) | **404** | sim | o painel é estático; dados só pelo `/api/admin` |
| `/api/admin/login` | **404** | sim | — (limitado) |
| `/api/admin/*` demais | **404** | sim | sessão + CSRF nos métodos que alteram estado |
| `/api/admin/*` **inexistente** | 404 | **sem sessão válida: 401** (JSON, sem traceback); com sessão: 404 JSON | a api decide; nunca vaza se a rota existe |
| qualquer outra rota | 404 | 404 | — |

Valores iniciais de limite (T7 implementa, pode ajustar sem baixar a proteção; `INFRA-NG-03` e `SEC-*` verificam):
- `login`: zona `limit_req` por IP real, `rate=10r/m burst=5 nodelay` (deixa passar 6 requisições seguidas para que o contador da api, 5 falhas/60 s → **6ª tentativa = 429**, seja alcançável e testável pelo Nginx; da 7ª em diante o Nginx devolve 429);
- demais rotas de `/api/admin`: `rate=10r/s burst=20`;
- rotas públicas: `rate=10r/s burst=30` por IP real; overlays consultam a cada 5 s, o limite não os afeta;
- `limit_req_status 429`; corpo de 429 em JSON `{"detail": "Muitas tentativas. Tente novamente em instantes."}` com `Retry-After`; `client_max_body_size 256k`.

IP real: na 8080 vem de `CF-Connecting-IP`, aceito **apenas** quando a conexão vem do IP fixo (**/32**) em `TRUSTED_PROXY_CIDR` — o container do cloudflared, com IP fixo na rede `edge`. O valor padrão é **vazio = não confiar em ninguém** (o cabeçalho é sempre ignorado e o limite conta o IP da conexão). **Nunca** colocar a sub-rede inteira da `edge` nem o IP do gateway da bridge (ex.: `172.29.80.1`): conexões que chegam pelo port-publish/userland-proxy aparecem com o IP do gateway, e confiar nele permitiria a qualquer cliente forjar `CF-Connecting-IP` e burlar o `limit_req`. O cabeçalho enviado por qualquer outra origem é ignorado. Na 8081 (C7): o IP real é o **último salto de `X-Forwarded-For`** (`real_ip_header X-Forwarded-For; real_ip_recursive off`), aceito **somente** quando a conexão vem do IP fixo do NPM em `ADMIN_TRUSTED_PROXY_IP` (/32; **padrão vazio = ignorar o cabeçalho**, vale o IP da conexão); mesma regra da C2 (nunca sub-rede nem gateway). O Nginx sempre sobrescreve `X-Real-IP` com `$remote_addr` ao falar com a api; a api usa esse valor para o contador de login por IP, e o contador **por usuário** continua valendo mesmo se o do Nginx falhar. Com o padrão vazio, o `set_real_ip_from` não é emitido (mecanismo a critério da T7).

## 7. Cabeçalhos de segurança (todas as respostas do Nginx, inclusive 4xx/5xx — `add_header ... always`)

- `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` — sem `unsafe-inline`/`unsafe-eval`. Exceção: as páginas de overlay (`/contador`, `/overlay/*`) usam `frame-ancestors *` (D11).
- `X-Content-Type-Options: nosniff`; `Referrer-Policy: no-referrer`; `X-Frame-Options: DENY` (menos nos overlays); `Permissions-Policy` restritivo; `Cross-Origin-Opener-Policy: same-origin`; `server_tokens off`.
- Respostas da api: `Cache-Control: no-store` em JSON de dados e de admin.
- **CORS (C6, decisão do usuário):** só nos GET públicos de dados (`/live{n}.json`, `/inscritos.json`, `/api/public/*`) o **Nginx** responde `Access-Control-Allow-Origin: *` e `Access-Control-Allow-Methods: GET` (inclusive em respostas de erro, `always`); `OPTIONS` de preflight nessas rotas → `204` com os mesmos dois cabeçalhos. **Nunca** `Access-Control-Allow-Credentials`. Todas as demais rotas (`/api/admin/*`, painel, overlays HTML, `/health`, estáticos) não enviam nenhum `Access-Control-Allow-*`. A api nunca emite CORS (evita cabeçalho duplicado).

## 8. Inventário de funcionalidades atuais → onde ficam

| # | Funcionalidade atual | Onde fica na LiveStats |
|---|---|---|
| F1 | 4 slots de URL de live, salvos pelo admin | `slots`; `PUT/GET /api/admin/slots`; painel T6 |
| F2 | Worker busca views/likes/título/status em lote (1 chamada/ciclo) | `worker-lives` (mesma lógica; chave por header) |
| F3 | `/live1.json`…`/live4.json` (array com 1 objeto, `ativo` string, `sem_url`) | alias legado na api, bytes idênticos (`legacy-formats.md` §1) |
| F4 | Painel Live Stats: status colorido, views/likes, título, "Aguardando dados…"/"Sem dados — insira uma URL", "Salvar URLs", "✓ Salvo com sucesso", atualização a cada 15 s, lista "Endpoints JSON" | `web/static/admin` (T6), `textContent` em vez de `innerHTML` |
| F5 | Scraper de inscritos (cookies → Studio → 3 estratégias de leitura → `filtrar_numero`) | `worker-inscritos` (Playwright) |
| F6 | Recarregar após 30 falhas; recarregar ao trocar cookies | mantido (detecção por `cookie_sets.id`) |
| F7 | `/inscritos.json` (objeto com `inscritos`, `raw`, `atualizado_em`) | alias legado, bytes idênticos |
| F8 | `/contador` (overlay transparente com carrossel de dígitos, 5 s) | `web/static/overlay` + alias `/contador` + `/overlay/inscritos` |
| F9 | Painel `/admin` do YT Inscritos: inscritos atuais, última leitura, badge "N cookies carregados"/"Nenhum cookie", instruções do EditThisCookie, validação do JSON no navegador, "Salvar cookies e recarregar scraper" | painel T6 + `POST /api/admin/cookies` + `GET /api/admin/workers/status` |
| F10 | Correção de domínios com markdown nos cookies | api (e worker, por segurança) |
| F11 | `/health` de cada app | `/health` unificado (D8) |
| F12 | Intervalos configuráveis (`LIVE_INTERVAL`, `SCRAPE_INTERVAL`), `YOUTUBE_CHANNEL_ID` | variáveis não secretas (D17) |
| F13 | Print de debug (`debug.png`) | só com `DEBUG_SCREENSHOT=1`, em tmpfs, nunca em `/data` nem pasta servida (S3) |
| F14 | Dados persistentes em `./data` | volume `livestats-data` (SQLite) |
| F15 | Reinício automático (`restart: unless-stopped`) | mantido |

## 9. Mapa dos achados do MAPA-DE-SEGURANCA

| Id | Achado | Medida que resolve | Verificação |
|---|---|---|---|
| S1 | Chave da API do YouTube no código e no compose | Chave nova (P0, fora do repo); só em `secrets/youtube_api_key.txt` → Docker secret montado **só** no `worker-lives`; nenhum default no código; enviada por header (não aparece em URL/log); gitleaks no CI | `LIV-SEC-*`, `SEC-08`, `SEC-10`, `INFRA-CFG-04` |
| S2 | Cookies de sessão do Google em `cookies.json` | Cifrados com SealedBox pela api (só chave pública); só `worker-inscritos` tem a privada; nunca em texto puro em disco, log, resposta ou Git; recomenda-se conta de função "Visualizador" | `API-COOKIE-*`, `SUB-CK-*`, `SEC-09`, `SEC-14` |
| S3 | Print do Studio com receita (`debug.png`) | Só com `DEBUG_SCREENSHOT=1`, gravado em tmpfs fora do volume e de qualquer pasta servida; `.gitignore` já cobre | `SUB-SEC-02` |
| S4 | Perfil do Chrome (`perfil_bot`) | Removido: não é usado (cookies vêm do banco; contexto do Chromium é efêmero em tmpfs) | `SUB-SEC-05` |
| S5 | ID do canal no código | `YOUTUBE_CHANNEL_ID` obrigatório no `.env`, sem default; só placeholders no repo | `SUB-CFG-01`, `SEC-10` |
| V1 | `/admin` e `POST /admin/cookies` sem autenticação | Todo `/api/admin/*` exige sessão (401) e CSRF (403); painel só na 8081 | `API-AUTH-01/02`, `SEC-01` |
| V2 | `POST /config` sem autenticação | `PUT /api/admin/slots` autenticado + CSRF + validação | `API-AUTH-01/02`, `API-SLOT-*` |
| V3 | XSS armazenado (título e URL em `innerHTML`) | Front só com `textContent`; CSP sem `unsafe-inline`; slots validados na api; título é texto no JSON | `WEB-XSS-*`, `WEB-CSP-01`, `SEC-03` |
| V4 | CORS `*` | O CORS aberto agora é **restrito a GET de dados públicos e somente leitura, sem credenciais** (pedido do usuário, para data sources do vMix e uso em qualquer lugar); o que tornava o V4 grave (admin e escrita sem autenticação alcançáveis de qualquer site) não existe mais: admin exige sessão `SameSite=Strict` + CSRF e não tem CORS | `API-PUB-05`, `INFRA-NG-08`, `SEC-16`, `SEC-23` |
| V5 | Admin e público no mesmo servidor/porta | Duas portas no Nginx; a 8080 não tem rota de admin (404) | `INFRA-NG-01/06`, `SEC-04` |
| V6 | Root, dependências sem versão, Chrome `current` | Usuário não-root, rootfs somente leitura, sem capabilities; imagens por digest; dependências com versão e hash; Playwright com Chromium de versão fixa | `INFRA-DF-*`, `SEC-07` |
| V7 | `--remote-debugging-port=9222` | Playwright via pipe; sem porta de debug; teste tenta conectar e deve falhar | `SUB-SEC-01`, `SEC-12` |

## 10. Decisões

Decisões tomadas por esta tarefa onde o PLANO não decide. Cada uma tem o motivo.

- **D1 — UID único e volume compartilhado.** `api`, `worker-lives` e `worker-inscritos` rodam como `10001:10001`; `/data` é `root:10001` modo `2770`, criado em cada imagem (o volume nomeado herda do primeiro container) e cada processo faz `os.umask(0o007)`. *Motivo:* os três escrevem no mesmo SQLite; UIDs diferentes só trariam atrito de permissão, já que o isolamento real é por container. O `web` não monta o volume (usa `101`, da imagem nginx-unprivileged). O volume `livestats-admin` (`/admin-state`, modo `0700`, dono `10001`) só existe na api.
- **D2 — Dois bancos; autorizador é defesa contra bug, não contra comprometimento (C1 do V0).** `livestats.db` (compartilhado: slots, estatísticas, inscritos, cookies cifrados, status dos workers) fica no volume `livestats-data`; `admin.db` (`admin_sessions`, `login_attempts`) fica no volume `livestats-admin`, montado **somente na api**. *Motivo:* o `set_authorizer` é imposto pelo próprio processo; um worker comprometido (o `worker-inscritos` processa páginas da internet no Chromium) executa código próprio, abre o arquivo sem autorizador e poderia inserir uma sessão de admin falsa se ela estivesse no volume compartilhado. A proteção real é **isolamento por volume + criptografia dos cookies**; cada processo ainda instala `set_authorizer` com as permissões de `data-model.md` §2.1, mas apenas como defesa contra bug no próprio código (ex.: SQL injection, escrita na tabela errada).
- **D3 — Sessão.** Cookie `__Host-livestats_session` (prefixo `__Host-` exige `Secure`, `Path=/`, sem `Domain`); id de 32 bytes, só o SHA-256 no banco; expiração absoluta 8 h + ociosidade 30 min; novo id a cada login; CSRF = token aleatório por sessão guardado em `admin_sessions` (no `admin.db`; permite o front recuperá-lo em `GET /api/admin/session`; sozinho não serve para nada sem o cookie).
- **D4 — Um único admin.** Usuário em `ADMIN_USERNAME` (obrigatório no `.env`, sem valor padrão no código) e hash argon2id no secret. *Motivo:* o PLANO fala em "senha" e em limite "por usuário"; o PLANO proíbe usuário/senha fixos em código. Troca de senha = gerar novo hash e reiniciar a api.
- **D5 — Regra de bloqueio do login.** 5 falhas em 60 s, por IP **ou** por usuário (a 6ª tentativa, mesmo com a senha certa, é 429); tentativas bloqueadas não são contadas; resposta 401 idêntica para usuário e senha errados. Nginx deixa passar 6 requisições antes de limitar (§6) para o 429 da api ser alcançável. *Consequência aceita:* quem digitar o usuário certo e errar 5 vezes bloqueia o login por até 1 min (também um atacante pode causar isso).
- **D6 — Rotas de admin novas e validação de slots.** `/config` → `GET/PUT /api/admin/slots`; `/admin/cookies` → `POST /api/admin/cookies`. Diferente do legado (aceitava qualquer texto), a api devolve **422** se um slot não vazio não tiver ID de vídeo extraível (mesmo padrão do worker), exatamente 4 itens, máx. 2048 caracteres, sem caracteres de controle. *Motivo:* V3/V2 (menos superfície) e feedback imediato; antes um texto inválido virava `sem_url` em silêncio.
- **D7 — Dois formatos para o mesmo dado.** `/api/public/lives/{n}` devolve objeto; `/live{n}.json` devolve array de 1 (legado). Ambos servidos pela api; o Nginx encaminha `^/live[0-9]+\.json$` e `/inscritos.json`. `/api/public/subscribers` = corpo de `/inscritos.json`.
- **D8 — `/health` único.** Superset dos dois legados, sem `cookies_file_exists` (revelaria estado do admin a quem for público); essa informação passou para `GET /api/admin/workers/status` (`cookies.configured`). Sempre 200.
- **D9 — Raiz.** `/` na 8081 é o painel; `/admin` é alias; na 8080 `/` e `/admin` são 404 (o índice JSON do yt-inscritos deixa de existir, Pendência P4).
- **D10 — CORS aberto só nos GET públicos de dados (C6; decisão do usuário, substitui o "CORS fechado" do PLANO).** `Access-Control-Allow-Origin: *` + `Access-Control-Allow-Methods: GET` em `/live{n}.json`, `/inscritos.json` e `/api/public/*` (preflight `OPTIONS` → 204), sem `Allow-Credentials`; nenhuma outra rota tem `Access-Control-Allow-*` (admin, overlays HTML, `/health`, painel). *Motivo:* o usuário consome os JSON em data sources do vMix e quer usá-los em qualquer lugar; os dados já são públicos e somente leitura, e a sessão de admin é `SameSite=Strict` com CSRF. Implementado no Nginx (um só lugar); a api não envia CORS.
- **D11 — Overlays embutíveis.** Só `/contador` e `/overlay/*` têm `frame-ancestors *` (e sem `X-Frame-Options`); todo o resto, `'none'`/`DENY`. *Motivo:* o legado não restringia; mantém quem embute o contador num iframe.
- **D12 — Overlay.** O HTML do `/contador` não é idêntico byte a byte (script/estilo inline proibidos pela CSP), mas o visual e o comportamento são. O JS busca `/api/public/subscribers` com caminho absoluto (o relativo `./inscritos.json` quebraria em `/overlay/inscritos`).
- **D13 — Cookies.** Armazenar só `name`, `value`, `domain`, `path`, `secure` (os 5 campos que o scraper usava; `domain` ausente = `.youtube.com`, `path` ausente = `/`, `secure` ausente = false); demais campos do export são descartados. Mantém-se somente o conjunto mais recente; mudança detectada por `id` em vez de `mtime`. Limites: 200 cookies, 256 KiB de corpo.
- **D14 — Estado dos workers sem texto livre.** `worker_status` guarda códigos de erro de enum fechado + mensagem curta (≤ 200) sanitizada. *Motivo:* o legado logava exceções com a URL (e portanto a chave) — o painel e os logs não podem vazar segredos.
- **D15 — Fontes.** O painel original carrega Bebas Neue e DM Sans do Google Fonts; a CSP (`font-src 'self'`, sem origens externas) bloqueia isso. O front não faz nenhuma requisição a terceiros: as fontes (licença OFL) são hospedadas em `web/static/admin/fonts/`; se o arquivo não puder ser obtido, cai em `Impact`/`sans-serif` (P6).
- **D16 — Chromium.** Playwright controla o navegador por pipe (sem porta de debug). Como o container não-root com `cap_drop: ALL` não permite o sandbox de namespaces do Chromium sem perfil seccomp específico, o worker lança o Chromium com `--no-sandbox` (o legado também usava). *Compensação:* o container é o sandbox (não-root, rootfs somente leitura, sem capabilities, sem acesso à api/web, contexto efêmero em tmpfs). Melhoria futura: perfil seccomp (P5).
- **D17 — Intervalos.** `LIVE_INTERVAL` padrão 30 s (valor do compose atual; o código usava 15 s; mínimo 10 s, consumo ≈ 2.880 chamadas/dia de 10.000 de cota); `SCRAPE_INTERVAL` padrão 10 s (mínimo 5 s). Backoff do `worker-lives` em erro/cota: dobra o intervalo até 900 s e volta ao normal no primeiro sucesso.
- **D18 — Chave por header.** `X-Goog-Api-Key` em vez de `?key=`. *Motivo:* o legado logava `raise_for_status` com a URL completa, incluindo a chave.
- **D19 — URLs-base configuráveis.** As três variáveis opcionais da §4 permitem apontar os workers a um servidor falso nos testes de integração sem nenhum caminho especial de teste no código (regra anti-burla 3): o padrão é o endereço oficial e a configuração é a mesma em produção.
- **D20 — Leitura de inscritos.** Linha única (sem histórico, como o legado); `raw` truncado em 256 caracteres pelo worker.
- **D21 — `PUBLIC_BASE_URL`.** Não-secreta, opcional; usada só para o painel (porta 8081) exibir as URLs públicas corretas em "Endpoints JSON" (no legado era `window.location.origin`, que agora seria a origem do admin).
- **D22 — Compose.** Contexto de build é a raiz do repositório com `dockerfile:` em cada pasta (a api precisa de `db/schema.sql` e `db/admin_schema.sql`); `web` fica também na rede `edge` com `enable_ip_masquerade=false`; `ADMIN_BIND` tem padrão **`127.0.0.1`** (a 8081 só ouve local; o acesso é pelo Nginx Proxy Manager na rede `edge`, ver P1/D32); `TRUSTED_PROXY_CIDR` tem padrão **vazio** (D29); o volume `livestats-admin` é só da api (D2); arquivos de secrets precisam ser legíveis pelo uid do container (`chmod 644` no arquivo, diretório `secrets/` `700` no host). O `docker-compose.yml` é do T7 após o esqueleto, **sem** mudar nomes, redes, mapeamento de secrets, endurecimento e portas (verificados por `INFRA-CFG-*`).
- **D23 — Healthchecks.** A api usa o módulo `app.healthcheck`; cada worker considera saudável um heartbeat com idade < 3×intervalo + 30 s.
- **D24 — Datas dos JSON legados** = UTC, `isoformat()` + `Z` (com microssegundos), para bytes compatíveis. Não usar `datetime.utcnow()` (obsoleto): `datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"` produz o mesmo texto.
- **D25 — Comportamento "dado velho" preservado.** Em erro da API do YouTube o slot continua servindo a última leitura (como hoje).
- **D26 — Testes de front sem a api real.** Em T6 a api ainda não existe; o front é testado contra um servidor-dublê do contrato HTTP (fronteira externa do front), e **toda resposta do dublê é validada contra `openapi.yaml`**. A mesma suíte roda de novo contra a pilha real em T8 (`WEB_BASE_URL`).
- **D27 — Golden files.** `tests/contract/legacy/*` foram gerados pela T1 a partir do algoritmo do código original; ninguém mais pode alterá-los (regra anti-burla 1).
- **D28 — Migração de dados (T9).** Slots atuais são recriados via painel/`PUT`; cookies são colados de novo no painel (nenhum `cookies.json` é copiado para o servidor novo); `perfil_bot/`, `debug.png` e `data/` antigos não migram.
- **D29 — Confiança no `CF-Connecting-IP` (C2 do V0).** `TRUSTED_PROXY_CIDR` é um único IP `/32` (o cloudflared, com IP fixo na `edge`); padrão vazio = ninguém é confiável. O gateway da bridge e a sub-rede inteira da `edge` nunca entram. Verificado por `INFRA-NG-04` e `SEC-05` (cliente com IP fixo na `edge` como "confiável"; requisição pela porta publicada, vinda do gateway, tem o cabeçalho ignorado).
- **D30 — `/api/admin/*` desconhecido (C3 do V0).** Qualquer caminho sob `/api/admin/` (existente ou não), com qualquer método, sem sessão válida → **401** JSON; com sessão válida e rota inexistente → **404** JSON; nunca traceback nem HTML. Uma rota "pega-tudo" na api implementa isso (documentada no `openapi.yaml`).
- **D31 — Fixtures do YouTube são gravadas, não inventadas (C5 do V0).** A T4 grava as respostas de `videos.list` com `tests/worker_lives/record_fixtures.py`, usando a chave real nova em `secrets/youtube_api_key.txt` (fora do Git); o script remove cabeçalhos/campos sensíveis e **falha se a chave aparecer** no arquivo gravado. Sem a chave, a T4 para e relata. A página local do `yta-smooth-counter` da T5 é derivada da estrutura que o scraper original lê (`.counter-value.last`, `.counter-sequence-inner`); o Studio real é validado no V2.
- **D32 — Acesso ao admin (C7; decisão do usuário).** Admin só pela LAN e pelo Tailscale, via Nginx Proxy Manager com HTTPS (Let's Encrypt por desafio DNS no Cloudflare; nome resolvido pelo Pi-hole), lista de acesso do NPM `192.168.15.0/24` + `100.64.0.0/10`; o NPM entra na `livestats-edge` com IP fixo; a 8081 só confia em `X-Forwarded-For` vindo desse IP (`ADMIN_TRUSTED_PROXY_IP`). `ADMIN_BIND` continua `127.0.0.1`. Tailscale Serve fica como alternativa documentada. Configurar NPM/Pi-hole/DNS é da T9.
- **D33 — Admin por IP e porta com TLS próprio na 8081 (C8; decisão do usuário, substitui D32 e a parte de admin da C4).** O usuário quer o admin em `https://<IP-da-LAN>:8081`, sem nome nem NPM. O Nginx do `web` termina TLS na 8081 (`listen 8081 ssl`; TLS 1.2 e 1.3; cifras modernas; `ssl_session_tickets off`; HSTS **não** é enviado, para não prender o IP a um certificado). Certificado e chave autoassinados, gerados **no servidor** na instalação (ferramenta em `deploy/`, T9), com o IP da LAN no SAN, ficam em dois Docker secrets novos montados **só** no `web`: `admin_tls_cert` (`secrets/admin_tls_cert.pem`) e `admin_tls_key` (`secrets/admin_tls_key.pem`); sem eles o `web` não sobe (falha explícita, nunca cai para HTTP). A 8081 passa a ter lista de permissão no Nginx, `ADMIN_ALLOW_CIDRS` (lista separada por espaço, validada como os /32 da D29; padrão `127.0.0.1/32`; no deploy: `192.168.15.0/24 100.64.0.0/10`), com `deny all` para o resto (403 JSON). `ADMIN_BIND` mantém o padrão `127.0.0.1` no repositório; no deploy vale `0.0.0.0` porque a lista de permissão filtra. A 8081 não confia em nenhum cabeçalho de IP (`ADMIN_TRUSTED_PROXY_IP` deixa de existir): o IP de conexão é o real. A 8080 continua em HTTP (o TLS público é da Cloudflare); como no host real o Cloudflare Tunnel entrega ao NPM, que repassa à 8080, o proxy confiável da 8080 é o NPM: `TRUSTED_PROXY_CIDR` recebe o /32 do NPM na `livestats-edge` (a T9 conecta o NPM à rede com IP fixo e aponta o proxy host `lives.vitex.xyz.br` para `livestats-web:8080`). Testes que falavam com `http://...:8081` passam a usar HTTPS aceitando o certificado de teste gerado pela própria suíte.
- **D34 — Overlay de likes com meta e barra por live (C10; pedido do usuário).** Nova funcionalidade, sem quebrar nada existente:
  - **Dados:** `slots` ganha `goal` (inteiro, `NULL` = sem meta, 1 a 1.000.000.000) e `color` (texto `#RRGGBB` em maiúsculas, padrão `#FF0033`). Esquema vai para a versão 2 com **migração idempotente** aplicada pela api na subida (`ALTER TABLE ... ADD COLUMN` só se a coluna não existir; `schema_meta.schema_version` = `2`); bancos novos já nascem na v2. Os workers continuam só lendo o que liam.
  - **Admin:** `GET/PUT /api/admin/slots` passam a carregar, por slot, `url`, `goal` e `color` (mantendo sessão + CSRF). Validação estrita na api: `color` só casa `^#[0-9A-Fa-f]{6}$` (normalizada para maiúsculas), `goal` inteiro no intervalo ou `null`; qualquer outra coisa → 422. O painel ganha, em cada slot, um campo numérico de meta e um `<input type="color">`.
  - **Público:** `/api/public/lives/{n}` (formato novo, objeto) ganha `goal` e `color`. Os legados `/live{n}.json` continuam **byte a byte iguais** (golden files intocados).
  - **Overlay:** `web/static/overlay/live.html?slot=N` (N de 1 a 4; inválido = nada exibido), fundo transparente, mesma animação de carrossel de dígitos do contador de inscritos aplicada aos **likes** (inteiro exato, separador de milhar `.`), no formato `likes/meta` quando há meta (só `likes` quando não há), e uma barra fina embaixo com largura `min(likes/meta, 1)` na cor do slot. Polling a cada 5 s em `/api/public/lives/{n}`. A cor é aplicada por CSSOM (`element.style.setProperty`), o que a CSP permite sem `unsafe-inline`; nenhum dado vira HTML. Servido pela `location ^~ /overlay/` já existente (sem mudança de Nginx); ganha `frame-ancestors *` como os demais overlays (D11).

## 11. Pendências

Itens do código/documentação atual que não cabem no contrato como estavam, ou que dependem de decisão do usuário.

- **P1 — Acesso ao admin com HTTPS — RESOLVIDA por decisão do usuário (C7), com premissas a confirmar na T9.** O cookie é `Secure` (sem TLS próprio e sem opção de desligar). Caminho escolhido: o **Nginx Proxy Manager** do host termina o HTTPS (Let's Encrypt por desafio DNS no Cloudflare), com lista de acesso `192.168.15.0/24` e `100.64.0.0/10`, e alcança `livestats-web:8081` entrando na rede `livestats-edge` com IP fixo; `ADMIN_BIND` permanece `127.0.0.1` e `ADMIN_TRUSTED_PROXY_IP` = o IP fixo do NPM (/32), para que a 8081 enxergue o IP real do cliente (último salto de `X-Forwarded-For`). *Premissas a confirmar na T9 (configuração do NPM, Pi-hole e DNS é da T9):* (1) o domínio está no Cloudflare e o NPM já emite certificado por desafio DNS; (2) o Pi-hole local resolve o nome para o IP da LAN do host; (3) clientes Tailscale resolvem o mesmo nome (DNS do Pi-hole ou rota de sub-rede para `192.168.15.0/24`); (4) o NPM pode ser conectado à rede `livestats-edge` (rede externa com IP fixo). **Alternativa documentada (não recomendada):** Tailscale Serve apontando para `127.0.0.1:8081` (nesse caso `ADMIN_TRUSTED_PROXY_IP` fica vazio).
- **P2 — Consumo cross-origin — RESOLVIDA (C6/D10).** Os GET públicos de dados têm CORS `*` (sem credenciais); páginas em outras origens (ex.: o exemplo do `SETUP.md` §7) voltam a funcionar.
- **P3 — Cloudflared.** Falta confirmar (P0 do PLANO) como o túnel chega ao host. Se o cloudflared rodar neste host, ele entra na rede `livestats-edge` com **IP fixo** (ex.: `172.29.80.10`), apontando para `http://livestats-web:8080`, e só esse `/32` vai em `TRUSTED_PROXY_CIDR`. Se estiver fora, deixar `TRUSTED_PROXY_CIDR` vazio (o limite por IP real não funcionará por visitante: todos contarão como o IP da conexão). O mesmo vale para o NPM e `ADMIN_TRUSTED_PROXY_IP` (ex.: `172.29.80.11`).
- **P4 — Removidos do legado:** índice JSON em `GET /` do yt-inscritos; campo `cookies_file_exists` no `/health` público (agora só no admin); erro 500 por arquivo corrompido (dado vem do banco).
- **P5 — Sandbox do Chromium** com perfil seccomp em vez de `--no-sandbox` (D16).
- **P6 — Fontes** do painel precisam ser baixadas e versionadas pela T6 (OFL); sem internet nesta fase, usar fallback.
- **P7 — Bugs do legado preservados de propósito** (bytes compatíveis): `formatar(999950)` → `"1000.0K"`; `filtrar_numero("1.234.567")` → `"1.234"`; só aceita `watch?v=`, `youtu.be/`, `/live/` e ID puro (não `/shorts/` nem `/embed/`). Podem ser corrigidos depois com aprovação, porque alteram a saída.
- **P8 — `SETUP.md` do yt-inscritos** (Proxmox/LXC, login manual com `perfil_bot`, Selenium) fica obsoleto; a T2 escreve o novo README e a T9 o procedimento de deploy no ZimaOS.
- **P9 — Gestão do admin:** sem UI para trocar senha, sem múltiplos usuários, sem recuperação (apenas novo hash no secret + reinício).
- **P10 — Fora do código:** trocar a chave da API (S1), rotas do Tunnel/NPM e permissões do diretório `secrets/` no host são ações do usuário (P0).
- **P11 — `/live{n}.json` com `n` não numérico** devolve 404 do Nginx em vez do 422 do FastAPI (irrelevante para OBS/vMix).
- **P12 — Compatibilidade Playwright × Selenium:** o contador do Studio muda de seletor com frequência; a equivalência das 3 estratégias só se confirma no V2 (leitura lado a lado com o app atual).
