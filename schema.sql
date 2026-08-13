-- Schema do motor de decaimento de engajamento (energia psicologica).
-- SQLite. Indices otimizados para os padroes de acesso reais do sistema:
-- (1) leitura de eventos de UM autor ao longo do tempo (recalculo de energia),
-- (2) leitura de TODOS os eventos de UM dia (job diario em lote), e
-- (3) leitura de eventos de UMA plataforma (cutoff incremental por rede).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
    author_channel_id   TEXT PRIMARY KEY,  -- namespaced: "{platform}:{author_id}"
    platform             TEXT NOT NULL,
    author_display_name TEXT NOT NULL DEFAULT '',
    first_seen_at        TEXT NOT NULL,  -- ISO 8601 UTC
    last_seen_at          TEXT NOT NULL   -- ISO 8601 UTC
);

CREATE TABLE IF NOT EXISTS engagement_events (
    event_id           TEXT PRIMARY KEY,
    event_source_id     TEXT NOT NULL,  -- ID original na plataforma de origem (comment_id, hash de mensagem, etc.)
    platform             TEXT NOT NULL,
    author_channel_id   TEXT NOT NULL REFERENCES authors(author_channel_id),
    content_id            TEXT NOT NULL,  -- video_id, nome do grupo/chat, etc.
    published_at         TEXT NOT NULL,  -- ISO 8601 UTC
    quality_score         REAL NOT NULL CHECK (quality_score >= 0.0 AND quality_score <= 1.0),
    categorias            TEXT NOT NULL DEFAULT ''
);

-- author_channel_id isolado: usado para reconstruir a trajetoria de um autor.
CREATE INDEX IF NOT EXISTS idx_events_author_channel_id
    ON engagement_events(author_channel_id);

-- published_at isolado: usado pelo job diario para carregar so a janela do dia.
CREATE INDEX IF NOT EXISTS idx_events_published_at
    ON engagement_events(published_at);

-- composto: consultas que cruzam autor + janela temporal (ex.: churn report).
CREATE INDEX IF NOT EXISTS idx_events_author_published
    ON engagement_events(author_channel_id, published_at);

CREATE INDEX IF NOT EXISTS idx_events_content_id
    ON engagement_events(content_id);

-- platform isolado: usado pelo cutoff incremental por-plataforma.
CREATE INDEX IF NOT EXISTS idx_events_platform
    ON engagement_events(platform);

CREATE INDEX IF NOT EXISTS idx_events_platform_published
    ON engagement_events(platform, published_at);

-- Estado corrente (N0) de cada autor: um snapshot, nao um historico.
-- O historico completo, quando necessario, e reconstruido reprocessando
-- engagement_events; author_engagement_state existe para permitir o
-- calculo diario incremental sem reler o dataset inteiro.
CREATE TABLE IF NOT EXISTS author_engagement_state (
    author_channel_id TEXT PRIMARY KEY REFERENCES authors(author_channel_id),
    energy             REAL NOT NULL CHECK (energy >= 0.0 AND energy <= 100.0),
    level               INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    last_update_at     TEXT NOT NULL,  -- timestamp de referencia de 'energy' (N0)
    last_event_at       TEXT,           -- timestamp do ultimo evento real de engajamento
    updated_at           TEXT NOT NULL   -- quando esta linha foi recalculada
);

CREATE INDEX IF NOT EXISTS idx_state_level
    ON author_engagement_state(level);
