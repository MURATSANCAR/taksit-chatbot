-- POC bootstrap ONLY. Hostnames are environment-specific service names.
-- Do not commit real credentials. credential_ref points to a secret store key.

INSERT INTO ai_model_profiles (
    profile_code, display_name, provider_type, model_reference,
    task_type, context_limit, max_output_tokens, temperature, timeout_ms,
    parallel_slots, status, configuration
) VALUES
(
    'FAST_UNDERSTANDING',
    'FAST Understanding (POC primary)',
    'LLAMA_CPP',
    'poc-fast-understanding',
    'UNDERSTANDING',
    4096, 128, 0.000, 3000, 4, 'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"candidate":"A"}'::jsonb
),
(
    'FAST_UNDERSTANDING_CHALLENGER',
    'FAST Understanding (POC challenger)',
    'LLAMA_CPP',
    'poc-fast-challenger',
    'UNDERSTANDING',
    4096, 128, 0.000, 3000, 4, 'CHALLENGER',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"candidate":"B"}'::jsonb
),
(
    'DEEP_UNDERSTANDING',
    'DEEP Understanding (POC fallback)',
    'LLAMA_CPP',
    'poc-deep-understanding',
    'UNDERSTANDING',
    8192, 256, 0.000, 8000, 1, 'ACTIVE',
    '{"thinking_enabled":false,"streaming_enabled":false,"json_schema_required":true,"role":"fallback"}'::jsonb
)
ON CONFLICT (profile_code) DO NOTHING;

INSERT INTO ai_provider_connections (
    connection_code, provider_type, base_url, credential_ref, configuration, status
) VALUES
(
    'POC_FAST_A',
    'LLAMA_CPP',
    'http://poc-llm-fast-a:8080',
    'secret://poc/llm-fast-a',
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
),
(
    'POC_FAST_B',
    'LLAMA_CPP',
    'http://poc-llm-fast-b:8080',
    'secret://poc/llm-fast-b',
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
),
(
    'POC_DEEP',
    'LLAMA_CPP',
    'http://poc-llm-deep:8080',
    'secret://poc/llm-deep',
    '{"chat_path":"/v1/chat/completions","health_path":"/health"}'::jsonb,
    'ACTIVE'
)
ON CONFLICT (connection_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT 'POC_FAST_PRIMARY', p.id, c.id, 'poc-fast-understanding', 100, 0.9000, 4, 'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_FAST_A'
WHERE p.profile_code = 'FAST_UNDERSTANDING'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT 'POC_FAST_CHALLENGER', p.id, c.id, 'poc-fast-challenger', 90, 0.1000, 4, 'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_FAST_B'
WHERE p.profile_code = 'FAST_UNDERSTANDING_CHALLENGER'
ON CONFLICT (deployment_code) DO NOTHING;

INSERT INTO ai_model_deployments (
    deployment_code, model_profile_id, provider_connection_id, runtime_alias,
    priority, traffic_weight, max_parallel_requests, status
)
SELECT 'POC_DEEP_FALLBACK', p.id, c.id, 'poc-deep-understanding', 100, 1.0000, 1, 'ACTIVE'
FROM ai_model_profiles p
JOIN ai_provider_connections c ON c.connection_code = 'POC_DEEP'
WHERE p.profile_code = 'DEEP_UNDERSTANDING'
ON CONFLICT (deployment_code) DO NOTHING;

-- Control route (90%)
INSERT INTO ai_route_versions (
    task_code, route_version, display_name,
    primary_deployment_id, fallback_deployment_id,
    confidence_policy_id, timeout_policy_id,
    condition_expression, traffic_weight, priority, is_active, notes
)
SELECT
    'NEED_UNDERSTANDING', 1, 'POC control FAST A',
    d.id, fb.id, cp.id, tp.id,
    '{"locale":"tr-TR","client":"MOBILE","experiment":"FAST_MODEL_2026_01"}'::jsonb,
    0.9000, 100, TRUE, 'POC control arm'
FROM ai_model_deployments d
JOIN ai_model_deployments fb ON fb.deployment_code = 'POC_DEEP_FALLBACK'
JOIN ai_confidence_policies cp ON cp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
JOIN ai_timeout_policies tp ON tp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
WHERE d.deployment_code = 'POC_FAST_PRIMARY'
ON CONFLICT (task_code, route_version) DO NOTHING;

-- Challenger route (10%)
INSERT INTO ai_route_versions (
    task_code, route_version, display_name,
    primary_deployment_id, fallback_deployment_id,
    confidence_policy_id, timeout_policy_id,
    condition_expression, traffic_weight, priority, is_active, notes
)
SELECT
    'NEED_UNDERSTANDING', 2, 'POC challenger FAST B',
    d.id, fb.id, cp.id, tp.id,
    '{"locale":"tr-TR","client":"MOBILE","experiment":"FAST_MODEL_2026_01"}'::jsonb,
    0.1000, 90, TRUE, 'POC challenger arm'
FROM ai_model_deployments d
JOIN ai_model_deployments fb ON fb.deployment_code = 'POC_DEEP_FALLBACK'
JOIN ai_confidence_policies cp ON cp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
JOIN ai_timeout_policies tp ON tp.policy_code = 'NEED_UNDERSTANDING_DEFAULT'
WHERE d.deployment_code = 'POC_FAST_CHALLENGER'
ON CONFLICT (task_code, route_version) DO NOTHING;
