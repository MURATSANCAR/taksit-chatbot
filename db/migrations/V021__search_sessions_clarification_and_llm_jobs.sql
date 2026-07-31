-- V021: ADR-011 — search sessions, clarification, LLM jobs, progress events.
-- No product/campaign seeds. Timeout values are policy-driven, not model-named.

-- ---------------------------------------------------------------------------
-- LLM / search timeout policies (no hardcoded model names)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS search_timeout_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    queue_soft_deadline_ms       INTEGER NOT NULL DEFAULT 2000
                    CHECK (queue_soft_deadline_ms > 0),
    inference_soft_deadline_ms   INTEGER NOT NULL DEFAULT 8000
                    CHECK (inference_soft_deadline_ms > 0),
    partial_result_deadline_ms   INTEGER NOT NULL DEFAULT 4000
                    CHECK (partial_result_deadline_ms > 0),
    ux_fallback_deadline_ms      INTEGER NOT NULL DEFAULT 12000
                    CHECK (ux_fallback_deadline_ms > 0),
    hard_timeout_ms              INTEGER NOT NULL DEFAULT 32000
                    CHECK (hard_timeout_ms > 0),
    max_clarifications_per_session INTEGER NOT NULL DEFAULT 2
                    CHECK (max_clarifications_per_session >= 0),
    max_clarifications_per_message INTEGER NOT NULL DEFAULT 1
                    CHECK (max_clarifications_per_message >= 0),
    status          VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    configuration   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO search_timeout_policies (
    policy_code, display_name, status
) VALUES (
    'SEARCH_DEFAULT',
    'Varsayılan arama / LLM timeout politikası',
    'ACTIVE'
) ON CONFLICT (policy_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Search sessions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS search_sessions (
    id                  UUID         PRIMARY KEY,
    conversation_id     UUID         NOT NULL,
    user_id             UUID,
    organization_id     UUID,
    status              VARCHAR(48)  NOT NULL
                        CHECK (status IN (
                            'RECEIVED', 'FAST_PARSING', 'ENTITY_RESOLVING',
                            'GAP_ANALYSIS', 'CLARIFICATION_REQUIRED',
                            'WAITING_USER_ANSWER', 'FAST_RETRIEVAL',
                            'LLM_QUEUED', 'LLM_RUNNING',
                            'PARTIAL_RESULTS_READY', 'FINANCE_OPTIONS_LOADING',
                            'RANKING', 'COMPLETED', 'COMPLETED_DEGRADED',
                            'TIMED_OUT', 'FAILED', 'CANCELLED', 'SUPERSEDED'
                        )),
    active_query_version INTEGER     NOT NULL DEFAULT 1
                        CHECK (active_query_version >= 1),
    clarification_count INTEGER      NOT NULL DEFAULT 0
                        CHECK (clarification_count >= 0),
    client_query_id     UUID,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    superseded_by       UUID         REFERENCES search_sessions(id),
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_sessions_conversation
    ON search_sessions (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_search_sessions_status
    ON search_sessions (status)
    WHERE status NOT IN ('COMPLETED', 'COMPLETED_DEGRADED', 'CANCELLED', 'FAILED', 'SUPERSEDED');

CREATE TABLE IF NOT EXISTS search_session_messages (
    id                  BIGSERIAL PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL CHECK (query_version >= 1),
    role                VARCHAR(32)  NOT NULL
                        CHECK (role IN ('USER', 'SYSTEM', 'CLARIFICATION', 'PROGRESS')),
    content             TEXT         NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_session_messages_session
    ON search_session_messages (search_session_id, query_version);

CREATE TABLE IF NOT EXISTS search_session_events (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL CHECK (query_version >= 1),
    event_type          VARCHAR(64)  NOT NULL,
    severity            VARCHAR(16)  NOT NULL DEFAULT 'INFO'
                        CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
    display_message     TEXT,
    data_origin         VARCHAR(48)
                        CHECK (data_origin IS NULL OR data_origin IN (
                            'LOCAL_VERIFIED_SNAPSHOT', 'MERCHANT_FEED',
                            'MERCHANT_API', 'FINANCIAL_INSTITUTION_API',
                            'CACHED_VERIFIED_RESULT'
                        )),
    payload             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_session_events_session
    ON search_session_events (search_session_id, created_at);

-- ---------------------------------------------------------------------------
-- Query versions / constraints / uncertainties
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS search_query_versions (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    version_number      INTEGER      NOT NULL CHECK (version_number >= 1),
    raw_user_text       TEXT         NOT NULL,
    normalized_text     TEXT         NOT NULL,
    state_snapshot      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    confidence          NUMERIC(5,4),
    requires_llm        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (search_session_id, version_number)
);

CREATE TABLE IF NOT EXISTS search_query_constraints (
    id                  BIGSERIAL PRIMARY KEY,
    query_version_id    UUID         NOT NULL REFERENCES search_query_versions(id) ON DELETE CASCADE,
    constraint_type     VARCHAR(64)  NOT NULL,
    entity_type         VARCHAR(64),
    entity_id           VARCHAR(128),
    operator            VARCHAR(32),
    raw_value           TEXT,
    normalized_value    JSONB,
    unit                VARCHAR(32),
    required            BOOLEAN      NOT NULL DEFAULT FALSE,
    source_type         VARCHAR(32)  NOT NULL
                        CHECK (source_type IN (
                            'USER_EXPLICIT', 'USER_CORRECTION', 'DETERMINISTIC_PARSE',
                            'FUZZY_RESOLUTION', 'CLARIFICATION_ANSWER', 'LLM_INFERENCE',
                            'CONVERSATION_STATE'
                        )),
    confidence          NUMERIC(5,4),
    evidence_span       TEXT,
    status              VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE', 'CANCELLED', 'SUPERSEDED')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_query_constraints_version
    ON search_query_constraints (query_version_id, status);

CREATE TABLE IF NOT EXISTS search_query_uncertainties (
    id                  BIGSERIAL PRIMARY KEY,
    query_version_id    UUID         NOT NULL REFERENCES search_query_versions(id) ON DELETE CASCADE,
    field               VARCHAR(64)  NOT NULL,
    reason_code         VARCHAR(64)  NOT NULL,
    candidate_values    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    confidence          NUMERIC(5,4),
    candidate_gap       NUMERIC(5,4),
    can_clarification_resolve BOOLEAN NOT NULL DEFAULT TRUE,
    expected_information_gain NUMERIC(5,4),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_query_uncertainties_version
    ON search_query_uncertainties (query_version_id);

-- ---------------------------------------------------------------------------
-- Clarification
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clarification_requests (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL,
    field               VARCHAR(64)  NOT NULL,
    question_text       TEXT         NOT NULL,
    question_signature  VARCHAR(128) NOT NULL,
    options             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status              VARCHAR(32)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'ANSWERED', 'SUPERSEDED', 'EXPIRED')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clarification_requests_session
    ON clarification_requests (search_session_id, status);

CREATE TABLE IF NOT EXISTS clarification_answers (
    id                  BIGSERIAL PRIMARY KEY,
    clarification_id    UUID         NOT NULL REFERENCES clarification_requests(id) ON DELETE CASCADE,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    selected_option_ids JSONB        NOT NULL DEFAULT '[]'::jsonb,
    free_text           TEXT,
    answered_query_version INTEGER   NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clarification_cache (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     UUID         NOT NULL,
    asked_field         VARCHAR(64)  NOT NULL,
    question_signature  VARCHAR(128) NOT NULL,
    query_version       INTEGER      NOT NULL,
    answer              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    answered_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, asked_field, question_signature)
);

-- ---------------------------------------------------------------------------
-- LLM understanding jobs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_understanding_jobs (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL,
    conversation_state_version INTEGER NOT NULL DEFAULT 0,
    status              VARCHAR(32)  NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN (
                            'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED',
                            'TIMED_OUT', 'CANCEL_REQUESTED', 'CANCELLED', 'STALE_RESULT'
                        )),
    platform_role       VARCHAR(64)  NOT NULL DEFAULT 'UNDERSTANDING_SERVICE',
    input_payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    output_payload      JSONB,
    error_code          VARCHAR(64),
    queued_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_understanding_jobs_session
    ON llm_understanding_jobs (search_session_id, query_version);

CREATE INDEX IF NOT EXISTS idx_llm_understanding_jobs_status
    ON llm_understanding_jobs (status)
    WHERE status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED');

CREATE TABLE IF NOT EXISTS llm_understanding_attempts (
    id                  BIGSERIAL PRIMARY KEY,
    job_id              UUID         NOT NULL REFERENCES llm_understanding_jobs(id) ON DELETE CASCADE,
    attempt_number      INTEGER      NOT NULL CHECK (attempt_number >= 1),
    status              VARCHAR(32)  NOT NULL
                        CHECK (status IN (
                            'STARTED', 'COMPLETED', 'FAILED', 'TIMED_OUT',
                            'SCHEMA_INVALID', 'STALE', 'CANCELLED'
                        )),
    latency_ms          INTEGER,
    error_code          VARCHAR(64),
    response_payload    JSONB,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, attempt_number)
);

-- ---------------------------------------------------------------------------
-- Result snapshots + metrics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS partial_result_snapshots (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL,
    label               VARCHAR(64)  NOT NULL DEFAULT 'Ön sonuçlar',
    product_ids         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    ranking_payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_result_snapshots (
    id                  UUID         PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version       INTEGER      NOT NULL,
    is_degraded         BOOLEAN      NOT NULL DEFAULT FALSE,
    product_ids         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    ranking_payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_session_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    search_session_id   UUID         NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    metric_name         VARCHAR(128) NOT NULL,
    metric_value        NUMERIC(18,6) NOT NULL,
    labels              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_session_metrics_session
    ON search_session_metrics (search_session_id, metric_name);
