# Regras anti-burla e plano de testes

## Regras anti-burla (literais do PLANO, seção 3 — vão no briefing de todo subagente)

Proibido, e o validador reprova a tarefa se encontrar:
1. Alterar, apagar ou enfraquecer um teste para ele passar (trocar `==` por `in`, afrouxar asserção, reduzir casos).
2. `skip`, `xfail`, `pytest.mark.skip`, `test.skip`, `# pragma: no cover` em código novo, ou teste que não verifica nada.
3. Caminho especial para teste no código de produção: `if TESTING`, `DISABLE_AUTH`, variável que desliga login/CSRF/rate limit, usuário/senha fixos.
4. Simular (mock) a própria unidade testada. Só pode simular a **fronteira externa** (HTTP do YouTube, relógio), e com respostas reais gravadas.
5. Respostas fixas no código para casar com o teste (hardcode do valor esperado).
6. Capturar exceção e engolir para o teste ficar verde.
7. Declarar "passou" sem colar a saída real do comando de teste.

Se a funcionalidade não puder ser implementada direito, o subagente deve **parar e relatar**, não contornar.

Checagem do validador (Opus) em cada V: ler o diff inteiro, `git diff` dos testes contra a versão aprovada no contrato, busca por padrões proibidos acima, rodar os testes ele mesmo do zero (container limpo) e conferir que cada teste falha quando a funcionalidade é removida (teste de mutação manual em amostra).

---

# Plano de testes — LiveStats

Contrato T1. Lista os testes **obrigatórios**; cada tarefa pode acrescentar outros, nunca remover ou enfraquecer estes. Nada aqui foi executado.

## Como ler

- **ID**: `<SUITE>-<ÁREA>-<NN>`. Os IDs são estáveis; o nome da função de teste deve conter o ID (ex.: `test_api_auth_01_...`), para o validador conferir a cobertura por busca.
- **Falha se (mutação)**: a alteração de produção que deve fazer o teste ficar vermelho. O validador (Opus) aplica uma amostra dessas mutações à mão (regra: "cada teste falha quando a funcionalidade é removida"). Um teste que continua verde sob a mutação é reprovado.
- Vetores de teste de `formatar`, `extrair_video_id` e `filtrar_numero`: tabelas em `docs/legacy-formats.md` §1.3 e §2.1. Golden files: `tests/contract/legacy/` (somente leitura).
- **Fronteiras que podem ser simuladas** (regra 4): HTTP do YouTube Data API (respx, com respostas reais gravadas) e relógio. O banco é SQLite real (arquivo temporário), a criptografia é PyNaCl real, o Chromium é real (T5). Para o front (T6) a fronteira é a API HTTP: o dublê é validado contra `docs/openapi.yaml` (D26) e a mesma suíte roda contra a pilha real em T8.
- Testes `docker`/`e2e` que não encontram o ambiente **falham** (nunca `skip`).
- Os testes de cada suíte devem ser independentes da ordem e criar o próprio banco/estado.

---

## T3 — api (`tests/api/`)

### Autenticação, sessão e CSRF

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| API-AUTH-01 | Toda rota registrada sob `/api/admin/` (enumerada de `app.routes`, **inclusive a rota pega-tudo `/api/admin/{path}`**, exceto `login`) sem cookie de sessão: `GET session`, `GET slots`, `PUT slots`, `POST cookies`, `GET workers/status`, `POST logout` e caminhos inexistentes (`GET /api/admin/nao-existe`, `POST /api/admin/x/y`) | 401 `{"detail":"Não autenticado"}` em todas; o teste falha se surgir uma rota admin nova não coberta | remover a dependência de auth de qualquer rota admin, ou deixar a rota inexistente responder 404 sem sessão |
| API-AUTH-02 | Com sessão válida: `PUT slots`, `POST cookies`, `POST logout` sem `X-CSRF-Token`, com token vazio e com token de outra sessão | 403 nos três casos; com token correto, 2xx; `GET`s funcionam sem token | remover/afrouxar a checagem CSRF (ex.: comparar só presença) |
| API-AUTH-03 | `POST /login` correto: atributos do `Set-Cookie` | nome `__Host-livestats_session`, `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, `Max-Age=28800`, sem `Domain`; corpo traz `csrf_token` e **não** contém o valor do cookie | tirar qualquer atributo do cookie |
| API-AUTH-04 | Usuário inexistente × senha errada | 401 idêntico (status e corpo), sem `Set-Cookie`; ambos executam a verificação argon2 (tempo da mesma ordem de grandeza) | devolver mensagens diferentes ou pular o hash para usuário inexistente |
| API-AUTH-05 | 5 falhas seguidas em < 60 s do mesmo IP/usuário, depois a 6ª (inclusive com a **senha correta**) | 1ª–5ª: 401; 6ª: 429 com `Retry-After`; após avançar o relógio 61 s o login correto funciona; tentativas bloqueadas não prolongam o bloqueio | mudar o limite (6, 4), contar sucesso, não checar bloqueio antes da senha |
| API-AUTH-06 | Limite **por usuário** com IPs diferentes (`X-Real-IP` variando) e **por IP** com usuários diferentes | em ambos, a 6ª falha → 429 | manter só um dos dois contadores |
| API-AUTH-07 | Logout | linha da sessão removida do banco; reuso do cookie antigo → 401; `Set-Cookie` com `Max-Age=0` | não apagar a sessão no servidor |
| API-AUTH-08 | Expiração (relógio): absoluta 8 h e ociosidade 30 min | 401 após cada limite; uso dentro de 30 min renova a ociosidade mas não o limite absoluto | ignorar `expires_at`/`last_seen_at` |
| API-AUTH-09 | Banco após login | em `admin.db`, `admin_sessions.id_hash` = SHA-256 hex do valor do cookie; o valor do cookie não aparece em nenhuma coluna nem nos bytes de `admin.db`, `admin.db-wal`, `livestats.db*` | gravar o id em texto |
| API-AUTH-10 | Fixação de sessão: login enviando um cookie de sessão antigo | novo id emitido; o antigo deixa de valer | reutilizar o id existente |
| API-AUTH-11 | Inicialização sem `ADMIN_PASSWORD_HASH_FILE`, com arquivo vazio, ou com conteúdo que não é argon2 PHC | o processo/app **não sobe** (erro explícito, sem imprimir o conteúdo) | usar valor padrão ou senha fixa |
| API-AUTH-12 | `POST /login` com corpo inválido (sem `password`, tipo errado) | 422 cujo corpo **não** contém a senha enviada nem a chave `input` | devolver o `input` do pydantic |
| API-AUTH-13 | `GET /api/admin/session` autenticado | 200 com `username`, `csrf_token` igual ao do login, `expires_at`, `idle_expires_at`, `public_base_url` (config ou `null`) | omitir o token ou o campo |
| API-AUTH-14 | Senha correta de `ADMIN_USERNAME` com maiúsculas/minúsculas diferentes no usuário | segue a regra definida (comparação exata do usuário); contador de falhas usa o usuário em minúsculo | normalização inconsistente entre login e contador |

### Slots

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| API-SLOT-01 | `GET /slots` em banco novo | 4 itens (`slot` 1..4, `url` `""`, `stats` `null`), `saved_at` `null` | devolver menos itens ou lixo |
| API-SLOT-02 | `PUT` com 4 textos válidos (e espaços nas pontas) | 200 `{ok:true, slots:[...trim...], saved_at}`; linhas de `slots` com o texto sem espaços; `GET` seguinte devolve o mesmo; `saved_at` preenchido | não persistir / não fazer trim |
| API-SLOT-03 | `PUT` com 3 e 5 itens, item não-string, item > 2048 caracteres, caractere de controle | 422 e **nada** gravado (banco igual ao anterior) | aceitar tamanhos diferentes de 4 ou não validar |
| API-SLOT-04 | Vetores de `extrair_video_id` (tabela do legacy-formats §1.3) | os que têm ID são aceitos; `shorts`, `embed`, `abc` → 422 | usar regex diferente |
| API-SLOT-05 | Payload XSS como URL (`<img src=x onerror=alert(1)>`, `javascript:alert(1)`) | 422 e não persiste | armazenar texto arbitrário (V3) |
| API-SLOT-06 | `GET /slots` com linhas em `live_stats` (incluindo título com HTML) | `stats` traz o objeto `LiveSlot` do slot; título devolvido literal (string JSON, sem escape HTML, `Content-Type: application/json`) | misturar slots ou escapar/renderizar HTML |
| API-SLOT-07 | Slots salvos são visíveis em `slots` para um segundo processo/conexão (simula o worker) | leitura direta do SQLite devolve as 4 URLs gravadas | escrever em memória/arquivo fora do banco |

### Cookies (cifragem)

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| API-COOKIE-01 | `POST /cookies` válido (array de cookies com valores-sentinela aleatórios) | 200 `{ok, message, cookies_count}`; 1 linha em `cookie_sets`; os bytes de **todo o arquivo** `.db`, `-wal` e `-shm` não contêm nenhum nome nem valor de cookie | gravar texto puro/base64 do JSON |
| API-COOKIE-02 | Decifrar a linha gravada com a chave **privada** (do teste) | resulta no array normalizado: 5 campos por cookie, valores idênticos aos enviados | cifrar com chave errada, ou outro formato |
| API-COOKIE-03 | A api não decifra | configuração da api só aceita chave pública (nenhuma variável/arquivo de chave privada é lido; busca estática no pacote `app`), e tentar abrir o `SealedBox` usando os bytes da chave pública como se fossem privada levanta erro; leitura SQL de `cookie_sets.ciphertext` pela conexão da api é negada pelo autorizador | api ler/aceitar a privada, ou liberar leitura da coluna |
| API-COOKIE-04 | Validação herdada do legado: corpo não-JSON; `{}`; `[]`; `"x"`; `[{"name":"a"}]`; segundo elemento sem `value` | 400 com os `detail` exatos do openapi (`Cookie #1 sem campo 'name' ou 'value'` com índice base 0) | trocar 400 por 422 ou alterar mensagens |
| API-COOKIE-05 | Normalização: domínio `.[youtube.com](http://youtube.com)` → `.youtube.com`; sem `domain` → `.youtube.com`; sem `path` → `/`; sem `secure` → `false`; campos extras (`httpOnly`, `expirationDate`) descartados | conferido após decifrar | não aplicar a correção |
| API-COOKIE-06 | 422: tipos errados, `name` > 256, `value` > 8192, `domain` com caracteres inválidos, 201 cookies | 422; corpo da resposta **não** contém nenhum valor enviado; nada gravado | ecoar `input`; gravar mesmo assim |
| API-COOKIE-07 | Dois uploads seguidos | 1 única linha em `cookie_sets`, com `id` maior; `cookie_count` do último | acumular linhas |
| API-COOKIE-08 | Nenhuma resposta de GET admin/público contém ciphertext, nomes ou valores de cookie (varre `session`, `slots`, `workers/status`, `/health`, públicos) | ausência dos sentinelas | incluir cookies em `workers/status` |
| API-COOKIE-09 | Logs capturados durante upload válido e inválido | não contêm sentinelas de cookie nem o corpo | logar o corpo/exceção com payload |
| API-COOKIE-10 | Chave pública ausente/corrompida na subida | a app não sobe (erro explícito) — nunca grava cookie sem cifrar | fallback para gravar sem cifrar |
| API-COOKIE-11 | `key_id` gravado | = 16 primeiros hex do SHA-256 da chave pública usada | valor fixo |

