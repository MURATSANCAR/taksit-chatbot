"""Product card progressive response API (ADR-010 P5).

Accepts already-resolved card sources — does not crawl merchants synchronously.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

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
