"""End-to-end product search composition (ADR-010 §52) without live crawl."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.chatbot_cards import (
    CardSourceProduct,
    ProgressiveResponse,
    build_finance_enriched_phase,
    build_first_cards_phase,
    build_searching_phase,
    card_to_public_dict,
)
from taksitlio.entity_resolution import (
    EntityCandidate,
    ResolutionAction,
    ResolutionPolicy,
    ResolutionResult,
    resolve_entity,
)
from taksitlio.entity_resolution.cache import (
    AliasResolutionCache,
    NoOpAliasResolutionCache,
    resolution_cache_key,
    resolution_to_cache_dict,
)
from taksitlio.ingestion_scheduler import (
    FreshnessVerdict,
    SchedulerJobSpec,
    SchedulerQueue,
    classify_freshness,
    enqueue_search_driven_refresh,
)
from taksitlio.product_query.ranking import (
    RankableProduct,
    RankingMode,
    RankingWeights,
    rank_products_with_sponsored_isolation,
)


@dataclass(frozen=True)
class SearchProductCandidate:
    product_id: str
    display_name: str
    brand_model: Optional[str]
    merchant_id: str
    merchant_display_name: str
    price: float
    list_price: Optional[float] = None
    currency: str = "TRY"
    stock_status: str = "UNKNOWN"
    price_freshness: str = "UNVERIFIED"
    has_primary_image: bool = False
    thumbnail_cdn_url: Optional[str] = None
    merchant_logo_cdn_url: Optional[str] = None
    product_url: Optional[str] = None
    price_checked_at: Optional[str] = None
    campaign_checked_at: Optional[str] = None
    query_relevance: float = 0.5
    attribute_coverage: float = 0.5
    budget_ok: bool = True
    best_monthly_payment: Optional[float] = None
    best_total_repayment: Optional[float] = None
    best_term_months: Optional[int] = None
    finance_active: bool = False
    rate_fresh: bool = False
    campaign_active: bool = True
    card_finance: Optional[Any] = None  # ProductCardFinanceSummary | None
    last_price_verified_at: Optional[Any] = None  # datetime | None
    price_snapshot_id: Optional[str] = None
    stock_snapshot_id: Optional[str] = None
    offer_id: Optional[str] = None
    is_sponsored: bool = False
    sponsor_weight: float = 0.0
    brand_name: Optional[str] = None
    category_name: Optional[str] = None


@dataclass(frozen=True)
class ProductSearchRequest:
    utterance: str
    merchant_text: Optional[str] = None
    institution_texts: tuple[str, ...] = ()
    ranking_mode: RankingMode = RankingMode.BEST_OVERALL_VALUE
    max_price: Optional[float] = None
    requested_term: Optional[int] = None
    phase: str = "FIRST_CARDS"  # SEARCHING | FIRST_CARDS | FINANCE_ENRICHED
    cache_version: str = "catalog-v0"
    locale: str = "tr-TR"
    sponsored_product_ids: tuple[str, ...] = ()
    sponsored_weights: Mapping[str, float] = field(default_factory=dict)
    # Optional: merchant_ids whose price results are disabled by circuit breaker
    price_disabled_merchant_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ProductSearchResponse:
    searching: ProgressiveResponse
    result_phase: ProgressiveResponse
    merchant_resolution: Optional[ResolutionResult]
    institution_resolutions: tuple[ResolutionResult, ...]
    refresh_jobs: tuple[SchedulerJobSpec, ...]
    clarifications: tuple[str, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


async def _resolve_cached(
    *,
    entity_type: str,
    text: str,
    catalog: Sequence[EntityCandidate],
    cache: AliasResolutionCache,
    cache_version: str,
    locale: str,
    policy: ResolutionPolicy,
    ttl_seconds: int,
) -> ResolutionResult:
    key = resolution_cache_key(
        entity_type=entity_type,
        query_text=text,
        cache_version=cache_version,
        locale=locale,
    )
    cached = await cache.get(key)
    if cached is not None:
        # Re-run resolve for typed object; cache is a hit marker / future hydrate.
        # Keep correctness: always resolve from catalog (source of truth).
        pass
    result = resolve_entity(text, catalog, policy=policy)
    await cache.put(key, resolution_to_cache_dict(result), ttl_seconds=ttl_seconds)
    return result


def _filter_products(
    products: Sequence[SearchProductCandidate],
    *,
    merchant_id: Optional[str],
    max_price: Optional[float],
    price_disabled_merchant_ids: frozenset[str] = frozenset(),
) -> list[SearchProductCandidate]:
    out: list[SearchProductCandidate] = []
    for p in products:
        if merchant_id and p.merchant_id != merchant_id:
            continue
        # Circuit breaker: merchant price results disabled → exclude from finance/price path
        if p.merchant_id in price_disabled_merchant_ids:
            continue
        if max_price is not None and p.price > max_price:
            continue
        out.append(p)
    return out


async def search_products(
    request: ProductSearchRequest,
    *,
    products: Sequence[SearchProductCandidate],
    merchant_catalog: Sequence[EntityCandidate] = (),
    institution_catalog: Sequence[EntityCandidate] = (),
    resolution_policy: Optional[ResolutionPolicy] = None,
    ranking_weights: Optional[RankingWeights] = None,
    cache: Optional[AliasResolutionCache] = None,
    alias_ttl_seconds: int = 300,
    price_ttl_seconds: int = 3600,
) -> ProductSearchResponse:
    """Resolve entities → filter/rank → progressive cards + refresh jobs."""

    pol = resolution_policy or ResolutionPolicy()
    cache_impl: AliasResolutionCache = cache or NoOpAliasResolutionCache()
    clarifications: list[str] = []

    merchant_res: Optional[ResolutionResult] = None
    merchant_id: Optional[str] = None
    if request.merchant_text:
        merchant_res = await _resolve_cached(
            entity_type="merchant",
            text=request.merchant_text,
            catalog=merchant_catalog,
            cache=cache_impl,
            cache_version=request.cache_version,
            locale=request.locale,
            policy=pol,
            ttl_seconds=alias_ttl_seconds,
        )
        if merchant_res.action is ResolutionAction.AUTO_SELECT:
            merchant_id = merchant_res.resolved_entity_id
        elif merchant_res.action is ResolutionAction.CLARIFY and merchant_res.candidates:
            top = merchant_res.candidates[0]
            clarifications.append(f"“{top.display_name}”ı mı kastettiniz?")

    inst_results: list[ResolutionResult] = []
    for text in request.institution_texts:
        inst_results.append(
            await _resolve_cached(
                entity_type="institution",
                text=text,
                catalog=institution_catalog,
                cache=cache_impl,
                cache_version=request.cache_version,
                locale=request.locale,
                policy=pol,
                ttl_seconds=alias_ttl_seconds,
            )
        )
        last = inst_results[-1]
        if last.action is ResolutionAction.CLARIFY and last.candidates:
            clarifications.append(f"“{last.candidates[0].display_name}”ı mı kastettiniz?")

    filtered = _filter_products(
        products,
        merchant_id=merchant_id,
        max_price=request.max_price,
        price_disabled_merchant_ids=request.price_disabled_merchant_ids,
    )

    refresh_jobs: list[SchedulerJobSpec] = []
    rankables: list[RankableProduct] = []
    for p in filtered:
        if p.last_price_verified_at is not None:
            verdict = classify_freshness(
                last_verified_at=p.last_price_verified_at,
                ttl_seconds=price_ttl_seconds,
                user_search_driven=True,
                queue_on_stale=SchedulerQueue.PRICE_REFRESH,
            )
        else:
            verdict = FreshnessVerdict(
                status=p.price_freshness,
                age_seconds=None,
                show_as_current_offer=p.price_freshness == "FRESH",
                enqueue_refresh=p.price_freshness != "FRESH",
                queue=SchedulerQueue.PRICE_REFRESH if p.price_freshness != "FRESH" else None,
                priority=10,
            )
        job = enqueue_search_driven_refresh(product_id=p.product_id, verdict=verdict)
        if job is not None:
            refresh_jobs.append(job)

        # Expired offers are not shown as current.
        if verdict.status == "EXPIRED" or (
            p.last_price_verified_at is None and p.price_freshness == "EXPIRED"
        ):
            continue

        rankables.append(
            RankableProduct(
                product_id=p.product_id,
                price=p.price,
                stock_status=p.stock_status,
                price_freshness=verdict.status if p.last_price_verified_at else p.price_freshness,
                has_primary_image=p.has_primary_image,
                query_relevance=p.query_relevance,
                attribute_coverage=p.attribute_coverage,
                budget_ok=p.budget_ok if request.max_price is None else p.price <= request.max_price,
                best_monthly_payment=p.best_monthly_payment,
                best_total_repayment=p.best_total_repayment,
                best_term_months=p.best_term_months or request.requested_term,
                finance_active=p.finance_active,
                rate_fresh=p.rate_fresh,
                campaign_active=p.campaign_active,
            )
        )

    sponsored_ids = tuple(
        dict.fromkeys(
            list(request.sponsored_product_ids)
            + [p.product_id for p in filtered if p.is_sponsored]
        )
    )
    sponsored_weights = dict(request.sponsored_weights)
    for p in filtered:
        if p.is_sponsored and p.product_id not in sponsored_weights:
            sponsored_weights[p.product_id] = float(p.sponsor_weight)

    ranked = rank_products_with_sponsored_isolation(
        rankables,
        mode=request.ranking_mode,
        weights=ranking_weights,
        sponsored_product_ids=sponsored_ids,
        sponsored_weights=sponsored_weights,
    )
    ranked_ids = {r.product_id for r in ranked if not r.disqualified}
    card_sources = tuple(
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
            best_finance=p.card_finance,
            price_snapshot_id=p.price_snapshot_id or (
                f"offer:{p.offer_id}" if p.offer_id else None
            ),
            stock_snapshot_id=p.stock_snapshot_id or (
                f"offer:{p.offer_id}:stock" if p.offer_id else None
            ),
        )
        for p in filtered
        if p.product_id in ranked_ids
    )

    searching = build_searching_phase()
    phase = (request.phase or "FIRST_CARDS").upper()
    if phase == "FINANCE_ENRICHED":
        result_phase = build_finance_enriched_phase(card_sources, ranked)
    else:
        result_phase = build_first_cards_phase(card_sources, ranked)

    return ProductSearchResponse(
        searching=searching,
        result_phase=result_phase,
        merchant_resolution=merchant_res,
        institution_resolutions=tuple(inst_results),
        refresh_jobs=tuple(refresh_jobs),
        clarifications=tuple(clarifications),
        diagnostics={
            "utterance": request.utterance,
            "candidate_count": len(products),
            "filtered_count": len(filtered),
            "ranked_count": len(ranked_ids),
            "refresh_job_count": len(refresh_jobs),
            "cards": [card_to_public_dict(c) for c in result_phase.cards],
        },
    )


__all__ = [
    "ProductSearchRequest",
    "ProductSearchResponse",
    "SearchProductCandidate",
    "search_products",
]
