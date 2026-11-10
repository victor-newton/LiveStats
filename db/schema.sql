-- LiveStats - esquema SQLite COMPARTILHADO (versao 9)
-- Contrato T1 (revisado no V0, correcao C1; versao 2 = D34/C10: slots ganha goal e color; versao 3 = D35/C11: tabela settings e slots ganha font e text_color; versao 4 = D37/C14: cookie_sets ganha source e renewed_from; versao 5 = D38/D40: sets/lives/channel_info no lugar de slots, live_stats por live, settings sem channel_id, worker_status com blocked/parse_error/not_found; versao 6 = D43/D44: historico, saude e eventos, e position das lives 1..n). Somente leitura para T2-T7: qualquer mudanca passa por nova versao do contrato.
-- Arquivo: /data/livestats.db (modo WAL), no volume livestats-data, montado em api, worker-lives e worker-inscritos.
-- Este arquivo NAO contem estado de admin: sessoes e tentativas de login ficam em outro banco
-- (db/admin_schema.sql -> /admin-state/admin.db), em volume montado SOMENTE na api.
-- Quem aplica este arquivo: SOMENTE o container api, na subida, de forma idempotente
-- (CREATE ... IF NOT EXISTS / INSERT OR IGNORE). Bancos criados na versao 1 sao migrados pela api na subida
-- (api/app/db.py: ALTER TABLE so se a coluna faltar; schema_version 1/2/3 -> 4 -> 5 -> 6 -> 7 -> 8 (D48-D51: usuarios, donos, contador por usuario) -> 9 (D54: counters.channel_override); slots com URL viram o conjunto Principal; settings e worker_status sao reconstruidas; nada se perde). Os workers esperam a tabela schema_meta existir
-- e nunca criam nem alteram tabelas.
-- Colunas e tabelas em ingles; os nomes dos campos do JSON publico (portugues) sao os de /inscritos.json.

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

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '9');

------------------------------------------------------------------------
-- v8 (D48/D50/D51): usuarios, donos e contador por usuario. A SENHA nao fica aqui (admin.db, credentials).
-- O mestre (id 1) e o contador 1 existem sempre; a api ajusta o username do mestre a ADMIN_USERNAME.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL UNIQUE CHECK (length(username) BETWEEN 3 AND 32),
    display_name TEXT    NOT NULL DEFAULT '' CHECK (length(display_name) <= 60),
    role         TEXT    NOT NULL DEFAULT 'user' CHECK (role IN ('master', 'user')),
    status       TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    has_counter  INTEGER NOT NULL DEFAULT 1 CHECK (has_counter IN (0, 1)),
    max_sets     INTEGER NOT NULL DEFAULT 5 CHECK (max_sets BETWEEN 0 AND 1000),
    max_lives    INTEGER NOT NULL DEFAULT 20 CHECK (max_lives BETWEEN 0 AND 10000),
    created_at   INTEGER NOT NULL,
    last_login_at INTEGER,
    last_seen_at  INTEGER
) STRICT;
INSERT OR IGNORE INTO users (id, username, role, created_at) VALUES (1, 'admin', 'master', 0);

CREATE TABLE IF NOT EXISTS counters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    font       TEXT    NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40),
    text_color TEXT    NOT NULL DEFAULT '#FFFFFF'
        CHECK (length(text_color) = 7 AND text_color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    created_at INTEGER NOT NULL,
    -- v9 (D54): canal que o worker deve ler no lugar do detectado pelos cookies. Escrito SO pela api; o worker le.
    channel_override TEXT CHECK (channel_override IS NULL OR (
        length(channel_override) = 24 AND channel_override GLOB 'UC*' AND channel_override NOT GLOB '*[^A-Za-z0-9_-]*'))
) STRICT;
INSERT OR IGNORE INTO counters (id, owner_id, created_at) VALUES (1, 1, 0);

