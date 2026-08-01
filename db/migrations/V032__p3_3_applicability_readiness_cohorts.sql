-- V032: P3.3 applicability-aware readiness + release cohorts.
-- Additive. Removes dependence on static READY merchant count for INTERNAL.
-- Category names are not encoded in application logic; dimension policies are data.

-- Allow INTERNAL as a cohort-oriented feature flag status (internal traffic only).
ALTER TABLE runtime_feature_flags
  DROP CONSTRAINT IF EXISTS runtime_feature_flags_status_check;
ALTER TABLE runtime_feature_flags
  ADD CONSTRAINT runtime_feature_flags_status_check
  CHECK (status IN ('DISABLED', 'SHADOW', 'INTERNAL', 'ENABLED'));

-- ---------------------------------------------------------------------------
-- Quality dimension applicability (per category; NULL category_id = default)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_quality_dimension_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS category_quality_dimension_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES category_quality_dimension_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    -- dimensions: { "BRAND": "REQUIRED|OPTIONAL|NOT_APPLICABLE|SOURCE_DEPENDENT", ... }
    dimensions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- optional per-category overrides: { "<category_id>": { "BRAND": "NOT_APPLICABLE", ... } }
    category_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at    TIMESTAMPTZ,
    UNIQUE (policy_id, version)
);

INSERT INTO category_quality_dimension_policies (policy_code)
VALUES ('default_applicability')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO category_quality_dimension_policy_versions (
  policy_id, version, status, dimensions, category_overrides, activated_at
)
SELECT p.id, 1, 'ACTIVE',
  '{
    "CATEGORY": "REQUIRED",
    "BRAND": "REQUIRED",
    "CRITICAL_ATTRIBUTES": "OPTIONAL",
    "STOCK": "OPTIONAL",
    "CARD_MEDIA": "REQUIRED",
    "PRICE": "REQUIRED",
    "PRODUCT_URL": "REQUIRED",
    "FINANCE": "OPTIONAL",
    "PAYMENT_PLAN": "OPTIONAL",
    "GTIN": "OPTIONAL",
    "MPN": "OPTIONAL",
    "PUBLISHER": "OPTIONAL",
    "AUTHOR": "OPTIONAL",
    "ISBN": "OPTIONAL",
    "MODEL": "OPTIONAL",
    "VARIANT": "OPTIONAL"
  }'::jsonb,
  '{}'::jsonb,
  NOW()
FROM category_quality_dimension_policies p
WHERE p.policy_code = 'default_applicability'
ON CONFLICT (policy_id, version) DO NOTHING;

