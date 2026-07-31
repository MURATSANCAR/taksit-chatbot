-- V020: ADR-010 P5 — freshness TTL policies + ingestion scheduler jobs
-- Search-driven refresh enqueues work; never blocks the user request.

CREATE TABLE IF NOT EXISTS freshness_ttl_policies (
    id                  BIGSERIAL PRIMARY KEY,
    policy_code         VARCHAR(64)  NOT NULL UNIQUE,
    price_ttl_seconds   INTEGER      NOT NULL DEFAULT 3600,
    stock_ttl_seconds   INTEGER      NOT NULL DEFAULT 3600,
    product_ttl_seconds INTEGER      NOT NULL DEFAULT 86400,
    image_ttl_seconds   INTEGER      NOT NULL DEFAULT 604800,
    campaign_ttl_seconds INTEGER     NOT NULL DEFAULT 3600,
    bank_terms_ttl_seconds INTEGER   NOT NULL DEFAULT 3600,
    location_ttl_seconds INTEGER     NOT NULL DEFAULT 86400,
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO freshness_ttl_policies (policy_code)
VALUES ('DEFAULT_FRESHNESS_TTL_V1')
ON CONFLICT (policy_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS ingestion_scheduler_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    queue_name          VARCHAR(64)  NOT NULL
                        CHECK (queue_name IN (
                            'PRODUCT_DISCOVERY', 'PRODUCT_DETAIL', 'PRICE_REFRESH',
                            'STOCK_REFRESH', 'MEDIA_FETCH', 'CAMPAIGN_REFRESH',
                            'RATE_REFRESH', 'FAILED_ITEM_RETRY'
                        )),
    source_id           BIGINT       REFERENCES ingestion_sources(id) ON DELETE SET NULL,
    product_id          BIGINT       REFERENCES products(id) ON DELETE SET NULL,
    external_item_id    VARCHAR(256),
    priority            INTEGER      NOT NULL DEFAULT 100,
    status              VARCHAR(32)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING', 'LEASED', 'RUNNING', 'SUCCEEDED',
                            'FAILED', 'CANCELLED'
                        )),
    lease_owner         VARCHAR(128),
    lease_until         TIMESTAMPTZ,
    attempts            INTEGER      NOT NULL DEFAULT 0,
    max_attempts        INTEGER      NOT NULL DEFAULT 5,
    error_code          VARCHAR(64),
    error_detail        TEXT,
    payload             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    available_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_scheduler_jobs_poll
    ON ingestion_scheduler_jobs (queue_name, status, priority, available_at)
    WHERE status IN ('PENDING', 'LEASED');

-- Prevent two workers from leasing the same logical item concurrently.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_scheduler_jobs_active_item
    ON ingestion_scheduler_jobs (queue_name, source_id, external_item_id)
    WHERE status IN ('PENDING', 'LEASED', 'RUNNING')
      AND external_item_id IS NOT NULL;
