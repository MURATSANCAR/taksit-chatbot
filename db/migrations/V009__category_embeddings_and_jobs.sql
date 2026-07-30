-- V009: Category catalog embeddings + async embedding jobs.
-- Uses DOUBLE PRECISION[] for portability in CI environments without pgvector.
-- CREATE EXTENSION IF NOT EXISTS vector attempted; if unavailable, plain array
-- column path remains fully functional (application layer reads the array).

DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        -- pgvector not installed; downstream operations must not depend on it.
        RAISE NOTICE 'pgvector extension unavailable; continuing with array-only embeddings';
    END;
END $$;

CREATE TABLE IF NOT EXISTS catalog_category_embeddings (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id           UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    catalog_revision      INTEGER NOT NULL CHECK (catalog_revision >= 0),
    locale                VARCHAR(16) NOT NULL,
    embedding_profile_id  UUID NOT NULL,
    content_hash          VARCHAR(64) NOT NULL,
    embedding             DOUBLE PRECISION[] NOT NULL,
    embedding_dimension   INTEGER NOT NULL CHECK (embedding_dimension > 0),
    projection_text       TEXT NOT NULL,
    status                VARCHAR(32) NOT NULL DEFAULT 'READY'
                          CHECK (status IN ('PENDING', 'READY', 'FAILED', 'STALE')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category_id, catalog_revision, locale, embedding_profile_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_catalog_category_embeddings_lookup
    ON catalog_category_embeddings (catalog_revision, locale, embedding_profile_id)
    WHERE status = 'READY';

CREATE TABLE IF NOT EXISTS category_embedding_jobs (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id            UUID NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    catalog_revision       INTEGER NOT NULL CHECK (catalog_revision >= 0),
    locale                 VARCHAR(16) NOT NULL,
    embedding_profile_id   UUID NOT NULL,
    content_hash           VARCHAR(64) NOT NULL,
    projection_text        TEXT NOT NULL,
    status                 VARCHAR(32) NOT NULL DEFAULT 'PENDING'
                           CHECK (status IN ('PENDING', 'READY', 'FAILED', 'STALE')),
    attempts               INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts           INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
    last_error             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category_id, catalog_revision, locale, embedding_profile_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_category_embedding_jobs_pending
    ON category_embedding_jobs (status, created_at)
    WHERE status = 'PENDING';
