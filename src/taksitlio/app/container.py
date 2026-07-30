"""Application container — wires production or in-memory stacks."""

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
from taksitlio.model_gateway.gateway import ModelGateway, ModelProfile
from taksitlio.model_gateway.repository import InMemoryProfileRepository
from taksitlio.model_router.router import (
    ConfidencePolicy,
    ModelRouter,
    TaskRoute,
    TimeoutPolicy,
)
from taksitlio.pipeline.orchestrator import ChatPipeline
from taksitlio.response.grounded import (
    GroundedResponseGenerator,
    ResponsePolicy,
    StaticResponsePolicyProvider,
)
from taksitlio.understanding.service import (
    DEFAULT_NEED_PROMPT,
    DEFAULT_UPDATE_PROMPT,
    StaticPromptProvider,
    UnderstandingService,
)


@dataclass
class AppContainer:
    settings: InfraSettings
    pipeline: ChatPipeline
    gateway: ModelGateway
    profiles: InMemoryProfileRepository
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


def _demo_profiles() -> list[ModelProfile]:
    common = dict(
        provider_type="LLAMA_CPP",
        task_type="UNDERSTANDING",
        context_limit=4096,
        max_output_tokens=128,
        temperature=0.0,
        timeout_ms=3000,
        parallel_slots=4,
        status="ACTIVE",
        configuration={
            "thinking_enabled": False,
            "streaming_enabled": False,
            "json_schema_required": True,
        },
    )
    return [
        ModelProfile(
            id=1,
            profile_code="FAST_UNDERSTANDING",
            display_name="FAST",
            endpoint_url="http://127.0.0.1:8080/v1/chat/completions",
            model_reference="Qwen3.5-4B",
            **common,
        ),
        ModelProfile(
            id=2,
            profile_code="DEEP_UNDERSTANDING",
            display_name="DEEP",
            endpoint_url="http://127.0.0.1:8082/v1/chat/completions",
            model_reference="local-deep-understanding",
            timeout_ms=8000,
            parallel_slots=1,
            provider_type="LLAMA_CPP",
            task_type="UNDERSTANDING",
            context_limit=8192,
            max_output_tokens=256,
            temperature=0.0,
            status="ACTIVE",
            configuration={
                "thinking_enabled": False,
                "streaming_enabled": False,
                "json_schema_required": True,
            },
        ),
        ModelProfile(
            id=3,
            profile_code="RESPONSE_GENERATION",
            display_name="Response",
            endpoint_url="http://127.0.0.1:8082/v1/chat/completions",
            model_reference="local-deep-understanding",
            provider_type="LLAMA_CPP",
            task_type="RESPONSE",
            context_limit=4096,
            max_output_tokens=512,
            temperature=0.2,
            timeout_ms=5000,
            parallel_slots=2,
            status="ACTIVE",
            configuration={"thinking_enabled": False, "grounded": True},
        ),
    ]


class _StaticRouteRepo:
    def __init__(self, route: TaskRoute) -> None:
        self._route = route

    def get_route(self, task_code: str) -> TaskRoute:
        if task_code != route_task(self._route):
            # allow NEED_UNDERSTANDING only in demo
            if task_code != "NEED_UNDERSTANDING":
                raise KeyError(task_code)
        return self._route


def route_task(route: TaskRoute) -> str:
    return route.task_code


