-- V023: ADR-012 — answer integrity, claim grounding, recommendation safety.
-- Fact links, source precedence, quality circuit breakers, feedback snapshots,
-- error classes, shadow comparisons. Existing snapshot/campaign/payment FKs reused.

CREATE TABLE IF NOT EXISTS source_precedence_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL,
    version         INTEGER      NOT NULL DEFAULT 1,
    data_kind       VARCHAR(64)  NOT NULL,
    source_order    TEXT[]       NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (policy_code, version, data_kind)
);

CREATE TABLE IF NOT EXISTS response_facts (
    id                  BIGSERIAL PRIMARY KEY,
    response_id         UUID         NOT NULL,
    fact_id             VARCHAR(64)  NOT NULL,
    fact_type           VARCHAR(64)  NOT NULL,
    value_text          TEXT         NOT NULL,
    truth_status        VARCHAR(32)  NOT NULL,
    display_label       TEXT,
    checked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (response_id, fact_id)
);

CREATE INDEX IF NOT EXISTS idx_response_facts_response
    ON response_facts (response_id);

CREATE TABLE IF NOT EXISTS response_fact_links (
    id              BIGSERIAL PRIMARY KEY,
    response_fact_id BIGINT NOT NULL REFERENCES response_facts(id) ON DELETE CASCADE,
    evidence_key    VARCHAR(64)  NOT NULL,
    evidence_id     TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (response_fact_id, evidence_key)
);

CREATE TABLE IF NOT EXISTS quality_circuit_breakers (
    id              BIGSERIAL PRIMARY KEY,
    scope           VARCHAR(64)  NOT NULL,
    source_key      VARCHAR(128) NOT NULL,
    action          VARCHAR(64)  NOT NULL,
    metric_value    NUMERIC(10,6),
    threshold_value NUMERIC(10,6),
    is_open         BOOLEAN      NOT NULL DEFAULT TRUE,
    opened_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    UNIQUE (scope, source_key, action)
);

CREATE TABLE IF NOT EXISTS feedback_result_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    feedback_id         UUID         NOT NULL,
    query_version       INTEGER      NOT NULL,
    parsed_constraints  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision    TEXT,
    price_snapshot      TEXT,
    campaign_snapshot   TEXT,
    selected_product    TEXT,
    selected_bank       TEXT,
    response_fact_ids   TEXT[]       NOT NULL DEFAULT '{}',
    error_class         VARCHAR(64),
    user_note           TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_result_snapshots_feedback
    ON feedback_result_snapshots (feedback_id);

CREATE TABLE IF NOT EXISTS error_class_events (
    id              BIGSERIAL PRIMARY KEY,
    error_class     VARCHAR(64)  NOT NULL
                    CHECK (error_class <> 'WRONG_ANSWER'),
    source_component VARCHAR(128),
    metric_key      VARCHAR(128),
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_error_class_events_class_time
    ON error_class_events (error_class, created_at DESC);

CREATE TABLE IF NOT EXISTS shadow_mode_comparisons (
    id              BIGSERIAL PRIMARY KEY,
    comparison_key  VARCHAR(128) NOT NULL,
    live_payload    JSONB        NOT NULL,
    shadow_payload  JSONB        NOT NULL,
    diffs           TEXT[]       NOT NULL DEFAULT '{}',
    shown_to_user   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed default precedence (policy_code DEFAULT / version 1)
INSERT INTO source_precedence_policies (policy_code, version, data_kind, source_order)
VALUES
    ('DEFAULT', 1, 'PRODUCT_ATTRIBUTE',
     ARRAY['manufacturer', 'merchant_feed', 'merchant_page', 'enrichment']),
    ('DEFAULT', 1, 'PRICE',
     ARRAY['merchant_api', 'merchant_feed', 'merchant_page']),
    ('DEFAULT', 1, 'STOCK',
     ARRAY['merchant_api', 'merchant_feed', 'merchant_page']),
    ('DEFAULT', 1, 'BANK_CAMPAIGN',
     ARRAY['bank_api', 'bank_official', 'merchant_agreement']),
    ('DEFAULT', 1, 'MERCHANT_BANK_AGREEMENT',
     ARRAY['taksitlio_verified', 'merchant_source', 'bank_source']),
    ('DEFAULT', 1, 'MONTHLY_PAYMENT',
     ARRAY['source_provided_plan', 'deterministic_calculation']),
    ('DEFAULT', 1, 'PRODUCT_IMAGE',
     ARRAY['merchant_verified', 'manufacturer_verified'])
ON CONFLICT (policy_code, version, data_kind) DO NOTHING;
