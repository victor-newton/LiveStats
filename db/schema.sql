-- LiveStats - esquema SQLite COMPARTILHADO (versao 3)
-- Contrato T1 (revisado no V0, correcao C1; versao 2 = D34/C10: slots ganha goal e color; versao 3 = D35/C11: tabela settings e slots ganha font e text_color). Somente leitura para T2-T7: qualquer mudanca passa por nova versao do contrato.
-- Arquivo: /data/livestats.db (modo WAL), no volume livestats-data, montado em api, worker-lives e worker-inscritos.
-- Este arquivo NAO contem estado de admin: sessoes e tentativas de login ficam em outro banco
-- (db/admin_schema.sql -> /admin-state/admin.db), em volume montado SOMENTE na api.
-- Quem aplica este arquivo: SOMENTE o container api, na subida, de forma idempotente
-- (CREATE ... IF NOT EXISTS / INSERT OR IGNORE). Bancos criados na versao 1 sao migrados pela api na subida
-- (api/app/db.py: ALTER TABLE slots ADD COLUMN so se a coluna faltar; schema_version 1/2 -> 3; nada se perde; tabela settings criada pelo proprio esquema). Os workers esperam a tabela schema_meta existir
-- e nunca criam nem alteram tabelas.
-- Colunas e tabelas em ingles; os nomes dos campos JSON legados (portugues) estao em docs/legacy-formats.md.
-- Quem le/escreve cada tabela: docs/data-model.md.

PRAGMA journal_mode = WAL;      -- persistente no arquivo
PRAGMA synchronous = NORMAL;    -- seguro com WAL
PRAGMA foreign_keys = ON;       -- por conexao: cada processo deve repetir
PRAGMA busy_timeout = 5000;     -- por conexao: cada processo deve repetir

------------------------------------------------------------------------
-- Metadados do esquema
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '3');

------------------------------------------------------------------------
-- Slots de lives configurados pelo admin (substitui lives_config.json)
-- Sempre 4 linhas (1..4). url vazia = slot sem URL.
-- goal  (v2): meta de likes do overlay; NULL = sem meta; 1 a 1.000.000.000.
-- color (v2): cor da barra do overlay, '#RRGGBB' em MAIUSCULAS (GLOB e sensivel a caixa); padrao #FF0033.
-- font       (v3): familia do texto do overlay; valor da lista fechada de api/app/fonts.py (a api valida; padrao Roboto).
-- text_color (v3): cor do texto do overlay, '#RRGGBB' em MAIUSCULAS; padrao #FFFFFF.
-- IMPORTANTE: a migracao em api/app/db.py repete as definicoes de coluna das versoes 2 e 3; mantenha-as iguais.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slots (
    slot       INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
    url        TEXT    NOT NULL DEFAULT '' CHECK (length(url) <= 2048),
    updated_at TEXT,                        -- ISO 8601 UTC com sufixo Z; NULL = nunca salvo
    goal       INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000),
    color      TEXT    NOT NULL DEFAULT '#FF0033'
        CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    font       TEXT    NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40),
    text_color TEXT    NOT NULL DEFAULT '#FFFFFF'
        CHECK (length(text_color) = 7 AND text_color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')
) STRICT;

INSERT OR IGNORE INTO slots (slot, url) VALUES (1, ''), (2, ''), (3, ''), (4, '');

------------------------------------------------------------------------
-- Configuracoes do painel (v3, D35): chave/valor com chaves de lista fechada.
--   channel_id      : canal do contador de inscritos (UC + 22 caracteres); LIDO pelo worker-inscritos a cada ciclo.
--                     Sem linha = vale YOUTUBE_CHANNEL_ID do ambiente.
--   subs_font       : fonte do overlay do contador (lista fechada da api).
--   subs_text_color : cor do texto do overlay do contador (#RRGGBB maiusculas).
-- Escrita SOMENTE pela api (a validacao de formato e dela; o CHECK so garante o conjunto de chaves e o tamanho).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY CHECK (key IN ('channel_id', 'subs_font', 'subs_text_color')),
    value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 40)
) STRICT;

