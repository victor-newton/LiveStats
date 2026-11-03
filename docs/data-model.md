# Modelo de dados — LiveStats

Contrato T1 (esquema na **versão 3**: ver §1.1). Fontes da verdade dos esquemas: [`db/schema.sql`](../db/schema.sql) (dados compartilhados) e [`db/admin_schema.sql`](../db/admin_schema.sql) (estado de admin, volume exclusivo da api); ambos somente leitura para T2–T7.

## 1. Bancos

Há **dois** arquivos SQLite, em volumes diferentes (correção C1 do V0):

| Arquivo | Esquema | Volume | Montado por | Conteúdo |
|---|---|---|---|---|
| `/data/livestats.db` | [`db/schema.sql`](../db/schema.sql) | `livestats-data` | `api`, `worker-lives`, `worker-inscritos` (rw) | dados compartilhados: slots, estatísticas, inscritos, cookies cifrados, status dos workers |
| `/admin-state/admin.db` | [`db/admin_schema.sql`](../db/admin_schema.sql) | `livestats-admin` | **somente `api`** (rw) | estado de admin: `admin_sessions`, `login_attempts` |

*Motivo:* os workers processam conteúdo da internet (o Chromium abre páginas do YouTube Studio; o worker-lives lê respostas externas). Um worker comprometido executa código próprio e consegue abrir qualquer arquivo que o seu container enxergue, sem passar por autorizador algum — se as sessões estivessem no volume compartilhado, ele poderia inserir uma sessão de admin falsa. Com o estado de admin num volume que **só a api monta**, isso deixa de ser possível por construção.

Ambos em modo **WAL** (`-wal` e `-shm` ao lado). O `web` não monta volume algum.
- Requer SQLite >= 3.37 (tabelas `STRICT`). As imagens `python:3.12-slim` trazem versão suficiente; o teste `API-DB-01` confere.
- **Quem cria os esquemas:** somente a `api`, na subida, executando `db/schema.sql` em `/data/livestats.db` e `db/admin_schema.sql` em `/admin-state/admin.db` (ambos idempotentes). Os workers esperam `schema_meta` existir (o `depends_on: api (service_healthy)` do compose garante a ordem, e cada worker ainda tenta de novo por conta própria) e nunca executam DDL.
- Cada processo, ao abrir uma conexão, repete `PRAGMA foreign_keys=ON` e `PRAGMA busy_timeout=5000`. Transações de escrita são curtas (um ciclo = uma transação).
- Datas: textos ISO 8601 UTC com sufixo `Z` onde o valor vai literalmente para um JSON legado (`live_stats.updated_at`, `subscriber_reading.read_at`, `slots.updated_at`, `cookie_sets.created_at`); inteiros epoch (segundos UTC) nas tabelas de segurança e de status (`admin_sessions`, `login_attempts`, `worker_status`), para comparação simples.
- Permissões: todos os containers que usam os bancos rodam como `10001:10001`; cada processo chama `os.umask(0o007)` na subida; `/data` é `2770` (D1) e `/admin-state` é `0700` (só a api). Nenhum arquivo é legível por "outros".

### 1.1 Versões do esquema

| Versão | Decisão | O que muda |
|---|---|---|
| 1 | T1 | esquema original |
| 2 | D34 (C10) | `slots` ganha `goal` (meta de likes, `NULL` = sem meta, 1 a 1.000.000.000) e `color` (`#RRGGBB` em maiúsculas, padrão `#FF0033`) |
| 3 | D35 (C11) | tabela nova `settings` e `slots` ganha `font` (família da lista fechada de `api/app/fonts.py`, padrão `Roboto`) e `text_color` (`#RRGGBB` em maiúsculas, padrão `#FFFFFF`) |

**Migração idempotente (feita só pela api, na subida):** `CREATE TABLE IF NOT EXISTS settings` (no próprio `schema.sql`), `ALTER TABLE slots ADD COLUMN` apenas para a coluna que faltar (as definições repetem as de `schema.sql`) e `schema_version` 1 ou 2 → 3, tudo numa transação. Repetir a migração não muda nada; dados existentes (URLs, metas, cores, `updated_at`) são preservados. Bancos novos já nascem na v3.

**`settings` (v3):** `key TEXT PRIMARY KEY CHECK (key IN ('channel_id', 'subs_font', 'subs_text_color'))`, `value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 40)`, `STRICT`. O CHECK só garante o conjunto de chaves e o tamanho; **o formato** (canal `^UC[0-9A-Za-z_-]{22}$`, fonte da lista fechada, cor `#RRGGBB`) é validado pela api antes de gravar. Ausência de linha = padrão: sem `channel_id` o `worker-inscritos` usa `YOUTUBE_CHANNEL_ID` do ambiente; fonte `Roboto`; cor `#FFFFFF`. O `worker-inscritos` lê `channel_id` a cada ciclo (autorizador: leitura de `settings`, nenhuma escrita), ignora valor que não case o formato (usa o do ambiente) e recarrega o Studio quando o canal muda.

