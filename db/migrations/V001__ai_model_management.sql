-- V001: Dinamik AI model yönetimi
-- Model isimleri kod / env içine gömülmez; yönetim panelinden yönetilir.

CREATE TABLE IF NOT EXISTS ai_confidence_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    minimum_confidence NUMERIC(4,3) NOT NULL DEFAULT 0.780,
    maximum_category_score_gap_for_clarification NUMERIC(4,3) NOT NULL DEFAULT 0.080,
    fallback_on_invalid_schema BOOLEAN NOT NULL DEFAULT TRUE,
    fallback_on_conflict BOOLEAN NOT NULL DEFAULT TRUE,
    fallback_on_multiple_needs BOOLEAN NOT NULL DEFAULT TRUE,
    fallback_on_budget_confusion BOOLEAN NOT NULL DEFAULT TRUE,
    fallback_on_low_confidence BOOLEAN NOT NULL DEFAULT TRUE,
    prefer_clarification_when_ambiguous BOOLEAN NOT NULL DEFAULT TRUE,
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
    retry_same_model BOOLEAN NOT NULL DEFAULT FALSE,
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_model_profiles (
    id              BIGSERIAL PRIMARY KEY,
    profile_code    VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    provider_type   VARCHAR(64)  NOT NULL
                    CHECK (provider_type IN (
                        'LLAMA_CPP', 'VLLM', 'OPENAI_COMPAT', 'EMBEDDING', 'CUSTOM'
                    )),
    endpoint_url    TEXT         NOT NULL,
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
    -- configuration örnek alanları:
    -- quantization, thinking_enabled, streaming_enabled, json_schema_required
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_model_profiles_status
    ON ai_model_profiles (status);

CREATE INDEX IF NOT EXISTS idx_ai_model_profiles_task_type
    ON ai_model_profiles (task_type);

CREATE TABLE IF NOT EXISTS ai_task_routes (
    id              BIGSERIAL PRIMARY KEY,
    task_code       VARCHAR(64)  NOT NULL UNIQUE,
    primary_model_profile_id  BIGINT NOT NULL
                    REFERENCES ai_model_profiles(id),
    fallback_model_profile_id BIGINT
                    REFERENCES ai_model_profiles(id),
    confidence_policy_id BIGINT
                    REFERENCES ai_confidence_policies(id),
    timeout_policy_id BIGINT
                    REFERENCES ai_timeout_policies(id),
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- Seed: politikalar
INSERT INTO ai_confidence_policies (
    policy_code, display_name
) VALUES (
    'NEED_UNDERSTANDING_DEFAULT',
    'İhtiyaç anlama varsayılan güven politikası'
) ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO ai_timeout_policies (
    policy_code, display_name, primary_timeout_ms, fallback_timeout_ms, total_budget_ms
) VALUES (
    'NEED_UNDERSTANDING_DEFAULT',
    'İhtiyaç anlama varsayılan timeout politikası',
    3000,
    8000,
    10000
) ON CONFLICT (policy_code) DO NOTHING;

-- Seed: FAST adayları + DEEP fallback (endpoint'ler yönetim panelinden güncellenir)
INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, endpoint_url, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES
(
    'FAST_UNDERSTANDING',
    'FAST Türkçe Anlama (Aday A — Qwen3.5-4B)',
    'LLAMA_CPP',
    'http://127.0.0.1:8080/v1/chat/completions',
    'Qwen3.5-4B',
    'UNDERSTANDING',
    4096,
    128,
    0.000,
    3000,
    4,
    'ACTIVE',
    '{"quantization":"Q4_K_M","thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"candidate":"A"}'::jsonb
),
(
    'FAST_UNDERSTANDING_CHALLENGER',
    'FAST Türkçe Anlama Challenger (Aday B — Qwen3-4B-Instruct-2507)',
    'LLAMA_CPP',
    'http://127.0.0.1:8081/v1/chat/completions',
    'Qwen3-4B-Instruct-2507',
    'UNDERSTANDING',
    4096,
    128,
    0.000,
    3000,
    4,
    'CHALLENGER',
    '{"quantization":"Q4_K_M","thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"candidate":"B"}'::jsonb
),
(
    'DEEP_UNDERSTANDING',
    'DEEP Türkçe Anlama Fallback',
    'LLAMA_CPP',
    'http://127.0.0.1:8082/v1/chat/completions',
    'local-deep-understanding',
    'UNDERSTANDING',
    8192,
    256,
    0.000,
    8000,
    1,
    'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"role":"fallback"}'::jsonb
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_task_routes (
    task_code,
    primary_model_profile_id,
    fallback_model_profile_id,
    confidence_policy_id,
    timeout_policy_id,
    status
)
SELECT
    'NEED_UNDERSTANDING',
    primary_p.id,
    fallback_p.id,
    cp.id,
    tp.id,
    'ACTIVE'
FROM ai_model_profiles primary_p
JOIN ai_model_profiles fallback_p ON fallback_p.profile_code = 'DEEP_UNDERSTANDING'
JOIN ai_confidence_policies cp ON cp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
JOIN ai_timeout_policies tp ON tp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
WHERE primary_p.profile_code = 'FAST_UNDERSTANDING'
ON CONFLICT (task_code) DO NOTHING;
