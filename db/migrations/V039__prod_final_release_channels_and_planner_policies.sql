-- V039: PROD-FINAL — reconcile markers + typed release-channel config + planner policies.
-- Additive only. Does NOT enable public traffic. Does NOT open Campaign Gate.
-- Does NOT re-execute V034–V038 SQL. History reconciliation is done by migrate.py probes.

-- ---------------------------------------------------------------------------
-- Typed separation: INTERNAL traffic cohort vs PUBLIC package (no auto-traffic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS release_channel_configs (
    id              BIGSERIAL PRIMARY KEY,
    channel_code    TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS release_channel_config_versions (
    id                      BIGSERIAL PRIMARY KEY,
    channel_id              BIGINT NOT NULL REFERENCES release_channel_configs(id) ON DELETE CASCADE,
    version                 INT NOT NULL,
    status                  VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    cohort_id               BIGINT,
    cohort_version          INT,
    package_state           VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
    traffic_state           VARCHAR(64) NOT NULL DEFAULT 'NOT_STARTED',
    notes                   TEXT,
    activated_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (channel_id, version)
);

INSERT INTO release_channel_configs (channel_code) VALUES
  ('internal_product_search'),
  ('public_canary_package')
ON CONFLICT (channel_code) DO NOTHING;

-- Seed INTERNAL channel from dynamic_readiness flag if present (no traffic enable).
INSERT INTO release_channel_config_versions (
  channel_id, version, status, cohort_id, cohort_version,
  package_state, traffic_state, notes, activated_at
)
SELECT c.id, 1, 'ACTIVE',
  COALESCE((f.config->>'cohort_id')::bigint, 1),
  COALESCE((f.config->>'cohort_version')::int, 1),
  'INTERNAL_ACTIVE',
  'INTERNAL_ONLY',
  'Seeded from dynamic_readiness_enabled; public package is separate',
  NOW()
FROM release_channel_configs c
LEFT JOIN runtime_feature_flags f ON f.flag_code = 'dynamic_readiness_enabled'
WHERE c.channel_code = 'internal_product_search'
  AND NOT EXISTS (
    SELECT 1 FROM release_channel_config_versions v
    WHERE v.channel_id = c.id AND v.status = 'ACTIVE'
  );

-- Seed PUBLIC package channel from cohort v2 package/traffic (read-only snapshot of state).
INSERT INTO release_channel_config_versions (
  channel_id, version, status, cohort_id, cohort_version,
  package_state, traffic_state, notes, activated_at
)
SELECT c.id, 1, 'ACTIVE',
  v.cohort_id, v.version,
  COALESCE(v.package_state, 'PUBLIC_CANARY_PACKAGE_READY'),
  COALESCE(v.traffic_state, 'NOT_STARTED'),
  'Public package channel — traffic must stay NOT_STARTED until human GO',
  NOW()
FROM release_channel_configs c
JOIN search_release_cohort_versions v
  ON v.cohort_id = 1 AND v.version = 2 AND v.status = 'PUBLIC_CANARY'
WHERE c.channel_code = 'public_canary_package'
  AND NOT EXISTS (
    SELECT 1 FROM release_channel_config_versions x
    WHERE x.channel_id = c.id AND x.status = 'ACTIVE'
  );

-- Keep dynamic_readiness config pointing at INTERNAL channel only; never copy public traffic.
UPDATE runtime_feature_flags
SET config = COALESCE(config, '{}'::jsonb) || jsonb_build_object(
  'traffic', 'internal_only',
  'internal_cohort_id', COALESCE((config->>'cohort_id')::int, 1),
  'internal_cohort_version', COALESCE((config->>'cohort_version')::int, 1),
  'public_package_cohort_id', 1,
  'public_package_cohort_version', 2,
  'public_package_state', 'PUBLIC_CANARY_PACKAGE_READY',
  'public_traffic_state', 'NOT_STARTED',
  'auto_enable_public_traffic', false
),
updated_at = NOW(),
updated_by = 'v039-release-channel-separation'
WHERE flag_code = 'dynamic_readiness_enabled';

-- ---------------------------------------------------------------------------
-- Complex query planner / conditional exception / bundle policies (versioned)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS query_plan_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_plan_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES query_plan_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'ACTIVE', 'ROLLED_BACK')),
    plan_version    TEXT NOT NULL DEFAULT 'v1',
    rules           JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO query_plan_policies (policy_code) VALUES ('canonical_search_plan_v1')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO query_plan_policy_versions (policy_id, version, status, plan_version, rules, activated_at)
