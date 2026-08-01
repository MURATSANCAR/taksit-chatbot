-- V033: P3.4 INTERNAL E2E — performance + cohort golden coverage policies.
-- Additive. No public cutover. Thresholds are data (policy seed), not app constants.

CREATE TABLE IF NOT EXISTS search_performance_policies (
    id           BIGSERIAL PRIMARY KEY,
    policy_code  TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_performance_policy_versions (
    id           BIGSERIAL PRIMARY KEY,
    policy_id    BIGINT NOT NULL REFERENCES search_performance_policies(id) ON DELETE CASCADE,
    version      INT NOT NULL,
    status       VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    thresholds   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    UNIQUE (policy_id, version)
);

INSERT INTO search_performance_policies (policy_code)
VALUES ('internal_full_path')
ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO search_performance_policy_versions (
  policy_id, version, status, thresholds, activated_at
)
SELECT p.id, 1, 'ACTIVE',
  '{
    "minimum_attempt_count": 1000,
    "minimum_success_count": 1000,
    "minimum_success_rate": 0.999,
    "maximum_http_5xx_rate": 0.001,
    "maximum_timeout_rate": 0.0,
    "maximum_critical_timeout": 0,
    "maximum_unknown_error_count": 0,
    "ranking_core_p95_ms": 50,
    "ranking_reason_codes_p95_ms": 25,
    "total_backend_p95_ms": 500,
    "diagnostic_minimum_requests": 100,
    "diagnostic_required_success_rate": 1.0,
    "llm_partial_p95_ms": 4000,
    "maximum_query_count_per_request": 40,
    "candidate_buckets": {
      "small": [0, 50],
      "medium": [51, 200],
      "large": [201, 1000],
      "very_large": [1001, 1000000]
    }
  }'::jsonb,
  NOW()
FROM search_performance_policies p
WHERE p.policy_code = 'internal_full_path'
ON CONFLICT (policy_id, version) DO NOTHING;

CREATE TABLE IF NOT EXISTS cohort_golden_coverage_policies (
    id           BIGSERIAL PRIMARY KEY,
    policy_code  TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cohort_golden_coverage_policy_versions (
    id           BIGSERIAL PRIMARY KEY,
    policy_id    BIGINT NOT NULL REFERENCES cohort_golden_coverage_policies(id) ON DELETE CASCADE,
    version      INT NOT NULL,
    status       VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SHADOW', 'APPROVED', 'ACTIVE', 'ROLLED_BACK')),
    thresholds   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    UNIQUE (policy_id, version)
);

INSERT INTO cohort_golden_coverage_policies (policy_code)
VALUES ('internal_active_cohort')
ON CONFLICT (policy_code) DO NOTHING;

-- Non-zero INTERNAL coverage — quality proof, not "0 = pass".
INSERT INTO cohort_golden_coverage_policy_versions (
  policy_id, version, status, thresholds, activated_at
)
SELECT p.id, 1, 'ACTIVE',
  '{
    "minimum_demand_weighted_coverage": 0.20,
    "minimum_active_merchant_scope_coverage": 1.0,
    "minimum_active_category_scope_coverage": 0.10,
    "minimum_typo_alias_cases": 5,
    "minimum_negation_correction_cases": 5,
    "minimum_finance_cases": 3,
    "minimum_clarification_cases": 3,
    "minimum_no_result_cases": 2,
    "minimum_llm_required_cases": 2,
    "require_dual_control": true,
    "forbid_auto_approve": true
  }'::jsonb,
  NOW()
FROM cohort_golden_coverage_policies p
WHERE p.policy_code = 'internal_active_cohort'
ON CONFLICT (policy_id, version) DO NOTHING;

CREATE TABLE IF NOT EXISTS cohort_golden_coverage_snapshots (
    id                              BIGSERIAL PRIMARY KEY,
    cohort_id                       BIGINT NOT NULL,
    cohort_version                  INT NOT NULL,
    golden_policy_version           INT,
    active_query_demand_total       NUMERIC(18, 6) NOT NULL DEFAULT 0,
    covered_query_demand            NUMERIC(18, 6) NOT NULL DEFAULT 0,
    demand_weighted_coverage        NUMERIC(8, 6),
    active_merchant_scope_count     INT NOT NULL DEFAULT 0,
    covered_merchant_scope_count    INT NOT NULL DEFAULT 0,
    active_category_scope_count     INT NOT NULL DEFAULT 0,
    covered_category_scope_count    INT NOT NULL DEFAULT 0,
    typo_alias_approved_count       INT NOT NULL DEFAULT 0,
    negation_correction_approved_count INT NOT NULL DEFAULT 0,
    finance_approved_count          INT NOT NULL DEFAULT 0,
    clarification_approved_count    INT NOT NULL DEFAULT 0,
    no_result_approved_count        INT NOT NULL DEFAULT 0,
    llm_required_approved_count     INT NOT NULL DEFAULT 0,
    status                          VARCHAR(32) NOT NULL DEFAULT 'FAIL',
    failed_rules                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    evaluated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Close the P3.3 policy hole: INTERNAL release cohort no longer accepts 0 golden coverage.
UPDATE release_cohort_policy_versions v
SET thresholds = thresholds || jsonb_build_object(
  'minimum_golden_bucket_coverage', 0.20,
  'minimum_approved_active_scope_cases', 20
)
FROM release_cohort_policies p
WHERE v.policy_id = p.id
  AND p.policy_code = 'internal_release'
  AND v.status = 'ACTIVE';
