-- POC FAST understanding deployment bootstrap (ADR-009 §5).
-- ------------------------------------------------------------
-- Model name / endpoint are NEVER hardcoded as production secrets.
-- Substitute via envsubst (or deploy tooling) before execution:
--
--   ${POC_FAST_PROVIDER_TYPE}      e.g. OPENAI_COMPAT / LLAMA_CPP / VLLM
--   ${POC_FAST_MODEL_REFERENCE}    opaque runtime alias (NOT vendor slug in app code)
--   ${POC_FAST_BASE_URL}           e.g. http://poc-llm-fast:8080
--   ${POC_FAST_CREDENTIAL_REF}     e.g. secret://poc/fast-token
--   ${POC_FAST_RUNTIME_ALIAS}      e.g. poc-fast-understanding
--   ${POC_FAST_CONTEXT_LIMIT}      e.g. 4096
--   ${POC_FAST_MAX_OUTPUT_TOKENS}  e.g. 128
--   ${POC_FAST_TEMPERATURE}        e.g. 0.000
--   ${POC_FAST_PARALLEL_SLOTS}     e.g. 4
--   ${POC_FAST_TIMEOUT_MS}         e.g. 3000
--   ${POC_FAST_QUANTIZATION}       e.g. q4_k_m / none  (metadata only)
--
-- Application code resolves deployments only via task_code = NEED_UNDERSTANDING.
-- If substitution is skipped, placeholder rows are harmless (ON CONFLICT DO NOTHING)
-- and runtime probes still report FAST_DEPLOYMENT_UNAVAILABLE until a live URL is set.

INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES (
    'FAST_UNDERSTANDING',
    'FAST Understanding (runtime verification)',
    COALESCE(NULLIF('${POC_FAST_PROVIDER_TYPE}', ''), 'OPENAI_COMPAT'),
    COALESCE(NULLIF('${POC_FAST_MODEL_REFERENCE}', ''), 'poc-fast-understanding'),
    'UNDERSTANDING',
    COALESCE(NULLIF('${POC_FAST_CONTEXT_LIMIT}', '')::int, 4096),
    COALESCE(NULLIF('${POC_FAST_MAX_OUTPUT_TOKENS}', '')::int, 128),
    COALESCE(NULLIF('${POC_FAST_TEMPERATURE}', '')::numeric, 0.000),
    COALESCE(NULLIF('${POC_FAST_TIMEOUT_MS}', '')::int, 3000),
    COALESCE(NULLIF('${POC_FAST_PARALLEL_SLOTS}', '')::int, 4),
    'ACTIVE',
    jsonb_build_object(
        'thinking_enabled', false,
        'streaming_enabled', false,
        'json_schema_required', true,
        'quantization', COALESCE(NULLIF('${POC_FAST_QUANTIZATION}', ''), 'unspecified'),
        'env_ref', 'POC_FAST_MODEL_REFERENCE'
    )
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_provider_connections (
    connection_code, provider_type, base_url, credential_ref, configuration, status
) VALUES (
    'POC_FAST_RUNTIME',
    COALESCE(NULLIF('${POC_FAST_PROVIDER_TYPE}', ''), 'OPENAI_COMPAT'),
    COALESCE(NULLIF('${POC_FAST_BASE_URL}', ''), 'http://poc-llm-fast:8080'),
    COALESCE(NULLIF('${POC_FAST_CREDENTIAL_REF}', ''), 'secret://poc/fast-token'),
    '{"chat_path":"/v1/chat/completions","health_path":"/health","env_ref":"POC_FAST_BASE_URL"}'::jsonb,
    'ACTIVE'
)
ON CONFLICT (connection_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT
    'POC_FAST_RUNTIME_PRIMARY',
    p.id,
    c.id,
    COALESCE(NULLIF('${POC_FAST_RUNTIME_ALIAS}', ''), 'poc-fast-understanding'),
    100, 1.0000,
    COALESCE(NULLIF('${POC_FAST_PARALLEL_SLOTS}', '')::int, 4),
    'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_FAST_RUNTIME'
WHERE p.profile_code = 'FAST_UNDERSTANDING'
ON CONFLICT (deployment_code) DO NOTHING;

-- Ensure NEED_UNDERSTANDING route points at the runtime deployment when missing.
-- Policies are expected from V002 / poc-models; if absent this INSERT is a no-op.
INSERT INTO ai_route_versions (
    task_code, route_version, display_name,
    primary_deployment_id, fallback_deployment_id,
    confidence_policy_id, timeout_policy_id,
    condition_expression, traffic_weight, priority, is_active, notes
)
SELECT
    'NEED_UNDERSTANDING',
    9001,
    'Runtime verification FAST route',
    d.id,
    NULL,
    cp.id,
    tp.id,
    '{"locale":"tr-TR","client":"MOBILE","experiment":"RUNTIME_VERIFICATION"}'::jsonb,
    1.0000,
    50,
    TRUE,
    'ADR-009 runtime verification arm'
FROM ai_model_deployments d
JOIN ai_confidence_policies cp ON cp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
JOIN ai_timeout_policies tp ON tp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
WHERE d.deployment_code = 'POC_FAST_RUNTIME_PRIMARY'
  AND NOT EXISTS (
      SELECT 1 FROM ai_route_versions r
      WHERE r.task_code = 'NEED_UNDERSTANDING'
        AND r.route_version = 9001
  )
ON CONFLICT (task_code, route_version) DO NOTHING;
