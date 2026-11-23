-- All tables for BharatAgent Studio. Safe to run on every startup (IF NOT EXISTS).
-- Embeddings are 1024-dim because the whole index uses one model: bge-m3 (multilingual).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO users (name) VALUES ('demo'), ('aditya')
ON CONFLICT (name) DO NOTHING;

-- Saved pipelines, stored exactly as the canvas exports them (nodes + edges).
CREATE TABLE IF NOT EXISTS graphs (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    graph       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per chunk of an ingested source, tagged with the source node id from the graph.
CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    source_id    TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    file_name    TEXT NOT NULL,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 3: page number of the chunk inside a PDF, shown in citations.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page INT;
-- Phase 5: scope keeps chunks of different saved graphs apart even when they use the same source id
-- ("graph:<id>" for a saved graph, "inline" for a graph sent with the question).
-- origin is where a chunk came from (a GitHub file or page URL), for clickable citations.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'inline';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS origin TEXT;
CREATE INDEX IF NOT EXISTS chunks_scope_source_idx ON chunks (scope, source_id);

CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks (source_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- One row per answered question: who asked, which model, tokens, estimated cost, speed, sources.
CREATE TABLE IF NOT EXISTS usage (
    id                BIGSERIAL PRIMARY KEY,
    user_id           INT NOT NULL REFERENCES users(id),
    model             TEXT NOT NULL,
    input_tokens      INT NOT NULL,
    output_tokens     INT NOT NULL,
    tokens_estimated  BOOLEAN NOT NULL DEFAULT false,
    cost_usd          NUMERIC(12, 6) NOT NULL DEFAULT 0,
    response_ms       INT NOT NULL,
    sources           TEXT[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_created_idx ON usage (created_at);

-- Phase 4: which model was asked for and which answered, fallbacks, failures, INR cost, local compute.
-- cost_usd is NULL when the model has no published price (never a made-up zero).
ALTER TABLE usage ALTER COLUMN cost_usd TYPE NUMERIC(16, 8);
ALTER TABLE usage ALTER COLUMN cost_usd DROP NOT NULL;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS cost_inr NUMERIC(16, 6);
ALTER TABLE usage ADD COLUMN IF NOT EXISTS model_requested TEXT;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ok';      -- ok / error
ALTER TABLE usage ADD COLUMN IF NOT EXISTS error_code TEXT;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS fallback_from TEXT;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS local_compute_ms INT NOT NULL DEFAULT 0;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS language TEXT;

CREATE INDEX IF NOT EXISTS usage_user_idx ON usage (user_id);