SELECT p.id, 1, 'ACTIVE', 'v1',
  '{
     "complex_route_signals": [
       "multi_item", "global_budget", "conditional", "hard_soft_mix",
       "ranking_priorities", "conflict", "stretch_budget", "many_constraints",
       "subjective_attribute", "complex_state_reference"
     ],
     "max_clarifications": 2,
     "llm_repair_retries": 1,
     "forbidden_llm_fields": [
       "product_id", "offer_id", "merchant_id", "price", "stock",
       "bank", "campaign_id", "agreement_id", "rate", "monthly_payment",
       "total_payment", "product_url"
     ]
   }'::jsonb,
  NOW()
FROM query_plan_policies p
WHERE p.policy_code = 'canonical_search_plan_v1'
  AND NOT EXISTS (
    SELECT 1 FROM query_plan_policy_versions v WHERE v.policy_id = p.id AND v.version = 1
  );

CREATE TABLE IF NOT EXISTS conditional_exception_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conditional_exception_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES conditional_exception_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO conditional_exception_policies (policy_code)
VALUES ('significant_value_improvement')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO conditional_exception_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "minimum_price_advantage": 0.08,
     "minimum_feature_advantage": 0.15,
     "minimum_overall_score_delta": 0.12
   }'::jsonb,
  NOW()
FROM conditional_exception_policies p
WHERE p.policy_code = 'significant_value_improvement'
  AND NOT EXISTS (
    SELECT 1 FROM conditional_exception_policy_versions v WHERE v.policy_id = p.id AND v.version = 1
  );

CREATE TABLE IF NOT EXISTS bundle_solver_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bundle_solver_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES bundle_solver_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO bundle_solver_policies (policy_code) VALUES ('bounded_beam_v1')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO bundle_solver_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "candidate_top_k": 12,
     "beam_width": 24,
     "maximum_combinations": 5000,
     "timeout_ms": 800,
     "finance_bundle": "NOT_SUPPORTED"
   }'::jsonb,
  NOW()
FROM bundle_solver_policies p
WHERE p.policy_code = 'bounded_beam_v1'
  AND NOT EXISTS (
    SELECT 1 FROM bundle_solver_policy_versions v WHERE v.policy_id = p.id AND v.version = 1
  );

CREATE TABLE IF NOT EXISTS merchant_selection_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merchant_selection_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES merchant_selection_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    weights         JSONB NOT NULL DEFAULT '{}'::jsonb,
    minimums        JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO merchant_selection_policies (policy_code) VALUES ('search_ready_expansion_v1')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO merchant_selection_policy_versions (policy_id, version, status, weights, minimums, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "category_coverage": 0.25,
     "brand_coverage": 0.20,
     "attribute_coverage": 0.15,
     "card_media_coverage": 0.15,
     "fresh_price_coverage": 0.10,
     "finance_coverage": 0.10,
     "active_products_norm": 0.05
   }'::jsonb,
  '{
     "category_coverage": 0.85,
     "brand_coverage": 0.70,
     "attribute_coverage": 0.50,
     "card_media_coverage": 0.90,
     "fresh_price_coverage": 0.85
   }'::jsonb,
  NOW()
FROM merchant_selection_policies p
WHERE p.policy_code = 'search_ready_expansion_v1'
  AND NOT EXISTS (
    SELECT 1 FROM merchant_selection_policy_versions v WHERE v.policy_id = p.id AND v.version = 1
  );

-- Conversation state operation audit (session-scoped)
CREATE TABLE IF NOT EXISTS search_plan_state_operations (
    id                  BIGSERIAL PRIMARY KEY,
    search_session_id   TEXT NOT NULL,
    operation_id        TEXT NOT NULL,
    query_version       INT NOT NULL,
    operation           VARCHAR(32) NOT NULL
      CHECK (operation IN (
        'ADD','REMOVE','REPLACE','RELAX','REQUIRE','PREFER',
        'TEMPORARY_EXCEPTION','ROLLBACK','CLEAR'
      )),
    target_constraint_id TEXT,
    before_state        JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_state         JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (search_session_id, operation_id)
);

CREATE INDEX IF NOT EXISTS idx_search_plan_state_ops_session
  ON search_plan_state_operations (search_session_id, query_version);

-- Ensure finance stays blocked at capability flag level (explicit).
INSERT INTO runtime_feature_flags (flag_code, description, status, config, updated_by)
VALUES (
  'finance_display',
  'Finance/campaign display capability — BLOCKED until finance-ready cohort gate',
  'DISABLED',
  '{"finance_capability":"BLOCKED","campaign_gate":"CLOSED"}'::jsonb,
  'v039-finance-gate'
)
ON CONFLICT (flag_code) DO UPDATE
SET config = COALESCE(runtime_feature_flags.config, '{}'::jsonb) || jsonb_build_object(
      'finance_capability', 'BLOCKED',
      'campaign_gate', 'CLOSED',
      'auto_enable_forbidden', true
    ),
    updated_at = NOW(),
    updated_by = 'v039-finance-gate'
WHERE runtime_feature_flags.status <> 'ENABLED';
