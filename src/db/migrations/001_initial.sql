-- TenderScout AI — Initial Schema
-- Requires PostgreSQL 14+ with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────
-- Primary tenders table
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenders (
    id               TEXT PRIMARY KEY,           -- RefNo or TOT_ID
    tot_id           TEXT,
    ref_no           TEXT,
    title            TEXT NOT NULL,
    description      TEXT,
    signal_summary   TEXT,
    search_keywords  TEXT[],
    project_tags     TEXT[],
    core_domain      TEXT NOT NULL DEFAULT 'Other',
    procurement_type TEXT NOT NULL DEFAULT 'Unknown',
    authority_name   TEXT,
    location_city    TEXT,
    location_state   TEXT,
    country          TEXT,
    closing_date     TEXT,   -- stored as ISO YYYY-MM-DD text; cast to DATE when filtering
    url              TEXT,
    is_corrigendum   BOOLEAN DEFAULT FALSE,
    embedding_text   TEXT,
    embedding        halfvec(3072),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast approximate cosine similarity search
-- HNSW supports any dimension (IVFFlat is limited to 2000).
-- m=16: neighbor connections per layer (higher = better recall, more memory)
-- ef_construction=64: build-time search width (higher = better index quality)
CREATE INDEX IF NOT EXISTS tenders_embedding_idx
    ON tenders USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Metadata indexes for filtered searches
CREATE INDEX IF NOT EXISTS tenders_core_domain_idx ON tenders (core_domain);
CREATE INDEX IF NOT EXISTS tenders_closing_date_idx ON tenders (closing_date);
CREATE INDEX IF NOT EXISTS tenders_is_corrigendum_idx ON tenders (is_corrigendum);
CREATE INDEX IF NOT EXISTS tenders_country_idx ON tenders (country);

-- ─────────────────────────────────────────────────────────────
-- Dead Letter Queue for failed ingestion records
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_dlq (
    id          SERIAL PRIMARY KEY,
    tender_id   TEXT,
    raw_data    JSONB,
    error       TEXT,
    retry_count INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dlq_created_at_idx ON ingestion_dlq (created_at DESC);
CREATE INDEX IF NOT EXISTS dlq_retry_count_idx ON ingestion_dlq (retry_count);