### Endpoints públicos e legados

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| API-PUB-01 | `GET /api/public/lives/{1..4}` com linhas semeadas | objeto `LiveSlot` (valida contra `openapi.yaml`), `ativo` é string | devolver booleano |
| API-PUB-02 | slot 0 e 5; `abc`; sem linha | 404 (`Slot inválido (1-4)`); 422; 503 (`Dados ainda não disponíveis`) | códigos diferentes |
| API-PUB-03 | `GET /api/public/subscribers` com e sem leitura | objeto `Subscribers` / 503 com a mensagem do scraper | — |
| API-PUB-04 | Cabeçalhos das respostas públicas | `Cache-Control: no-store`; `Content-Type: application/json` | omitir |
| API-PUB-05 | Requisição com `Origin: https://evil.example` e preflight `OPTIONS` em rotas públicas e de admin, **direto na api** | a api **nunca** emite `Access-Control-*` (o CORS dos GET públicos é do Nginx, evitando cabeçalho duplicado); preflight `OPTIONS` na api → 405/404 sem `Access-Control-*` | reativar CORSMiddleware na api (cabeçalho duplicado com o Nginx) |
| API-PUB-06 | Dados públicos não vazam admin | resposta pública não contém URL de slot, `csrf`, nem campos de worker_status | expor `slots.url` |
| API-PUB-07 | Título `<img src=x onerror=alert(1)>` no banco | devolvido como texto JSON literal | sanitizar/alterar o conteúdo |
| API-LEG-01 | `/live1.json` … `/live4.json` com as linhas que geram os golden files (`live1_ao_vivo`, `live2_agendada`, `live3_encerrada`, `live4_sem_url`) | corpo **byte a byte** igual ao arquivo-ouro; `Content-Type: application/json`; `Cache-Control: no-store` | espaços após `:`/`,`, `\u` escapes, ordem de chaves, `ativo` booleano |
| API-LEG-02 | Ordem das chaves: slot ativo termina em `slot,ativo`; `sem_url` começa com `slot,ativo` | conferido nos golden | serializar sempre na mesma ordem |
| API-LEG-03 | `/live0.json`, `/live5.json`; sem linha | 404 `error_slot_invalido.json` / 503 `error_dados_indisponiveis.json` byte a byte | mensagem diferente |
| API-LEG-04 | `/inscritos.json` com leitura e sem leitura | corpo = `inscritos.json` golden; 503 = `error_inscritos_indisponiveis.json` | idem |
| API-LEG-05 | `/health` | 200; chaves exatamente `status,server_time,data_file_exists,inscritos,last_update`; `server_time` no formato `…Z`; `data_file_exists` reflete a existência da leitura; funciona com banco vazio | exigir dados; incluir `cookies_file_exists` |
| API-LEG-06 | Consistência: `/api/public/lives/n` == elemento 0 de `/live{n}.json`; `/api/public/subscribers` == `/inscritos.json` (byte a byte) | iguais | divergência entre os caminhos |
| API-LEG-07 | Título com acentos, emoji e `<b>`; `atualizado_em` com e sem microssegundos | UTF-8 literal, sem escape; texto preservado | `ensure_ascii=True` |
| API-LEG-08 | `GET /` e `/admin` na api | 404 (a api não serve páginas) | a api servir HTML de painel |

### Banco, segurança e disponibilidade

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| API-DB-01 | Aplicar `db/schema.sql` em `livestats.db` e `db/admin_schema.sql` em `admin.db`, duas vezes cada, em bancos novos | idempotente; `journal_mode=wal` nos dois; tabelas/colunas = `data-model.md` (comparação por `PRAGMA table_info`); `livestats.db` tem 4 linhas em `slots` e **não** tem `admin_sessions` nem `login_attempts`; `admin.db` tem só `schema_meta`, `admin_sessions`, `login_attempts` | divergir do contrato / voltar a criar tabelas de admin no banco compartilhado |
| API-DB-02 | Autorizador da api (**defesa contra bug no próprio código**, ex.: SQL injection; não contra processo comprometido) | por meio do helper de conexão da api, executar SQL "malicioso" simulado (injeção que tenta `SELECT ciphertext FROM cookie_sets`, `INSERT INTO live_stats`, `UPDATE worker_status`) → `not authorized`; as leituras/escritas permitidas funcionam | autorizador ausente ou permissivo |
| API-DB-03 | Isolamento do estado de admin (C1) | após login, logins falhos e logout, **nenhuma** linha de sessão/tentativa existe em `livestats.db` (todas as tabelas varridas); a api recusa subir se `ADMIN_DB_PATH` == `DB_PATH` ou se `ADMIN_DB_PATH` não estiver definido; a api não usa `livestats.db` para sessão em nenhum caminho | gravar sessões no banco compartilhado |
| API-SEC-01 | Respostas de validação (422) em todas as rotas com corpo | itens só com `loc,msg,type` | expor `input`/`ctx` |
| API-SEC-02 | `Cache-Control: no-store` nas respostas admin | presente | omitir |
| API-SEC-03 | Logs de subida e de requisições (login ok/falho) | não contêm senha, hash argon2, chave pública/privada, token CSRF nem id de sessão | logar corpo/cabeçalhos |
| API-SEC-04 | A api não tem cliente HTTP de saída | `httpx`, `requests`, `aiohttp`, `urllib.request`, `socket.create_connection` ausentes do pacote `app` e de `requirements.txt` (varredura estática); ver também `SEC-11` | adicionar dependência de rede |
| API-SEC-05 | IP do cliente | usa `X-Real-IP` quando presente, senão o endereço da conexão; limites por usuário valem mesmo com `X-Real-IP` forjado/rotativo | confiar só no IP |
| API-SEC-06 | Caminhos desconhecidos sob `/api/admin/` (D30): `GET /api/admin/nao-existe`, `POST /api/admin/x`, `DELETE /api/admin/slots/1` | **sem sessão válida: 401 JSON** `{"detail":"Não autenticado"}`; **com sessão válida: 404 JSON**; nunca traceback, HTML nem 500; o mesmo vale com sessão expirada (401) | responder 404 sem sessão (vaza existência) ou 500 |
| API-SEC-07 | Ausência de caminhos de teste no código (`TESTING`, `DISABLE_AUTH`, `SKIP_CSRF`, senhas/usuários literais) | busca estática no pacote `app` e `tools` retorna vazio | existir tal variável |
| API-ADM-01 | `GET /api/admin/workers/status` com linhas de `worker_status`, `cookie_sets` e `subscriber_reading` | corpo valida contra `WorkersStatus`; `cookies.count`/`configured`/`applied` corretos; `state:"unknown"` quando o worker nunca gravou | não refletir o banco |
| API-HEALTH-01 | `/health` com banco inacessível (arquivo removido/bloqueado) | 503 `Error` (contrato do openapi) | mascarar falha de banco |
| API-VAL-01 | Todos os exemplos e respostas dos testes acima validados contra `docs/openapi.yaml` (jsonschema) | respostas conformes aos schemas | resposta fora do schema |