## 2. Tabelas e quem lê/escreve

Legenda: **R** = lê, **W** = escreve (INSERT/UPDATE/DELETE), **—** = não toca.

**`/data/livestats.db`** (compartilhado):

| Tabela | api | worker-lives | worker-inscritos | Conteúdo |
|---|---|---|---|---|
| `schema_meta` | W (cria) / R | R | R | versão do esquema |
| `slots` | **R/W** | R | — | URL de cada um dos 4 slots (admin salva → api grava → worker-lives lê); `goal`, `color` (v2) e `font`, `text_color` (v3) são só da api e do overlay |
| `settings` (v3) | **R/W** (INSERT/UPDATE; sem DELETE) | — | **R** (só `channel_id`) | chave/valor com chaves de lista fechada: `channel_id`, `subs_font`, `subs_text_color` |
| `live_stats` | R | **W** | — | estatísticas de cada slot, já no formato dos JSON legados |
| `subscriber_reading` | R | — | **W** | última leitura de inscritos (linha única) |
| `cookie_sets` | **W** (INSERT + DELETE das antigas); R só de metadados (`id`, `key_id`, `cookie_count`, `uploaded_by`, `created_at`) | — | **R** (inclui `ciphertext`) | cookies cifrados |
| `worker_status` | R | **W** (linha `lives`) | **W** (linha `inscritos`) | heartbeat e estado dos workers |

**`/admin-state/admin.db`** (somente api):

| Tabela | api | worker-lives | worker-inscritos | Conteúdo |
|---|---|---|---|---|
| `schema_meta` | W / R | — | — | versão do esquema |
| `admin_sessions` | **R/W** | — | — | sessões de admin (hash do id) |
| `login_attempts` | **R/W** | — | — | tentativas de login |

Os workers **não enxergam** este arquivo: o volume `livestats-admin` não é montado neles (verificado por `INFRA-CFG-07/11` e `SEC-20`).

### 2.1 Isolamento real × autorizador do SQLite

**A proteção real é o isolamento por volume e a criptografia**, não o autorizador:
- estado de admin num volume que só a api monta (acima);
- cookies cifrados com SealedBox: quem lê o banco (inclusive um worker comprometido, ou a própria api) não obtém texto puro; só o `worker-inscritos` tem a chave privada;
- cada segredo existe em um só container.

Um processo comprometido executa código próprio e pode abrir `/data/livestats.db` sem autorizador algum; portanto o autorizador **não** é defesa contra processo comprometido. Ele é uma **defesa contra bug no próprio código** (ex.: SQL injection, query errada que escreve na tabela errada): **cada processo instala `sqlite3.Connection.set_authorizer`** em toda conexão que abre, com a lista de permissões acima, e ações fora dela falham com erro (`sqlite3.DatabaseError: not authorized`). Em particular:

- `api` (em `livestats.db`): **negada** a leitura da coluna `cookie_sets.ciphertext` (a api só grava; não precisa ler de volta); negada escrita em `live_stats`, `subscriber_reading`, `worker_status`. Em `admin.db`: acesso total às duas tabelas.
- `worker-lives`: leitura de `slots`, `schema_meta`; escrita em `live_stats` e `worker_status`. Nada mais (nem leitura de `cookie_sets`).
- `worker-inscritos`: leitura de `cookie_sets` e `schema_meta`; escrita em `subscriber_reading` e `worker_status`. Nada mais.

Testes: `API-DB-02`, `LIV-DB-01`, `SUB-DB-01` (autorizador) e `API-DB-03`, `INFRA-CFG-11`, `SEC-20` (isolamento).

## 3. Detalhes por tabela

### `slots`
Sempre 4 linhas (1..4), semeadas pelo esquema. `url` vazia = slot sem URL. A api grava a URL já sem espaços nas pontas (`strip`) e só aceita vazia ou com ID de vídeo extraível (decisão D6). `updated_at` = momento do último PUT; a API devolve `saved_at = MAX(updated_at)` (equivale ao `salvo_em` do `lives_config.json`; `null` se nunca salvo).

### `live_stats`
Mapeamento coluna → campo do JSON legado (ver `legacy-formats.md`):

| Coluna | Campo JSON | Observação |
|---|---|---|
| `slot` | `slot` (int) | |
| `active` (0/1) | `ativo` (**string** `"true"`/`"false"`) | o serializador converte |
| `video_id` | `video_id` | `""` quando `sem_url` |
| `title` | `titulo` | texto puro; nunca HTML (o front usa `textContent`) |
| `status` | `status` | `ao_vivo`, `agendada`, `encerrada`, `sem_url` |
| `views` / `likes` | `views` / `likes` (int) | `views` = espectadores simultâneos quando `ao_vivo` e o campo existe e é > 0 |
| `views_formatted` / `likes_formatted` | `views_formatado` / `likes_formatado` | calculado pelo worker (`formatar`) |
| `updated_at` | `atualizado_em` | `datetime.utcnow().isoformat() + "Z"` equivalente, **com microssegundos** (o `isoformat` omite quando é exatamente zero; manter) |

