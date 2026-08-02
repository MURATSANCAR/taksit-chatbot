-- V038: P4.2 human evidence closeout — real-shadow unique store, statistical
-- review sample policy, human golden/UAT provenance fields, evidence dashboard SoT.
-- Additive. Does NOT enable live canary traffic. Does NOT auto-approve.

-- Unique real shadow queries (one row per normalized unique; no with-replacement)
CREATE TABLE IF NOT EXISTS public_real_shadow_unique_queries (
    id                      BIGSERIAL PRIMARY KEY,
    shadow_id               UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    source_request_hash     TEXT NOT NULL,
    anonymized_query        TEXT NOT NULL,
    normalized_query_hash   TEXT NOT NULL,
    normalized_query        TEXT NOT NULL,
    tenant_scope            TEXT NOT NULL DEFAULT 'default',
    session_id_hash         TEXT,
    bucket                  VARCHAR(64) NOT NULL,
    cohort_id               BIGINT,
    cohort_version          INT,
    catalog_revision        TEXT,
    policy_revision         TEXT,
    source_table            TEXT NOT NULL DEFAULT 'search_query_versions',
    provenance_ok           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (normalized_query_hash, tenant_scope)
);

CREATE INDEX IF NOT EXISTS idx_real_shadow_unique_created
  ON public_real_shadow_unique_queries (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_real_shadow_unique_bucket
  ON public_real_shadow_unique_queries (bucket);

-- Statistical shadow review sample policy (not a hardcoded sample size)
CREATE TABLE IF NOT EXISTS public_shadow_review_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public_shadow_review_policy_versions (
    id              BIGSERIAL PRIMARY KEY,
    policy_id       BIGINT NOT NULL REFERENCES public_shadow_review_policies(id) ON DELETE CASCADE,
    version         INT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'ROLLED_BACK')),
    thresholds      JSONB NOT NULL DEFAULT '{}'::jsonb,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (policy_id, version)
);

INSERT INTO public_shadow_review_policies (policy_code)
VALUES ('product_search_shadow_review')
ON CONFLICT DO NOTHING;

INSERT INTO public_shadow_review_policy_versions (policy_id, version, status, thresholds, activated_at)
SELECT p.id, 1, 'ACTIVE',
  '{
     "confidence_level": 0.95,
     "margin_of_error": 0.05,
     "assumed_proportion": 0.5,
     "minimum_sample_size": 30,
     "maximum_sample_size": 400,
     "bucket_stratification": true,
     "risk_oversampling": {
       "TOP1_CHANGED": 1.5,
       "FINANCE_NOT_SUPPORTED": 1.5,
       "NO_RESULT_CHANGED": 1.25,
       "ROUTE_CHANGED": 1.25
     },
     "maximum_misclassified_major_rate": 0.02,
     "maximum_misclassified_critical": 0
   }'::jsonb,
  NOW()
FROM public_shadow_review_policies p
WHERE p.policy_code='product_search_shadow_review'
  AND NOT EXISTS (
    SELECT 1 FROM public_shadow_review_policy_versions v
    WHERE v.policy_id=p.id AND v.status='ACTIVE'
  );

-- Extend difference reviews for human session evidence
ALTER TABLE public_shadow_difference_reviews
  ADD COLUMN IF NOT EXISTS reviewer_user_id TEXT;
ALTER TABLE public_shadow_difference_reviews
  ADD COLUMN IF NOT EXISTS review_session_id TEXT;
ALTER TABLE public_shadow_difference_reviews
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public_shadow_difference_reviews
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE public_shadow_difference_reviews
  ADD COLUMN IF NOT EXISTS review_duration_ms INT;
ALTER TABLE public_shadow_difference_reviews
  DROP CONSTRAINT IF EXISTS public_shadow_difference_reviews_human_class_check;
ALTER TABLE public_shadow_difference_reviews
  ADD CONSTRAINT public_shadow_difference_reviews_human_class_check
  CHECK (
    human_class IS NULL OR human_class IN (
      'TRUE_EQUIVALENT', 'TRUE_MINOR', 'EXPECTED_IMPROVEMENT',
      'MISCLASSIFIED_MAJOR', 'MISCLASSIFIED_CRITICAL', 'NEEDS_REVIEW'
    )
  );

-- Human golden provenance fields
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS prepared_by_human_id TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS prepared_session_id TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS prepare_duration_ms INT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS prepare_notes TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS reviewed_by_human_id TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS review_session_id TEXT;
ALTER TABLE continuous_golden_cases
  ADD COLUMN IF NOT EXISTS review_duration_ms INT;

-- UAT participation consent/invitation record
ALTER TABLE public_uat_participants
  ADD COLUMN IF NOT EXISTS authenticated_user_id TEXT;
ALTER TABLE public_uat_participants
  ADD COLUMN IF NOT EXISTS invitation_id TEXT;
ALTER TABLE public_uat_participants
  ADD COLUMN IF NOT EXISTS consent_recorded_at TIMESTAMPTZ;

-- Evidence dashboard snapshot is computed live; optional ledger for audits
CREATE TABLE IF NOT EXISTS public_evidence_gate_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    sprint_code     TEXT NOT NULL,
    gate_code       TEXT NOT NULL,
    gate_status     VARCHAR(32) NOT NULL,
    metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    catalog_revision TEXT,
    cohort_id       BIGINT,
    cohort_version  INT,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_gate_snapshots_sprint
  ON public_evidence_gate_snapshots (sprint_code, measured_at DESC);