---

## T4 — worker-lives (`tests/worker_lives/`)

Fronteira simulada: HTTP do YouTube (respx), reproduzindo **respostas reais gravadas** de `videos.list` (regra anti-burla 4; C5 do V0), gravadas com `tests/worker_lives/record_fixtures.py` (ver `docs/repo-layout.md` T4): live, upcoming, none/encerrada, sem `likeCount`, sem `concurrentViewers`, vídeo ausente em `items`, chave inválida (400 real). Os erros que não se consegue provocar na API real (`quotaExceeded` 403, 429, 5xx, timeout) usam fixtures marcadas `"synthetic": true` com o corpo no formato documentado pelo Google, listadas no relatório. Relógio injetável. **Sem a chave real em `secrets/youtube_api_key.txt`, a T4 para e relata — não inventa fixtures.**

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| LIV-ID-01 | Vetores de `extrair_video_id` | iguais à tabela (inclusive `None` para shorts/embed/abc e espaço inicial) | regex diferente |
| LIV-FMT-01 | Vetores de `formatar` | iguais à tabela, inclusive `999950 → "1000.0K"` | arredondar diferente |
| LIV-API-01 | 4 slots com 3 vídeos únicos (um repetido) | **exatamente 1** requisição `GET …/videos` com `id` = 3 IDs únicos separados por vírgula e `part=statistics,snippet,liveStreamingDetails` | uma chamada por slot |
| LIV-API-02 | Autenticação da chamada | cabeçalho `X-Goog-Api-Key` = conteúdo do arquivo de segredo; **nenhum** parâmetro `key` na URL | usar `?key=` |
| LIV-MAP-01 | Resposta `live` com `concurrentViewers` | `status=ao_vivo`, `views=concurrentViewers`, `likes=likeCount` | usar viewCount |
| LIV-MAP-02 | `live` sem `concurrentViewers` (ou `"0"`) | `views=viewCount` | confiar em campo ausente |
| LIV-MAP-03 | `upcoming` → `agendada`; `none`/ausente → `encerrada` | status corretos | mapeamento trocado |
| LIV-MAP-04 | `likeCount` ausente | `likes=0`, `likes_formatado="0"` | KeyError/None |
| LIV-MAP-05 | Linha gravada vs. golden | os campos gravados em `live_stats`, serializados pelo formato legado, reproduzem `live1_ao_vivo.json`…`live3_encerrada.json` (mesmos valores de entrada) | campo/valor divergente |
| LIV-SLOT-01 | Slot vazio e slot com URL inválida | linha `sem_url`: `active=0`, `video_id=''`, `title=''`, `views=0`, `likes=0`, `'0'`, reescrita a cada ciclo (`updated_at` muda) | não reescrever |
| LIV-SLOT-02 | Mesmo vídeo em 2 slots | duas linhas, cada uma com seu `slot` | sobrescrever uma pela outra |
| LIV-SLOT-03 | Mudança de URL no banco entre ciclos | o ciclo seguinte usa o novo ID (sem reiniciar) | cache de configuração |
| LIV-ERR-01 | HTTP 500 / timeout / erro de rede | linhas dos slots ativos **inalteradas**; sem exceção fatal; `worker_status.state='degraded'`, `last_error_code` ∈ {`http_error`,`network_error`}, `consecutive_failures` sobe | apagar dados ou encerrar |
| LIV-ERR-02 | 403 `quotaExceeded` e 429 | `last_error_code='quota_exceeded'/'rate_limited'`; intervalo de espera dobra a cada falha (30, 60, 120 … teto 900 s) e volta a `LIVE_INTERVAL` no primeiro sucesso (relógio injetado) | sem backoff |
| LIV-ERR-03 | Vídeo ausente em `items` (removido/privado) | linha do slot inalterada; demais slots atualizados | zerar ou apagar |
| LIV-ERR-04 | Resposta 200 com corpo inválido (não-JSON / sem `items`) | tratado como falha (`invalid_response`), sem gravar lixo | gravar dados vazios |
| LIV-SEC-01 | Chave vem de `YOUTUBE_API_KEY_FILE` | arquivo ausente/vazio → processo termina com código ≠ 0 e mensagem sem o caminho do valor; sem valor padrão | default no código |
| LIV-SEC-02 | Variável de ambiente `YOUTUBE_API_KEY` definida e sem arquivo | ignorada → mesmo erro de LIV-SEC-01 | ler do ambiente |
| LIV-SEC-03 | Logs e `worker_status.last_error_message` durante falhas HTTP (inclusive exceções do httpx) | **não contêm a chave** (sentinela aleatória) nem URL com `key=` | logar `str(exception)` bruto |
| LIV-SEC-04 | Varredura estática de `worker_lives/` e `tests/worker_lives/` | nenhum padrão `AIza[0-9A-Za-z_-]{35}` nem chave literal | chave embutida |
| LIV-SEC-05 | Alvo das requisições | só `YOUTUBE_API_BASE_URL` (padrão oficial); valor `http://` não-loopback ou outro esquema → erro de configuração | aceitar qualquer URL |
| LIV-DB-01 | Autorizador do worker (**defesa contra bug no próprio código**, não contra comprometimento) | por meio do helper de conexão: escrever em `slots`, `cookie_sets`, `subscriber_reading`; ler `cookie_sets` → `not authorized`; ler `slots` e escrever `live_stats`/`worker_status` funcionam | sem autorizador |
| LIV-DB-02 | O worker não tem acesso ao estado de admin | busca estática em `worker_lives/`: nenhuma referência a `admin.db`, `ADMIN_DB_PATH`, `admin_sessions`, `login_attempts` ou `/admin-state`; a configuração do worker só conhece `DB_PATH` | worker tocar o estado de admin |
| LIV-HB-01 | Heartbeat | `worker_status('lives')` atualizado a cada ciclo (também sem novidade), `state='ok'` após sucesso, `interval_seconds` = intervalo configurado | não atualizar |
| LIV-HB-02 | `python -m worker_lives.healthcheck` | exit 0 com heartbeat recente; exit 1 com heartbeat mais velho que 3×intervalo+30 s ou ausente | sempre 0 |
| LIV-CFG-01 | `LIVE_INTERVAL` | padrão 30; valores < 10 rejeitados (erro de configuração); respeitado entre ciclos | ignorar |
| LIV-TS-01 | `updated_at` | UTC, formato `YYYY-MM-DDTHH:MM:SS.ffffffZ` | horário local/sem `Z` |
| LIV-START-01 | Banco ainda sem `schema_meta` | o worker espera e tenta de novo (sem criar tabelas); ao existir, começa | criar esquema por conta própria |
| LIV-FIX-01 | Integridade das fixtures reais | cada arquivo em `tests/worker_lives/fixtures/` tem metadados (`recorded_at`, status HTTP, `source: youtube-data-api-v3`, `synthetic` booleano); só os de erro impossíveis de provocar têm `synthetic: true`; nenhum contém cabeçalhos além de `Content-Type`; nenhum contém o valor de `secrets/youtube_api_key.txt` (se presente) nem o padrão `AIza[0-9A-Za-z_-]{35}`; as fixtures de vídeo têm a estrutura real (`kind: youtube#videoListResponse`, `items[].snippet.liveBroadcastContent`) | fixture escrita à mão sem metadados / com chave |
| LIV-FIX-02 | `record_fixtures.py` falha se a chave aparecer no arquivo a gravar | com um servidor HTTP local (fronteira externa) que devolve uma resposta contendo o valor da chave, o script termina com código ≠ 0 e **não grava** o arquivo; com resposta limpa, grava só corpo + metadados (sem cabeçalhos extras) | remover a verificação |
| LIV-FIX-03 | Origem das fixtures | o script lê a chave **só** de arquivo (padrão `secrets/youtube_api_key.txt`), envia em `X-Goog-Api-Key`, e termina com erro claro e sem gravar nada se o arquivo não existir (nunca usa chave embutida nem variável de ambiente) | default no script |

