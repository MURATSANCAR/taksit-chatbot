"""Build search orchestrator product_pool from crawl/catalog (ADR-011 P2)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from taksitlio.entity_resolution import EntityCandidate
from taksitlio.media.logo_resolver import LogoResolver
from taksitlio.merchant.directory import MerchantDirectory
from taksitlio.product.catalog import ProductCatalogRepository
from taksitlio.product_query.candidates import load_search_candidates_from_catalog
from taksitlio.product_query.finance_index import FinanceOptionIndex, InstitutionLabelResolver
from taksitlio.query_understanding import CatalogHints
from taksitlio.search_sessions.orchestrator import SearchOrchestrator


def candidate_to_pool_dict(cand: Any) -> dict[str, Any]:
    finance = None
    if getattr(cand, "card_finance", None) is not None:
        f = cand.card_finance
        finance = {
            "institution_display_name": f.institution_display_name,
            "term_months": f.term_months,
            "monthly_payment": f.monthly_payment,
            "total_repayment": f.total_repayment,
            "display_label": f.display_label,
            "institution_logo_cdn_url": getattr(f, "institution_logo_cdn_url", None),
        }
    return {
        "product_id": cand.product_id,
        "display_name": cand.display_name,
        "brand_model": cand.brand_model,
        "merchant_id": cand.merchant_id,
        "merchant_display_name": cand.merchant_display_name,
        "price": cand.price,
        "stock_status": cand.stock_status,
        "price_freshness": cand.price_freshness,
        "has_primary_image": cand.has_primary_image,
        "thumbnail_cdn_url": cand.thumbnail_cdn_url,
        "merchant_logo_cdn_url": cand.merchant_logo_cdn_url,
        "query_relevance": cand.query_relevance,
        "attribute_coverage": cand.attribute_coverage,
        "finance_active": cand.finance_active,
        "rate_fresh": cand.rate_fresh,
        "best_monthly_payment": cand.best_monthly_payment,
        "best_total_repayment": cand.best_total_repayment,
        "best_finance": finance,
    }


async def refresh_orchestrator_from_catalog(
    orch: SearchOrchestrator,
    *,
    catalog: ProductCatalogRepository,
    merchants: Optional[MerchantDirectory] = None,
    finance_index: Optional[FinanceOptionIndex] = None,
    institutions: Optional[InstitutionLabelResolver] = None,
    logos: Optional[LogoResolver] = None,
    utterance: str = "",
    limit: int = 80,
) -> int:
    """Replace demo pool with crawled/catalog products when any exist."""

    cands = await load_search_candidates_from_catalog(
        catalog,
        utterance=utterance,
        limit=limit,
        merchants=merchants,
        finance_index=finance_index,
        institutions=institutions,
    )
    if not cands:
        # Keep existing demo/synthetic pool for empty catalogs (tests/dev).
        if logos is not None:
            orch.logo_resolver = logos  # type: ignore[attr-defined]
        return 0

    pool = [candidate_to_pool_dict(c) for c in cands]
    # Attach merchant logos from resolver if candidate missing
    if logos is not None:
        for row in pool:
            if not row.get("merchant_logo_cdn_url"):
                row["merchant_logo_cdn_url"] = logos.merchant(row.get("merchant_id"))
            bf = row.get("best_finance") or {}
            # institution id may be absent on summary; leave as-is

    orch.product_pool = pool
    orch.logo_resolver = logos  # type: ignore[attr-defined]

    # Dynamic catalog hints from merchant directory (no hardcoded names)
    merchant_cands: list[EntityCandidate] = []
    if merchants is not None:
        for m in await merchants.list_active(limit=200):
            merchant_cands.append(
                EntityCandidate(
                    entity_id=str(m.id),
                    display_name=m.display_name,
                    canonical_name=m.display_name,
                    aliases=(m.merchant_code,),
                    entity_type="merchant",
                )
            )
    if merchant_cands:
        orch.catalog = CatalogHints(
            merchants=tuple(merchant_cands),
            categories=orch.catalog.categories,
            brands=orch.catalog.brands,
            institutions=orch.catalog.institutions,
        )
    return len(pool)
