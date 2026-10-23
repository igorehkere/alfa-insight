-- Dima owns only dima_rag_* objects. Run explicitly, never at web startup.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS dima_rag_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    model_key text NOT NULL,
    corpus_version text NOT NULL
);
CREATE TABLE IF NOT EXISTS dima_rag_records (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('bank', 'news')),
    parent_id text NOT NULL,
    text text NOT NULL CHECK (length(text) > 0),
    published_at date NOT NULL,
    topics text[] NOT NULL,
    metadata jsonb NOT NULL,
    model_key text NOT NULL,
    fingerprint text NOT NULL,
    embedding vector(384) NOT NULL,
    search_ru tsvector GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED,
    search_simple tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS dima_rag_parent ON dima_rag_records(kind, parent_id);
CREATE INDEX IF NOT EXISTS dima_rag_date ON dima_rag_records(kind, published_at);
CREATE INDEX IF NOT EXISTS dima_rag_ru ON dima_rag_records USING gin(search_ru);
CREATE INDEX IF NOT EXISTS dima_rag_simple ON dima_rag_records USING gin(search_simple);
-- Exact vector scan is deliberate for a small corpus; measure before adding HNSW.
