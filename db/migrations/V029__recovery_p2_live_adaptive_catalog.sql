-- V029: Recovery P2-LIVE — event-driven adaptive catalog, controlled learning,
-- merchant readiness policies, ranking feature projection, continuous golden.
-- Business names/thresholds live in tables — not production code constants.

-- ---------------------------------------------------------------------------
-- Generic learning lifecycle (never create PROMOTED directly)
-- ---------------------------------------------------------------------------
-- Status vocabulary used across learning tables:
--   OBSERVED | CANDIDATE | VALIDATED | SHADOW | PROMOTED | REJECTED | ROLLED_BACK

-- ---------------------------------------------------------------------------
-- Catalog domain events (selective projection refresh)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_domain_events (
    event_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type            VARCHAR(64) NOT NULL,
    source_id             TEXT,
    source_revision       TEXT,
    source_item_id        TEXT,
    ingestion_run_id      TEXT,
    content_hash          TEXT,
    entity_type           VARCHAR(64),
    entity_id             TEXT,
    merchant_id           BIGINT REFERENCES merchants(id) ON DELETE SET NULL,
    product_id            BIGINT REFERENCES products(id) ON DELETE SET NULL,
    offer_id              BIGINT REFERENCES product_offers(id) ON DELETE SET NULL,
    payload               JSONB NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision      TEXT,
    received_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at          TIMESTAMPTZ,
    processing_status     VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        CHECK (processing_status IN (
            'PENDING', 'PROCESSING', 'DONE', 'FAILED', 'QUARANTINED', 'SKIPPED_IDEMPOTENT'
        )),
    error_code            TEXT,
    attempt               INT NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_catalog_domain_events_pending
    ON catalog_domain_events (processing_status, received_at)
    WHERE processing_status IN ('PENDING', 'FAILED');

CREATE INDEX IF NOT EXISTS idx_catalog_domain_events_type
    ON catalog_domain_events (event_type, received_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_domain_events_idempotency
    ON catalog_domain_events (source_id, source_item_id, source_revision, content_hash)
    WHERE source_id IS NOT NULL AND source_item_id IS NOT NULL
      AND source_revision IS NOT NULL AND content_hash IS NOT NULL;

-- Feed processing funnel metrics (revision-scoped snapshots)
CREATE TABLE IF NOT EXISTS feed_processing_metrics (
    id                        BIGSERIAL PRIMARY KEY,
    catalog_revision          TEXT NOT NULL,
    feed_revision             TEXT,
    measured_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    feed_received_count       BIGINT NOT NULL DEFAULT 0,
    feed_deduplicated_count   BIGINT NOT NULL DEFAULT 0,
    feed_processed_count      BIGINT NOT NULL DEFAULT 0,
    feed_rejected_count       BIGINT NOT NULL DEFAULT 0,
    feed_quarantined_count    BIGINT NOT NULL DEFAULT 0,
    feed_pending_count        BIGINT NOT NULL DEFAULT 0,
    feed_failed_count         BIGINT NOT NULL DEFAULT 0,
    feed_retry_count          BIGINT NOT NULL DEFAULT 0,
    feed_processing_lag_seconds DOUBLE PRECISION,
    db_persisted_count        BIGINT NOT NULL DEFAULT 0,
    projection_ready_count    BIGINT NOT NULL DEFAULT 0,
    media_ready_count         BIGINT NOT NULL DEFAULT 0,
    finance_ready_count       BIGINT NOT NULL DEFAULT 0,
    search_ready_count        BIGINT NOT NULL DEFAULT 0,
    details                   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_feed_processing_metrics_measured
    ON feed_processing_metrics (measured_at DESC);

-- ---------------------------------------------------------------------------
-- Source taxonomy learning (merchant-scoped; no cross-merchant name equality)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_taxonomies (
    id                BIGSERIAL PRIMARY KEY,
    merchant_id       BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL,
    taxonomy_code     TEXT NOT NULL,
    display_name      TEXT,
    status            VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    catalog_revision  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (merchant_id, source_id, taxonomy_code)
);

CREATE TABLE IF NOT EXISTS source_taxonomy_nodes (
    id                    BIGSERIAL PRIMARY KEY,
    source_taxonomy_id    BIGINT NOT NULL REFERENCES source_taxonomies(id) ON DELETE CASCADE,
    source_node_id        TEXT NOT NULL,
    parent_source_node_id TEXT,
    path                  TEXT NOT NULL,
    normalized_path       TEXT NOT NULL,
    depth                 INT NOT NULL DEFAULT 0,
    raw_label             TEXT,
    status                VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sample_product_count  BIGINT NOT NULL DEFAULT 0,
    UNIQUE (source_taxonomy_id, source_node_id)
);

CREATE INDEX IF NOT EXISTS idx_source_taxonomy_nodes_path
    ON source_taxonomy_nodes (source_taxonomy_id, normalized_path);

CREATE TABLE IF NOT EXISTS taxonomy_mapping_candidates (
    id                    BIGSERIAL PRIMARY KEY,
    source_taxonomy_id    BIGINT NOT NULL REFERENCES source_taxonomies(id) ON DELETE CASCADE,
    source_node_id        BIGINT NOT NULL REFERENCES source_taxonomy_nodes(id) ON DELETE CASCADE,
    candidate_category_id BIGINT REFERENCES categories(id) ON DELETE SET NULL,
    learning_status       VARCHAR(32) NOT NULL DEFAULT 'OBSERVED'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
    candidate_gap         DOUBLE PRECISION,
    evidence_score        DOUBLE PRECISION NOT NULL DEFAULT 0,
    sample_consistency    DOUBLE PRECISION,
    conflict_count        INT NOT NULL DEFAULT 0,
    observation_count     INT NOT NULL DEFAULT 0,
    match_method          VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    evidence              JSONB NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision      TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at           TIMESTAMPTZ,
    rejected_at           TIMESTAMPTZ,
    CONSTRAINT taxonomy_mapping_candidates_no_direct_promoted
        CHECK (learning_status <> 'PROMOTED' OR promoted_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_mapping_candidates_status
    ON taxonomy_mapping_candidates (learning_status, confidence DESC);

CREATE TABLE IF NOT EXISTS taxonomy_mapping_versions (
    id                    BIGSERIAL PRIMARY KEY,
    source_taxonomy_id    BIGINT NOT NULL REFERENCES source_taxonomies(id) ON DELETE CASCADE,
    source_node_id        BIGINT NOT NULL REFERENCES source_taxonomy_nodes(id) ON DELETE CASCADE,
    canonical_category_id BIGINT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    candidate_id          BIGINT REFERENCES taxonomy_mapping_candidates(id) ON DELETE SET NULL,
    version               INT NOT NULL DEFAULT 1,
    status                VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('DRAFT', 'SHADOW', 'ACTIVE', 'SUPERSEDED', 'ROLLED_BACK')),
    catalog_revision      TEXT NOT NULL,
    published_at          TIMESTAMPTZ,
    rolled_back_at        TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_taxonomy_id, source_node_id, version)
);

CREATE TABLE IF NOT EXISTS taxonomy_mapping_evidence (
    id              BIGSERIAL PRIMARY KEY,
    candidate_id    BIGINT NOT NULL REFERENCES taxonomy_mapping_candidates(id) ON DELETE CASCADE,
    evidence_type   VARCHAR(64) NOT NULL,
    weight          DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    polarity        VARCHAR(16) NOT NULL DEFAULT 'POSITIVE'
        CHECK (polarity IN ('POSITIVE', 'NEGATIVE')),
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_mapping_evidence_candidate
    ON taxonomy_mapping_evidence (candidate_id, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Brand / alias / attribute learning
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brand_learning_candidates (
    id                 BIGSERIAL PRIMARY KEY,
    merchant_id        BIGINT REFERENCES merchants(id) ON DELETE SET NULL,
    observed_token     TEXT NOT NULL,
    normalized_token   TEXT NOT NULL,
    candidate_brand_id BIGINT REFERENCES brands(id) ON DELETE SET NULL,
    learning_status    VARCHAR(32) NOT NULL DEFAULT 'OBSERVED'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    confidence         DOUBLE PRECISION NOT NULL DEFAULT 0,
    candidate_gap      DOUBLE PRECISION,
    match_count        INT NOT NULL DEFAULT 0,
    conflict_count     INT NOT NULL DEFAULT 0,
    match_method       VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at        TIMESTAMPTZ,
    rejected_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_brand_learning_candidates_token
    ON brand_learning_candidates (normalized_token, learning_status);

CREATE TABLE IF NOT EXISTS attribute_definitions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attribute_code     TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    value_type         VARCHAR(32) NOT NULL
        CHECK (value_type IN ('STRING', 'NUMERIC', 'BOOLEAN', 'ENUM', 'DIMENSION')),
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attribute_units (
    id                 BIGSERIAL PRIMARY KEY,
    unit_code          TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    dimension          VARCHAR(32) NOT NULL,
    to_base_factor     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS attribute_aliases (
    id                 BIGSERIAL PRIMARY KEY,
    attribute_id       UUID NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE,
    alias_text         TEXT NOT NULL,
    normalized_alias   TEXT NOT NULL,
    learning_status    VARCHAR(32) NOT NULL DEFAULT 'PROMOTED'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    locale             VARCHAR(16),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (attribute_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS category_attribute_policies (
    id                 BIGSERIAL PRIMARY KEY,
    category_id        BIGINT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    attribute_id       UUID NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE,
    required           BOOLEAN NOT NULL DEFAULT FALSE,
    filterable         BOOLEAN NOT NULL DEFAULT TRUE,
    policy_version     INT NOT NULL DEFAULT 1,
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    UNIQUE (category_id, attribute_id, policy_version)
);

CREATE TABLE IF NOT EXISTS attribute_extraction_candidates (
    id                 BIGSERIAL PRIMARY KEY,
    product_id         BIGINT REFERENCES products(id) ON DELETE CASCADE,
    attribute_id       UUID REFERENCES attribute_definitions(id) ON DELETE SET NULL,
    raw_value          TEXT,
    normalized_value   TEXT,
    unit_code          TEXT,
    source             VARCHAR(64) NOT NULL,
    confidence         DOUBLE PRECISION NOT NULL DEFAULT 0,
    evidence_span      TEXT,
    extractor_version  TEXT NOT NULL DEFAULT 'v1',
    learning_status    VARCHAR(32) NOT NULL DEFAULT 'OBSERVED'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    catalog_revision   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attribute_extraction_candidates_product
    ON attribute_extraction_candidates (product_id, learning_status);

CREATE TABLE IF NOT EXISTS attribute_extraction_versions (
    id                 BIGSERIAL PRIMARY KEY,
    attribute_id       UUID NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE,
    extractor_version  TEXT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'SHADOW'
        CHECK (status IN ('DRAFT', 'SHADOW', 'ACTIVE', 'SUPERSEDED', 'ROLLED_BACK')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (attribute_id, extractor_version)
);

-- Query alias learning (never single-observation promote)
CREATE TABLE IF NOT EXISTS query_resolution_observations (
    id                     BIGSERIAL PRIMARY KEY,
    observation_id         UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    raw_token              TEXT NOT NULL,
    normalized_token       TEXT NOT NULL,
    entity_type            VARCHAR(64) NOT NULL,
    resolved_entity_id     TEXT,
    resolution_confidence  DOUBLE PRECISION,
    top1_top2_gap          DOUBLE PRECISION,
    match_method           VARCHAR(64),
    context                VARCHAR(64),
    tenant_scope           VARCHAR(32) NOT NULL DEFAULT 'GLOBAL_ENTITY_LEARNING'
        CHECK (tenant_scope IN (
            'USER_PREFERENCE_MEMORY', 'TENANT_PREFERENCE', 'GLOBAL_ENTITY_LEARNING'
        )),
    anonymized             BOOLEAN NOT NULL DEFAULT TRUE,
    signal_type            VARCHAR(64),
    ranking_policy_version TEXT,
    catalog_revision       TEXT,
    search_session_id      TEXT,
    observed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload                JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_query_resolution_observations_token
    ON query_resolution_observations (normalized_token, entity_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS alias_learning_candidates (
    id                     BIGSERIAL PRIMARY KEY,
    entity_type            VARCHAR(64) NOT NULL,
    observed_alias         TEXT NOT NULL,
    normalized_alias       TEXT NOT NULL,
    candidate_entity_id    TEXT NOT NULL,
    learning_status        VARCHAR(32) NOT NULL DEFAULT 'OBSERVED'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    confidence             DOUBLE PRECISION NOT NULL DEFAULT 0,
    candidate_gap          DOUBLE PRECISION,
    observation_count      INT NOT NULL DEFAULT 0,
    positive_evidence      INT NOT NULL DEFAULT 0,
    negative_evidence      INT NOT NULL DEFAULT 0,
    match_method           VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    context                VARCHAR(64),
    evidence               JSONB NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision       TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at            TIMESTAMPTZ,
    rejected_at            TIMESTAMPTZ,
    CONSTRAINT alias_learning_no_zero_obs_promote
        CHECK (learning_status <> 'PROMOTED' OR observation_count >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_alias_learning_candidates_key
    ON alias_learning_candidates (entity_type, normalized_alias, candidate_entity_id);

CREATE TABLE IF NOT EXISTS alias_learning_evidence (
    id              BIGSERIAL PRIMARY KEY,
    candidate_id    BIGINT NOT NULL REFERENCES alias_learning_candidates(id) ON DELETE CASCADE,
    evidence_type   VARCHAR(64) NOT NULL,
    polarity        VARCHAR(16) NOT NULL DEFAULT 'POSITIVE'
        CHECK (polarity IN ('POSITIVE', 'NEGATIVE')),
    weight          DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    observation_id  BIGINT REFERENCES query_resolution_observations(id) ON DELETE SET NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alias_learning_versions (
    id                  BIGSERIAL PRIMARY KEY,
    candidate_id        BIGINT REFERENCES alias_learning_candidates(id) ON DELETE SET NULL,
    entity_type         VARCHAR(64) NOT NULL,
    alias_text          TEXT NOT NULL,
    normalized_alias    TEXT NOT NULL,
    entity_id           TEXT NOT NULL,
    version             INT NOT NULL DEFAULT 1,
    status              VARCHAR(32) NOT NULL DEFAULT 'SHADOW'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    catalog_revision    TEXT NOT NULL,
    published_at        TIMESTAMPTZ,
    rolled_back_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Ranking adaptation (champion / challenger; safety rules stay deterministic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ranking_feature_definitions (
    id                 BIGSERIAL PRIMARY KEY,
    feature_code       TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    feature_kind       VARCHAR(32) NOT NULL DEFAULT 'PRECOMPUTED'
        CHECK (feature_kind IN ('PRECOMPUTED', 'QUERY_DEPENDENT', 'USER_DEPENDENT')),
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ranking_policy_versions (
    id                 BIGSERIAL PRIMARY KEY,
    policy_code        TEXT NOT NULL,
    version            INT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK', 'SUPERSEDED')),
    role               VARCHAR(32) NOT NULL DEFAULT 'CHALLENGER'
        CHECK (role IN ('CHAMPION', 'CHALLENGER', 'RETIRED')),
    weights            JSONB NOT NULL DEFAULT '{}'::jsonb,
    traffic_pct        DOUBLE PRECISION NOT NULL DEFAULT 0,
    catalog_revision   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at       TIMESTAMPTZ,
    rolled_back_at     TIMESTAMPTZ,
    UNIQUE (policy_code, version)
);

CREATE TABLE IF NOT EXISTS ranking_experiments (
    id                 BIGSERIAL PRIMARY KEY,
    experiment_code    TEXT NOT NULL UNIQUE,
    champion_version_id BIGINT NOT NULL REFERENCES ranking_policy_versions(id),
    challenger_version_id BIGINT NOT NULL REFERENCES ranking_policy_versions(id),
    status             VARCHAR(32) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'ABORTED', 'PROMOTED', 'REJECTED')),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    gate_result        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ranking_feedback_events (
    id                     BIGSERIAL PRIMARY KEY,
    event_type             VARCHAR(64) NOT NULL,
    polarity               VARCHAR(16) NOT NULL DEFAULT 'POSITIVE'
        CHECK (polarity IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL')),
    query_version          TEXT,
    ranking_policy_version TEXT,
    catalog_revision       TEXT,
    product_id             BIGINT REFERENCES products(id) ON DELETE SET NULL,
    position               INT,
    search_session_id      TEXT,
    tenant_scope           VARCHAR(32) NOT NULL DEFAULT 'USER_PREFERENCE_MEMORY',
    anonymized             BOOLEAN NOT NULL DEFAULT TRUE,
    event_time             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload                JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_ranking_feedback_events_time
    ON ranking_feedback_events (event_time DESC);

CREATE TABLE IF NOT EXISTS product_ranking_feature_projection (
    product_id                 BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    offer_id                   BIGINT REFERENCES product_offers(id) ON DELETE SET NULL,
    merchant_id                BIGINT REFERENCES merchants(id) ON DELETE SET NULL,
    category_id                BIGINT REFERENCES categories(id) ON DELETE SET NULL,
    brand_id                   BIGINT REFERENCES brands(id) ON DELETE SET NULL,
    price_rank                 DOUBLE PRECISION,
    stock_rank                 DOUBLE PRECISION,
    media_rank                 DOUBLE PRECISION,
    quality_rank               DOUBLE PRECISION,
    freshness_rank             DOUBLE PRECISION,
    finance_count              INT NOT NULL DEFAULT 0,
    minimum_monthly_payment    NUMERIC(18, 4),
    minimum_total_repayment    NUMERIC(18, 4),
    maximum_term               INT,
    merchant_readiness         VARCHAR(32),
    feature_revision           TEXT NOT NULL,
    rebuilt_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id)
);

CREATE INDEX IF NOT EXISTS idx_product_ranking_feature_projection_merchant
    ON product_ranking_feature_projection (merchant_id, merchant_readiness);

-- ---------------------------------------------------------------------------
-- Media quality policy store (no merchant-name thresholds in code)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_quality_policies (
    id                 BIGSERIAL PRIMARY KEY,
    policy_code        TEXT NOT NULL,
    version            INT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    card_ready_rules   JSONB NOT NULL DEFAULT '{}'::jsonb,
    detail_ready_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at       TIMESTAMPTZ,
    UNIQUE (policy_code, version)
);

CREATE TABLE IF NOT EXISTS media_quality_learning_candidates (
    id                 BIGSERIAL PRIMARY KEY,
    candidate_code     TEXT NOT NULL,
    learning_status    VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE'
        CHECK (learning_status IN (
            'OBSERVED', 'CANDIDATE', 'VALIDATED', 'SHADOW',
            'PROMOTED', 'REJECTED', 'ROLLED_BACK'
        )),
    rules              JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Merchant readiness (event-driven snapshots + versioned policies)
-- ---------------------------------------------------------------------------
ALTER TABLE merchants
    DROP CONSTRAINT IF EXISTS merchants_activation_gate_check;
ALTER TABLE merchants
    ADD CONSTRAINT merchants_activation_gate_check
    CHECK (activation_gate IN ('READY', 'PARTIAL', 'BLOCKED', 'DEGRADED', 'DISABLED'));

CREATE TABLE IF NOT EXISTS merchant_readiness_policies (
    id                 BIGSERIAL PRIMARY KEY,
    policy_code        TEXT NOT NULL DEFAULT 'default',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant_readiness_policy_versions (
    id                 BIGSERIAL PRIMARY KEY,
    policy_id          BIGINT NOT NULL REFERENCES merchant_readiness_policies(id) ON DELETE CASCADE,
    version            INT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    thresholds         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at       TIMESTAMPTZ,
    UNIQUE (policy_id, version)
);

CREATE TABLE IF NOT EXISTS merchant_readiness_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    merchant_id             BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    catalog_revision        TEXT NOT NULL,
    active_products         BIGINT NOT NULL DEFAULT 0,
    searchable_products     BIGINT NOT NULL DEFAULT 0,
    category_coverage       DOUBLE PRECISION NOT NULL DEFAULT 0,
    brand_coverage          DOUBLE PRECISION NOT NULL DEFAULT 0,
    attribute_coverage      DOUBLE PRECISION NOT NULL DEFAULT 0,
    stock_coverage          DOUBLE PRECISION NOT NULL DEFAULT 0,
    card_media_coverage     DOUBLE PRECISION NOT NULL DEFAULT 0,
    fresh_price_coverage    DOUBLE PRECISION NOT NULL DEFAULT 0,
    valid_url_coverage      DOUBLE PRECISION NOT NULL DEFAULT 0,
    finance_coverage        DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_plan_coverage   DOUBLE PRECISION NOT NULL DEFAULT 0,
    golden_pass_rate        DOUBLE PRECISION,
    critical_error_count    INT NOT NULL DEFAULT 0,
    status                  VARCHAR(32) NOT NULL
        CHECK (status IN ('READY', 'PARTIAL', 'BLOCKED', 'DEGRADED', 'DISABLED')),
    previous_status         VARCHAR(32),
    policy_version_id       BIGINT REFERENCES merchant_readiness_policy_versions(id),
    reasons                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    evaluated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_merchant_readiness_snapshots_merchant
    ON merchant_readiness_snapshots (merchant_id, evaluated_at DESC);

-- Dynamic search release scope
CREATE TABLE IF NOT EXISTS search_release_scope (
    id                      BIGSERIAL PRIMARY KEY,
    catalog_revision        TEXT NOT NULL,
    merchant_id             BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    category_id             BIGINT REFERENCES categories(id) ON DELETE CASCADE,
    include_in_search       BOOLEAN NOT NULL DEFAULT FALSE,
    readiness_status        VARCHAR(32) NOT NULL,
    media_ready             BOOLEAN NOT NULL DEFAULT FALSE,
    finance_ready           BOOLEAN NOT NULL DEFAULT FALSE,
    reasons                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (catalog_revision, merchant_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_search_release_scope_active
    ON search_release_scope (catalog_revision, include_in_search)
    WHERE include_in_search;

-- ---------------------------------------------------------------------------
-- Continuous golden (CORE immutable + ROLLING reviewed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS continuous_golden_sets (
    id                 BIGSERIAL PRIMARY KEY,
    set_code           TEXT NOT NULL,
    set_kind           VARCHAR(16) NOT NULL
        CHECK (set_kind IN ('CORE_GOLDEN', 'ROLLING_GOLDEN')),
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (set_code)
);

CREATE TABLE IF NOT EXISTS continuous_golden_cases (
    id                 BIGSERIAL PRIMARY KEY,
    set_id             BIGINT NOT NULL REFERENCES continuous_golden_sets(id) ON DELETE CASCADE,
    case_id            TEXT NOT NULL,
    query_text         TEXT NOT NULL,
    expected           JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status      VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (review_status IN ('DRAFT', 'REVIEWED', 'APPROVED', 'REJECTED')),
    prepared_by        TEXT,
    reviewed_by        TEXT,
    source_signal      VARCHAR(64),
    anonymized         BOOLEAN NOT NULL DEFAULT TRUE,
    catalog_revision   TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (set_id, case_id)
);

-- ---------------------------------------------------------------------------
-- Drift detection + Auto Ops job ledger
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalog_drift_alarms (
    id                 BIGSERIAL PRIMARY KEY,
    alarm_code         VARCHAR(64) NOT NULL,
    drift_type         VARCHAR(64) NOT NULL,
    merchant_id        BIGINT REFERENCES merchants(id) ON DELETE SET NULL,
    severity           VARCHAR(16) NOT NULL DEFAULT 'WARNING'
        CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL', 'BLOCKER')),
    status             VARCHAR(32) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED')),
    catalog_revision   TEXT,
    baseline           JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed           JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_taken       TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_catalog_drift_alarms_open
    ON catalog_drift_alarms (status, created_at DESC)
    WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS auto_ops_jobs (
    job_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type           VARCHAR(64) NOT NULL,
    catalog_revision   TEXT,
    affected_scope     JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt            INT NOT NULL DEFAULT 1,
    status             VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN (
            'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED', 'RETRYING'
        )),
    started_at         TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ,
    error_code         TEXT,
    details            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_ops_jobs_type_status
    ON auto_ops_jobs (job_type, status, created_at DESC);

-- Search session revision pinning
ALTER TABLE search_sessions
    ADD COLUMN IF NOT EXISTS catalog_revision TEXT;
ALTER TABLE search_sessions
    ADD COLUMN IF NOT EXISTS entity_index_revision TEXT;
ALTER TABLE search_sessions
    ADD COLUMN IF NOT EXISTS finance_revision TEXT;
ALTER TABLE search_sessions
    ADD COLUMN IF NOT EXISTS ranking_policy_version TEXT;

-- ---------------------------------------------------------------------------
-- Policy seeds (thresholds are data — not code constants)
-- ---------------------------------------------------------------------------
INSERT INTO merchant_readiness_policies (policy_code)
VALUES ('default')
ON CONFLICT DO NOTHING;

INSERT INTO merchant_readiness_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
    '{
      "minimum_searchable_products": 50,
      "minimum_category_coverage": 0.95,
      "minimum_brand_coverage": 0.90,
      "minimum_critical_attribute_coverage": 0.90,
      "minimum_card_media_coverage": 0.95,
      "minimum_fresh_price_coverage": 0.95,
      "minimum_valid_url_coverage": 0.99,
      "maximum_critical_error": 0,
      "minimum_golden_pass_rate": 1.0,
      "wrong_mapping_tolerance": 0,
      "payment_calculation_error_tolerance": 0
    }'::jsonb,
    NOW()
FROM merchant_readiness_policies p
WHERE p.policy_code = 'default'
  AND NOT EXISTS (
    SELECT 1 FROM merchant_readiness_policy_versions v
    WHERE v.policy_id = p.id AND v.version = 1
  );

INSERT INTO media_quality_policies (policy_code, version, status, card_ready_rules, detail_ready_rules, activated_at)
VALUES (
    'default',
    1,
    'ACTIVE',
    '{
      "min_short_edge": 400,
      "min_long_edge": 600,
      "max_aspect_ratio": 3.0,
      "min_aspect_ratio": 0.33,
      "require_decode": true,
      "require_product_relation": true,
      "reject_blank": true,
      "max_bytes": 15728640
    }'::jsonb,
    '{
      "min_short_edge": 800,
      "min_long_edge": 1000,
      "max_aspect_ratio": 3.0,
      "min_aspect_ratio": 0.33,
      "require_decode": true,
      "max_bytes": 15728640
    }'::jsonb,
    NOW()
)
ON CONFLICT (policy_code, version) DO NOTHING;

INSERT INTO ranking_feature_definitions (feature_code, display_name, feature_kind)
VALUES
    ('query_relevance', 'Query relevance', 'QUERY_DEPENDENT'),
    ('required_attribute_coverage', 'Required attribute coverage', 'QUERY_DEPENDENT'),
    ('price_compatibility', 'Price compatibility', 'QUERY_DEPENDENT'),
    ('stock_availability', 'Stock availability', 'PRECOMPUTED'),
    ('image_readiness', 'Image readiness', 'PRECOMPUTED'),
    ('merchant_readiness', 'Merchant readiness', 'PRECOMPUTED'),
    ('finance_availability', 'Finance availability', 'PRECOMPUTED'),
    ('monthly_payment_compatibility', 'Monthly payment compatibility', 'QUERY_DEPENDENT'),
    ('total_repayment_compatibility', 'Total repayment compatibility', 'QUERY_DEPENDENT'),
    ('data_freshness', 'Data freshness', 'PRECOMPUTED')
ON CONFLICT (feature_code) DO NOTHING;

INSERT INTO ranking_policy_versions (policy_code, version, status, role, weights, traffic_pct, activated_at)
VALUES (
    'product_overall_value',
    1,
    'ACTIVE',
    'CHAMPION',
    '{
      "query_relevance": 0.25,
      "attribute_coverage": 0.15,
      "budget_compatibility": 0.15,
      "stock": 0.10,
      "price": 0.10,
      "finance": 0.10,
      "total_repayment": 0.10,
      "freshness": 0.05
    }'::jsonb,
    100,
    NOW()
)
ON CONFLICT (policy_code, version) DO NOTHING;

INSERT INTO continuous_golden_sets (set_code, set_kind, status)
VALUES
    ('core_production_retrieval', 'CORE_GOLDEN', 'ACTIVE'),
    ('rolling_production_queries', 'ROLLING_GOLDEN', 'ACTIVE')
ON CONFLICT (set_code) DO NOTHING;

-- Alias / taxonomy promotion policy (thresholds as versioned config)
CREATE TABLE IF NOT EXISTS learning_promotion_policies (
    id                 BIGSERIAL PRIMARY KEY,
    policy_code        TEXT NOT NULL,
    version            INT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    entity_kind        VARCHAR(64) NOT NULL,
    thresholds         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at       TIMESTAMPTZ,
    UNIQUE (policy_code, version)
);

INSERT INTO learning_promotion_policies (policy_code, version, status, entity_kind, thresholds, activated_at)
VALUES
(
    'alias_promotion',
    1,
    'ACTIVE',
    'ALIAS',
    '{
      "minimum_observations": 5,
      "minimum_confidence": 0.85,
      "minimum_candidate_gap": 0.15,
      "minimum_positive_minus_negative": 3,
      "allow_single_observation_promote": false,
      "require_shadow_before_promote": true
    }'::jsonb,
    NOW()
),
(
    'taxonomy_promotion',
    1,
    'ACTIVE',
    'TAXONOMY',
    '{
      "minimum_observations": 10,
      "minimum_confidence": 0.90,
      "minimum_candidate_gap": 0.20,
      "minimum_sample_consistency": 0.85,
      "maximum_conflict_count": 0,
      "require_shadow_before_promote": true
    }'::jsonb,
    NOW()
),
(
    'brand_promotion',
    1,
    'ACTIVE',
    'BRAND',
    '{
      "minimum_observations": 5,
      "minimum_confidence": 0.88,
      "minimum_candidate_gap": 0.15,
      "maximum_conflict_ratio": 0.05,
      "require_shadow_before_promote": true
    }'::jsonb,
    NOW()
),
(
    'attribute_numeric_validation',
    1,
    'ACTIVE',
    'ATTRIBUTE',
    '{
      "minimum_confidence_for_required_filter": 0.95,
      "require_unit_context": true,
      "reject_cross_dimension": true
    }'::jsonb,
    NOW()
)
ON CONFLICT (policy_code, version) DO NOTHING;