O CHECK da tabela força a coerência: `active=0` ⇔ `status='sem_url'` e `video_id=''`; `active=1` ⇒ `video_id` com 11 caracteres.

Comportamentos herdados do legado, que o worker precisa manter:
- slot sem URL ou com URL inválida: a linha `sem_url` é **reescrita a cada ciclo**;
- slot com vídeo válido: a linha só muda se a API do YouTube devolveu dados para o vídeo; em erro da API (rede, cota, 5xx) ou vídeo não retornado, **a linha anterior permanece** (dado "velho" continua sendo servido, como hoje);
- sem linha (worker ainda não rodou) ⇒ endpoints públicos respondem 503.

### `subscriber_reading`
Linha única `id=1` (UPSERT). `value` = string que o legado chama de `inscritos` (ex.: `"460600"`, `"460.600"`); `raw` = texto cru lido da página, **truncado em 256 caracteres** pelo worker (decisão D20); `read_at` = `atualizado_em`. Reescrita a cada leitura válida (mesmo com valor igual), como o `scraper.py`. Não há histórico (o legado não tinha).

### `cookie_sets`
- `ciphertext`: `nacl.public.SealedBox(PublicKey).encrypt(json_bytes)`, onde `json_bytes` é o array JSON normalizado (`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`) e cada cookie tem exatamente 5 campos: `name`, `value`, `domain`, `path`, `secure` (decisão D13).
- `key_id`: primeiros 16 hex do SHA-256 dos 32 bytes da chave pública; permite ao worker avisar "cookie cifrado com outra chave" (`decrypt_failed`) sem tentar decifrar à toa.
- `cookie_count`, `uploaded_by`, `created_at`: metadados sem valor sensível. **Nomes e valores de cookies nunca aparecem em coluna alguma, log ou resposta.**
- A cada upload: `INSERT` da nova linha e `DELETE` das demais na mesma transação. O worker detecta mudança comparando `id` com `worker_status.cookies_applied_id` (substitui a checagem de `mtime`).
- O `ciphertext` nunca é devolvido por endpoint algum.

### `admin_sessions` (em `admin.db`)
- `id_hash` = SHA-256 hex do id da sessão (32 bytes aleatórios, base64url, só existe no cookie do navegador). O id em si **nunca** é gravado.
- `csrf_token`: 32 bytes aleatórios base64url, por sessão; devolvido ao front no login e em `GET /api/admin/session`; comparado em tempo constante (decisão D3).
- Expiração: `expires_at` absoluto = criação + 8 h; ocioso: `last_seen_at + 30 min` (atualizado no máximo 1x/min). Linhas expiradas são removidas pela api (na própria checagem e em varredura a cada 10 min).
- Login sempre cria sessão nova (id novo); logout apaga a linha.

### `login_attempts` (em `admin.db`)
Registra cada tentativa (`success` 0/1). A regra de bloqueio conta apenas `success=0` dos últimos 60 s, por `ip` **ou** por `username` (minúsculo, truncado em 64): com 5 falhas na janela, a tentativa seguinte recebe 429 sem verificar a senha. Tentativas bloqueadas (429) não são registradas. Varredura apaga linhas com mais de 24 h.

### `worker_status`
Uma linha por worker, atualizada a cada ciclo (heartbeat mesmo sem novidade). `state`: `starting`, `ok`, `degraded` (falhas recentes, ainda tentando), `error` (parado de produzir dado útil, ex.: `login_required`), `stopped`. `last_error_code` é enum fechado; `last_error_message` é curto e **sanitizado** (sem URL com chave, sem cookie, sem trecho de resposta). `auth_state` e `cookies_applied_id` são do worker `inscritos`. O `healthcheck` de cada worker considera saudável um heartbeat mais novo que `3 × interval_seconds + 30 s`.

## 4. Retenção e limpeza

| Dado | Retenção | Quem limpa |
|---|---|---|
| `cookie_sets` | apenas a última | api, no upload |
| `admin_sessions` | até expirar | api |
| `login_attempts` | 24 h | api |
| `live_stats`, `subscriber_reading`, `worker_status` | uma linha por chave, sobrescrita | workers |

## 5. Backup e migração

O estado a preservar é o volume `livestats-data` (dados) e, opcionalmente, `livestats-admin` (sessões; perdê-lo só obriga a logar de novo). Migração do legado (T9, fora deste contrato): `lives_config.json` → `slots` (via `PUT /api/admin/slots`); `cookies.json` **não** é migrado em disco, é colado de novo no painel (para nunca existir texto puro no servidor novo).