---

## T5 — worker-inscritos (`tests/worker_inscritos/`)

Chromium e Playwright reais, contra um servidor local (`http://127.0.0.1`, porta efêmera) que serve a página de fixture e simula `accounts.google.com`/login por redirecionamento; URLs-base via `YOUTUBE_WWW_BASE_URL`/`YOUTUBE_STUDIO_BASE_URL`. Os cookies de teste usam `domain` = `127.0.0.1`/`localhost` (vêm cifrados com um par de chaves gerado pelo teste). A página de fixture é **derivada da estrutura que o scraper original lê** (`yta-smooth-counter` com `.counter-value.last`, `.counter-sequence-inner` e `div`s empilhados, ver `legacy-formats.md` §2.2); o Studio real só é validado no V2.

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| SUB-NUM-01 | Vetores de `filtrar_numero` | iguais à tabela do legacy-formats §2.1 (inclui `1.234.567 → 1.234`, `None` para `abc`) | lógica diferente |
| SUB-NUM-02 | Valor com menos de 2 caracteres (`"1"`) | **não** grava; conta como falha | gravar |
| SUB-DOM-01 | Fixture A: dígitos em `.counter-value.last` | lê o número montado (estratégia 1) | seletor errado |
| SUB-DOM-02 | Fixture B: só `.counter-sequence-inner` (último `div` de cada) | estratégia 2 | idem |
| SUB-DOM-03 | Fixture C: só texto no contador | estratégia 3 (`innerText`) | idem |
| SUB-DOM-04 | Fixture com dois `yta-smooth-counter`; o primeiro sem 3 dígitos | usa o segundo; sem nenhum: "contador não encontrado" | só olhar o primeiro |
| SUB-DOM-05 | Fixture animada (dígitos em `div`s empilhados como slot machine) | lê o dígito atual de cada posição, não a soma da pilha | usar `innerText` do container |
| SUB-DOM-06 | Fidelidade da fixture ao scraper original | as fixtures A/B/C contêm `yta-smooth-counter`, `.counter-value.last` (A), `.counter-sequence-inner` com `div`s (B) e texto direto (C), exatamente os seletores listados em `legacy-formats.md` §2.2; o teste falha se uma fixture usar seletor que o scraper não lê | fixture com estrutura inventada |
| SUB-ROT-01 | 30 falhas seguidas (contador ausente) | recarrega a página (o servidor de fixture conta uma nova navegação) e zera as falhas; antes de 30, não recarrega | limite diferente / sem reload |
| SUB-ROT-02 | Troca de cookies (`cookie_sets.id` novo) | recarrega com os novos cookies; `cookies_applied_id` atualizado; `falhas=0` | ignorar mudança |
| SUB-WR-01 | Leitura válida | `subscriber_reading` (id=1) com `value`, `raw` e `read_at`; a cada ciclo `read_at` muda mesmo com o mesmo valor; `raw` truncado a 256 | gravar só quando muda |
| SUB-WR-02 | Saída vista pela api | linha gravada, serializada no formato legado, reproduz `inscritos.json` golden (mesmos valores de entrada) | campos/ordem divergentes |
| SUB-CK-01 | Cookies cifrados pelo teste (SealedBox da chave pública) | o worker decifra com `COOKIE_PRIVATE_KEY_FILE` e o servidor de fixture recebe, na requisição ao "Studio", exatamente `name=value` de cada cookie (cabeçalho `Cookie`) | não injetar / injetar só alguns |
| SUB-CK-02 | Cookies sem `cookie_sets` | `auth_state='no_cookies'`, sem crash, tenta de novo a cada ciclo | exceção fatal |
| SUB-CK-03 | Ciphertext cifrado com outra chave / corrompido | `auth_state='decrypt_failed'`, `last_error_code='decrypt_failed'`; sem crash; **sem** texto puro em logs/banco | engolir erro e fingir `ok` |
| SUB-CK-04 | Domínio com markdown `.[youtube.com](http://…)` e `domain` ausente | corrigido para `.youtube.com` (padrão) | não normalizar |
| SUB-CK-05 | Só 5 campos usados | cookie com `httpOnly/sameSite/expirationDate` injetado sem esses atributos (como o Selenium legado) | repassar tudo |
| SUB-AUTH-01 | Studio redireciona para `accounts.google.com`/URL com `signin` | `auth_state='login_required'`, `state='error'`, `last_error_code='login_required'`; **nenhum** valor gravado em `subscriber_reading` | gravar valor da página de login |
| SUB-AUTH-02 | Voltando a ter cookies bons | recupera: `auth_state='ok'`, `state='ok'` | ficar preso em erro |
| SUB-AUTH-04 | Studio abre em `studio.youtube.com` mas mostra "Ops, você não tem permissão…" / "you don't have permission…" (pt-BR e inglês), sem contador | `state='error'`, `auth_state='ok'`, `last_error_code='config_error'`, mensagem constante de permissão; nada gravado em `subscriber_reading` (último valor fica); WARNING sem texto da página; conta para o reload de 30 | mostrar só "contador não encontrado"; gravar valor |
| SUB-AUTH-05 | Página com o contador e a palavra "permissão" em outro lugar | lida normalmente (`state='ok'`); não é tratada como sem permissão | falso positivo que bloqueia leitura válida |
| SUB-SEC-01 | Sem porta de debug | argumentos de lançamento não contêm `--remote-debugging-port`; nenhuma conexão TCP aceita em 9222 durante a execução | manter 9222 (V7) |
| SUB-SEC-02 | Screenshot | por padrão, **nenhum** arquivo de imagem é criado; com `DEBUG_SCREENSHOT=1` grava em `/tmp/debug` (ou diretório temporário), nunca sob `/data` nem sob pasta servida | gravar sempre / em `/data` |
| SUB-SEC-03 | Logs durante cookies válidos, inválidos e falha de `add_cookies` | não contêm valores nem nomes de cookie | logar o cookie |
| SUB-SEC-04 | Chave privada | vem só de `COOKIE_PRIVATE_KEY_FILE`; ausente → processo termina com erro; não lê de variável de ambiente | default/ambiente |
| SUB-SEC-05 | Contexto efêmero | nenhum diretório de perfil persistente (`perfil_bot`, `user_data_dir`) é criado; nenhum arquivo é escrito fora de `/tmp` e do home em tmpfs | persistir perfil (S4) |
| SUB-SEC-06 | Varredura estática de `worker_inscritos/` e `tests/worker_inscritos/` | nenhum ID de canal real (`UC[0-9A-Za-z_-]{22}` fora do placeholder `UCxxxxxxxxxxxxxxxxxxxxxx`), nenhum cookie real | ID embutido (S5) |
| SUB-SEC-07 | URLs-base | só `https`, ou `http` para `localhost`/`127.0.0.1`/`::1`; outro → erro de configuração | aceitar qualquer destino |
| SUB-CFG-01 | `YOUTUBE_CHANNEL_ID` | obrigatório, formato `UC` + 22 caracteres; ausente/inválido → erro; URL do Studio igual, caractere a caractere, à do legado (com o placeholder) | default no código |
| SUB-CFG-02 | `SCRAPE_INTERVAL` | padrão 10; < 5 rejeitado; respeitado | ignorar |
| SUB-BR-01 | Navegador | `navigator.webdriver` indefinido na fixture; user-agent igual ao do legado; janela 1280x900; headless | detecção de automação à mostra |
| SUB-DB-01 | Autorizador do worker (**defesa contra bug no próprio código**, não contra comprometimento) | ler `cookie_sets` e escrever `subscriber_reading`/`worker_status` funcionam; escrever `slots`/`live_stats`/`cookie_sets`, ler qualquer outra tabela → `not authorized` | sem autorizador |
| SUB-DB-02 | O worker não tem acesso ao estado de admin | busca estática em `worker_inscritos/`: nenhuma referência a `admin.db`, `ADMIN_DB_PATH`, `admin_sessions`, `login_attempts` ou `/admin-state` | worker tocar o estado de admin |
| SUB-HB-01 | Heartbeat e `healthcheck` | como LIV-HB-01/02 para a linha `inscritos` | idem |
| SUB-START-01 | Banco sem `schema_meta` | espera, não cria tabelas | criar esquema |

---

