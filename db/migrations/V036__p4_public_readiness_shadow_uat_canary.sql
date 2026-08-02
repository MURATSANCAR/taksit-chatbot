-- V036: P4 public readiness — shadow observations, UAT, canary policy, product-search public cohort.
-- Additive. Finance stays NOT_APPLICABLE. Campaign Gate stays CLOSED. No auto-approve.

CREATE TABLE IF NOT EXISTS public_shadow_observations (
    id                      BIGSERIAL PRIMARY KEY,
    shadow_id               UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    source_request_id_hash  TEXT NOT NULL,
    tenant_scope            TEXT NOT NULL DEFAULT 'default',
    anonymized_query        TEXT NOT NULL,
    query_bucket            VARCHAR(64) NOT NULL,
    public_route            TEXT,
    shadow_route            TEXT,
    public_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    shadow_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    difference_class        VARCHAR(32)
        CHECK (difference_class IS NULL OR difference_class IN (
          'EXPECTED_IMPROVEMENT', 'EQUIVALENT', 'MINOR_DIFFERENCE',
          'MAJOR_DIFFERENCE', 'CRITICAL_DIFFERENCE', 'NOT_COMPARABLE'
        )),
    difference_reasons      JSONB NOT NULL DEFAULT '[]'::jsonb,
    cohort_id               BIGINT,
    cohort_version          INT,
    catalog_revision        TEXT,
    human_review_status     VARCHAR(32) NOT NULL DEFAULT 'NONE'
        CHECK (human_review_status IN ('NONE', 'PENDING', 'RESOLVED')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_public_shadow_created
  ON public_shadow_observations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_public_shadow_bucket
  ON public_shadow_observations (query_bucket, difference_class);

CREATE TABLE IF NOT EXISTS public_uat_cases (
    id                  BIGSERIAL PRIMARY KEY,
    uat_case_id         TEXT NOT NULL UNIQUE,
    reviewer            TEXT NOT NULL,
    reviewer_role       VARCHAR(32) NOT NULL
        CHECK (reviewer_role IN ('END_USER', 'CATALOG_EXPERT', 'BUSINESS_OPS')),
    anonymized_query    TEXT NOT NULL,
    cohort_id           BIGINT,
    cohort_version      INT,
    expected_behavior   JSONB NOT NULL DEFAULT '{}'::jsonb,
    actual_behavior     JSONB NOT NULL DEFAULT '{}'::jsonb,
    shown_products      JSONB NOT NULL DEFAULT '[]'::jsonb,
    shown_claims        JSONB NOT NULL DEFAULT '[]'::jsonb,
    severity            VARCHAR(16) NOT NULL DEFAULT 'INFO'
        CHECK (severity IN ('INFO', 'MINOR', 'MAJOR', 'CRITICAL', 'BLOCKER')),
    decision            VARCHAR(32) NOT NULL
        CHECK (decision IN ('PASS', 'FAIL', 'NEEDS_REVIEW', 'NOT_APPLICABLE')),
    notes               TEXT,
    scenario_family     VARCHAR(64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_canary_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_canary_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_canary_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    stages          JSONB NOT NULL DEFAULT '[]'::jsonb,
    rollback_triggers JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

CREATE TABLE IF NOT EXISTS public_canary_assignments (
    id              BIGSERIAL PRIMARY KEY,
    assignment_key  TEXT NOT NULL,
    stage_percent   INT NOT NULL,
    cohort_id       BIGINT NOT NULL,
    cohort_version  INT NOT NULL,
    path            VARCHAR(32) NOT NULL
        CHECK (path IN ('CHAMPION', 'CANARY')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (assignment_key, stage_percent, cohort_id, cohort_version)
);

CREATE TABLE IF NOT EXISTS public_shadow_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_shadow_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_shadow_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

CREATE TABLE IF NOT EXISTS public_load_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_load_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_load_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO public_canary_policies (policy_code) VALUES ('product_search_canary')
ON CONFLICT DO NOTHING;

INSERT INTO public_canary_policy_versions (policy_id, version, status, stages, rollback_triggers, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '[
     {"percent": 5,  "minimum_request_count": 500,  "minimum_observation_hours": 24, "maximum_critical_difference": 0, "maximum_5xx_rate": 0.001, "maximum_timeout_rate": 0.005, "maximum_leakage": 0, "maximum_forbidden_claim": 0},
     {"percent": 25, "minimum_request_count": 2000, "minimum_observation_hours": 48, "maximum_critical_difference": 0, "maximum_5xx_rate": 0.001, "maximum_timeout_rate": 0.005, "maximum_leakage": 0, "maximum_forbidden_claim": 0},
     {"percent": 50, "minimum_request_count": 5000, "minimum_observation_hours": 72, "maximum_critical_difference": 0, "maximum_5xx_rate": 0.001, "maximum_timeout_rate": 0.005, "maximum_leakage": 0, "maximum_forbidden_claim": 0},
     {"percent": 100,"minimum_request_count": 10000,"minimum_observation_hours": 96, "maximum_critical_difference": 0, "maximum_5xx_rate": 0.001, "maximum_timeout_rate": 0.005, "maximum_leakage": 0, "maximum_forbidden_claim": 0}
   ]'::jsonb,
  '{
     "wrong_product": 0, "wrong_price": 0, "forbidden_finance_claim": 0,
     "cohort_leakage": 0, "mixed_revision": 0,
     "critical_difference_gt": 0, "http_5xx_rate_gt": 0.001, "timeout_rate_gt": 0.01
   }'::jsonb,
  NOW()
FROM public_canary_policies p
WHERE p.policy_code='product_search_canary'
  AND NOT EXISTS (
    SELECT 1 FROM public_canary_policy_versions v WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

INSERT INTO public_shadow_policies (policy_code) VALUES ('product_search_shadow')
ON CONFLICT DO NOTHING;

INSERT INTO public_shadow_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "minimum_completed_shadow_queries": 1000,
     "maximum_critical_difference": 0,
     "maximum_cohort_leakage": 0,
     "maximum_forbidden_finance_claim": 0,
     "maximum_negative_resurrection": 0,
     "maximum_mixed_revision": 0,
     "maximum_unhandled_error": 0,
     "maximum_major_difference_rate": 0.05
   }'::jsonb,
  NOW()
FROM public_shadow_policies p
WHERE p.policy_code='product_search_shadow'
  AND NOT EXISTS (
    SELECT 1 FROM public_shadow_policy_versions v WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

INSERT INTO public_load_policies (policy_code) VALUES ('product_search_load')
ON CONFLICT DO NOTHING;

INSERT INTO public_load_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "concurrency_levels": [10, 50, 100, 250],
     "maximum_5xx_rate": 0.001,
     "maximum_critical_timeout": 0,
     "maximum_cohort_leakage": 0,
     "maximum_mixed_revision": 0,
     "maximum_forbidden_finance_claim": 0,
     "collapse_at_250_blocks_canary": true
   }'::jsonb,
  NOW()
FROM public_load_policies p
WHERE p.policy_code='product_search_load'
  AND NOT EXISTS (
    SELECT 1 FROM public_load_policy_versions v WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

-- Product-search public golden mins (finance N/A)
INSERT INTO cohort_golden_coverage_policies (policy_code)
VALUES ('public_product_search')
ON CONFLICT DO NOTHING;

INSERT INTO cohort_golden_coverage_policy_versions (
  policy_id, version, status, thresholds, activated_at
)
SELECT p.id, 1, 'ACTIVE',
  '{
     "minimum_demand_weighted_coverage": 0.20,
     "minimum_approved_rolling_golden": 250,
     "minimum_product_search_cases": 80,
     "minimum_typo_alias_cases": 30,
     "minimum_negation_correction_cases": 30,
     "minimum_clarification_cases": 20,
     "minimum_no_result_cases": 15,
     "minimum_llm_required_cases": 15,
     "minimum_out_of_scope_cases": 10,
     "minimum_finance_cases": 0,
     "finance_capability": "NOT_APPLICABLE",
     "require_dual_control": true,
     "forbid_auto_approve": true
   }'::jsonb,
  NOW()
FROM cohort_golden_coverage_policies p
WHERE p.policy_code='public_product_search'
  AND NOT EXISTS (
    SELECT 1 FROM cohort_golden_coverage_policy_versions v
    WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );
