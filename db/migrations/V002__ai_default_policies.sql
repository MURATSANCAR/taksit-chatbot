-- V002: Technical default policies only (no hosts, no model names, no ports).

INSERT INTO ai_confidence_policies (
    policy_code,
    display_name,
    minimum_system_confidence,
    minimum_confidence,
    maximum_category_score_gap_for_clarification,
    fallback_on_invalid_schema,
    fallback_on_conflict,
    fallback_on_multiple_needs,
    fallback_on_budget_confusion,
    fallback_on_low_confidence,
    prefer_clarification_when_ambiguous,
    clarify_on_session_conflict,
    clarify_on_multiple_needs
) VALUES (
    'NEED_UNDERSTANDING_DEFAULT',
    'İhtiyaç anlama varsayılan sistem güven politikası',
    0.780,
    0.780,
    0.080,
    TRUE,
    FALSE,
    FALSE,
    FALSE,
    TRUE,
    TRUE,
    TRUE,
    TRUE
) ON CONFLICT (policy_code) DO NOTHING;

INSERT INTO ai_timeout_policies (
    policy_code,
    display_name,
    primary_timeout_ms,
    fallback_timeout_ms,
    total_budget_ms,
    min_fallback_remaining_ms,
    retry_same_model
) VALUES (
    'NEED_UNDERSTANDING_DEFAULT',
    'İhtiyaç anlama varsayılan timeout politikası',
    3000,
    8000,
    10000,
    500,
    FALSE
) ON CONFLICT (policy_code) DO NOTHING;
