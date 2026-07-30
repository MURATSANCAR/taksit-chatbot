"""Comprehensive MVP pipeline and domain tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from taksitlio.app.container import (
    build_demo_campaigns,
    build_demo_categories,
    build_in_memory_container,
)
from taksitlio.campaign.eligibility import EligibilityEngine
from taksitlio.campaign.models import CampaignRetriever, InMemoryCampaignRepository
from taksitlio.campaign.ranking import RankingEngine
from taksitlio.category.matcher import (
    InMemoryCategoryRepository,
    SemanticCategoryMatcher,
)
from taksitlio.conversation.session import ConversationStateManager, InMemorySessionStore
from taksitlio.conversation.state import apply_conversation_update
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.eval.golden import (
    intent_accuracy,
    load_golden_cases,
    summarize_buckets,
    valid_json_rate,
)
from taksitlio.model_router.router import (
    ConfidencePolicy,
    ModelRouter,
    RouteDecision,
    UnderstandingResult,
)
from taksitlio.pipeline.orchestrator import ChatPipeline, ChatRequest
from taksitlio.response.grounded import (
    GroundedResponseGenerator,
    ResponsePolicy,
    StaticResponsePolicyProvider,
)


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


@pytest.mark.asyncio
async def test_session_manager_roundtrip():
    mgr = ConversationStateManager(InMemorySessionStore())
    state = await mgr.apply_need_profile(
        "s1",
        {"need_description": "telefon", "budget": {"value": 40000}},
        category_codes=["MOBILE_PHONE"],
    )
    assert state.turn_count == 1
    loaded = await mgr.get_or_create("s1")
    assert loaded.need_profile["need_description"] == "telefon"
    assert loaded.matched_category_codes == ["MOBILE_PHONE"]


@pytest.mark.asyncio
async def test_semantic_category_match_phone():
    repo = InMemoryCategoryRepository(build_demo_categories())
    matcher = SemanticCategoryMatcher(repo, LexicalEmbedder())
    result = await matcher.match("kamera kaliteli cep telefonu arıyorum")
    assert result.matches
    assert result.matches[0].category.category_code == "MOBILE_PHONE"


@pytest.mark.asyncio
async def test_eligibility_filters_over_budget():
    campaigns = build_demo_campaigns()
    engine = EligibilityEngine()
    need = {
        "budget": {
            "type": "APPROXIMATE",
            "value": 20000,
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        }
    }
    rules = [
        {"type": "STATUS_ACTIVE"},
        {"type": "WITHIN_DATE_WINDOW"},
        {"type": "BUDGET_COMPATIBLE"},
        {"type": "CATEGORY_MATCH"},
    ]
    eligible = engine.filter_eligible(
        campaigns,
        need,
        category_codes=["MOBILE_PHONE"],
        rules=rules,
    )
    codes = {c.campaign_code for c in eligible}
    assert "PHONE_CAM_40K" not in codes


@pytest.mark.asyncio
async def test_ranking_prefers_camera_campaign():
    campaigns = [c for c in build_demo_campaigns() if c.category_code == "MOBILE_PHONE"]
    engine = RankingEngine()
    need = {
        "budget": {"type": "APPROXIMATE", "value": 40000, "currency": "TRY"},
        "preferences": [
            {"concept": "camera_quality", "importance": 0.95},
            {"concept": "installment", "importance": 0.8},
        ],
    }
    ranked = engine.rank(
        campaigns,
        need,
        weights={
            "budget_fit": 0.35,
            "preference_fit": 0.35,
            "semantic_relevance": 0.1,
            "installment_fit": 0.15,
            "freshness": 0.05,
        },
        max_results=2,
    )
    assert ranked
    assert ranked[0].campaign.campaign_code == "PHONE_CAM_40K"


def _phone_need_profile() -> dict[str, Any]:
    return {
        "intent": {"type": "PRODUCT_PURCHASE", "confidence": 0.97},
        "need_description": "kamera kalitesi iyi ve taksitle alınabilecek mobil cihaz",
        "budget": {
            "type": "APPROXIMATE",
            "value": 40000,
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        },
        "preferences": [
            {"concept": "camera_quality", "importance": 0.88},
            {"concept": "installment", "importance": 0.94},
        ],
        "usage_context": [],
        "entities": [],
        "ambiguities": [],
        "clarification": {"required": False, "question_intent": None},
        "confidence": 0.95,
    }


@dataclass
class _StubUnderstanding:
    sessions: ConversationStateManager
    profile: dict[str, Any]
    decision: RouteDecision = RouteDecision.CONTINUE

    async def process_message(self, *, session_id: str, message: str, user_id: str | None = None):
        session = await self.sessions.apply_need_profile(session_id, self.profile)
        return type(
            "Turn",
            (),
            {
                "session": session,
                "understanding": UnderstandingResult(
                    decision=self.decision,
                    need_profile=self.profile,
                    used_profile_code="STUB",
                    latency_ms=1.0,
                ),
                "need_profile": self.profile,
                "was_update": False,
            },
        )()


@pytest.mark.asyncio
async def test_pipeline_end_to_end_with_stub_understanding():
    sessions = ConversationStateManager(InMemorySessionStore())
    understanding = _StubUnderstanding(sessions=sessions, profile=_phone_need_profile())
    categories = InMemoryCategoryRepository(build_demo_categories())
    matcher = SemanticCategoryMatcher(categories, LexicalEmbedder())
    campaign_repo = InMemoryCampaignRepository(build_demo_campaigns())
    responder = GroundedResponseGenerator(
        gateway=None,
        response_profile=None,
        prompts=None,
        policies=StaticResponsePolicyProvider(ResponsePolicy()),
    )
    pipeline = ChatPipeline(
        understanding=understanding,  # type: ignore[arg-type]
        category_matcher=matcher,
        retriever=CampaignRetriever(campaign_repo),
        eligibility=EligibilityEngine(),
        ranking=RankingEngine(),
        responder=responder,
        campaign_repo=campaign_repo,
    )
    result = await pipeline.handle(
        ChatRequest(session_id="demo", message="Telefon bakıyoruz, 40 bin civarı.")
    )
    assert result.decision == "CONTINUE"
    assert result.campaigns
    assert "Taksitlio" in result.reply or result.cta is not None
    assert any(c["campaign_code"] == "PHONE_CAM_40K" for c in result.campaigns)


@pytest.mark.asyncio
async def test_pipeline_clarification_path():
    sessions = ConversationStateManager(InMemorySessionStore())
    understanding = _StubUnderstanding(
        sessions=sessions,
        profile=_phone_need_profile(),
        decision=RouteDecision.CLARIFY,
    )
    # Override understanding result clarification
    async def process_message(*, session_id: str, message: str, user_id: str | None = None):
        session = await sessions.get_or_create(session_id)
        return type(
            "Turn",
            (),
            {
                "session": session,
                "understanding": UnderstandingResult(
                    decision=RouteDecision.CLARIFY,
                    need_profile=_phone_need_profile(),
                    used_profile_code="STUB",
                    latency_ms=1.0,
                    clarification_question_intent="budget",
                ),
                "need_profile": _phone_need_profile(),
                "was_update": False,
            },
        )()

    understanding.process_message = process_message  # type: ignore[method-assign]
    categories = InMemoryCategoryRepository(build_demo_categories())
    matcher = SemanticCategoryMatcher(categories, LexicalEmbedder())
    campaign_repo = InMemoryCampaignRepository(build_demo_campaigns())
    responder = GroundedResponseGenerator(
        gateway=None,
        response_profile=None,
        prompts=None,
        policies=StaticResponsePolicyProvider(ResponsePolicy()),
    )
    pipeline = ChatPipeline(
        understanding=understanding,  # type: ignore[arg-type]
        category_matcher=matcher,
        retriever=CampaignRetriever(campaign_repo),
        eligibility=EligibilityEngine(),
        ranking=RankingEngine(),
        responder=responder,
        campaign_repo=campaign_repo,
    )
    result = await pipeline.handle(ChatRequest(session_id="c1", message="bilmiyorum"))
    assert result.decision == "CLARIFY"
    assert "bütçe" in result.reply.casefold() or "ödeme" in result.reply.casefold()


def test_golden_dataset_loads():
    cases = load_golden_cases()
    assert len(cases) >= 20
    buckets = summarize_buckets(cases)
    assert "open_product" in buckets
    preds = {
        c.id: {
            "intent": {"type": (c.expected.get("intent") or {}).get("type")},
        }
        for c in cases
        if c.expected.get("intent")
    }
    assert intent_accuracy(cases, preds) == 1.0
    assert valid_json_rate([{"a": 1}, None, {"b": 2}]) == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_api_health_in_memory():
    from httpx import ASGITransport, AsyncClient

    from taksitlio.api.app import create_app

    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        models = await client.get("/v1/admin/models")
        assert models.status_code == 200
        assert models.json()["profiles"]
    await container.aclose()
