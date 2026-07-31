-- POC CATEGORY_EMBEDDING deployment bootstrap (ADR-009 §8).
-- ------------------------------------------------------------
-- Substitute via envsubst before execution:
--
--   ${POC_EMBEDDING_PROVIDER_TYPE}   e.g. OPENAI_COMPAT / LOCAL_HF / VLLM
--   ${POC_EMBEDDING_MODEL_REFERENCE} opaque runtime alias
--   ${POC_EMBEDDING_BASE_URL}        e.g. http://poc-embedder:8080
--   ${POC_EMBEDDING_CREDENTIAL_REF}  e.g. secret://poc/embedder-token
--   ${POC_EMBEDDING_RUNTIME_ALIAS}   e.g. poc-category-embedding
--   ${POC_EMBEDDING_DIM}             e.g. 384 / 768 / 1024
--   ${POC_EMBEDDING_TIMEOUT_MS}      e.g. 5000
--   ${POC_EMBEDDING_PARALLEL_SLOTS}  e.g. 4
--   ${POC_EMBEDDING_QUANTIZATION}    metadata only
--   ${POC_EMBEDDING_SPACE_ID}        embedding space isolation key
--
-- Application resolves via CATEGORY_EMBEDDING profile / task route — never
-- hardcodes model names. Missing live URL → EMBEDDING_DEPLOYMENT_UNAVAILABLE.
-- LexicalEmbedder must never be treated as a successful real measurement.

DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'pgvector extension unavailable; refusing CATEGORY_EMBEDDING bootstrap';
    END;
END $$;

INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES (
    'CATEGORY_EMBEDDING',
    'Category embedding (runtime verification)',
    COALESCE(NULLIF('${POC_EMBEDDING_PROVIDER_TYPE}', ''), 'OPENAI_COMPAT'),
    COALESCE(NULLIF('${POC_EMBEDDING_MODEL_REFERENCE}', ''), 'poc-category-embedding'),
    'EMBEDDING',
    2048, 0, 0.000,
    COALESCE(NULLIF('${POC_EMBEDDING_TIMEOUT_MS}', '')::int, 5000),
    COALESCE(NULLIF('${POC_EMBEDDING_PARALLEL_SLOTS}', '')::int, 4),
    'ACTIVE',
    jsonb_build_object(
        'role', 'embedding',
        'json_schema_required', false,
        'embedding_dim', COALESCE(NULLIF('${POC_EMBEDDING_DIM}', '')::int, 0),
        'normalize', true,
        'quantization', COALESCE(NULLIF('${POC_EMBEDDING_QUANTIZATION}', ''), 'unspecified'),
        'embedding_space_id', COALESCE(NULLIF('${POC_EMBEDDING_SPACE_ID}', ''), 'default'),
        'env_ref', 'POC_EMBEDDING_MODEL_REFERENCE'
    )
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_provider_connections (
    connection_code, provider_type, base_url, credential_ref, configuration, status
) VALUES (
    'POC_CATEGORY_EMBEDDING',
    COALESCE(NULLIF('${POC_EMBEDDING_PROVIDER_TYPE}', ''), 'OPENAI_COMPAT'),
    COALESCE(NULLIF('${POC_EMBEDDING_BASE_URL}', ''), 'http://poc-embedder:8080'),
    COALESCE(NULLIF('${POC_EMBEDDING_CREDENTIAL_REF}', ''), 'secret://poc/embedder-token'),
    '{"embed_path":"/v1/embeddings","health_path":"/health","env_ref":"POC_EMBEDDING_BASE_URL"}'::jsonb,
    'ACTIVE'
)
ON CONFLICT (connection_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT
    'POC_CATEGORY_EMBEDDING_PRIMARY',
    p.id,
    c.id,
    COALESCE(NULLIF('${POC_EMBEDDING_RUNTIME_ALIAS}', ''), 'poc-category-embedding'),
    100, 1.0000,
    COALESCE(NULLIF('${POC_EMBEDDING_PARALLEL_SLOTS}', '')::int, 4),
    'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_CATEGORY_EMBEDDING'
WHERE p.profile_code = 'CATEGORY_EMBEDDING'
ON CONFLICT (deployment_code) DO NOTHING;
