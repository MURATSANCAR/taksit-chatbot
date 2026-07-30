from taksitlio.conversation.state import apply_conversation_update
from taksitlio.model_router.router import ConfidencePolicy, ModelRouter, RouteDecision


def test_apply_budget_update_preserves_need():
    current = {
        "need_description": "telefon",
        "budget": {"type": "APPROXIMATE", "value": 40000, "currency": "TRY"},
        "preferences": [{"concept": "camera_quality", "importance": 0.88}],
    }
    update = {
        "operation": "UPDATE",
        "updates": [
            {"field": "budget.value", "old_value": 40000, "new_value": 50000}
        ],
        "preserve": ["need_description", "preferences.camera_quality"],
        "confidence": 0.98,
    }
    result = apply_conversation_update(current, update)
    assert result["budget"]["value"] == 50000
    assert result["need_description"] == "telefon"
    assert result["preferences"][0]["concept"] == "camera_quality"


def test_confidence_policy_routes_low_confidence_to_fallback():
    policy = ConfidencePolicy(policy_code="t")
    decision, reason = ModelRouter._apply_confidence_policy(
        {"confidence": 0.4, "clarification": {"required": False}, "ambiguities": []},
        policy,
    )
    assert decision == RouteDecision.FALLBACK
    assert reason == "low_confidence"


def test_confidence_policy_prefers_clarification():
    policy = ConfidencePolicy(policy_code="t")
    decision, reason = ModelRouter._apply_confidence_policy(
        {
            "confidence": 0.9,
            "clarification": {"required": True, "question_intent": "device_type"},
            "ambiguities": [],
        },
        policy,
    )
    assert decision == RouteDecision.CLARIFY
    assert reason == "clarification_required"
