-- V001: AI model management schema ONLY (no environment seeds, no hostnames).
-- Model identity ≠ provider connection ≠ runtime deployment are separated.
-- endpoint_url on ai_model_profiles is DEPRECATED — do not use in application code.

CREATE TABLE IF NOT EXISTS ai_confidence_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    minimum_system_confidence NUMERIC(4,3) NOT NULL DEFAULT 0.780,
    -- legacy alias column kept for older readers; prefer minimum_system_confidence
    minimum_confidence NUMERIC(4,3) NOT NULL DEFAULT 0.780,
    maximum_category_score_gap_for_clarification NUMERIC(4,3) NOT NULL DEFAULT 0.080,
    fallback_on_invalid_schema BOOLEAN NOT NULL DEFAULT TRUE,
    fallback_on_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_on_multiple_needs BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_on_budget_confusion BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_on_low_confidence BOOLEAN NOT NULL DEFAULT TRUE,
    prefer_clarification_when_ambiguous BOOLEAN NOT NULL DEFAULT TRUE,
    clarify_on_session_conflict BOOLEAN NOT NULL DEFAULT TRUE,
    clarify_on_multiple_needs BOOLEAN NOT NULL DEFAULT TRUE,
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_timeout_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    primary_timeout_ms INTEGER NOT NULL DEFAULT 3000,
    fallback_timeout_ms INTEGER NOT NULL DEFAULT 8000,
    total_budget_ms INTEGER NOT NULL DEFAULT 10000,
    min_fallback_remaining_ms INTEGER NOT NULL DEFAULT 500,
    retry_same_model BOOLEAN NOT NULL DEFAULT FALSE,
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Behaviour / inference settings only. NOT a live endpoint.
CREATE TABLE IF NOT EXISTS ai_model_profiles (
    id              BIGSERIAL PRIMARY KEY,
    profile_code    VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    provider_type   VARCHAR(64)  NOT NULL
                    CHECK (provider_type IN (
                        'LLAMA_CPP', 'VLLM', 'OPENAI_COMPAT', 'EMBEDDING', 'CUSTOM'
                    )),
    -- DEPRECATED: runtime URLs belong in ai_provider_connections.
    endpoint_url    TEXT,
    model_reference VARCHAR(256) NOT NULL,
    task_type       VARCHAR(64)  NOT NULL
                    CHECK (task_type IN (
                        'UNDERSTANDING', 'RESPONSE', 'EMBEDDING', 'RERANKING', 'OTHER'
                    )),
    context_limit   INTEGER      NOT NULL DEFAULT 4096,
    max_output_tokens INTEGER    NOT NULL DEFAULT 128,
    temperature     NUMERIC(4,3) NOT NULL DEFAULT 0.000,
    timeout_ms      INTEGER      NOT NULL DEFAULT 3000,
    parallel_slots  INTEGER      NOT NULL DEFAULT 1,
    status          VARCHAR(32)  NOT NULL DEFAULT 'INACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'CHALLENGER', 'DEPRECATED')),
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_model_profiles_status
    ON ai_model_profiles (status);
CREATE INDEX IF NOT EXISTS idx_ai_model_profiles_task_type
    ON ai_model_profiles (task_type);

CREATE TABLE IF NOT EXISTS ai_provider_connections (
    id              BIGSERIAL PRIMARY KEY,
    connection_code VARCHAR(64)  NOT NULL UNIQUE,
    provider_type   VARCHAR(64)  NOT NULL
                    CHECK (provider_type IN (
                        'LLAMA_CPP', 'VLLM', 'OPENAI_COMPAT', 'EMBEDDING', 'CUSTOM'
                    )),
    base_url        TEXT         NOT NULL,
    credential_ref  VARCHAR(128),
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'INACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_model_deployments (
    id              BIGSERIAL PRIMARY KEY,
    deployment_code VARCHAR(64)  NOT NULL UNIQUE,
    model_profile_id BIGINT NOT NULL REFERENCES ai_model_profiles(id),
    provider_connection_id BIGINT NOT NULL REFERENCES ai_provider_connections(id),
    runtime_alias   VARCHAR(128) NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 100,
    traffic_weight  NUMERIC(5,4) NOT NULL DEFAULT 1.0000
                    CHECK (traffic_weight >= 0 AND traffic_weight <= 1),
    max_parallel_requests INTEGER,
    status          VARCHAR(32) NOT NULL DEFAULT 'INACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAINING', 'DEPRECATED')),
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_model_deployments_profile
    ON ai_model_deployments (model_profile_id);
