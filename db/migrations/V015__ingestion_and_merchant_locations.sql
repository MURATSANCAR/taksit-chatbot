-- V015: ADR-010 P0 — ingestion sources + merchant locations
-- Extends V004 merchants; does not create products/campaigns yet.

-- ---------------------------------------------------------------------------
-- Merchant locations (branches). Product catalog stays at merchant level.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS merchant_locations (
    id              BIGSERIAL PRIMARY KEY,
    merchant_id     BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    location_code   VARCHAR(64)  NOT NULL,
    display_name    VARCHAR(256) NOT NULL,
    city            VARCHAR(128),
    district        VARCHAR(128),
    address_line    TEXT,
    postal_code     VARCHAR(32),
    country_code    VARCHAR(2)   NOT NULL DEFAULT 'TR',
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'CLOSED')),
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (merchant_id, location_code)
);

CREATE INDEX IF NOT EXISTS idx_merchant_locations_merchant_status
    ON merchant_locations (merchant_id, status);

CREATE INDEX IF NOT EXISTS idx_merchant_locations_city
    ON merchant_locations (city)
    WHERE status = 'ACTIVE';

-- Optional searchable aliases for dynamic fuzzy resolution (no static code maps).
CREATE TABLE IF NOT EXISTS merchant_aliases (
    id              BIGSERIAL PRIMARY KEY,
    merchant_id     BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    alias_text      VARCHAR(256) NOT NULL,
    normalized_alias VARCHAR(256) NOT NULL,
    locale          VARCHAR(16)  NOT NULL DEFAULT 'tr-TR',
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (normalized_alias, locale)
);

CREATE INDEX IF NOT EXISTS idx_merchant_aliases_normalized
    ON merchant_aliases (normalized_alias)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Ingestion source catalog
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_sources (
    id                  BIGSERIAL PRIMARY KEY,
    merchant_id         BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    source_code         VARCHAR(64)  NOT NULL UNIQUE,
    source_type         VARCHAR(32)  NOT NULL
                        CHECK (source_type IN (
                            'API', 'FEED_XML', 'FEED_CSV', 'FEED_JSON',
                            'AFFILIATE', 'SITEMAP_JSONLD', 'HTML', 'BROWSER'
                        )),
    base_url            TEXT,
    adapter_code        VARCHAR(128) NOT NULL,
    status              VARCHAR(32)  NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN (
                            'DRAFT', 'ACTIVE', 'PAUSED', 'DEGRADED', 'DISABLED'
                        )),
    priority            INTEGER      NOT NULL DEFAULT 100,
    schedule_policy_id  BIGINT,
    credential_ref      TEXT,
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    consecutive_failures INTEGER     NOT NULL DEFAULT 0,
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_sources_merchant_status
    ON ingestion_sources (merchant_id, status);

CREATE INDEX IF NOT EXISTS idx_ingestion_sources_adapter
    ON ingestion_sources (adapter_code);

CREATE TABLE IF NOT EXISTS ingestion_source_capabilities (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    capability      VARCHAR(64)  NOT NULL
                    CHECK (capability IN (
                        'PRODUCT_DISCOVERY', 'PRODUCT_DETAIL', 'PRICE', 'STOCK',
                        'MEDIA', 'CATEGORY', 'ATTRIBUTE', 'CAMPAIGN',
                        'FINANCE_OPTION', 'BRANCH_AVAILABILITY'
                    )),
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, capability)
);

-- credential_ref points at secret manager; never store secrets here.
CREATE TABLE IF NOT EXISTS ingestion_source_credentials (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    credential_ref  TEXT         NOT NULL,
    purpose         VARCHAR(64)  NOT NULL DEFAULT 'DEFAULT',
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'ROTATED', 'REVOKED')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, purpose)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    run_type        VARCHAR(64)  NOT NULL
                    CHECK (run_type IN (
                        'PRODUCT_DISCOVERY', 'PRODUCT_DETAIL', 'PRICE_REFRESH',
                        'STOCK_REFRESH', 'MEDIA_FETCH', 'CAMPAIGN_REFRESH',
                        'RATE_REFRESH', 'FAILED_ITEM_RETRY', 'FULL'
                    )),
    status          VARCHAR(32)  NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN (
                        'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED',
                        'PARTIAL', 'CANCELLED'
                    )),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    items_discovered INTEGER     NOT NULL DEFAULT 0,
    items_changed   INTEGER      NOT NULL DEFAULT 0,
    items_skipped   INTEGER      NOT NULL DEFAULT 0,
    items_failed    INTEGER      NOT NULL DEFAULT 0,
    error_code      VARCHAR(64),
    error_summary   TEXT,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_started
    ON ingestion_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_run_items (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT       NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
    external_item_id VARCHAR(256),
    item_kind       VARCHAR(64)  NOT NULL DEFAULT 'PRODUCT',
    content_hash    VARCHAR(128),
    action          VARCHAR(32)  NOT NULL
                    CHECK (action IN (
                        'DISCOVERED', 'UNCHANGED', 'UPSERTED', 'SKIPPED', 'FAILED'
                    )),
    error_code      VARCHAR(64),
    error_detail    TEXT,
    source_reference TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_run_items_run
    ON ingestion_run_items (run_id);

CREATE TABLE IF NOT EXISTS ingestion_failures (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    run_id          BIGINT       REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    failure_code    VARCHAR(64)  NOT NULL
                    CHECK (failure_code IN (
                        'SOURCE_TIMEOUT', 'SOURCE_BLOCKED', 'SOURCE_SCHEMA_CHANGED',
                        'PRODUCT_PARSE_FAILED', 'MEDIA_FETCH_FAILED',
                        'CAMPAIGN_PARSE_FAILED', 'RATE_UNAVAILABLE',
                        'AUTH_FAILED', 'RATE_LIMITED', 'UNKNOWN'
                    )),
    external_item_id VARCHAR(256),
    detail          TEXT,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_failures_source_open
    ON ingestion_failures (source_id, occurred_at DESC)
    WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS source_rate_limits (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    window_seconds  INTEGER      NOT NULL DEFAULT 60,
    max_requests    INTEGER      NOT NULL DEFAULT 60,
    burst           INTEGER      NOT NULL DEFAULT 10,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source_id)
);

CREATE TABLE IF NOT EXISTS source_health_status (
    source_id           BIGINT PRIMARY KEY REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    health              VARCHAR(32)  NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (health IN (
                            'HEALTHY', 'DEGRADED', 'UNAVAILABLE', 'UNKNOWN'
                        )),
    last_check_at       TIMESTAMPTZ,
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    consecutive_failures INTEGER     NOT NULL DEFAULT 0,
    detail              TEXT,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Merchant-level activation gate (ADR-010 §74). Default BLOCKED until verified.
ALTER TABLE merchants
    ADD COLUMN IF NOT EXISTS activation_gate VARCHAR(32) NOT NULL DEFAULT 'BLOCKED'
        CHECK (activation_gate IN ('READY', 'PARTIAL', 'BLOCKED'));

ALTER TABLE merchants
    ADD COLUMN IF NOT EXISTS canonical_name VARCHAR(256);

ALTER TABLE merchants
    ADD COLUMN IF NOT EXISTS normalized_name VARCHAR(256);

CREATE INDEX IF NOT EXISTS idx_merchants_normalized_name
    ON merchants (normalized_name)
    WHERE status = 'ACTIVE';