-- Data-driven override for existing BOOKS_MUSIC taxonomy node (if present).
-- BRAND is NOT_APPLICABLE; publisher/author/isbn remain OPTIONAL (source-backed only).
UPDATE category_quality_dimension_policy_versions v
SET category_overrides = COALESCE(v.category_overrides, '{}'::jsonb) || jsonb_build_object(
  c.id::text,
  jsonb_build_object(
    'BRAND', 'NOT_APPLICABLE',
    'PUBLISHER', 'OPTIONAL',
    'AUTHOR', 'OPTIONAL',
    'ISBN', 'OPTIONAL'
  )
)
FROM categories c, category_quality_dimension_policies p
WHERE v.policy_id = p.id
  AND p.policy_code = 'default_applicability'
  AND v.status = 'ACTIVE'
  AND c.category_code = 'BOOKS_MUSIC'
  AND c.status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Semantic product entity roles (generic; no vertical hardcoding in code)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_entity_roles (
    id                   BIGSERIAL PRIMARY KEY,
    product_id           BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    entity_id            BIGINT,
    role_type            VARCHAR(64) NOT NULL,
    source               TEXT,
    confidence           NUMERIC(8, 6),
    verification_status  VARCHAR(32) NOT NULL DEFAULT 'OBSERVED'
        CHECK (verification_status IN (
          'OBSERVED', 'CANDIDATE', 'VALIDATED', 'REJECTED'
        )),
    catalog_revision     TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, role_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_product_entity_roles_product
  ON product_entity_roles (product_id);

-- ---------------------------------------------------------------------------
-- Source capability profiles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_capability_profiles (
    id                   BIGSERIAL PRIMARY KEY,
    merchant_id          BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    category_id          BIGINT REFERENCES categories(id) ON DELETE CASCADE,
    source_id            TEXT,
    provides_category    VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_brand       VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_attributes  VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_stock       VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_media       VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_gtin        VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_mpn         VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_publisher   VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_author      VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    provides_finance     VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    observed_coverage    JSONB NOT NULL DEFAULT '{}'::jsonb,
    sample_size          INT NOT NULL DEFAULT 0,
    last_observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    catalog_revision     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_capability_merchant_level
  ON source_capability_profiles (merchant_id, source_id)
  WHERE category_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_capability_merchant_category
  ON source_capability_profiles (merchant_id, category_id, source_id)
  WHERE category_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Merchant-category readiness snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_category_readiness_snapshots (
    id                              BIGSERIAL PRIMARY KEY,
    merchant_id                     BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    category_id                     BIGINT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    catalog_revision                TEXT NOT NULL,
    readiness_policy_version        TEXT,
    quality_dimension_policy_version TEXT,
    active_product_count            INT NOT NULL DEFAULT 0,
    eligible_product_count          INT NOT NULL DEFAULT 0,
    search_ready_product_count      INT NOT NULL DEFAULT 0,
    category_resolution_coverage    NUMERIC(8, 6) NOT NULL DEFAULT 0,
    brand_coverage                  NUMERIC(8, 6) NOT NULL DEFAULT 0,
    critical_attribute_coverage     NUMERIC(8, 6) NOT NULL DEFAULT 0,
    stock_coverage                  NUMERIC(8, 6) NOT NULL DEFAULT 0,
    card_media_coverage             NUMERIC(8, 6) NOT NULL DEFAULT 0,
    fresh_price_coverage            NUMERIC(8, 6) NOT NULL DEFAULT 0,
    valid_url_coverage              NUMERIC(8, 6) NOT NULL DEFAULT 0,
    finance_coverage                NUMERIC(8, 6) NOT NULL DEFAULT 0,
    payment_plan_coverage           NUMERIC(8, 6) NOT NULL DEFAULT 0,
    wrong_category_count            INT NOT NULL DEFAULT 0,
    wrong_media_count               INT NOT NULL DEFAULT 0,
    wrong_finance_count             INT NOT NULL DEFAULT 0,
    critical_error_count            INT NOT NULL DEFAULT 0,
    status                          VARCHAR(32) NOT NULL
        CHECK (status IN ('READY', 'PARTIAL', 'BLOCKED', 'DEGRADED', 'DISABLED')),
    failed_policy_rules             JSONB NOT NULL DEFAULT '[]'::jsonb,
    dimension_applicability         JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mc_readiness_merchant_cat
  ON merchant_category_readiness_snapshots (merchant_id, category_id, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS idx_mc_readiness_status
  ON merchant_category_readiness_snapshots (status);

-- ---------------------------------------------------------------------------
-- Product readiness projection
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_readiness_projection (
    product_id                BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    offer_id                  BIGINT REFERENCES product_offers(id) ON DELETE SET NULL,
    merchant_id               BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    category_id               BIGINT REFERENCES categories(id) ON DELETE SET NULL,
    category_ready            BOOLEAN NOT NULL DEFAULT FALSE,
    entity_roles_ready        BOOLEAN NOT NULL DEFAULT FALSE,
    critical_attributes_ready BOOLEAN NOT NULL DEFAULT FALSE,
    stock_ready               BOOLEAN NOT NULL DEFAULT FALSE,
    card_media_ready          BOOLEAN NOT NULL DEFAULT FALSE,
    price_ready               BOOLEAN NOT NULL DEFAULT FALSE,
    url_ready                 BOOLEAN NOT NULL DEFAULT FALSE,
    finance_ready             BOOLEAN NOT NULL DEFAULT FALSE,
    readiness_status          VARCHAR(32) NOT NULL
        CHECK (readiness_status IN (
          'READY_FOR_SEARCH', 'READY_FOR_FINANCE_SEARCH', 'PARTIAL', 'BLOCKED', 'QUARANTINED'
        )),
    failed_dimensions         JSONB NOT NULL DEFAULT '[]'::jsonb,
    catalog_revision          TEXT NOT NULL,
    policy_version            TEXT,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_readiness_merchant_status
  ON product_readiness_projection (merchant_id, readiness_status);

-- ---------------------------------------------------------------------------
-- Release cohorts + policies
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS release_cohort_policies (
    id           BIGSERIAL PRIMARY KEY,
    policy_code  TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS release_cohort_policy_versions (
    id           BIGSERIAL PRIMARY KEY,
    policy_id    BIGINT NOT NULL REFERENCES release_cohort_policies(id) ON DELETE CASCADE,
    version      INT NOT NULL,
    status       VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    thresholds   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    UNIQUE (policy_id, version)
);

INSERT INTO release_cohort_policies (policy_code)
VALUES ('internal_release'), ('public_release')
ON CONFLICT (policy_code) DO NOTHING;

-- INTERNAL: no merchant-count gate; product/leakage/error based.
INSERT INTO release_cohort_policy_versions (
  policy_id, version, status, thresholds, activated_at
)
SELECT p.id, 1, 'ACTIVE',
  '{
    "minimum_search_ready_products": 500,
    "minimum_finance_ready_products": 0,
    "minimum_search_demand_coverage": 0.0,
    "minimum_ready_category_scopes": 1,
    "minimum_golden_bucket_coverage": 0.0,
    "maximum_critical_errors": 0,
    "maximum_projection_leakage": 0,
    "maximum_wrong_mapping": 0,
    "require_merchant_count": false
  }'::jsonb,
  NOW()
FROM release_cohort_policies p
WHERE p.policy_code = 'internal_release'
ON CONFLICT (policy_id, version) DO NOTHING;

-- PUBLIC: stricter (golden coverage required later); not activated here.
INSERT INTO release_cohort_policy_versions (
  policy_id, version, status, thresholds, activated_at
)
SELECT p.id, 1, 'SHADOW',
  '{
    "minimum_search_ready_products": 5000,
    "minimum_finance_ready_products": 100,
    "minimum_search_demand_coverage": 0.5,
    "minimum_ready_category_scopes": 10,
    "minimum_golden_bucket_coverage": 1.0,
    "maximum_critical_errors": 0,
    "maximum_projection_leakage": 0,
    "maximum_wrong_mapping": 0,
    "require_merchant_count": false,
    "minimum_approved_rolling_golden": 250
  }'::jsonb,
  NULL
FROM release_cohort_policies p
WHERE p.policy_code = 'public_release'
ON CONFLICT (policy_id, version) DO NOTHING;

CREATE TABLE IF NOT EXISTS search_release_cohorts (
    id              BIGSERIAL PRIMARY KEY,
    cohort_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_release_cohort_versions (
    id                          BIGSERIAL PRIMARY KEY,
    cohort_id                   BIGINT NOT NULL REFERENCES search_release_cohorts(id) ON DELETE CASCADE,
    version                     INT NOT NULL,
    status                      VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN (
          'DRAFT', 'SHADOW', 'INTERNAL', 'PUBLIC_CANARY', 'PUBLIC', 'ROLLED_BACK'
        )),
    policy_version_id           BIGINT REFERENCES release_cohort_policy_versions(id),
    search_ready_product_count  INT NOT NULL DEFAULT 0,
    finance_ready_product_count INT NOT NULL DEFAULT 0,
    search_demand_coverage      NUMERIC(8, 6),
    category_scope_count        INT NOT NULL DEFAULT 0,
    merchant_count              INT NOT NULL DEFAULT 0,
    category_coverage           NUMERIC(8, 6),
    card_media_coverage         NUMERIC(8, 6),
    golden_coverage             NUMERIC(8, 6),
    critical_error_count        INT NOT NULL DEFAULT 0,
    projection_leakage_count    INT NOT NULL DEFAULT 0,
    catalog_revision            TEXT,
    metrics                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at                TIMESTAMPTZ,
    UNIQUE (cohort_id, version)
);

CREATE TABLE IF NOT EXISTS search_release_cohort_members (
    id                  BIGSERIAL PRIMARY KEY,
    cohort_version_id   BIGINT NOT NULL REFERENCES search_release_cohort_versions(id) ON DELETE CASCADE,
    product_id          BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    offer_id            BIGINT,
    merchant_id         BIGINT NOT NULL,
    category_id         BIGINT,
    membership_reason   TEXT NOT NULL DEFAULT 'READY_MERCHANT_PRODUCT',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cohort_version_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_cohort_members_cohort
  ON search_release_cohort_members (cohort_version_id);

INSERT INTO search_release_cohorts (cohort_code)
VALUES ('internal_ready_merchants')
ON CONFLICT (cohort_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Search-ready projection v2 (cohort membership)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_ready_product_projection_v2 (
    product_id                         BIGINT NOT NULL,
    cohort_id                          BIGINT NOT NULL,
    cohort_version                     INT NOT NULL,
    offer_id                           BIGINT,
    merchant_id                        BIGINT NOT NULL,
    category_id                        BIGINT,
    product_readiness_status           VARCHAR(32) NOT NULL,
    merchant_category_readiness_status VARCHAR(32),
    card_media_id                      BIGINT,
    current_price                      NUMERIC(18, 4),
    stock_status                       VARCHAR(32),
    finance_ready                      BOOLEAN NOT NULL DEFAULT FALSE,
    catalog_revision                   TEXT NOT NULL,
    taxonomy_revision                  TEXT,
    media_policy_version               TEXT,
    finance_revision                   TEXT,
    ranking_policy_version             TEXT,
    updated_at                         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cohort_id, cohort_version, product_id)
);

CREATE INDEX IF NOT EXISTS idx_srp_v2_merchant
  ON search_ready_product_projection_v2 (merchant_id);

CREATE INDEX IF NOT EXISTS idx_srp_v2_category
  ON search_ready_product_projection_v2 (category_id)
  WHERE category_id IS NOT NULL;
