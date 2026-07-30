-- Development bootstrap ONLY. Never apply in production automatically.
-- Placeholders use docker-compose service DNS names, not 127.0.0.1.
-- Replace runtime_alias / model_reference via admin panel after validation.

INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES
(
    'FAST_UNDERSTANDING',
    'FAST Understanding (dev primary)',
    'LLAMA_CPP',
    'dev-fast-understanding',
    'UNDERSTANDING',
    4096, 128, 0.000, 3000, 4, 'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"role":"fast"}'::jsonb
),
(
    'FAST_UNDERSTANDING_CHALLENGER',
    'FAST Understanding (dev challenger)',
    'LLAMA_CPP',
    'dev-fast-challenger',
    'UNDERSTANDING',
    4096, 128, 0.000, 3000, 4, 'CHALLENGER',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"role":"challenger"}'::jsonb
),
(
    'DEEP_UNDERSTANDING',
    'DEEP Understanding (dev fallback)',
    'LLAMA_CPP',
    'dev-deep-understanding',
    'UNDERSTANDING',
    8192, 256, 0.000, 8000, 1, 'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"role":"fallback"}'::jsonb
),
(
    'RESPONSE_GENERATION',
    'Grounded response (dev)',
    'LLAMA_CPP',
    'dev-response-generation',
    'RESPONSE',
    4096, 512, 0.200, 5000, 2, 'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"grounded":true}'::jsonb
),
(
    'EMBEDDING_DEFAULT',
    'Embedding (dev)',
    'EMBEDDING',
    'dev-embedding',
    'EMBEDDING',
    512, 0, 0.000, 2000, 8, 'ACTIVE',
    '{"embedding_dim":768,"normalize":true}'::jsonb
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_provider_connections (
    connection_code, provider_type, base_url, credential_ref, configuration, status
) VALUES
(
    'DEV_FAST_PRIMARY',
    'LLAMA_CPP',
    'http://llm-fast:8080',
    NULL,
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
),
(
    'DEV_FAST_CHALLENGER',
    'LLAMA_CPP',
    'http://llm-fast-challenger:8080',
    NULL,
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
),
(
    'DEV_DEEP',
    'LLAMA_CPP',
    'http://llm-deep:8080',
    NULL,
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
),
(
    'DEV_EMBEDDING',
    'EMBEDDING',
    'http://llm-embed:8080',
    NULL,
    '{"embedding_path":"/v1/embeddings","health_path":"/health"}'::jsonb,
    'ACTIVE'
)
ON CONFLICT (connection_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status, configuration
)
SELECT
    'DEV_FAST_PRIMARY',
    p.id, c.id, 'dev-fast-understanding',
    100, 1.0000, 4, 'ACTIVE', '{}'::jsonb
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'DEV_FAST_PRIMARY'
WHERE p.profile_code = 'FAST_UNDERSTANDING'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status, configuration
)
SELECT
    'DEV_FAST_CHALLENGER',
    p.id, c.id, 'dev-fast-challenger',
    90, 1.0000, 4, 'ACTIVE', '{}'::jsonb
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'DEV_FAST_CHALLENGER'
WHERE p.profile_code = 'FAST_UNDERSTANDING_CHALLENGER'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status, configuration
)
SELECT
    'DEV_DEEP_FALLBACK',
    p.id, c.id, 'dev-deep-understanding',
    100, 1.0000, 1, 'ACTIVE', '{}'::jsonb
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'DEV_DEEP'
WHERE p.profile_code = 'DEEP_UNDERSTANDING'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status, configuration
)
SELECT
    'DEV_RESPONSE',
    p.id, c.id, 'dev-response-generation',
    100, 1.0000, 2, 'ACTIVE', '{}'::jsonb
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'DEV_DEEP'
WHERE p.profile_code = 'RESPONSE_GENERATION'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status, configuration
)
SELECT
    'DEV_EMBEDDING',
    p.id, c.id, 'dev-embedding',
    100, 1.0000, 8, 'ACTIVE', '{}'::jsonb
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'DEV_EMBEDDING'
WHERE p.profile_code = 'EMBEDDING_DEFAULT'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_route_versions (
    task_code, route_version, display_name,
    primary_deployment_id, fallback_deployment_id,
    confidence_policy_id, timeout_policy_id,
    condition_expression, traffic_weight, priority,
    is_active, notes
)
SELECT
    'NEED_UNDERSTANDING',
    1,
    'Dev default FAST→DEEP',
    primary_d.id,
    fallback_d.id,
    cp.id,
    tp.id,
    '{"locale":"tr-TR","client":"MOBILE"}'::jsonb,
    1.0000,
    100,
    TRUE,
    'Development bootstrap route'
FROM ai_model_deployments primary_d
JOIN ai_model_deployments fallback_d ON fallback_d.deployment_code = 'DEV_DEEP_FALLBACK'
JOIN ai_confidence_policies cp ON cp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
JOIN ai_timeout_policies tp ON tp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
WHERE primary_d.deployment_code = 'DEV_FAST_PRIMARY'
ON CONFLICT (task_code, route_version) DO NOTHING;