## T6 — front (`tests/web/`, Playwright; roda contra o dublê validado e, em T8, contra a pilha real)

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| WEB-XSS-01 | Dublê/pilha devolve `titulo` = `<img src=x onerror=alert(1)>` no slot 1 | o painel mostra exatamente esse texto; **nenhum** `dialog` (alert) disparado; nenhum `<img>` criado dentro do card; `window.__xss` indefinido | trocar `textContent` por `innerHTML` (V3) |
| WEB-XSS-02 | Mais payloads: `"><script>…`, `<svg onload=…>`, `javascript:` na URL do slot, HTML em mensagens de erro do servidor (`detail`) e em `username` | tudo renderizado como texto | idem |
| WEB-CSP-01 | Varredura estática de `web/static/**` | sem `<script>` inline, sem atributos `on*=` e `style=`, sem `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, `setTimeout("string")`, sem `<link>`/`<script>` externos; todos os `.js` com `// @ts-check` | existir qualquer um |
| WEB-CSP-02 | Páginas carregadas num servidor de teste que envia a CSP do contrato (`ARCHITECTURE.md` §7) | zero violações de CSP no console, painel e overlay funcionais | depender de inline/`unsafe-inline` |
| WEB-NET-01 | Todas as requisições feitas pelas páginas (admin e overlay) | só para a própria origem (nada em `fonts.googleapis.com` etc.) | manter link do Google Fonts |
| WEB-LOGIN-01 | Visita sem sessão | só o formulário de login; nenhuma chamada a `/api/admin/slots` ou `/workers/status`; título "LiveStats" | mostrar painel sem sessão |
| WEB-LOGIN-02 | Senha errada (401) e bloqueio (429 com `Retry-After`) | mensagens claras e distintas ("Credenciais inválidas", "Muitas tentativas…"); campo de senha limpo | mensagem genérica única / senha mantida |
| WEB-LOGIN-03 | Login ok | painel aparece; `X-CSRF-Token` enviado em `PUT slots`, `POST cookies`, `POST logout` (o dublê rejeita sem ele); o token **não** está em `localStorage`, `sessionStorage`, `document.cookie` nem no DOM | persistir o token |
| WEB-LOGIN-04 | 401 durante a sessão (expirou) | volta ao login sem recarregar dados antigos; painel limpo | continuar mostrando dados |
| WEB-LOGIN-05 | Logout | chama `POST /logout` com CSRF e volta ao login | só esconder o painel |
| WEB-SLOT-01 | Painel carregado | 4 cartões "Slot 1..4", `placeholder` `https://youtube.com/watch?v=...`, valores vindos de `GET /slots`; botão "Salvar URLs" | campos ausentes |
| WEB-SLOT-02 | Salvar | `PUT /api/admin/slots` com `{slots:[4 strings]}`; aparece "✓ Salvo com sucesso" e some em 2,5 s; 3 s depois recarrega estatísticas (relógio do Playwright) | não enviar / sem feedback |
| WEB-SLOT-03 | Estados do cartão | ponto de status com classe `ao_vivo`/`encerrada`/`agendada`/sem classe; com `views>0` mostra `views_formatado`/`likes_formatado` e rótulos "Views"/"Likes"; sem dados: "Aguardando dados..." (com URL) ou "Sem dados — insira uma URL" (sem URL); título presente como texto | classes/mensagens trocadas |
| WEB-SLOT-04 | Atualização periódica | polling a cada 15 s (relógio do Playwright avança 15 s → nova chamada a `GET /slots`) **sem** apagar o texto que o usuário está digitando nos campos | recarregar sobrescrevendo edição |
| WEB-SLOT-05 | Erro 422 ao salvar (URL inválida) | mensagem de erro visível (texto); nada de "Salvo" | ignorar erro |
| WEB-SLOT-06 | Lista "Endpoints JSON" | 4 linhas `GET` com `{public_base_url ou origem}/live{n}.json` | lista errada |
| WEB-CK-01 | Painel de cookies: texto de instruções do EditThisCookie, textarea | presentes | — |
| WEB-CK-02 | Validação no navegador: vazio; JSON malformado; JSON que não é array | mensagens "Cole o JSON dos cookies antes de salvar.", "JSON inválido: …", "JSON inválido: Deve ser um array JSON"; **nenhuma** requisição enviada | enviar mesmo assim |
| WEB-CK-03 | Envio válido | `POST /api/admin/cookies` com o **array** como corpo e `X-CSRF-Token`; mostra `message` do servidor; limpa o textarea; atualiza o status (sem `location.reload`) | enviar objeto/sem CSRF; manter segredo na tela |
| WEB-CK-04 | Erros do servidor (400/401/403/422/503) | mensagem do `detail` como texto; em 401 volta ao login | engolir |
| WEB-CK-05 | Cookies nunca reaparecem | após enviar, nenhum texto do cookie no DOM, `localStorage`, `sessionStorage` | guardar rascunho |
| WEB-ST-01 | Status | "Inscritos atuais", "Última leitura", badge "✅ N cookies carregados" ou "❌ Nenhum cookie — scraper não autenticado"; estado de cada worker; aviso destacado quando `auth_state=login_required` ("cookies expirados") | campos faltando |
| WEB-OV-01 | Overlay `/contador` e `/overlay/inscritos`: estrutura | com valor `460600`: container `#contador`, 6 `.digito-wrap`, cada `.digito-inner` com 10 `span`; com `460.600`: 6 dígitos + 1 `.separador`; `,` idem; transform final de cada dígito = `translateY(-<d>em)` | estrutura diferente |
| WEB-OV-02 | Estilos computados vs. original (`tests/contract/legacy/contador.original.html`) | fundo transparente, `font-size 80px`, `font-weight 700`, cor branca, `letter-spacing -2px`, `drop-shadow(0 2px 8px rgba(0,0,0,.5))`, `.digito-wrap` altura `1em` com `overflow:hidden` | CSS alterado |
| WEB-OV-03 | Comparação visual: servir o original e o novo com o **mesmo** JSON (`460600`, `460.600`, `1.234.567`), esperar as animações, capturar screenshot com fundo magenta | diferença de pixels = 0 | visual diferente |
| WEB-OV-04 | Animação: mudança `460600 → 460601` (mesma estrutura) | só o último dígito muda `transform`, com `transition: transform 0.45s cubic-bezier(0.25,0.46,0.45,0.94)`; `99999 → 100000` (estrutura diferente) reconstrói com atrasos de 40 ms por dígito e 0,5 s | recriar sempre ou sem animação |
| WEB-OV-05 | Polling (relógio do Playwright) | uma requisição a `/api/public/subscribers?t=<ms>` na carga e outra a cada 5000 ms; resposta não-OK e valor igual/vazio são ignorados sem erro; exceção só gera `console.warn` | intervalo ≠ 5000 |
| WEB-OV-06 | HTML do overlay | sem script/estilo inline; CSS e JS do mesmo domínio em `/overlay/`; `<title>Inscritos</title>`; fundo transparente | inline |
| WEB-A11Y-01 | Painel e login | campos com `label`/`aria-label`, foco visível, navegação por teclado, `lang="pt-BR"` | — |
| WEB-RESP-01 | Largura 375 px | sem rolagem horizontal; `slots-grid` em 1 coluna (< 600 px) | layout quebrado |

---

## T7 — infra (`tests/infra/`)

