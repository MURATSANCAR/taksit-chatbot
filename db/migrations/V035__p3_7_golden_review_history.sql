-- V035: Golden review dual-control — optimistic locking + immutable review history.
-- Additive. Does not public-cutover. Does not enable finance.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='continuous_golden_cases') THEN
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS row_version INT NOT NULL DEFAULT 1;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS claimed_by TEXT;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
    ALTER TABLE continuous_golden_cases
      ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS continuous_golden_review_history (
    id                  BIGSERIAL PRIMARY KEY,
    case_pk             BIGINT NOT NULL REFERENCES continuous_golden_cases(id) ON DELETE CASCADE,
    case_id             TEXT NOT NULL,
    action              VARCHAR(32) NOT NULL
        CHECK (action IN (
          'CLAIM', 'PREPARE', 'APPROVE', 'REJECT', 'NEEDS_REVISION', 'RELEASE'
        )),
    actor               TEXT NOT NULL,
    from_lifecycle      VARCHAR(32),
    to_lifecycle        VARCHAR(32),
    row_version_before  INT,
    row_version_after   INT,
    notes               TEXT,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_golden_review_history_case
  ON continuous_golden_review_history (case_pk, created_at DESC);
