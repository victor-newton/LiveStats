-- LiveStats - esquema SQLite do ESTADO DE ADMIN (versao 1)
-- Contrato T1 (revisado no V0, correcao C1). Somente leitura para T2-T7.
-- Arquivo: /admin-state/admin.db (modo WAL), no volume livestats-admin, montado SOMENTE no container api.
-- Nenhum worker e nenhum outro container monta este volume: um worker comprometido nao alcanca sessoes
-- nem tentativas de login (o isolamento por volume e a protecao real).
-- Quem aplica este arquivo: a api, na subida, de forma idempotente.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;       -- por conexao
PRAGMA busy_timeout = 5000;     -- por conexao

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1');

------------------------------------------------------------------------
-- Sessoes de admin. Guarda o HASH (SHA-256 hex) do id da sessao; o id nunca e gravado.
-- Datas em epoch (segundos UTC) para comparacao simples.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_sessions (
    id_hash      TEXT    PRIMARY KEY CHECK (length(id_hash) = 64),
    csrf_token   TEXT    NOT NULL CHECK (length(csrf_token) BETWEEN 32 AND 128),
    user_id      INTEGER NOT NULL DEFAULT 1,                         -- v8 (D48)
    username     TEXT    NOT NULL CHECK (length(username) <= 64),
    client_ip    TEXT    NOT NULL DEFAULT '' CHECK (length(client_ip) <= 64),
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL                        -- limite absoluto
) STRICT;

CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions (expires_at);

------------------------------------------------------------------------
-- Tentativas de login (rate limit na api, por IP e por usuario)
-- Regra: 5 falhas em 60 s por IP OU por usuario => proxima tentativa recebe 429.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS login_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at INTEGER NOT NULL,                       -- epoch (s, UTC)
    ip           TEXT    NOT NULL CHECK (length(ip) <= 64),
    username     TEXT    NOT NULL CHECK (length(username) <= 64),  -- minusculo, truncado em 64
    success      INTEGER NOT NULL CHECK (success IN (0, 1))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip   ON login_attempts (ip, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user ON login_attempts (username, attempted_at);

-- v8 (D48): credenciais por usuario. So a api le/escreve. must_change = 1 forca a troca de senha no proximo acesso.
CREATE TABLE IF NOT EXISTS credentials (
    user_id       INTEGER PRIMARY KEY,
    password_hash TEXT    NOT NULL CHECK (length(password_hash) BETWEEN 20 AND 300),
    must_change   INTEGER NOT NULL DEFAULT 0 CHECK (must_change IN (0, 1)),
    updated_at    INTEGER NOT NULL
) STRICT;
