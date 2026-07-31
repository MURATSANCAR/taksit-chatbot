"""Application container — wires production or in-memory stacks.

Never embeds vendor model names, IPs, or ports. Those live in DB bootstrap only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from taksitlio.campaign.eligibility import EligibilityEngine
from taksitlio.campaign.models import Campaign, CampaignRetriever, InMemoryCampaignRepository
from taksitlio.campaign.ranking import RankingEngine
from taksitlio.category.matcher import (
    Category,
    InMemoryCategoryRepository,
    SemanticCategoryMatcher,
    bootstrap_category_with_lexical_embedding,
)
from taksitlio.config.settings import InfraSettings
from taksitlio.conversation.session import (
    ConversationStateManager,
    InMemorySessionStore,
)
from taksitlio.embeddings.client import LexicalEmbedder
from taksitlio.model_gateway.health import InMemoryRuntimeHealthRegistry
from taksitlio.model_gateway.gateway import ModelGateway
from taksitlio.pipeline.orchestrator import ChatPipeline
from taksitlio.response.grounded import (
    GroundedResponseGenerator,
    ResponsePolicy,
    StaticResponsePolicyProvider,
)


@dataclass
class AppContainer:
    settings: InfraSettings
    pipeline: ChatPipeline
    gateway: ModelGateway
    http_client: httpx.AsyncClient
    extras: dict[str, Any]

    async def aclose(self) -> None:
        await self.http_client.aclose()
        pool = self.extras.get("pool")
        if pool is not None:
            await pool.close()
        redis = self.extras.get("redis")
        if redis is not None:
            await redis.aclose()


def build_demo_categories() -> list[Category]:
    raw = [
        Category(
            id=1,
            category_code="MOBILE_PHONE",
            display_name="Cep Telefonu",
            description="Akıllı telefon, mobil cihaz, cep telefonu ürünleri",
            synonyms=("telefon", "cep telefonu", "akıllı telefon", "mobil"),
        ),
        Category(
            id=2,
            category_code="LAPTOP",
            display_name="Dizüstü Bilgisayar",
            description="Laptop, notebook, dizüstü bilgisayar ürünleri",
            synonyms=("laptop", "notebook", "dizüstü", "bilgisayar"),
        ),
        Category(
            id=3,
            category_code="TABLET",
            display_name="Tablet",
            description="Tablet bilgisayar ve benzeri taşınabilir ekranlı cihazlar",
            synonyms=("tablet",),
        ),
    ]
    return [bootstrap_category_with_lexical_embedding(c) for c in raw]


def build_demo_campaigns() -> list[Campaign]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return [
        Campaign(
            id=1,
            campaign_code="PHONE_CAM_40K",
            title="Kamera odaklı telefon — 12 taksit",
            summary="Yüksek kamera kaliteli akıllı telefon, 12 aya varan taksit fırsatı",
            category_code="MOBILE_PHONE",
            category_id=1,
            brand="DemoBrand",
            product_name="CamPhone X",
            list_price=39999.0,
            installment_count=12,
            monthly_payment=3333.0,
            cash_price=37999.0,
            min_budget=30000.0,
            max_budget=45000.0,
            membership_cta_url="https://taksitlio.example/uye-ol",
            membership_cta_label="Taksitlio'ya üye ol",
            starts_at=now - timedelta(days=7),
            ends_at=now + timedelta(days=90),
            attributes={"camera_quality": 0.95, "installment": 0.9},
            search_text="kamera kaliteli akıllı telefon taksit",
        ),
        Campaign(
            id=2,
            campaign_code="PHONE_BUDGET_35K",
            title="Uygun fiyatlı telefon — düşük aylık ödeme",
            summary="Bütçe dostu telefon, düşük aylık taksit",
            category_code="MOBILE_PHONE",
            category_id=1,
            list_price=32999.0,
            installment_count=12,
            monthly_payment=2749.0,
            min_budget=20000.0,
            max_budget=36000.0,
            membership_cta_url="https://taksitlio.example/uye-ol",
            starts_at=now - timedelta(days=7),
            ends_at=now + timedelta(days=90),
            attributes={"camera_quality": 0.6, "installment": 0.95},
            search_text="uygun fiyatlı telefon düşük taksit",
        ),
        Campaign(
            id=3,
            campaign_code="LAPTOP_SCHOOL_25K",
            title="Okul için hafif laptop",
            summary="Üniversite ve okul kullanımı için hafif dizüstü bilgisayar",
            category_code="LAPTOP",
            category_id=2,
            list_price=24999.0,
            installment_count=9,
            monthly_payment=2777.0,
            min_budget=15000.0,
            max_budget=30000.0,
            membership_cta_url="https://taksitlio.example/uye-ol",
            starts_at=now - timedelta(days=7),
            ends_at=now + timedelta(days=90),
            attributes={"weight_light": 0.95, "installment": 0.8},
            search_text="okul üniversite hafif laptop",
        ),
        Campaign(
            id=4,
            campaign_code="TABLET_SCHOOL_18K",
            title="Okul için tablet",
            summary="Hafif tablet, ders ve not alma için uygun",
            category_code="TABLET",
            category_id=3,
            list_price=17999.0,
            installment_count=6,
            monthly_payment=2999.0,
            min_budget=10000.0,
            max_budget=22000.0,
            membership_cta_url="https://taksitlio.example/uye-ol",
            starts_at=now - timedelta(days=7),
            ends_at=now + timedelta(days=90),
            attributes={"weight_light": 0.9},
            search_text="okul tablet hafif ders",
        ),
    ]


class _StubUnderstanding:
    """In-memory demo without live inference — pipeline still exercises post-understanding."""

    def __init__(self, sessions: ConversationStateManager) -> None:
        self.sessions = sessions

    async def process_message(self, *, session_id: str, message: str, user_id: str | None = None):
        from taksitlio.model_router.router_types import (
            ReasonCode,
            RouteDecision,
            UnderstandingResult,
        )

        profile = {
            "intent": {"type": "PRODUCT_PURCHASE", "confidence": 0.9},
            "need_description": message[:200] or "ürün ihtiyacı",
            "budget": {
                "type": "UNKNOWN",
                "value": None,
                "minimum": None,
                "maximum": None,
                "monthly_payment": None,
                "currency": "TRY",
            },
            "preferences": [],
            "usage_context": [],
            "entities": [],
            "ambiguities": [],
            "clarification": {"required": False, "question_intent": None},
            "confidence": 0.9,
        }
        session = await self.sessions.apply_need_profile(session_id, profile)
        return type(
            "Turn",
            (),
            {
                "session": session,
                "understanding": UnderstandingResult(
                    decision=RouteDecision.CONTINUE,
                    reason_code=ReasonCode.OK,
                    need_profile=profile,
                    used_deployment_code=None,
                    used_profile_code=None,
                    latency_ms=0.0,
                    system_confidence=0.9,
                    model_reported_confidence=0.9,
                ),
                "need_profile": profile,
                "was_update": False,
            },
        )()


def build_in_memory_container(
    settings: InfraSettings | None = None,
    *,
    stub_understanding: bool = True,
) -> AppContainer:
    settings = settings or InfraSettings.from_env(allow_missing=True)
    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    health = InMemoryRuntimeHealthRegistry()
    gateway = ModelGateway(client=client, health=health)
    sessions = ConversationStateManager(InMemorySessionStore())
    understanding = _StubUnderstanding(sessions)

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
    from taksitlio.product_query.cache_wiring import build_product_query_caches

    product_query_caches = build_product_query_caches(settings, redis=None)
    return AppContainer(
        settings=settings,
        pipeline=pipeline,
        gateway=gateway,
        http_client=client,
        extras={
            "sessions": sessions,
            "campaign_repo": campaign_repo,
            "category_repo": categories,
            "understanding": understanding,
            "health": health,
            "profiles": None,
            "product_query_caches": product_query_caches,
        },
    )


async def build_production_container(settings: InfraSettings) -> AppContainer:
    """Production wiring loads deployments/routes from Postgres — no hardcoded hosts."""
    from redis.asyncio import Redis

    from taksitlio.campaign.repository import PostgresCampaignRepository
    from taksitlio.category.repository import PostgresCategoryRepository
    from taksitlio.conversation.session import RedisSessionStore
    from taksitlio.db.pool import create_pool
    from taksitlio.embeddings.client import LexicalEmbedder
    from taksitlio.understanding.service import UnderstandingService

    pool = await create_pool(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    health = InMemoryRuntimeHealthRegistry()
    gateway = ModelGateway(client=client, health=health)

    # Route/deployment repos are loaded by a follow-up wiring module; until then
    # production boot requires bootstrap SQL + repository adapters.
    from taksitlio.db.route_repository import PostgresRouteVersionRepository

    route_repo = PostgresRouteVersionRepository(pool)
    await route_repo.refresh()

    from taksitlio.model_router.router import ModelRouter
    from taksitlio.understanding.service import StaticPromptProvider, DEFAULT_NEED_PROMPT

    router = ModelRouter(gateway, route_repo, health=health)
    sessions = ConversationStateManager(
        RedisSessionStore(
            redis,
            key_prefix=settings.redis_key_prefix,
            ttl_seconds=settings.session_ttl_seconds,
        )
    )
    prompts = StaticPromptProvider({"NEED_UNDERSTANDING": DEFAULT_NEED_PROMPT})
    understanding = UnderstandingService(router, gateway, sessions, prompts)

    category_repo = PostgresCategoryRepository(pool)
    matcher = SemanticCategoryMatcher(category_repo, LexicalEmbedder())
    campaign_repo = PostgresCampaignRepository(pool)
    responder = GroundedResponseGenerator(
        gateway=None,
        response_profile=None,
        prompts=None,
        policies=StaticResponsePolicyProvider(ResponsePolicy()),
    )
    pipeline = ChatPipeline(
        understanding=understanding,
        category_matcher=matcher,
        retriever=CampaignRetriever(campaign_repo),
        eligibility=EligibilityEngine(),
        ranking=RankingEngine(),
        responder=responder,
        campaign_repo=campaign_repo,
    )
    from taksitlio.product_query.cache_wiring import build_product_query_caches

    product_query_caches = build_product_query_caches(settings, redis=redis)
    return AppContainer(
        settings=settings,
        pipeline=pipeline,
        gateway=gateway,
        http_client=client,
        extras={
            "pool": pool,
            "redis": redis,
            "sessions": sessions,
            "route_repo": route_repo,
            "health": health,
            "product_query_caches": product_query_caches,
        },
    )