-- escrito SO pelo worker-inscritos; a api le
CREATE TABLE IF NOT EXISTS counter_status (
    counter_id           INTEGER PRIMARY KEY REFERENCES counters(id) ON DELETE CASCADE,
    state                TEXT NOT NULL CHECK (state IN ('starting', 'ok', 'degraded', 'error', 'paused')),
    auth_state           TEXT CHECK (auth_state IS NULL OR auth_state IN ('unknown', 'ok', 'login_required', 'no_cookies', 'decrypt_failed')),
    last_success_at      INTEGER,
    last_attempt_at      INTEGER,
    next_read_at         INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_error_code      TEXT CHECK (last_error_code IS NULL OR length(last_error_code) <= 40),
    last_error_message   TEXT CHECK (last_error_message IS NULL OR length(last_error_message) <= 200),
    cookies_applied_id   INTEGER,
    loads_last_hour      INTEGER NOT NULL DEFAULT 0 CHECK (loads_last_hour >= 0)
) STRICT;

-- escrito SO pela api; mantem as 5000 linhas mais recentes
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         INTEGER NOT NULL,
    actor_id   INTEGER,
    actor_name TEXT    NOT NULL DEFAULT '' CHECK (length(actor_name) <= 64),
    action     TEXT    NOT NULL CHECK (length(action) <= 40),
    target     TEXT    NOT NULL DEFAULT '' CHECK (length(target) <= 80),
    detail     TEXT    NOT NULL DEFAULT '' CHECK (length(detail) <= 200)
) STRICT;
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log(at);

