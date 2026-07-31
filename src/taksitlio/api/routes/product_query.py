"""Product card progressive response API (ADR-010 P5).

Accepts already-resolved card sources — does not crawl merchants synchronously.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.chatbot_cards import (
    CardSourceProduct,
    ProductCardFinanceSummary,
    ResponsePhase,
    build_finance_enriched_phase,
    build_first_cards_phase,
    build_searching_phase,
    card_to_public_dict,
)
from taksitlio.product_query.ranking import RankedProduct

router = APIRouter(tags=["product-query"])


class FinanceIn(BaseModel):
    institution_display_name: str
    term_months: int = Field(..., gt=0)
    monthly_payment: float
    total_repayment: float
    display_label: str = "Tahmini aylık ödeme"
    campaign_ends_at: Optional[str] = None
    fees_total: float = 0.0
    institution_logo_cdn_url: Optional[str] = None


class CardSourceIn(BaseModel):
    product_id: str
    display_name: str
    brand_model: Optional[str] = None
    merchant_display_name: str
    price: float
    list_price: Optional[float] = None
    currency: str = "TRY"
    stock_status: str = "AVAILABLE"
    thumbnail_cdn_url: Optional[str] = None
    has_primary_image: bool = False
    merchant_logo_cdn_url: Optional[str] = None
    product_url: Optional[str] = None
    price_checked_at: Optional[str] = None
    campaign_checked_at: Optional[str] = None
    best_finance: Optional[FinanceIn] = None
    ranking_label: Optional[str] = None
    disqualified: bool = False


class ProgressiveCardsIn(BaseModel):
    phase: str = Field(
        default="FIRST_CARDS",
        description="SEARCHING | FIRST_CARDS | FINANCE_ENRICHED",
    )
    message: Optional[str] = None
    products: List[CardSourceIn] = Field(default_factory=list)


class ProgressiveCardsOut(BaseModel):
    phase: str
    message: Optional[str] = None
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


@router.post("/product-query/progressive-cards", response_model=ProgressiveCardsOut)
async def progressive_cards(payload: ProgressiveCardsIn) -> ProgressiveCardsOut:
    sources = []
    ranked = []
    for p in payload.products:
        finance = None
        if p.best_finance is not None:
            finance = ProductCardFinanceSummary(**p.best_finance.model_dump())
        sources.append(
            CardSourceProduct(
                product_id=p.product_id,
                display_name=p.display_name,
                brand_model=p.brand_model,
                merchant_display_name=p.merchant_display_name,
                price=p.price,
                list_price=p.list_price,
                currency=p.currency,
                stock_status=p.stock_status,
                thumbnail_cdn_url=p.thumbnail_cdn_url,
                has_primary_image=p.has_primary_image,
                merchant_logo_cdn_url=p.merchant_logo_cdn_url,
                product_url=p.product_url,
                price_checked_at=p.price_checked_at,
                campaign_checked_at=p.campaign_checked_at,
                best_finance=finance,
            )
        )
        ranked.append(
            RankedProduct(
                product_id=p.product_id,
                score=0.0 if p.disqualified else 1.0,
                label=p.ranking_label or "Kriterlerinize en yakın seçenek",
                disqualified=p.disqualified,
                disqualify_reasons=("client_flag",) if p.disqualified else (),
            )
        )

    phase = (payload.phase or "FIRST_CARDS").upper()
    if phase == ResponsePhase.SEARCHING.value:
        result = build_searching_phase(payload.message or "Ürünleri arıyorum")
    elif phase == ResponsePhase.FINANCE_ENRICHED.value:
        result = build_finance_enriched_phase(sources, ranked, message=payload.message)
    else:
        result = build_first_cards_phase(sources, ranked, message=payload.message)

    return ProgressiveCardsOut(
        phase=result.phase.value,
        message=result.message,
        cards=[card_to_public_dict(c) for c in result.cards],
        diagnostics=dict(result.diagnostics),
    )


class EntityCandidateIn(BaseModel):
    entity_id: str
    display_name: str
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    entity_type: str = "unknown"


class SearchProductIn(BaseModel):
    product_id: str
    display_name: str
    brand_model: Optional[str] = None
    merchant_id: str
    merchant_display_name: str
    price: float
    list_price: Optional[float] = None
    currency: str = "TRY"
    stock_status: str = "AVAILABLE"
    price_freshness: str = "FRESH"
    has_primary_image: bool = False
    thumbnail_cdn_url: Optional[str] = None
    merchant_logo_cdn_url: Optional[str] = None
    product_url: Optional[str] = None
    price_checked_at: Optional[str] = None
    campaign_checked_at: Optional[str] = None
    query_relevance: float = 0.5
    attribute_coverage: float = 0.5
    best_monthly_payment: Optional[float] = None
    best_total_repayment: Optional[float] = None
    best_term_months: Optional[int] = None
    finance_active: bool = False
    rate_fresh: bool = False
    campaign_active: bool = True
    best_finance: Optional[FinanceIn] = None


class ProductSearchIn(BaseModel):
    utterance: str = Field(..., min_length=1, max_length=4000)
    merchant_text: Optional[str] = None
    institution_texts: List[str] = Field(default_factory=list)
    ranking_mode: str = "CHEAPEST_PRODUCT_PRICE"
    max_price: Optional[float] = None
    requested_term: Optional[int] = None
    phase: str = "FIRST_CARDS"
    cache_version: Optional[str] = None
    merchants: List[EntityCandidateIn] = Field(default_factory=list)
    institutions: List[EntityCandidateIn] = Field(default_factory=list)
    products: List[SearchProductIn] = Field(default_factory=list)
    use_popular_cache: bool = True
    use_catalog: bool = True
    catalog_merchant_id: Optional[int] = None
    catalog_limit: int = Field(default=50, ge=1, le=200)


class ProductSearchOut(BaseModel):
    phase: str
    message: Optional[str] = None
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    clarifications: List[str] = Field(default_factory=list)
    refresh_jobs: List[Dict[str, Any]] = Field(default_factory=list)
    merchant_resolution: Optional[Dict[str, Any]] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class ResolveEntitiesIn(BaseModel):
    texts: List[str] = Field(..., min_length=1)
    entity_type: str = "merchant"
    cache_version: Optional[str] = None
    candidates: List[EntityCandidateIn] = Field(default_factory=list)


class ResolveEntitiesOut(BaseModel):
    resolutions: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


def _entity_candidates(rows: List[EntityCandidateIn]) -> list:
    from taksitlio.entity_resolution import EntityCandidate

    return [
        EntityCandidate(
            entity_id=r.entity_id,
            display_name=r.display_name,
            canonical_name=r.canonical_name,
            aliases=tuple(r.aliases),
            entity_type=r.entity_type,
        )
        for r in rows
    ]


def _resolve_cache_bundle(request: Request, cache_version: Optional[str]):
    from taksitlio.entity_resolution.cache import InMemoryAliasResolutionCache
    from taksitlio.product_query.cache_wiring import (
        build_product_query_caches,
        caches_from_container,
    )
    from taksitlio.product_query.query_cache import InMemoryPopularQueryCache

    container = container_from(request)
    caches = caches_from_container(container)
    if caches is None:
        caches = build_product_query_caches(container.settings, redis=None)
    version = cache_version or caches.catalog_cache_version
    alias = caches.alias
    if isinstance(alias, InMemoryAliasResolutionCache):
        alias.set_version(version)
    popular = caches.popular
    if isinstance(popular, InMemoryPopularQueryCache):
        popular.set_version(version)
    return caches, version, alias, popular


@router.post("/product-query/resolve-entities", response_model=ResolveEntitiesOut)
async def resolve_entities(payload: ResolveEntitiesIn, request: Request) -> ResolveEntitiesOut:
    """Fuzzy resolve against request-supplied catalog candidates (no static maps)."""

    from taksitlio.entity_resolution import resolve_entity
    from taksitlio.entity_resolution.cache import (
        resolution_cache_key,
        resolution_to_cache_dict,
    )

    caches, version, alias, _popular = _resolve_cache_bundle(
        request, payload.cache_version
    )
    catalog = _entity_candidates(payload.candidates)
    resolutions: List[Dict[str, Any]] = []
    for text in payload.texts:
        key = resolution_cache_key(
            entity_type=payload.entity_type,
            query_text=text,
            cache_version=version,
        )
        cached = await alias.get(key)
        result = resolve_entity(text, catalog)
        blob = resolution_to_cache_dict(result)
        await alias.put(key, blob, ttl_seconds=caches.alias_ttl_seconds)
        row = dict(blob)
        row["cache_hit"] = cached is not None
        resolutions.append(row)

    return ResolveEntitiesOut(
        resolutions=resolutions,
        diagnostics={
            "entity_type": payload.entity_type,
            "cache_version": version,
            "candidate_count": len(catalog),
        },
    )


@router.post("/product-query/search", response_model=ProductSearchOut)
async def product_search(payload: ProductSearchIn, request: Request) -> ProductSearchOut:
    """Compose fuzzy resolve + rank + progressive cards. No synchronous crawl."""

    from taksitlio.chatbot_cards import ProductCardFinanceSummary
    from taksitlio.entity_resolution.cache import resolution_to_cache_dict
    from taksitlio.product_query.query_cache import popular_query_cache_key
    from taksitlio.product_query.ranking import RankingMode
    from taksitlio.product_query.search import (
        ProductSearchRequest,
        SearchProductCandidate,
        search_products,
    )

    caches, version, alias, popular = _resolve_cache_bundle(
        request, payload.cache_version
    )

    try:
        mode = RankingMode(payload.ranking_mode)
    except ValueError:
        mode = RankingMode.BEST_OVERALL_VALUE

    popular_key = popular_query_cache_key(
        utterance=payload.utterance,
        ranking_mode=mode.value,
        cache_version=version,
    )
    if payload.use_popular_cache:
        hit = await popular.get(popular_key)
        if hit is not None and isinstance(hit.get("cards"), list):
            diagnostics = dict(hit.get("diagnostics") or {})
            diagnostics["cache_hit"] = "popular"
            return ProductSearchOut(
                phase=str(hit.get("phase") or "FIRST_CARDS"),
                message=hit.get("message"),
                cards=list(hit.get("cards") or []),
                clarifications=list(hit.get("clarifications") or []),
                refresh_jobs=list(hit.get("refresh_jobs") or []),
                merchant_resolution=hit.get("merchant_resolution"),
                diagnostics=diagnostics,
            )

    products = []
    for p in payload.products:
        finance = None
        if p.best_finance is not None:
            finance = ProductCardFinanceSummary(**p.best_finance.model_dump())
        products.append(
            SearchProductCandidate(
                product_id=p.product_id,
                display_name=p.display_name,
                brand_model=p.brand_model,
                merchant_id=p.merchant_id,
                merchant_display_name=p.merchant_display_name,
                price=p.price,
                list_price=p.list_price,
                currency=p.currency,
                stock_status=p.stock_status,
                price_freshness=p.price_freshness,
                has_primary_image=p.has_primary_image,
                thumbnail_cdn_url=p.thumbnail_cdn_url,
                merchant_logo_cdn_url=p.merchant_logo_cdn_url,
                product_url=p.product_url,
                price_checked_at=p.price_checked_at,
                campaign_checked_at=p.campaign_checked_at,
                query_relevance=p.query_relevance,
                attribute_coverage=p.attribute_coverage,
                best_monthly_payment=p.best_monthly_payment,
                best_total_repayment=p.best_total_repayment,
                best_term_months=p.best_term_months,
                finance_active=p.finance_active,
                rate_fresh=p.rate_fresh,
                campaign_active=p.campaign_active,
                card_finance=finance,
            )
        )

    catalog_source = "request"
    if not products and payload.use_catalog:
        container = container_from(request)
        product_catalog = container.extras.get("product_catalog")
        if product_catalog is not None:
            from taksitlio.product_query.candidates import (
                load_search_candidates_from_catalog,
            )

            products = list(
                await load_search_candidates_from_catalog(
                    product_catalog,
                    utterance=payload.utterance,
                    merchant_id=payload.catalog_merchant_id,
                    limit=payload.catalog_limit,
                    merchants=container.extras.get("merchant_directory"),
                    finance_index=container.extras.get("finance_option_index"),
                    institutions=container.extras.get("institution_labels"),
                )
            )
            catalog_source = "catalog"

    result = await search_products(
        ProductSearchRequest(
            utterance=payload.utterance,
            merchant_text=payload.merchant_text,
            institution_texts=tuple(payload.institution_texts),
            ranking_mode=mode,
            max_price=payload.max_price,
            requested_term=payload.requested_term,
            phase=payload.phase,
            cache_version=version,
        ),
        products=products,
        merchant_catalog=_entity_candidates(payload.merchants),
        institution_catalog=_entity_candidates(payload.institutions),
        cache=alias,
        alias_ttl_seconds=caches.alias_ttl_seconds,
    )

    merchant_payload = None
    if result.merchant_resolution is not None:
        merchant_payload = resolution_to_cache_dict(result.merchant_resolution)

    cards = [card_to_public_dict(c) for c in result.result_phase.cards]
    refresh_jobs = [
        {
            "queue": j.queue_name.value,
            "priority": j.priority,
            "product_id": j.product_id,
            "payload": j.payload or {},
        }
        for j in result.refresh_jobs
    ]
    out = ProductSearchOut(
        phase=result.result_phase.phase.value,
        message=result.result_phase.message,
        cards=cards,
        clarifications=list(result.clarifications),
        refresh_jobs=refresh_jobs,
        merchant_resolution=merchant_payload,
        diagnostics={
            **dict(result.diagnostics),
            "cache_hit": None,
            "candidate_source": catalog_source,
        },
    )

    if payload.use_popular_cache and cards:
        await popular.put(
            popular_key,
            {
                "phase": out.phase,
                "message": out.message,
                "cards": out.cards,
                "clarifications": out.clarifications,
                "refresh_jobs": out.refresh_jobs,
                "merchant_resolution": out.merchant_resolution,
                "diagnostics": {"source": "computed"},
            },
            ttl_seconds=caches.popular_ttl_seconds,
        )

    return out
