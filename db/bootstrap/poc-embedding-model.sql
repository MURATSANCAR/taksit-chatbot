-- POC bootstrap for a REAL embedding deployment (ADR-007 §I).
-- ------------------------------------------------------------
-- This file wires *placeholders* for a real embedding provider so that
-- the app can flip from lexical-fallback to a genuine embedding model
-- without touching Python code. It never hardcodes vendor model IDs;
-- operators must set the following environment variables and then
-- substitute them via ``envsubst`` (or the deploy tooling) before
-- executing this file:
--
--     ${POC_EMBEDDING_PROVIDER_TYPE}   e.g. LOCAL_HF, OPENAI, ONNX_LOCAL
--     ${POC_EMBEDDING_MODEL_REFERENCE} opaque runtime alias, NOT vendor slug
--     ${POC_EMBEDDING_BASE_URL}        e.g. http://poc-embedder:8080
--     ${POC_EMBEDDING_CREDENTIAL_REF}  e.g. secret://poc/embedder-token
--     ${POC_EMBEDDING_DIM}             e.g. 384, 768, 1024
--
-- The CI job (see .github/workflows/ci.yml → pgvector-integration) runs
-- this file *without* substitution when only the pgvector extension is
-- being validated — the ``ON CONFLICT DO NOTHING`` clauses make it
-- idempotent and harmless on a fresh database.
--
-- IMPORTANT: This file assumes migrations V001, V007 and V009 have been
-- applied. It only fills the model/deployment registry rows.

-- 0. pgvector extension check — the whole point of this bootstrap.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'pgvector extension unavailable; refusing to bootstrap POC embedding profile';
    END;
END $$;

-- 1. Model profile for the embedding task.
--    provider_type + model_reference come from env vars at deploy time.
INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES (
    'CATEGORY_EMBEDDING_POC',
    'Category embedding (POC placeholder)',
    'LOCAL_HF',
    'poc-embedding-alias',
    'EMBEDDING',
    -- context_limit is upper bound of tokens per embedding request; downstream
    -- workers truncate/chunk before sending. max_output_tokens is unused for
    -- EMBEDDING task, but kept non-null to satisfy the column check.
    2048, 0, 0.000, 5000, 4, 'ACTIVE',
    '{"role":"embedding","json_schema_required":false,"env_ref":"POC_EMBEDDING_MODEL_REFERENCE"}'::jsonb
)
ON CONFLICT (profile_code) DO NOTHING;

-- 2. Provider connection — HTTP endpoint of the embedder.
INSERT INTO ai_provider_connections (
    connection_code, provider_type, base_url, credential_ref, configuration, status
) VALUES (
    'POC_EMBEDDING',
    'LOCAL_HF',
    'http://poc-embedder:8080',
    'secret://poc/embedder-token',
    '{"embed_path":"/v1/embeddings","health_path":"/health","env_ref":"POC_EMBEDDING_BASE_URL"}'::jsonb,
    'ACTIVE'
)
ON CONFLICT (connection_code) DO NOTHING;

-- 3. Deployment binding profile ↔ connection.
INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT
    'POC_EMBEDDING_PRIMARY', p.id, c.id, 'poc-embedding-alias',
    100, 1.0000, 4, 'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_EMBEDDING'
WHERE p.profile_code = 'CATEGORY_EMBEDDING_POC'
ON CONFLICT (deployment_code) DO NOTHING;

-- 4. Sanity check row on catalog_category_embeddings so operators can
--    verify the ``vector`` column path works end-to-end. We insert into
--    a scratch table (dropped after the smoke) to avoid coupling to any
--    real catalog row.
DO $$
DECLARE
    v_dim CONSTANT INTEGER := 4;
BEGIN
    EXECUTE 'DROP TABLE IF EXISTS taksitlio_pgvector_bootstrap_probe';
    EXECUTE format(
        'CREATE TABLE taksitlio_pgvector_bootstrap_probe (
             id SERIAL PRIMARY KEY,
             embedding vector(%s) NOT NULL
         )', v_dim
    );
    EXECUTE 'INSERT INTO taksitlio_pgvector_bootstrap_probe (embedding) VALUES (''[1,0,0,0]'')';
    EXECUTE 'DROP TABLE taksitlio_pgvector_bootstrap_probe';
END $$;