CREATE INDEX IF NOT EXISTS idx_ai_model_deployments_status
    ON ai_model_deployments (status);

-- Legacy single-route table (DEPRECATED). Prefer ai_route_versions.
CREATE TABLE IF NOT EXISTS ai_task_routes (
    id              BIGSERIAL PRIMARY KEY,
    task_code       VARCHAR(64)  NOT NULL UNIQUE,
    primary_model_profile_id  BIGINT REFERENCES ai_model_profiles(id),
    fallback_model_profile_id BIGINT REFERENCES ai_model_profiles(id),
    primary_deployment_id BIGINT REFERENCES ai_model_deployments(id),
    fallback_deployment_id BIGINT REFERENCES ai_model_deployments(id),
    confidence_policy_id BIGINT REFERENCES ai_confidence_policies(id),
    timeout_policy_id BIGINT REFERENCES ai_timeout_policies(id),
    status          VARCHAR(32) NOT NULL DEFAULT 'INACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT', 'DEPRECATED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_route_versions (
    id              BIGSERIAL PRIMARY KEY,
    task_code       VARCHAR(64)  NOT NULL,
    route_version   INTEGER      NOT NULL,
    display_name    VARCHAR(128) NOT NULL,
    primary_deployment_id  BIGINT NOT NULL REFERENCES ai_model_deployments(id),
    fallback_deployment_id BIGINT REFERENCES ai_model_deployments(id),
    confidence_policy_id BIGINT NOT NULL REFERENCES ai_confidence_policies(id),
    timeout_policy_id BIGINT NOT NULL REFERENCES ai_timeout_policies(id),
    condition_expression JSONB NOT NULL DEFAULT '{}'::jsonb,
    traffic_weight  NUMERIC(5,4) NOT NULL DEFAULT 1.0000
                    CHECK (traffic_weight >= 0 AND traffic_weight <= 1),
    priority        INTEGER NOT NULL DEFAULT 100,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_until TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_code, route_version)
);

CREATE INDEX IF NOT EXISTS idx_ai_route_versions_active
    ON ai_route_versions (task_code, is_active, priority DESC)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS ai_prompt_versions (
    id              BIGSERIAL PRIMARY KEY,
    prompt_code     VARCHAR(64)  NOT NULL,
    version         INTEGER      NOT NULL,
    task_code       VARCHAR(64)  NOT NULL,
    content         TEXT         NOT NULL,
    json_schema_ref VARCHAR(256),
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (prompt_code, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_prompt_versions_active
    ON ai_prompt_versions (prompt_code)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS ai_schema_versions (
    id              BIGSERIAL PRIMARY KEY,
    schema_code     VARCHAR(64)  NOT NULL,
    version         INTEGER      NOT NULL,
    schema_body     JSONB        NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (schema_code, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_schema_versions_active
    ON ai_schema_versions (schema_code)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS ai_configuration_audit (
    id              BIGSERIAL PRIMARY KEY,
    actor_id        VARCHAR(128),
    entity_type     VARCHAR(64)  NOT NULL,
    entity_id       VARCHAR(128) NOT NULL,
    action          VARCHAR(64)  NOT NULL,
    before_value    JSONB,
    after_value     JSONB,
    reason          TEXT,
    correlation_id  VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_configuration_audit_entity
    ON ai_configuration_audit (entity_type, entity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_model_execution_logs (
    id              BIGSERIAL PRIMARY KEY,
    correlation_id  VARCHAR(64)  NOT NULL,
    task_code       VARCHAR(64),
    route_version_id BIGINT REFERENCES ai_route_versions(id),
    deployment_id   BIGINT REFERENCES ai_model_deployments(id),
    profile_code    VARCHAR(64),
    decision        VARCHAR(32),
    reason_code     VARCHAR(64),
    system_confidence NUMERIC(4,3),
    model_reported_confidence NUMERIC(4,3),
    latency_ms      INTEGER,
    success         BOOLEAN NOT NULL DEFAULT FALSE,
    error_class     VARCHAR(64),
    -- Never store raw user message text here.
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_model_execution_logs_corr
    ON ai_model_execution_logs (correlation_id, created_at DESC);
