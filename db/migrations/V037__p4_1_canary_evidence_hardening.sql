-- V037: P4.1 canary evidence hardening — load SLO, shadow diversity, overload,
-- canary traffic state. Additive. Does not enable live %5 traffic or finance.

-- Separate package readiness vs live traffic on cohort versions
ALTER TABLE search_release_cohort_versions
  ADD COLUMN IF NOT EXISTS package_state VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE search_release_cohort_versions
  ADD COLUMN IF NOT EXISTS traffic_state VARCHAR(64) NOT NULL DEFAULT 'NOT_STARTED';

UPDATE search_release_cohort_versions
SET package_state = 'PUBLIC_CANARY_PACKAGE_READY',
    traffic_state = 'NOT_STARTED'
WHERE status = 'PUBLIC_CANARY'
  AND (package_state = 'UNKNOWN' OR package_state IS NULL OR traffic_state IS NULL);

-- Golden provenance honesty
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS provenance_class VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS human_participant_id TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS review_session_id TEXT;

-- Overload / backpressure policy
CREATE TABLE IF NOT EXISTS public_overload_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_overload_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_overload_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO public_overload_policies (policy_code) VALUES ('product_search_overload')
ON CONFLICT DO NOTHING;

INSERT INTO public_overload_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "maximum_queue_depth": 64,
     "maximum_inflight": 64,
     "maximum_queue_wait_ms": 2000,
     "admission_control": true,
     "retry_after_seconds": 2,
     "degraded_mode": "BUSY_REJECT"
   }'::jsonb,
  NOW()
FROM public_overload_policies p
WHERE p.policy_code='product_search_overload'
  AND NOT EXISTS (
    SELECT 1 FROM public_overload_policy_versions v WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

-- Load SLO (open-loop + concurrency) — supersede prior “errors-only PASS”
INSERT INTO public_load_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 2, 'ACTIVE',
  '{
     "concurrency_levels": [10, 25, 50, 100, 250],
     "open_loop_profiles": [
       {"requests_per_second": 5,  "test_duration_s": 30, "warmup_duration_s": 5,  "mode": "sustained"},
       {"requests_per_second": 10, "test_duration_s": 30, "warmup_duration_s": 5,  "mode": "sustained"},
       {"requests_per_second": 25, "test_duration_s": 30, "warmup_duration_s": 5,  "mode": "sustained"},
       {"requests_per_second": 50, "test_duration_s": 20, "warmup_duration_s": 5,  "mode": "sustained"},
       {"requests_per_second": 100,"test_duration_s": 10, "warmup_duration_s": 3,  "mode": "burst", "burst_duration_s": 10}
     ],
     "slo": {
       "maximum_P95_response_time_ms": 1500,
       "maximum_P99_response_time_ms": 3000,
       "maximum_queue_wait_P95_ms": 500,
       "maximum_first_product_P95_ms": 2000,
       "minimum_success_rate": 0.99,
       "maximum_5xx_rate": 0.001,
       "maximum_timeout_rate": 0.005,
       "maximum_resource_saturation_duration_s": 5
     },
     "collapse_at_250_blocks_canary": true,
     "pass_requires_slo": true
   }'::jsonb,
  NOW()
FROM public_load_policies p
WHERE p.policy_code='product_search_load'
  AND NOT EXISTS (
    SELECT 1 FROM public_load_policy_versions v
    WHERE v.policy_id=p.id AND v.version=2
  );

-- Deactivate prior load policy version when v2 inserted
UPDATE public_load_policy_versions v
SET status='ROLLED_BACK'
WHERE v.status='ACTIVE'
  AND v.version < 2
  AND v.policy_id = (SELECT id FROM public_load_policies WHERE policy_code='product_search_load');

UPDATE public_load_policy_versions v
SET status='ACTIVE'
WHERE v.version=2
  AND v.policy_id = (SELECT id FROM public_load_policies WHERE policy_code='product_search_load');

-- Shadow diversity policy
CREATE TABLE IF NOT EXISTS public_shadow_diversity_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_shadow_diversity_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_shadow_diversity_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO public_shadow_diversity_policies (policy_code)
VALUES ('product_search_shadow_diversity')
ON CONFLICT DO NOTHING;

INSERT INTO public_shadow_diversity_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "minimum_completed_queries": 1000,
     "minimum_unique_normalized_queries": 500,
     "minimum_unique_ratio": 0.50,
     "maximum_single_query_share": 0.01,
     "maximum_top_10_query_share": 0.10,
     "minimum_bucket_coverage": {
       "PRODUCT_SEARCH": 50,
       "TYPO_ALIAS": 10,
       "NEGATION_CORRECTION": 10,
       "CLARIFICATION": 10,
       "NO_RESULT": 5,
       "LLM_REQUIRED": 10,
       "OUT_OF_SCOPE": 10,
       "FINANCE_NOT_SUPPORTED": 10
     },
     "exclude_golden_from_unique": true,
     "with_replacement_counts_as_load_only": true,
     "stratified_minor_review_sample_size": 80
   }'::jsonb,
  NOW()
FROM public_shadow_diversity_policies p
WHERE p.policy_code='product_search_shadow_diversity'
  AND NOT EXISTS (
    SELECT 1 FROM public_shadow_diversity_policy_versions v
    WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

-- Shadow human review of difference classes
CREATE TABLE IF NOT EXISTS public_shadow_difference_reviews (
    id                  BIGSERIAL PRIMARY KEY,
    observation_id      BIGINT REFERENCES public_shadow_observations(id) ON DELETE SET NULL,
    stratum             VARCHAR(64) NOT NULL,
    anonymized_query    TEXT NOT NULL,
    auto_class          VARCHAR(32),
    human_class         VARCHAR(32)
        CHECK (human_class IS NULL OR human_class IN (
          'TRUE_MINOR', 'EXPECTED_IMPROVEMENT',
          'MISCLASSIFIED_MAJOR', 'MISCLASSIFIED_CRITICAL'
        )),
    reviewer            TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- UAT human participants
CREATE TABLE IF NOT EXISTS public_uat_participants (
    id                  BIGSERIAL PRIMARY KEY,
    human_participant_id TEXT NOT NULL UNIQUE,
    role_family         VARCHAR(32) NOT NULL
        CHECK (role_family IN ('END_USER', 'CATALOG_EXPERT', 'BUSINESS_OPS')),
    display_label       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS human_participant_id TEXT;
ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS duration_ms INT;
ALTER TABLE public_uat_cases
  ADD COLUMN IF NOT EXISTS evidence_class VARCHAR(32) NOT NULL DEFAULT 'OPERATOR_SIMULATED';

-- Demote bulk script dual-control from P4 (not HUMAN_VERIFIED)
UPDATE continuous_golden_cases
SET provenance_class = 'OPERATOR_GENERATED',
    lifecycle_status = 'REVIEW_REQUIRED',
    review_status = 'DRAFT',
    review_decision = NULL
WHERE lifecycle_status = 'APPROVED'
  AND prepared_by = 'p4-preparer-ops'
  AND reviewed_by = 'p4-reviewer-ops';

-- Keep P3.7 closeout dual-control as OPERATOR_DUAL_CONTROL (not external human panel)
UPDATE continuous_golden_cases
SET provenance_class = 'OPERATOR_DUAL_CONTROL'
WHERE lifecycle_status = 'APPROVED'
  AND prepared_by = 'golden-preparer-ops'
  AND reviewed_by = 'golden-reviewer-ops';