------------------------------------------------------------------------
-- Estatisticas por slot (substitui live1.json..live4.json)
-- Uma linha por slot, escrita SOMENTE pelo worker-lives.
-- Os textos ja estao no formato final do JSON legado (updated_at inclui microssegundos e 'Z').
-- Nao existe linha ate o worker rodar a primeira vez => API responde 503 (igual ao legado).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live_stats (
    slot            INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
    active          INTEGER NOT NULL CHECK (active IN (0, 1)),            -- JSON legado: "ativo" = "true"/"false" (string)
    video_id        TEXT    NOT NULL DEFAULT '',
    title           TEXT    NOT NULL DEFAULT '' CHECK (length(title) <= 1000),
    status          TEXT    NOT NULL CHECK (status IN ('ao_vivo', 'agendada', 'encerrada', 'sem_url')),
    views           INTEGER NOT NULL DEFAULT 0 CHECK (views >= 0),
    likes           INTEGER NOT NULL DEFAULT 0 CHECK (likes >= 0),
    views_formatted TEXT    NOT NULL DEFAULT '0',                          -- JSON legado: views_formatado
    likes_formatted TEXT    NOT NULL DEFAULT '0',                          -- JSON legado: likes_formatado
    updated_at      TEXT    NOT NULL                                       -- JSON legado: atualizado_em
        CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z'),
    CHECK ((active = 0 AND status = 'sem_url' AND video_id = '')
        OR (active = 1 AND status <> 'sem_url' AND length(video_id) = 11))
) STRICT;

------------------------------------------------------------------------
-- Ultima leitura de inscritos (substitui inscritos.json)
-- Linha unica (id = 1), reescrita a CADA leitura valida (read_at sempre atualiza), como no legado.
-- Escrita SOMENTE pelo worker-inscritos.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriber_reading (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    value   TEXT NOT NULL CHECK (length(value) BETWEEN 2 AND 32),   -- JSON legado: inscritos (string)
    raw     TEXT NOT NULL CHECK (length(raw) <= 256),               -- JSON legado: raw
    read_at TEXT NOT NULL                                           -- JSON legado: atualizado_em
        CHECK (read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;

------------------------------------------------------------------------
-- Cookies do YouTube Studio, CIFRADOS (substitui cookies.json)
-- ciphertext = PyNaCl SealedBox(chave PUBLICA) sobre o JSON normalizado do array de cookies.
-- NUNCA gravar texto puro, nomes ou valores de cookies em nenhuma coluna.
-- Inserida pela api; lida e decifrada somente pelo worker-inscritos. Mantem-se apenas a ultima
-- linha (a api apaga as anteriores na mesma transacao do INSERT).
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cookie_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ciphertext   BLOB    NOT NULL CHECK (length(ciphertext) BETWEEN 49 AND 1048576),  -- SealedBox soma 48 bytes
    key_id       TEXT    NOT NULL CHECK (length(key_id) = 16),   -- 16 primeiros hex do SHA-256 da chave publica usada
    cookie_count INTEGER NOT NULL CHECK (cookie_count BETWEEN 1 AND 200),
    uploaded_by  TEXT    NOT NULL CHECK (length(uploaded_by) <= 64),
    created_at   TEXT    NOT NULL
        CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;

------------------------------------------------------------------------
-- Heartbeat/status dos workers. Uma linha por worker, escrita pelo proprio worker.
-- last_error_code e um enum fechado; last_error_message e curto e NAO pode conter segredos.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_status (
    worker               TEXT PRIMARY KEY CHECK (worker IN ('lives', 'inscritos')),
    state                TEXT NOT NULL CHECK (state IN ('starting', 'ok', 'degraded', 'error', 'stopped')),
    last_heartbeat_at    INTEGER NOT NULL,               -- epoch (s, UTC)
    last_success_at      INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    interval_seconds     INTEGER NOT NULL CHECK (interval_seconds BETWEEN 1 AND 3600),
    next_attempt_at      INTEGER,                        -- epoch; preenchido durante backoff
    last_error_code      TEXT CHECK (last_error_code IS NULL OR last_error_code IN (
        'quota_exceeded', 'rate_limited', 'http_error', 'network_error', 'invalid_response',
        'login_required', 'counter_not_found', 'invalid_value', 'browser_error',
        'decrypt_failed', 'config_error', 'db_error')),
    last_error_message   TEXT CHECK (last_error_message IS NULL OR length(last_error_message) <= 200),
    auth_state           TEXT CHECK (auth_state IS NULL OR auth_state IN (
        'unknown', 'ok', 'login_required', 'no_cookies', 'decrypt_failed')),  -- so o worker 'inscritos'
    cookies_applied_id   INTEGER,                        -- cookie_sets.id aplicado (so 'inscritos')
    version              TEXT NOT NULL DEFAULT ''
) STRICT;