def build_demo_categories() -> list[Category]:
    raw = [
        Category(
            id=1,
            category_code="MOBILE_PHONE",
            display_name="Cep Telefonu",
            description="Akıllı telefon, mobil cihaz, cep telefonu ürünleri",
            synonyms=("telefon", "cep telefonu", "akıllı telefon", "mobil", "iphone"),
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
            synonyms=("tablet", "ipad"),
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
            brand="DemoBrand",
            product_name="ValuePhone 12",
            list_price=32999.0,
            installment_count=12,
            monthly_payment=2749.0,
            cash_price=30999.0,
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


def build_in_memory_container(
    settings: InfraSettings | None = None,
    *,
    stub_understanding: bool = False,
) -> AppContainer:
    """
    Fully runnable stack without Postgres/Redis.

    When stub_understanding=True, ModelRouter is not called; use for unit/integration
    tests that inject a custom UnderstandingService via extras override.
    """
    settings = settings or InfraSettings.from_env(allow_missing=True)
    profiles = InMemoryProfileRepository(_demo_profiles())
    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    gateway = ModelGateway(profiles, client=client)

    fast = profiles.get_by_code("FAST_UNDERSTANDING")
    deep = profiles.get_by_code("DEEP_UNDERSTANDING")
    route = TaskRoute(
        task_code="NEED_UNDERSTANDING",
        primary=fast,
        fallback=deep,
        confidence_policy=ConfidencePolicy(policy_code="DEFAULT"),
        timeout_policy=TimeoutPolicy(policy_code="DEFAULT"),
    )
    router = ModelRouter(gateway, _StaticRouteRepo(route))
    sessions = ConversationStateManager(InMemorySessionStore())
    prompts = StaticPromptProvider(
        {
            "NEED_UNDERSTANDING": DEFAULT_NEED_PROMPT,
            "CONVERSATION_UPDATE": DEFAULT_UPDATE_PROMPT,
            "GROUNDED_RESPONSE": "Yalnızca verilen kampanyalara dayanarak Türkçe cevap yaz.",
        }
    )
    understanding = UnderstandingService(router, gateway, sessions, prompts)

    categories = InMemoryCategoryRepository(build_demo_categories())
    matcher = SemanticCategoryMatcher(categories, LexicalEmbedder())
    campaign_repo = InMemoryCampaignRepository(build_demo_campaigns())
    retriever = CampaignRetriever(campaign_repo)
    response_profile = profiles.get_by_code("RESPONSE_GENERATION")
    responder = GroundedResponseGenerator(
        gateway=gateway if not stub_understanding else None,
        response_profile=response_profile if not stub_understanding else None,
        prompts=prompts,
        policies=StaticResponsePolicyProvider(ResponsePolicy()),
    )
    pipeline = ChatPipeline(
        understanding=understanding,
        category_matcher=matcher,
        retriever=retriever,
        eligibility=EligibilityEngine(),
        ranking=RankingEngine(),
        responder=responder,
        campaign_repo=campaign_repo,
    )
    return AppContainer(
        settings=settings,
        pipeline=pipeline,
        gateway=gateway,
        profiles=profiles,
        http_client=client,
        extras={
            "sessions": sessions,
            "campaign_repo": campaign_repo,
            "category_repo": categories,
            "understanding": understanding,
            "router": router,
        },
    )


async def build_production_container(settings: InfraSettings) -> AppContainer:
    from redis.asyncio import Redis

    from taksitlio.campaign.repository import PostgresCampaignRepository
    from taksitlio.category.repository import PostgresCategoryRepository
    from taksitlio.conversation.session import RedisSessionStore
    from taksitlio.db.ai_repository import (
        AsyncProfileAdapter,
        PostgresProfileRepository,
        PostgresPromptRepository,
        PostgresTaskRouteRepository,
    )
    from taksitlio.db.pool import create_pool
    from taksitlio.embeddings.client import ProfileEmbedder

    pool = await create_pool(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    profile_repo = PostgresProfileRepository(pool)
    adapter = AsyncProfileAdapter(profile_repo)
    await adapter.refresh()
    route_repo = PostgresTaskRouteRepository(pool, adapter)
    await route_repo.refresh()
    prompt_repo = PostgresPromptRepository(pool)

    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    gateway = ModelGateway(adapter, client=client)
    router = ModelRouter(gateway, route_repo)
    sessions = ConversationStateManager(
        RedisSessionStore(
            redis,
            key_prefix=settings.redis_key_prefix,
            ttl_seconds=settings.session_ttl_seconds,
        )
    )
    understanding = UnderstandingService(router, gateway, sessions, prompt_repo)

    category_repo = PostgresCategoryRepository(pool)
    try:
        emb_profile = adapter.get_by_code("EMBEDDING_DEFAULT")
        embedder = ProfileEmbedder(emb_profile, client, fallback_lexical=True)
    except KeyError:
        embedder = LexicalEmbedder()
    matcher = SemanticCategoryMatcher(category_repo, embedder)

    campaign_repo = PostgresCampaignRepository(pool)
    retriever = CampaignRetriever(campaign_repo)

    response_profile = None
    try:
        response_profile = adapter.get_by_code("RESPONSE_GENERATION")
    except KeyError:
        pass

    responder = GroundedResponseGenerator(
        gateway=gateway,
        response_profile=response_profile,
        prompts=prompt_repo,
        policies=StaticResponsePolicyProvider(ResponsePolicy()),
    )
    pipeline = ChatPipeline(
        understanding=understanding,
        category_matcher=matcher,
        retriever=retriever,
        eligibility=EligibilityEngine(),
        ranking=RankingEngine(),
        responder=responder,
        campaign_repo=campaign_repo,
    )
    return AppContainer(
        settings=settings,
        pipeline=pipeline,
        gateway=gateway,
        profiles=adapter.as_in_memory(),
        http_client=client,
        extras={
            "pool": pool,
            "redis": redis,
            "profile_repo": profile_repo,
            "route_repo": route_repo,
            "prompt_repo": prompt_repo,
            "sessions": sessions,
            "adapter": adapter,
        },
    )
