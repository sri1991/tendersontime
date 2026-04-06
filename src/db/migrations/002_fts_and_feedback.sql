-- TenderScout AI — Migration 002
-- Adds PostgreSQL full-text search column (replaces in-memory BM25)
-- and a feedback table (replaces flat JSONL file).
--
-- Run once against the live DB:
--   psql $DATABASE_URL -f src/db/migrations/002_fts_and_feedback.sql

-- ─────────────────────────────────────────────────────────────
-- 1. Full-text search: generated tsvector column + GIN index
--    PostgreSQL auto-computes and maintains this for every row.
--    Covers title, signal_summary, description, keywords, tags.
-- ─────────────────────────────────────────────────────────────
-- Add plain tsvector column (trigger-maintained — array_to_string is not IMMUTABLE
-- so it cannot be used in a GENERATED column)
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS fts tsvector;

-- Trigger function: recomputes fts on every INSERT or UPDATE
CREATE OR REPLACE FUNCTION tenders_fts_update() RETURNS trigger AS $$
BEGIN
    NEW.fts := to_tsvector('pg_catalog.english',
        coalesce(NEW.title, '') || ' ' ||
        coalesce(NEW.signal_summary, '') || ' ' ||
        coalesce(NEW.description, '') || ' ' ||
        coalesce(array_to_string(NEW.search_keywords, ' '), '') || ' ' ||
        coalesce(array_to_string(NEW.project_tags, ' '), '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tenders_fts_trigger ON tenders;
CREATE TRIGGER tenders_fts_trigger
    BEFORE INSERT OR UPDATE ON tenders
    FOR EACH ROW EXECUTE FUNCTION tenders_fts_update();

-- Back-fill for all existing rows (runs once; ~84k rows, takes a few seconds)
UPDATE tenders SET fts = to_tsvector('pg_catalog.english',
    coalesce(title, '') || ' ' ||
    coalesce(signal_summary, '') || ' ' ||
    coalesce(description, '') || ' ' ||
    coalesce(array_to_string(search_keywords, ' '), '') || ' ' ||
    coalesce(array_to_string(project_tags, ' '), '')
);

CREATE INDEX IF NOT EXISTS idx_tenders_fts ON tenders USING GIN(fts);

-- ─────────────────────────────────────────────────────────────
-- 2. Feedback table (replaces data/feedback_logs.jsonl)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feedback (
    id          SERIAL PRIMARY KEY,
    query       TEXT        NOT NULL,
    result_id   TEXT,
    rating      INTEGER     NOT NULL,   -- +1 relevant, -1 not relevant
    position    INTEGER,                -- rank in result list (0-based)
    session_id  TEXT,
    comment     TEXT,
    meta        JSONB,                  -- snapshot of result metadata
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_query     ON feedback(query);
CREATE INDEX IF NOT EXISTS idx_feedback_result_id ON feedback(result_id);
CREATE INDEX IF NOT EXISTS idx_feedback_created   ON feedback(created_at DESC);
