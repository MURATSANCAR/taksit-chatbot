"""Project catalog products into search candidates (ADR-010 P10/P11).

Chatbot-visible only: skips QUARANTINED/REJECTED. CDN thumbnails only.
Merchant labels and finance come from catalog indexes — never hardcoded brands/banks.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from taksitlio.merchant.directory import (
    MerchantDirectory,
    resolve_merchant_display_name,
)
from taksitlio.product.catalog import ProductCatalogRepository, StoredOffer, StoredProduct
from taksitlio.product_query.finance_index import (
    FinanceOptionIndex,
    InstitutionLabelResolver,
    enrich_candidate_with_finance,
)
from taksitlio.product_query.search import SearchProductCandidate
from taksitlio.semantic_matching.turkish_normalize import normalize_turkish


def _token_overlap(utterance: str, display_name: str) -> float:
    q = set((normalize_turkish(utterance).value or "").split())
    n = set((normalize_turkish(display_name).value or "").split())
    if not q or not n:
        return 0.35
    return len(q & n) / len(q | n)


async def product_to_search_candidate(
    product: StoredProduct,
    offer: Optional[StoredOffer],
    *,
    utterance: str = "",
    merchants: Optional[MerchantDirectory] = None,
    finance_index: Optional[FinanceOptionIndex] = None,
    institutions: Optional[InstitutionLabelResolver] = None,
) -> Optional[SearchProductCandidate]:
    if product.status in {"QUARANTINED", "REJECTED", "DRAFT"}:
        return None
    if product.data_quality_status in {"QUARANTINED", "REJECTED"}:
        return None
    if offer is None:
        return None

    has_image = (
        product.primary_media_status == "READY"
        and bool(product.primary_cdn_url)
    )
    relevance = _token_overlap(utterance, product.display_name)
    brand_model = None
    brand_name = None
    category_name = None
    attrs = dict(product.attributes or {})
    brand_name = str(attrs["brand"]).strip() if attrs.get("brand") else None
    category_name = str(attrs["category"]).strip() if attrs.get("category") else None
    model = attrs.get("model") or product.model_number
    if brand_name or model:
        brand_model = " / ".join(str(x) for x in (brand_name, model) if x)

    merchant_name = await resolve_merchant_display_name(product.merchant_id, merchants)
    merchant_logo = None
    if merchants is not None:
        get_logo = getattr(merchants, "get_logo_cdn_url", None)
        if callable(get_logo):
            merchant_logo = await get_logo(product.merchant_id)
        else:
            entry = await merchants.get(product.merchant_id)
            merchant_logo = getattr(entry, "logo_cdn_url", None) if entry else None
    candidate = SearchProductCandidate(
        product_id=str(product.id),
        display_name=product.display_name,
        brand_model=brand_model,
        brand_name=brand_name,
        category_name=category_name,
        merchant_id=str(product.merchant_id),
        merchant_display_name=merchant_name,
        price=float(offer.current_price),
        list_price=None,
        currency=offer.currency or "TRY",
        stock_status=offer.stock_status,
        price_freshness=offer.freshness_status,
        has_primary_image=has_image,
        thumbnail_cdn_url=product.primary_cdn_url if has_image else None,
        merchant_logo_cdn_url=merchant_logo,
        query_relevance=max(0.2, float(relevance)),
        attribute_coverage=min(1.0, 0.4 + 0.1 * len(attrs)),
        finance_active=False,
        rate_fresh=False,
        campaign_active=True,
        offer_id=str(offer.id),
        price_snapshot_id=f"offer:{offer.id}",
        stock_snapshot_id=f"offer:{offer.id}:stock",
    )
    if finance_index is not None:
        rows = await finance_index.list_for_product(str(product.id))
        candidate = enrich_candidate_with_finance(
            candidate, rows, institutions=institutions
        )
    return candidate


async def load_search_candidates_from_catalog(
    catalog: ProductCatalogRepository,
    *,
    utterance: str,
    merchant_id: Optional[int] = None,
    limit: int = 50,
    merchants: Optional[MerchantDirectory] = None,
    finance_index: Optional[FinanceOptionIndex] = None,
    institutions: Optional[InstitutionLabelResolver] = None,
    category_candidates: Optional[Sequence[Any]] = None,
) -> tuple[SearchProductCandidate, ...]:
    from taksitlio.progressive_results.category_match import utterance_name_terms

    terms = utterance_name_terms(
        utterance, category_candidates=category_candidates or ()
    )
    if terms and hasattr(catalog, "list_products_matching"):
        products = await catalog.list_products_matching(
            name_terms=terms, merchant_id=merchant_id, limit=limit
        )
        # Fallback if category heuristic matched nothing.
        if not products:
            products = await catalog.list_products(merchant_id=merchant_id, limit=limit)
    else:
        products = await catalog.list_products(merchant_id=merchant_id, limit=limit)
    out: list[SearchProductCandidate] = []
    for product in products:
        offer = await catalog.get_offer(product.id)
        cand = await product_to_search_candidate(
            product,
            offer,
            utterance=utterance,
            merchants=merchants,
            finance_index=finance_index,
            institutions=institutions,
        )
        if cand is not None:
            out.append(cand)
    return tuple(out)


__all__ = [
    "load_search_candidates_from_catalog",
    "product_to_search_candidate",
]