Testes marcados `static` rodam sem Docker (parse de YAML, Dockerfile e `nginx.conf` por texto/estrutura). Os `docker` (`nginx -t`, `docker compose config`, build) rodam onde há Docker (T8/V2).

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| INFRA-CFG-01 | `docker-compose.yml` | YAML válido; `name: livestats`; exatamente os serviços `web`, `api`, `worker-lives`, `worker-inscritos`; `docker compose config` ok (marcador `docker`) | renomear/remover serviço |
| INFRA-CFG-02 | Endurecimento de **cada** serviço | `user` numérico ≠ 0; `read_only: true`; `cap_drop` contém `ALL` e não há `cap_add`; `security_opt` contém `no-new-privileges:true`; sem `privileged`, `network_mode: host`, `pid: host`, `devices`; sem montagem de `docker.sock` | tirar qualquer item |
| INFRA-CFG-03 | Redes | `internal`: `internal: true`, membros exatos `{web, api}`; `api` só em `internal`; `egress` = `{worker-lives, worker-inscritos}`; `web` ∉ `egress`; workers ∉ `internal`/`edge` | api em rede com saída |
| INFRA-CFG-04 | Secrets | `youtube_api_key`→apenas `worker-lives`; `cookie_private_key`→apenas `worker-inscritos`; `cookie_public_key` e `admin_password_hash`→apenas `api`; `web` sem secrets; arquivos em `./secrets/*.txt` | segredo em outro serviço |
| INFRA-CFG-05 | Sem segredo em `environment` | nenhuma variável de ambiente com nome contendo `KEY`, `PASSWORD`, `TOKEN`, `COOKIE`, `SECRET`, `HASH` com **valor** literal (só `*_FILE` com caminho `/run/secrets/...`); nenhum valor com padrão `AIza…` | `YOUTUBE_API_KEY=…` |
| INFRA-CFG-06 | Portas | `web` publica exatamente 8080 e 8081; a 8081 usa `${ADMIN_BIND:-127.0.0.1}` (**padrão `127.0.0.1`**, nunca `0.0.0.0`); nenhum outro serviço tem `ports`; nada em 9222/8000/8001; `.env.example` traz `ADMIN_BIND=127.0.0.1` | padrão `0.0.0.0` |
| INFRA-CFG-07 | Volumes | `livestats-data` montado em `/data` (rw) apenas por `api`, `worker-lives`, `worker-inscritos`; `web` sem volumes; nenhum bind-mount de código-fonte | web com /data |
| INFRA-CFG-08 | Chromium | `worker-inscritos`: `shm_size` ≥ 256mb, `tmpfs` para `/tmp`; nenhum arquivo do repositório contém `--remote-debugging-port` | — |
| INFRA-CFG-09 | `healthcheck` e `depends_on` | todos os serviços têm healthcheck; workers e web dependem de `api` saudável | sem healthcheck |
| INFRA-CFG-10 | Metadados ZimaOS | bloco `x-casaos` presente com título "LiveStats", porta de entrada 8080/8081 e ícone (sem segredos) | ausente |
| INFRA-CFG-11 | Volume de admin exclusivo (C1) | volume `livestats-admin` definido e montado em `/admin-state` **somente** no serviço `api`; ausente em `web`, `worker-lives`, `worker-inscritos`; a api tem `ADMIN_DB_PATH` dentro de `/admin-state` e `DB_PATH` dentro de `/data` | admin.db no volume compartilhado |
| INFRA-CFG-12 | Confiança do proxy (C2) | `TRUSTED_PROXY_CIDR` no compose e no `.env.example` tem padrão **vazio**; se um valor de exemplo for dado, é um único `/32` e nunca termina em `.1` (gateway) nem é a sub-rede inteira (`/24` etc.) | padrão com a sub-rede |
| INFRA-CFG-13 | Lista de IPs do admin (D33, substitui a confiança de proxy da C7) | `ADMIN_ALLOW_CIDRS` no compose tem padrão `127.0.0.1/32` e está no `.env.example`; `ADMIN_TRUSTED_PROXY_IP` **não existe** em lugar nenhum (compose, `.env.example`); `ADMIN_BIND` continua `127.0.0.1`; só o `web` recebe a variável | padrão aberto ou variável antiga mantida |
| INFRA-CFG-14 | Secrets TLS do admin (D33) | `admin_tls_cert` e `admin_tls_key` existem em `secrets:` apontando para `./secrets/admin_tls_cert.pem` e `./secrets/admin_tls_key.pem`, e são montados **somente** no `web`; nenhum certificado/chave literal no compose | secret montado em outro serviço ou valor literal |
| INFRA-DF-01 | Dockerfiles | multi-stage; `FROM` com `@sha256:`; `USER` numérico não-root no estágio final; `HEALTHCHECK`; `pip install` com `--require-hashes`; sem `ARG`/`ENV` de segredo; sem `COPY` de `secrets/`, `.env`, `codigo-original`, `tests` | tag móvel (`latest`/`current`) |
| INFRA-DF-02 | `/data` | imagens api e workers criam `/data` `root:10001` modo `2770` | permissões erradas |
| INFRA-DF-03 | `.dockerignore` | exclui `secrets/`, `.env*`, `codigo-original/`, `data/`, `.git`, `tests/`, `*.tar.gz` | omissão |
| INFRA-DF-04 | Versões fixas | requirements com `==` e hashes; Playwright com versão do Chromium fixada pela biblioteca | `>=`/sem hash |
| INFRA-NG-01 | `nginx.conf` | dois `server`: `listen 8080` e `listen 8081`; **no 8080 não existe** `location` para `/admin`, `/admin-assets`, `/api/admin`, `/api/admin/login` (resposta 404 por padrão `return 404`) | rota admin na 8080 |
| INFRA-NG-02 | Cabeçalhos | CSP do contrato (sem `unsafe-inline`/`unsafe-eval`, com `frame-ancestors`), `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `always` em todos os blocos, inclusive páginas de erro e 429; overlays com `frame-ancestors *`, resto `'none'` | cabeçalho ausente |
| INFRA-NG-03 | `limit_req` | zonas `login` (por IP real; permite ≥ 6 requisições seguidas e bloqueia depois), `admin` e `public`; `limit_req_status 429`; corpo JSON de 429 | sem limite |
| INFRA-NG-04 | IP real (C2) | `real_ip_header CF-Connecting-IP` e `set_real_ip_from` com o valor de `TRUSTED_PROXY_CIDR` **somente** no `server` 8080; com `TRUSTED_PROXY_CIDR` vazio **nenhuma** diretiva `set_real_ip_from` é emitida (cabeçalho sempre ignorado); valor com máscara diferente de `/32` (ou `0.0.0.0/0`, sub-rede, IP final `.1`) faz a renderização do template **falhar**; no 8081 a regra é a de `INFRA-NG-09`; `proxy_set_header X-Real-IP $remote_addr` e `X-Forwarded-For` sobrescritos (não repassam cabeçalho do cliente) | confiar em sub-rede/qualquer origem |
| INFRA-NG-05 | Endurecimento | `server_tokens off`; `client_max_body_size` ≤ 256k; métodos limitados a GET/HEAD/OPTIONS (e POST/PUT nas rotas admin); sem `autoindex`; `Access-Control-Allow-*` só como em `INFRA-NG-08` | CORS fora do escopo permitido |
| INFRA-NG-06 | Mapa de rotas do proxy | 8080 encaminha à api **somente** `/api/public/`, `/live[0-9]+.json`, `/inscritos.json`, `/health`; 8081 acrescenta `/api/admin/`; estáticos mapeados como no `repo-layout.md` §2 (T6); qualquer outra rota → 404 | proxy aberto (`location /`) |
| INFRA-NG-07 | `nginx -t` com a configuração renderizada (`TRUSTED_PROXY_CIDR` definido) | sintaxe ok (marcador `docker`) | conf inválida |
| INFRA-NG-08 | CORS no Nginx (C6) | **somente** nas locations de `/live[0-9]+.json`, `/inscritos.json` e `/api/public/`: `add_header Access-Control-Allow-Origin "*" always` e `Access-Control-Allow-Methods "GET" always`, e `OPTIONS` → `return 204` com os mesmos dois cabeçalhos; em **nenhuma** location há `Access-Control-Allow-Credentials`, nem `Access-Control-Allow-*` em `/api/admin/`, `/health`, `/contador`, `/overlay/`, `/`, `/admin*` | CORS global / `Allow-Credentials` |
| INFRA-NG-09 | 8081 ignora cabeçalhos de IP (D33, substitui a C7) | a 8081 **não** tem diretiva `real_ip` (nenhuma lê `X-Forwarded-For`/`CF-Connecting-IP`); em container real, qualquer origem (inclusive o IP confiável da 8080 e o gateway do host) com `X-Forwarded-For`/`CF-Connecting-IP` forjados é vista pelo IP da conexão: a api recebe `X-Real-IP` = IP de conexão e variar o cabeçalho não escapa do limite de login; o `server` 8080 não usa `X-Forwarded-For` | confiar em cabeçalho de IP na 8081 |
| INFRA-NG-10 | `ADMIN_ALLOW_CIDRS` (D33) | renderização: lista válida gera `allow <cidr>;` por item (IP sem máscara vira `/32`) mais `deny all;` no `server` 8081 e as mesmas entradas no `geo` avaliado no `rewrite` do `server`; vazia/ausente ⇒ só `127.0.0.1/32`; a 8080 não ganha lista; valores perigosos (`0.0.0.0/0`, prefixo < 8, `0.x`, texto, IPv6, bits de host preenchidos, zero à esquerda, octeto > 255, injeção de diretiva, quebra de linha, item bom junto de item ruim) **abortam a subida** com `15-livestats-realip: ERRO ... ADMIN_ALLOW_CIDRS` | aceitar `0.0.0.0/0` ou texto |
| INFRA-NG-11 | TLS obrigatório na 8081 (D33) | `nginx -T`: `listen 8081 ssl`, `ssl_protocols TLSv1.2 TLSv1.3`, `ssl_session_tickets off`, certificado/chave em `/run/secrets/admin_tls_*`, nenhum HSTS; **sem** um dos secrets, secret vazio, não-PEM ou chave ilegível pelo uid 101 o `web` **não sobe** (erro explícito no log, nunca cai para HTTP) | subir em HTTP sem os secrets |
| INFRA-NG-12 | 8081 só em TLS (D33, container real) | TLS 1.2 e 1.3 aceitos (cadeia e IP no SAN verificados), TLS ≤ 1.1 recusado; nenhuma resposta da 8081 (200/401/404/429) traz `Strict-Transport-Security`; HTTP simples na 8081 recebe `400` JSON sem rota nem `Set-Cookie`; a 8080 continua em HTTP, não fala TLS e não tem rota de admin | 8081 aceitando HTTP, HSTS enviado |
| INFRA-NG-13 | Lista de IPs em container real (D33) | origem dentro da lista: painel e `/api/admin/*` funcionam; fora: `403` JSON (`{"detail":"Forbidden"}`) com os cabeçalhos de segurança em **todas** as rotas da 8081 (inclusive 404 e `return`), sem chegar à api, mesmo com `X-Forwarded-For`/`CF-Connecting-IP` de IP permitido; a 8080 não tem lista | `deny` que não cobre `location` com `return` |
| INFRA-NG-14 | Configuração estática da 8081 (D33) | `listen 8081 ssl default_server`; cifras sem CBC/RC4/3DES; `if ($livestats_admin_allowed = 0) { return 403; }` antes de qualquer `location`; `deny all`; `error_page 497 =400 @e400`; nenhuma diretiva `real_ip` na 8081; sem `Strict-Transport-Security` em nenhum arquivo do Nginx | TLS fraco ou lista depois das locations |
| INFRA-ZOS-01 | Compose roda como usuário não-root em volumes novos | `docker compose up` do zero (com os 4 segredos de teste e o par TLS de teste gerado na hora) sobe os 4 serviços saudáveis (marcador `docker`) | permissão de `/data` |

---

## T8 — integração e segurança (`tests/e2e/`, `tests/security/`)

Pilha completa via `docker compose` (arquivo de teste em `tests/e2e/`) com: segredos **gerados na hora** com sentinelas aleatórias (nunca a chave real), servidor falso do YouTube (respostas gravadas) apontado por `YOUTUBE_API_BASE_URL`, e página falsa do Studio por `YOUTUBE_*_BASE_URL`. Os testes do PLANO §4 são os `SEC-*`.

### Segurança (PLANO §4)

| ID | Item do PLANO §4 | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| SEC-01 | Toda rota `/api/admin/*` | sem sessão → 401; com sessão e sem CSRF (métodos que alteram estado) → 403 — pela porta 8081, atravessando o Nginx | remover auth/CSRF |
| SEC-02 | Rate limit de login | 6ª tentativa errada em 1 min → 429 (via Nginx → api); a 7ª pode ser 429 do Nginx | afrouxar qualquer camada |
| SEC-03 | XSS armazenado de ponta a ponta | servidor falso do YouTube devolve título `<img src=x onerror=alert(1)>`; `worker-lives` grava; painel (Playwright, 8081) mostra como texto e **nenhum alert** dispara | `innerHTML` |
| SEC-04 | Porta pública não tem admin | em 8080: `/`, `/admin`, `/admin/`, `/admin-assets/index.html`, `/api/admin/session`, `/api/admin/slots`, `/api/admin/cookies`, `/api/admin/workers/status`, `/api/admin/login` (GET e POST) → **404** | rota admin na 8080 |
| SEC-05 | `limit_req` por IP real (8080) e confiança só no `/32` (C2) | pilha de teste com `TRUSTED_PROXY_CIDR` = IP fixo de um **container cliente** na `edge` (o "cloudflared" do teste): por esse cliente, `CF-Connecting-IP: 198.51.100.1` estoura o limite → 429 enquanto `198.51.100.2` ainda recebe 200 (limites separados). Requisições com o mesmo cabeçalho vindas de **qualquer outra origem** — outro container na `edge` com IP diferente, o host pela porta publicada (aparece com o IP do **gateway** da bridge) — têm o cabeçalho **ignorado** (dois valores diferentes compartilham o mesmo limite). Com `TRUSTED_PROXY_CIDR` vazio, o cabeçalho é ignorado para todos | ignorar o cabeçalho / confiar na sub-rede ou no gateway |
| SEC-06 | Cabeçalhos | em respostas 200, 404 e 429 de ambas as portas: CSP sem `unsafe-inline`/`unsafe-eval`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `frame-ancestors` | remover cabeçalho |
| SEC-07 | `docker inspect` | cada container: `Config.User` não-root e numérico, `HostConfig.ReadonlyRootfs=true`, `CapDrop=[ALL]` e `CapAdd` vazio, `SecurityOpt` com `no-new-privileges`, `Privileged=false`; `docker exec id -u` ≠ 0 | root / rootfs gravável |
| SEC-08 | Chave da API (sentinela) fora de lugares indevidos | ausente em `docker history --no-trunc` de todas as imagens, em `docker inspect` (env, mounts) de todos, nos logs de todos os containers, e no sistema de arquivos e `/proc/*/environ` do container `api` (e `web`); presente **apenas** em `/run/secrets/youtube_api_key` do `worker-lives` | chave em env/imagem/log |
| SEC-09 | A api não decifra cookies | `/run/secrets` da api contém só `cookie_public_key` e `admin_password_hash`; `cookie_private_key` não existe no `api` nem no `worker-lives`; dentro da api, tentar decifrar o ciphertext (lido do volume) com qualquer chave disponível falha | privada na api |
| SEC-10 | gitleaks e Trivy | gitleaks (`detect`, histórico completo) sem achados; Trivy (fs + cada imagem, HIGH/CRITICAL) sem achados; sem arquivo de ignorados | segredo/CVE presente |
| SEC-11 | Isolamento de rede | da api: conexão a IP público (443) e resolução DNS externa **falham**; dos workers: internet alcançável (servidor falso externo ao compose), mas `api`/`web` inalcançáveis por nome/IP; do `web`: sem saída | api com internet |
| SEC-12 | Sem porta de debug e sem portas extras | do host: 9222 fechada; `docker port` mostra apenas 8080 e 8081; dentro do `worker-inscritos` nenhum socket em escuta | porta aberta (V7) |
| SEC-13 | Segredos não estão no ambiente | `docker inspect` `Config.Env` de todos sem sentinelas e sem variável `YOUTUBE_API_KEY`/`COOKIE_*KEY` com valor | segredo em env |
| SEC-14 | Cookies cifrados no disco | após upload de cookies com sentinelas, `grep` em `/data/livestats.db*` (volume) não encontra nome nem valor; ciphertext decifra no `worker-inscritos` | texto puro |
| SEC-15 | Sessão | o id do cookie não aparece em `/admin-state/admin.db*` (volume `livestats-admin`) nem em `/data/livestats.db*`; em `admin.db` só o SHA-256 | id em claro |
| SEC-16 | CORS (C6) | **rotas públicas de dados** (`/live1..4.json`, `/inscritos.json`, `/api/public/lives/1`, `/api/public/subscribers`) em 8080 e 8081, com `Origin: https://qualquer.example`: `Access-Control-Allow-Origin: *` e `Access-Control-Allow-Methods: GET`, também nas respostas 404/503; `OPTIONS` (preflight) → 204 com esses dois cabeçalhos; **demais rotas** (`/health`, `/contador`, `/overlay/inscritos`, `/`, `/admin`, `/api/admin/*`) com o mesmo `Origin`: nenhum `Access-Control-Allow-*` | CORS ausente nos públicos, ou presente nas demais |
| SEC-17 | Cookie de sessão via Nginx | `Set-Cookie` com `__Host-livestats_session`, `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` (cliente HTTPS simulado ou `localhost`) | atributo ausente |
| SEC-18 | Permissões dos volumes | arquivos de `/data` sem acesso de "outros", `/data` `2770`; `/admin-state` `0700` dono `10001`; os 3 containers leem/escrevem `/data`; só a api acessa `/admin-state` | 777 ou falha de permissão |
| SEC-19 | Segredos fora do Git | `git ls-files secrets` lista só `secrets/README.md`; nenhum `.env`; nenhuma ocorrência de `AIza[0-9A-Za-z_-]{35}` ou de ID de canal real no repositório rastreado | arquivo de segredo versionado |
| SEC-20 | Estado de admin isolado dos workers (C1) | `docker inspect` mostra `livestats-admin` montado só na `api`; em `worker-lives` e `worker-inscritos`, `find / -name 'admin.db*'` vazio; "worker comprometido" simulado: com acesso de escrita a `/data/livestats.db`, inserir linhas forjadas em qualquer tabela e tentar entrar com um cookie de sessão cujo hash o atacante escolheu → **401** (a api não consulta o banco compartilhado para sessão) | sessões no volume compartilhado |
| SEC-21 | `ADMIN_BIND` seguro por padrão (C4) | na pilha subida sem `ADMIN_BIND`: `docker port livestats-web` mostra `8081/tcp -> 127.0.0.1:8081`; conexão ao IP da LAN/outro container na porta 8081 do host é recusada; a 8080 continua acessível; com `ADMIN_BIND=<IP>` a 8081 responde nesse IP | padrão `0.0.0.0` |
| SEC-22 | Cookie `Secure` sem opção de desligar; TLS só com secrets (C4, ajustado pela D33) | busca em compose, `.env.example`, nginx e código da api: nenhuma variável ou opção que remova `Secure`, `HttpOnly` ou `SameSite`; o TLS do Nginx (permitido pela D33) só usa `/run/secrets/admin_tls_*`, só `TLSv1.2 TLSv1.3`, sem `ssl off`; nenhum `.pem`/`.key`/`.crt` rastreado no repositório | opção de desligar `Secure`, certificado no repositório |
| SEC-23 | Credenciais e admin sem CORS (C6) | varredura de **todas** as rotas conhecidas (do `openapi.yaml`) em ambas as portas, com e sem `Origin`: nenhuma resposta contém `Access-Control-Allow-Credentials`; **nenhuma** rota `/api/admin/*` (inclusive 401, 403, 404, 429 e preflight `OPTIONS`) contém qualquer `Access-Control-Allow-*`; um `fetch` com `credentials: 'include'` de outra origem (Playwright) a `/api/public/subscribers` falha no navegador | `Allow-Credentials` ou CORS no admin |
| SEC-24 | 8081 ignora SEMPRE `X-Forwarded-For`/`CF-Connecting-IP` (D33, substitui a C7) | requisições de login na 8081 de qualquer origem (container permitido, o "cloudflared" da 8080, o host pela porta publicada) com cabeçalhos de IP variados a cada tentativa são contadas pelo IP de conexão (5×401 + 429, variar não escapa); outra origem declarando o IP bloqueado tem limite próprio; o log do `web` registra o IP de conexão, nunca o do cabeçalho | confiar em cabeçalho de IP na 8081 |
| SEC-25 | Admin em HTTPS próprio (D33, substitui a C7) | Chromium em `https://127.0.0.1:8081` **recusa** o certificado autoassinado sem `ignore_https_errors` (`ERR_CERT_*`) e, com ele (só no teste), carrega o painel; o cookie `__Host-livestats_session` é aceito (`Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`, host-only), o login de ponta a ponta funciona e `fetch('/api/admin/session')` da mesma origem devolve 200; sem violação de CSP | cookie sem `Secure` ou TLS ausente |
| SEC-26 | Lista de IPs do admin na pilha real (D33) | `ADMIN_ALLOW_CIDRS` restrita a um bloco que exclui o host: o host recebe `403` JSON com cabeçalhos de segurança em todas as rotas da 8081, mesmo com sessão/CSRF válidos e cabeçalhos de IP forjados; origem dentro do bloco usa painel e `/api/admin/session`; origem fora do bloco recebe 403 na 8081 mas usa a 8080 normalmente; ao restaurar a lista a sessão volta a valer | lista não aplicada a alguma rota |
| SEC-27 | Subida recusada sem configuração segura (D33) | na pilha real, `ADMIN_ALLOW_CIDRS` = `0.0.0.0/0`, `10.0.0.0/7`, texto, IPv6 ou com injeção faz o `web` não subir (`15-livestats-realip: ERRO` no log, nada responde 200); com `admin_tls_cert` ou `admin_tls_key` vazio o `web` não sobe e nenhuma porta (8080 e 8081, HTTP ou TLS) responde; a pilha volta saudável ao restaurar | `web` subindo em HTTP ou aberto |
| SEC-28 | Login só por TLS, cookie `Secure`, sem HSTS (D33) | `Set-Cookie` do login na 8081 tem `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` e nenhum `Domain`; sem `Strict-Transport-Security`; HTTP simples na 8081 devolve 400 JSON sem `Set-Cookie`; TLS 1.2 e 1.3 negociam; a chave privada não aparece nas variáveis de ambiente do container | login por HTTP, cookie sem `Secure` |
| SEC-ANTI-01 | Varredura anti-burla (`scripts/check_antiburla.py`) | no código de produção e testes: nenhum `skip`, `xfail`, `pytest.mark.skip`, `test.skip`, `# pragma: no cover`, `if TESTING`, `DISABLE_AUTH`, `SKIP_CSRF`, `NO_RATE_LIMIT`, `unittest.mock`/`monkeypatch` sobre a unidade testada; `git diff` dos arquivos de `tests/contract/` e dos testes aprovados vazio | padrão presente |
| SEC-ANTI-02 | Integridade dos golden files | `tests/contract/legacy/*` idênticos ao commit do contrato (hash registrado) | alteração |

