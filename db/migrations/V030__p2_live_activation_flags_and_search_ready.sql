-- V030: P2-LIVE activation — feature flags + search-ready projection.
-- Additive only. Safe after V029. No destructive rewrites of product/offer tables.

CREATE TABLE IF NOT EXISTS runtime_feature_flags (
    flag_code          TEXT PRIMARY KEY,
    description        TEXT,
    status             VARCHAR(32) NOT NULL DEFAULT 'DISABLED'
        CHECK (status IN ('DISABLED', 'SHADOW', 'ENABLED')),
    config             JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by         TEXT
);

INSERT INTO runtime_feature_flags (flag_code, description, status, config) VALUES
(
  'adaptive_catalog_enabled',
  'Master switch for adaptive catalog consumers',
  'ENABLED',
  '{}'::jsonb
),
(
  'learning_candidate_generation_enabled',
  'Allow Auto Ops to create OBSERVED/CANDIDATE learning rows',
  'ENABLED',
  '{}'::jsonb
),
(
  'learning_auto_promotion_enabled',
  'Allow automatic SHADOW→PROMOTED without human/gate approval',
  'DISABLED',
  '{"require_gate": true}'::jsonb
),
(
  'dynamic_readiness_enabled',
  'Merchant readiness evaluation mode',
  'SHADOW',
  '{"write_snapshots": true, "apply_activation_gate": false}'::jsonb
),
(
  'adaptive_ranking_enabled',
  'Use ranking feature projection + top-K path',
  'SHADOW',
  '{"topk": 50}'::jsonb
),
(
  'rolling_golden_enabled',
  'Collect anonymized rolling golden candidates',
  'ENABLED',
  '{}'::jsonb
)
ON CONFLICT (flag_code) DO NOTHING;

-- Search-ready projection: DB membership ≠ search visibility
CREATE TABLE IF NOT EXISTS search_ready_product_projection (
    product_id                 BIGINT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    offer_id                   BIGINT REFERENCES product_offers(id) ON DELETE SET NULL,
    merchant_id                BIGINT NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    category_id                BIGINT REFERENCES categories(id) ON DELETE SET NULL,
    brand_id                   BIGINT REFERENCES brands(id) ON DELETE SET NULL,
    readiness_status           VARCHAR(32) NOT NULL,
    card_media_id              BIGINT,
    current_price              NUMERIC(18, 4),
    currency                   VARCHAR(8),
    stock_status               VARCHAR(32),
    checkout_url_present       BOOLEAN NOT NULL DEFAULT FALSE,
    finance_ready              BOOLEAN NOT NULL DEFAULT FALSE,
    catalog_revision           TEXT NOT NULL,
    readiness_policy_version   TEXT,
    media_quality_policy_version TEXT,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_ready_product_projection_merchant
    ON search_ready_product_projection (merchant_id, readiness_status);

CREATE INDEX IF NOT EXISTS idx_search_ready_product_projection_category
    ON search_ready_product_projection (category_id)
    WHERE category_id IS NOT NULL;

-- Merchant priority scoring policy (weights are data)
CREATE TABLE IF NOT EXISTS merchant_priority_policies (
    id                 BIGSERIAL PRIMARY KEY,
    policy_code        TEXT NOT NULL,
    version            INT NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('DRAFT', 'SHADOW', 'ACTIVE', 'ROLLED_BACK')),
    weights            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at       TIMESTAMPTZ,
    UNIQUE (policy_code, version)
);

INSERT INTO merchant_priority_policies (policy_code, version, status, weights, activated_at)
VALUES (
  'activation_priority',
  1,
  'ACTIVE',
  '{
    "searchable_product_potential": 0.20,
    "category_coverage": 0.20,
    "media_coverage": 0.15,
    "price_freshness": 0.10,
    "finance_coverage": 0.10,
    "payment_plan_coverage": 0.05,
    "user_query_demand": 0.10,
    "unresolved_product_penalty": 0.05,
    "drift_risk_penalty": 0.03,
    "critical_error_penalty": 0.02
  }'::jsonb,
  NOW()
)
ON CONFLICT (policy_code, version) DO NOTHING;

-- Rolling golden case lifecycle columns (extend continuous_golden_cases if present)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='continuous_golden_cases') THEN
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'OBSERVED';
    ALTER TABLE continuous_golden_cases
      DROP CONSTRAINT IF EXISTS continuous_golden_cases_lifecycle_check;
    ALTER TABLE continuous_golden_cases
      ADD CONSTRAINT continuous_golden_cases_lifecycle_check
      CHECK (lifecycle_status IN (
        'OBSERVED', 'ANONYMIZED', 'CANDIDATE', 'REVIEW_REQUIRED',
        'APPROVED', 'ACTIVE', 'REJECTED'
      ));
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS expected_entities JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS expected_constraints JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS expected_route TEXT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS expected_invariants JSONB NOT NULL DEFAULT '[]'::jsonb;
  END IF;
END $$;
