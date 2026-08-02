"""JSON schema and forbidden-field registry for canonical search plans."""

from __future__ import annotations

FORBIDDEN_PLAN_FIELDS: frozenset[str] = frozenset({
    "product_id",
    "product_ids",
    "user_id",
    "session_id",
    "ip_address",
    "cookie",
    "token",
    "password",
    "secret",
    "api_key",
    "credit_card",
    "tc_kimlik",
    "phone_number",
    "email",
    "address",
})

_CONSTRAINT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "constraint_id": {"type": "string"},
        "dimension": {"type": "string"},
        "operator": {
            "type": "string",
            "enum": ["EQ", "NEQ", "GT", "GTE", "LT", "LTE", "IN", "NOT_IN", "CONTAINS", "EXCLUDES", "RANGE"],
        },
        "value": {},
        "unit": {"type": ["string", "null"]},
        "strength": {"type": "string", "enum": ["HARD", "SOFT", "OPTIONAL"]},
        "priority": {"type": "integer"},
        "source_text": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source": {"type": "string"},
        "scope": {"type": "string"},
    },
    "required": ["constraint_id", "dimension", "operator", "strength"],
}

_EXCEPTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "exception_id": {"type": "string"},
        "target_constraint_id": {"type": "string"},
        "condition_code": {"type": "string"},
        "stretch_value": {},
        "policy_thresholds": {"type": ["object", "null"]},
    },
    "required": ["exception_id", "target_constraint_id", "condition_code"],
}

_CATEGORY_REF_SCHEMA: dict = {
    "type": ["object", "null"],
    "properties": {
        "resolved_id": {"type": ["string", "null"]},
        "raw_text": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "category": _CATEGORY_REF_SCHEMA,
        "hard_constraints": {"type": "array", "items": _CONSTRAINT_SCHEMA},
        "soft_preferences": {"type": "array", "items": _CONSTRAINT_SCHEMA},
        "excluded_constraints": {"type": "array", "items": _CONSTRAINT_SCHEMA},
        "conditional_exceptions": {"type": "array", "items": _EXCEPTION_SCHEMA},
        "ranking_priorities": {"type": "array", "items": {"type": "string"}},
        "unsupported_dimensions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["item_id"],
}

_BUDGET_SCHEMA: dict = {
    "type": ["object", "null"],
    "properties": {
        "minimum": {"type": ["number", "null"]},
        "target_maximum": {"type": ["number", "null"]},
        "stretch_maximum": {"type": ["number", "null"]},
        "currency": {"type": "string"},
    },
}

_GLOBAL_CONSTRAINTS_SCHEMA: dict = {
    "type": ["object", "null"],
    "properties": {
        "budget": _BUDGET_SCHEMA,
        "allowed_merchants": {"type": "array", "items": {"type": "string"}},
        "excluded_merchants": {"type": "array", "items": {"type": "string"}},
    },
}

_CAMPAIGN_INTENT_SCHEMA: dict = {
    "type": ["object", "null"],
    "properties": {
        "requested": {"type": "boolean"},
        "required": {"type": "boolean"},
        "institution_preferences": {"type": "array", "items": {"type": "string"}},
        "requested_terms": {"type": "array", "items": {"type": "integer"}},
        "zero_rate_preferred": {"type": "boolean"},
        "ranking_mode": {"type": ["string", "null"]},
    },
}

_SOURCE_SCHEMA: dict = {
    "type": ["object", "null"],
    "properties": {
        "fast_parser_used": {"type": "boolean"},
        "llm_patch_used": {"type": "boolean"},
    },
}

CANONICAL_PLAN_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CanonicalSearchPlan",
    "type": "object",
    "properties": {
        "plan_version": {"type": "string", "const": "v1"},
        "request_type": {
            "type": "string",
            "enum": [
                "SINGLE_PRODUCT_SEARCH",
                "MULTI_ITEM_BUNDLE",
                "PRODUCT_AND_CAMPAIGN_SEARCH",
                "EXPLORATORY_SEARCH",
                "COMPARISON",
                "OUT_OF_SCOPE",
            ],
        },
        "items": {"type": "array", "items": _ITEM_SCHEMA},
        "global_constraints": _GLOBAL_CONSTRAINTS_SCHEMA,
        "campaign_intent": _CAMPAIGN_INTENT_SCHEMA,
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array"},
        "clarification_required": {"type": "boolean"},
        "clarification_questions": {"type": "array"},
        "unsupported_capabilities": {"type": "array", "items": {"type": "string"}},
        "source": _SOURCE_SCHEMA,
    },
    "required": ["plan_version", "request_type", "items"],
}