### Integração e compatibilidade

| ID | Verifica | Resultado esperado | Falha se (mutação) |
|---|---|---|---|
| INT-01 | Fluxo de lives de ponta a ponta | login (8081) → `PUT slots` com 3 URLs → `worker-lives` consulta o servidor falso **uma** vez por ciclo com os IDs únicos → `/live1.json`..`/live4.json` (8080) refletem os dados, slot 4 `sem_url`, tudo dentro de 2 ciclos | cadeia quebrada |
| INT-02 | Fluxo de cookies de ponta a ponta | login → `POST cookies` com cookies-sentinela → `worker-inscritos` decifra → página falsa do Studio recebe os cookies → `/inscritos.json` mostra o número configurado e `atualizado_em` avança a cada ~10 s | cadeia quebrada |
| INT-03 | Compatibilidade legada pelas duas portas | `/live1.json`..`/live4.json`, `/inscritos.json`, `/health`, `/contador` respondem em 8080 e 8081; `live*.json` e `inscritos.json` **byte a byte** iguais aos golden quando o banco é semeado com os mesmos valores; mesmos cabeçalhos (inclui os CORS `*`/`GET` nos GET públicos de dados e a ausência em `/health` e `/contador`) | divergência |
| INT-04 | Comparação com os apps atuais (V2) | `scripts/compare_with_legacy.py` consulta os apps antigos (se disponíveis) e a nova pilha com as mesmas entradas e compara campo a campo e ordem de chaves (`/live1.json`, `/inscritos.json`); relatório anexado | campo divergente |
| INT-05 | Persistência | `docker compose restart` de api/workers: slots, última leitura e cookies persistem; sessão de admin continua válida até expirar | perda de dados |
| INT-06 | Subida do zero | `docker compose up -d` em volume novo: todos `healthy` em ≤ 120 s; ordem api → web/workers | falha de permissão |
| INT-07 | Degradação | com a api parada, workers continuam e retomam gravação ao voltar; com `worker-lives` parado, o último dado continua servido e `workers/status` mostra `lives` sem heartbeat recente | acoplamento forte |
| INT-08 | Painel por Playwright em 8081 | roda a suíte `tests/web` (WEB-*) contra a pilha real (`WEB_BASE_URL`) | divergência dublê × real |
| INT-09 | Overlay por 8080 | `tests/web` WEB-OV-* contra a porta pública; o `/contador` anima ao mudar o número no worker | — |
| INT-10 | Troca de URL de slot | alterar o slot 2 leva ≤ 1 ciclo para `/live2.json` refletir; slot esvaziado vira `sem_url` | cache |
| INT-11 | Cookies expirados | página falsa passa a redirecionar para login → `workers/status.auth_state='login_required'` e painel mostra aviso; `/inscritos.json` mantém o último valor | silenciar |
| INT-12 | Limites públicos | 100 requisições rápidas de um IP à 8080 → algumas 429; overlays (1 requisição/5 s) nunca limitados | limite quebra overlay |

---

## Cobertura cruzada (PLANO §4 → testes)

| Item do PLANO §4 | Testes |
|---|---|
| admin sem sessão 401 / sem CSRF 403 | API-AUTH-01/02, SEC-01 |
| 6ª tentativa → 429 | API-AUTH-05/06, SEC-02 |
| título com `<img onerror>` como texto | WEB-XSS-01/02, API-PUB-07, SEC-03 |
| 8080 sem admin (404) | INFRA-NG-01/06, SEC-04 |
| `limit_req` por IP real | INFRA-NG-03/04, SEC-05 |
| cabeçalhos | INFRA-NG-02, SEC-06 |
| docker inspect (não-root, read-only, sem caps) | INFRA-CFG-02, SEC-07 |
| chave fora de history/inspect/logs/api | LIV-SEC-*, SEC-08, SEC-13 |
| api não decifra | API-COOKIE-03, SEC-09 |
| gitleaks e Trivy | SEC-10, SEC-19 |
| formatos legados idênticos | API-LEG-*, INT-03, INT-04 |
