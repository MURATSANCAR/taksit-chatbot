-- V034: P3.7 product-search INTERNAL — golden review fields + temp cohort lifecycle statuses.
-- Additive. Does not public-cutover. Does not enable finance.

-- Golden dual-control columns
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='continuous_golden_cases') THEN
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS review_decision VARCHAR(32);
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS review_notes TEXT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS demand_weight NUMERIC(12, 4) NOT NULL DEFAULT 1;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS cohort_id BIGINT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS cohort_version INT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS source_query_id TEXT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS bucket VARCHAR(64);

    ALTER TABLE continuous_golden_cases
      DROP CONSTRAINT IF EXISTS continuous_golden_cases_lifecycle_check;
    ALTER TABLE continuous_golden_cases
      ADD CONSTRAINT continuous_golden_cases_lifecycle_check
      CHECK (lifecycle_status IN (
        'OBSERVED', 'ANONYMIZED', 'CANDIDATE', 'REVIEW_REQUIRED',
        'NEEDS_REVISION', 'APPROVED', 'ACTIVE', 'REJECTED', 'ARCHIVED'
      ));

    ALTER TABLE continuous_golden_cases
      DROP CONSTRAINT IF EXISTS continuous_golden_cases_review_decision_check;
    ALTER TABLE continuous_golden_cases
      ADD CONSTRAINT continuous_golden_cases_review_decision_check
      CHECK (
        review_decision IS NULL
        OR review_decision IN ('APPROVED', 'REJECTED', 'NEEDS_REVISION')
      );
  END IF;
END $$;

-- Allow SYNTHETIC_CORE_GOLDEN set kind via new set (kind stays CORE_GOLDEN)
INSERT INTO continuous_golden_sets (set_code, set_kind, status)
VALUES ('synthetic_core_golden', 'CORE_GOLDEN', 'ACTIVE')
ON CONFLICT (set_code) DO NOTHING;

-- Temp / lifecycle cohort statuses for controlled INTERNAL tests (not public)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='search_release_cohort_versions') THEN
    ALTER TABLE search_release_cohort_versions
      DROP CONSTRAINT IF EXISTS search_release_cohort_versions_status_check;
    ALTER TABLE search_release_cohort_versions
      ADD CONSTRAINT search_release_cohort_versions_status_check
      CHECK (status IN (
        'DRAFT', 'SHADOW', 'INTERNAL', 'PUBLIC_CANARY', 'PUBLIC', 'ROLLED_BACK',
        'DEGRADED', 'SHADOW_VALIDATION', 'ARCHIVED'
      ));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS search_release_cohort_lifecycle_events (
    id              BIGSERIAL PRIMARY KEY,
    cohort_id       BIGINT NOT NULL REFERENCES search_release_cohorts(id) ON DELETE CASCADE,
    cohort_version  INT NOT NULL,
    from_status     VARCHAR(32),
    to_status       VARCHAR(32) NOT NULL,
    reason          TEXT,
    actor           TEXT,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS verification_evidence_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    sprint_code         TEXT NOT NULL,
    metric_name         TEXT NOT NULL,
    metric_value        JSONB NOT NULL,
    source_type         VARCHAR(32) NOT NULL
        CHECK (source_type IN (
          'DATABASE_QUERY', 'HTTP_TEST_RESULT', 'BROWSER_TEST_RESULT',
          'SSE_TRACE', 'MANUAL_REVIEW', 'POLICY_EVALUATION'
        )),
    source_table_or_endpoint TEXT,
    source_query_hash   TEXT,
    catalog_revision    TEXT,
    cohort_id           BIGINT,
    cohort_version      INT,
    measured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_verification_evidence_sprint
  ON verification_evidence_metrics (sprint_code, measured_at DESC);

-- Ensure ACTIVE golden policy carries product-search mins + finance N/A
DO $$
DECLARE
  pid BIGINT;
  cur_ver INT;
  thr JSONB;
BEGIN
  SELECT id INTO pid FROM cohort_golden_coverage_policies WHERE policy_code='internal_active_cohort';
  IF pid IS NULL THEN
    RETURN;
  END IF;
  SELECT version, thresholds INTO cur_ver, thr
  FROM cohort_golden_coverage_policy_versions
  WHERE policy_id=pid AND status='ACTIVE'
  ORDER BY version DESC LIMIT 1;
  IF thr IS NULL THEN
    thr := '{}'::jsonb;
  END IF;
  thr := thr || jsonb_build_object(
    'minimum_product_search_cases', GREATEST(COALESCE((thr->>'minimum_product_search_cases')::int, 0), 5),
    'minimum_typo_alias_cases', GREATEST(COALESCE((thr->>'minimum_typo_alias_cases')::int, 0), 5),
    'minimum_negation_correction_cases', GREATEST(COALESCE((thr->>'minimum_negation_correction_cases')::int, 0), 5),
    'minimum_clarification_cases', GREATEST(COALESCE((thr->>'minimum_clarification_cases')::int, 0), 3),
    'minimum_no_result_cases', GREATEST(COALESCE((thr->>'minimum_no_result_cases')::int, 0), 2),
    'minimum_llm_required_cases', GREATEST(COALESCE((thr->>'minimum_llm_required_cases')::int, 0), 2),
    'minimum_finance_cases', 0,
    'finance_capability', 'NOT_APPLICABLE',
    'finance_coverage_rule', 'NOT_APPLICABLE_WHEN_COHORT_HAS_NO_FINANCE_SOURCE',
    'require_dual_control', true,
    'forbid_auto_approve', true,
    'minimum_demand_weighted_coverage', COALESCE((thr->>'minimum_demand_weighted_coverage')::numeric, 0.20)
  );
  UPDATE cohort_golden_coverage_policy_versions
     SET status='ROLLED_BACK'
   WHERE policy_id=pid AND status='ACTIVE';
  INSERT INTO cohort_golden_coverage_policy_versions (
    policy_id, version, status, thresholds, activated_at
  ) VALUES (pid, COALESCE(cur_ver, 0) + 1, 'ACTIVE', thr, NOW());
END $$;
