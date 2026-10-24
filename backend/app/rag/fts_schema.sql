-- Explicit import job. No extensions or changes to Igor's tables.
CREATE TABLE IF NOT EXISTS dima_rag_fts_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    corpus_version text NOT NULL
);
CREATE TABLE IF NOT EXISTS dima_rag_fts_records (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('bank', 'news')),
    parent_id text NOT NULL,
    text text NOT NULL CHECK (length(text) > 0),
    published_at date NOT NULL,
    topics text[] NOT NULL,
    metadata jsonb NOT NULL,
    search_ru tsvector GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED,
    search_simple tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS dima_rag_fts_parent ON dima_rag_fts_records(kind, parent_id);
CREATE INDEX IF NOT EXISTS dima_rag_fts_ru ON dima_rag_fts_records USING gin(search_ru);
CREATE INDEX IF NOT EXISTS dima_rag_fts_simple ON dima_rag_fts_records USING gin(search_simple);