------------------------------------------------------------------------
-- Conjuntos e lives (v5, D40; substituem a tabela slots). Escrita SOMENTE pela api (admin).
-- sets : agrupa lives; meta de likes (goal) e aparencia do overlay do conjunto.
-- lives: url digitada no painel; platform e ref sao detectados pela api ao salvar
--        (youtube: ID de 11 chars do video, ou @handle / UC... quando a URL e de canal; twitch: login minusculo; kick: slug minusculo).
-- color / text_color: '#RRGGBB' em MAIUSCULAS (GLOB e sensivel a caixa). font: lista fechada de api/app/fonts.py.
-- Limites de protecao (aplicados pela api): 50 conjuntos e 200 lives no total.
-- position das lives (v6, D44): 1..n sem buracos dentro do conjunto (a api recompacta ao apagar).
-- IMPORTANTE: a migracao em api/app/db.py repete estas definicoes; mantenha-as iguais.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    goal       INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000),
    color      TEXT    NOT NULL DEFAULT '#FF0033'
        CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    font       TEXT    NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40),
    text_color TEXT    NOT NULL DEFAULT '#FFFFFF'
        CHECK (length(text_color) = 7 AND text_color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
        CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;

CREATE TABLE IF NOT EXISTS lives (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id     INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    url        TEXT    NOT NULL CHECK (length(url) BETWEEN 1 AND 2048),
    platform   TEXT    NOT NULL CHECK (platform IN ('youtube', 'twitch', 'kick')),
    ref        TEXT    NOT NULL CHECK (length(ref) BETWEEN 1 AND 100),
    label      TEXT    NOT NULL DEFAULT '' CHECK (length(label) <= 60),
    goal       INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000),
    color      TEXT    NOT NULL DEFAULT '#FF0033'
        CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    font       TEXT    NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40),
    text_color TEXT    NOT NULL DEFAULT '#FFFFFF'
        CHECK (length(text_color) = 7 AND text_color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
        CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;
CREATE INDEX IF NOT EXISTS lives_set_idx ON lives(set_id, position);
CREATE INDEX IF NOT EXISTS sets_owner_idx ON sets(owner_id, position);

------------------------------------------------------------------------
-- Configuracoes do painel (v3, D35; v5 sem channel_id): chave/valor com chaves de lista fechada.
--   subs_font       : fonte do overlay do contador (lista fechada da api).
--   subs_text_color : cor do texto do overlay do contador (#RRGGBB maiusculas).
-- Escrita SOMENTE pela api (a validacao de formato e dela; o CHECK so garante o conjunto de chaves e o tamanho).
-- O canal do contador nao e mais configurado: o worker-inscritos o descobre pelos cookies (channel_info, D38).
------------------------------------------------------------------------
-- settings (subs_font, subs_text_color) deixou de existir na v8: a aparencia do contador vive em counters.

------------------------------------------------------------------------
-- Ultima leitura de cada live (v5, D40; substitui o live_stats de 4 linhas por slot)
-- Uma linha por live, escrita SOMENTE pelo worker-lives (UPSERT por live_id). A api so le
-- (e apaga a linha quando a URL da live muda). Lives apagadas: o CASCADE limpa.
-- likes e sempre NULL em twitch/kick. Sem linha = o worker ainda nao leu a live.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live_stats (
    live_id    INTEGER PRIMARY KEY REFERENCES lives(id) ON DELETE CASCADE,
    status     TEXT    NOT NULL CHECK (status IN ('ao_vivo', 'agendada', 'encerrada', 'offline', 'erro')),
    title      TEXT    NOT NULL DEFAULT '' CHECK (length(title) <= 1000),
    viewers    INTEGER CHECK (viewers IS NULL OR viewers >= 0),
    likes      INTEGER CHECK (likes IS NULL OR likes >= 0),
    updated_at TEXT    NOT NULL
        CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z'),
    error_code TEXT    CHECK (error_code IS NULL OR error_code IN (
        'blocked', 'parse_error', 'not_found', 'http_error', 'network_error', 'rate_limited', 'invalid_response'))
) STRICT;

------------------------------------------------------------------------
-- Canal do YouTube descoberto pelos cookies (v5, D38). Linha unica (id = 1), escrita SOMENTE pelo worker-inscritos.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_info (
    counter_id   INTEGER PRIMARY KEY REFERENCES counters(id) ON DELETE CASCADE,
    channel_id   TEXT NOT NULL CHECK (length(channel_id) = 24 AND channel_id GLOB 'UC*'),
    channel_name TEXT NOT NULL DEFAULT '' CHECK (length(channel_name) <= 200),
    detected_at  TEXT NOT NULL
        CHECK (detected_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;

------------------------------------------------------------------------
-- Ultima leitura de inscritos (substitui inscritos.json)
-- Linha unica (id = 1), reescrita a CADA leitura valida (read_at sempre atualiza).
-- Escrita SOMENTE pelo worker-inscritos.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriber_reading (
    counter_id INTEGER PRIMARY KEY REFERENCES counters(id) ON DELETE CASCADE,
    value   TEXT NOT NULL CHECK (length(value) BETWEEN 2 AND 32),   -- JSON publico: inscritos (string)
    raw     TEXT NOT NULL CHECK (length(raw) <= 256),               -- JSON publico: raw
    read_at TEXT NOT NULL                                           -- JSON publico: atualizado_em
        CHECK (read_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;

------------------------------------------------------------------------
-- Cookies do YouTube Studio, CIFRADOS (substitui cookies.json)
-- ciphertext = PyNaCl SealedBox(chave PUBLICA) sobre o JSON normalizado do array de cookies.
-- NUNCA gravar texto puro, nomes ou valores de cookies em nenhuma coluna.
-- Inserida pela api (source='admin') e pelo worker-inscritos ao renovar a sessao (source='worker', D37);
-- lida e decifrada somente pelo worker-inscritos. Mantem-se apenas a ultima linha (quem insere apaga as
-- anteriores na mesma transacao do INSERT).
-- source       (v4): origem do conjunto: 'admin' (colado no painel, padrao) ou 'worker' (renovacao automatica).
-- renewed_from (v4): id do conjunto aplicado que o worker renovou; NULL quando source='admin'.
-- IMPORTANTE: a migracao em api/app/db.py repete as definicoes de coluna da versao 4; mantenha-as iguais.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cookie_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    counter_id   INTEGER NOT NULL DEFAULT 1 REFERENCES counters(id) ON DELETE CASCADE,
    ciphertext   BLOB    NOT NULL CHECK (length(ciphertext) BETWEEN 49 AND 1048576),  -- SealedBox soma 48 bytes
    key_id       TEXT    NOT NULL CHECK (length(key_id) = 16),   -- 16 primeiros hex do SHA-256 da chave publica usada
    cookie_count INTEGER NOT NULL CHECK (cookie_count BETWEEN 1 AND 200),
    uploaded_by  TEXT    NOT NULL CHECK (length(uploaded_by) <= 64),
    created_at   TEXT    NOT NULL
        CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z'),
    source       TEXT    NOT NULL DEFAULT 'admin' CHECK (source IN ('admin', 'worker')),
    renewed_from INTEGER CHECK (renewed_from IS NULL OR renewed_from >= 1)
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
        'decrypt_failed', 'config_error', 'db_error', 'blocked', 'parse_error', 'not_found')),
    last_error_message   TEXT CHECK (last_error_message IS NULL OR length(last_error_message) <= 200),
    auth_state           TEXT CHECK (auth_state IS NULL OR auth_state IN (
        'unknown', 'ok', 'login_required', 'no_cookies', 'decrypt_failed')),  -- so o worker 'inscritos'
    cookies_applied_id   INTEGER,                        -- cookie_sets.id aplicado (so 'inscritos')
    version              TEXT NOT NULL DEFAULT '',
    -- v9 (D56): memoria em uso pelo container do worker no ultimo batimento (cgroup); escrita SO pelo worker, lida pela api
    mem_bytes            INTEGER CHECK (mem_bytes IS NULL OR mem_bytes >= 0)
) STRICT;

------------------------------------------------------------------------
-- Historico, saude e eventos (v6, D43). Datas em epoch segundos UTC. A api so le (e escreve events, source 'api').
-- live_samples      : uma linha por leitura OK de cada live (worker-lives); apaga > 7200 s.
-- set_peaks         : maior soma de espectadores do conjunto por dia (fuso America/Sao_Paulo); apaga > 30 dias.
-- subscriber_samples: uma amostra por minuto e canal (worker-inscritos); apaga > 8 dias.
-- live_reads        : ultima leitura (ok ou erro) de cada live, com latencia e proxima leitura agendada.
-- health_minutes    : agregado por minuto de cada worker; apaga > 2 h.
-- events            : so MUDANCAS; no maximo 500 linhas por origem.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live_samples (
    live_id INTEGER NOT NULL REFERENCES lives(id) ON DELETE CASCADE,
    at      INTEGER NOT NULL,
    viewers INTEGER CHECK (viewers IS NULL OR viewers >= 0),
    likes   INTEGER CHECK (likes IS NULL OR likes >= 0),
    PRIMARY KEY (live_id, at)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS set_peaks (
    set_id  INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    day     TEXT    NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    viewers INTEGER NOT NULL CHECK (viewers >= 0),
    at      INTEGER NOT NULL,
    PRIMARY KEY (set_id, day)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS subscriber_samples (
    counter_id INTEGER NOT NULL REFERENCES counters(id) ON DELETE CASCADE,
    channel_id TEXT    NOT NULL,
    at         INTEGER NOT NULL,
    value      INTEGER NOT NULL CHECK (value >= 0),
    PRIMARY KEY (counter_id, channel_id, at)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS live_reads (
    live_id      INTEGER PRIMARY KEY REFERENCES lives(id) ON DELETE CASCADE,
    last_read_at INTEGER NOT NULL,
    next_read_at INTEGER NOT NULL,
    latency_ms   INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    error_code   TEXT CHECK (error_code IS NULL OR error_code IN (
        'blocked', 'parse_error', 'not_found', 'http_error', 'network_error', 'rate_limited', 'invalid_response'))
) STRICT;

CREATE TABLE IF NOT EXISTS health_minutes (
    worker TEXT    NOT NULL CHECK (worker IN ('lives', 'inscritos')),
    minute INTEGER NOT NULL CHECK (minute % 60 = 0),
    avg_ms INTEGER NOT NULL CHECK (avg_ms >= 0),
    max_ms INTEGER NOT NULL CHECK (max_ms >= 0),
    ok     INTEGER NOT NULL CHECK (ok >= 0),
    errors INTEGER NOT NULL CHECK (errors >= 0),
    PRIMARY KEY (worker, minute)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      INTEGER NOT NULL,
    source  TEXT    NOT NULL CHECK (source IN ('lives', 'inscritos', 'api')),
    level   TEXT    NOT NULL CHECK (level IN ('info', 'ok', 'warn', 'bad')),
    code    TEXT    NOT NULL CHECK (length(code) BETWEEN 1 AND 40),
    message TEXT    NOT NULL CHECK (length(message) <= 200),
    live_id INTEGER
) STRICT;
CREATE INDEX IF NOT EXISTS events_at_idx ON events(at);
