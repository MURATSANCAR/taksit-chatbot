"""Progressive chatbot product cards (ADR-010 §53–54).

First payload uses thumbnail CDN URLs — never merchant hotlinks.
Finance details may arrive in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from taksitlio.media.primary import IMAGE_UNAVAILABLE
from taksitlio.media.variants import chatbot_default_variant_code
from taksitlio.product_query.ranking import RankedProduct


class ResponsePhase(str, Enum):
    SEARCHING = "SEARCHING"  # 0–100 ms messaging
    FIRST_CARDS = "FIRST_CARDS"  # 100–300 ms
    FINANCE_ENRICHED = "FINANCE_ENRICHED"  # 300–600 ms
    DETAILS_LAZY = "DETAILS_LAZY"  # 600 ms+


@dataclass(frozen=True)
class ProductCardImage:
    status: str  # READY | IMAGE_UNAVAILABLE
    thumbnail_cdn_url: Optional[str]
    variant_code: Optional[str] = None


@dataclass(frozen=True)
class ProductCardFinanceSummary:
    institution_display_name: str
    term_months: int
    monthly_payment: float
    total_repayment: float
    display_label: str
    campaign_ends_at: Optional[str] = None
    fees_total: float = 0.0
    institution_logo_cdn_url: Optional[str] = None
    payment_calculation_id: Optional[str] = None
    rate_snapshot_id: Optional[str] = None
    campaign_version_id: Optional[str] = None
    merchant_finance_agreement_id: Optional[str] = None
    rate_type: Optional[str] = None


@dataclass(frozen=True)
class ProductCard:
    product_id: str
    display_name: str
    brand_model: Optional[str]
    merchant_display_name: str
    merchant_logo_cdn_url: Optional[str]
    price: float
    list_price: Optional[float]
    currency: str
    stock_status: str
    image: ProductCardImage
    ranking_label: Optional[str]
    best_finance: Optional[ProductCardFinanceSummary]
    price_checked_at: Optional[str]
    campaign_checked_at: Optional[str]
    product_url: Optional[str]
    tags: tuple[str, ...] = ()
    price_snapshot_id: Optional[str] = None
    stock_snapshot_id: Optional[str] = None


@dataclass(frozen=True)
class ProgressiveResponse:
    phase: ResponsePhase
    message: Optional[str]
    cards: tuple[ProductCard, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CardSourceProduct:
    product_id: str
    display_name: str
    brand_model: Optional[str]
    merchant_display_name: str
    price: float
    list_price: Optional[float] = None
    currency: str = "TRY"
    stock_status: str = "UNKNOWN"
    thumbnail_cdn_url: Optional[str] = None
    has_primary_image: bool = False
    merchant_logo_cdn_url: Optional[str] = None
    product_url: Optional[str] = None
    price_checked_at: Optional[str] = None
    campaign_checked_at: Optional[str] = None
    best_finance: Optional[ProductCardFinanceSummary] = None
    price_snapshot_id: Optional[str] = None
    stock_snapshot_id: Optional[str] = None


def build_searching_phase(message: str = "Ürünleri arıyorum") -> ProgressiveResponse:
    return ProgressiveResponse(phase=ResponsePhase.SEARCHING, message=message, cards=())


def build_product_card(
    source: CardSourceProduct,
    *,
    ranking: Optional[RankedProduct] = None,
    include_finance: bool = False,
) -> ProductCard:
    if source.has_primary_image and source.thumbnail_cdn_url:
        image = ProductCardImage(
            status="READY",
            thumbnail_cdn_url=source.thumbnail_cdn_url,
            variant_code=chatbot_default_variant_code(prefer_width=640),
        )
    else:
        image = ProductCardImage(
            status=IMAGE_UNAVAILABLE,
            thumbnail_cdn_url=None,
            variant_code=None,
        )

    tags: list[str] = []
    if source.stock_status == "AVAILABLE":
        tags.append("Stokta")
    if ranking and ranking.label and not ranking.disqualified:
        tags.append(ranking.label)
    if include_finance and source.best_finance is not None:
        tags.append("Kampanyalı" if source.best_finance.campaign_ends_at else "Finansman mevcut")

    return ProductCard(
        product_id=source.product_id,
        display_name=source.display_name,
        brand_model=source.brand_model,
        merchant_display_name=source.merchant_display_name,
        merchant_logo_cdn_url=source.merchant_logo_cdn_url,
        price=source.price,
        list_price=source.list_price,
        currency=source.currency,
        stock_status=source.stock_status,
        image=image,
        ranking_label=None if ranking is None or ranking.disqualified else ranking.label,
        best_finance=source.best_finance if include_finance else None,
        price_checked_at=source.price_checked_at,
        campaign_checked_at=source.campaign_checked_at if include_finance else None,
        product_url=source.product_url,
        tags=tuple(tags),
        price_snapshot_id=source.price_snapshot_id,
        stock_snapshot_id=source.stock_snapshot_id,
    )


def build_first_cards_phase(
    sources: Sequence[CardSourceProduct],
    ranked: Sequence[RankedProduct] = (),
    *,
    message: Optional[str] = None,
) -> ProgressiveResponse:
    by_id = {r.product_id: r for r in ranked}
    cards = tuple(
        build_product_card(s, ranking=by_id.get(s.product_id), include_finance=False)
        for s in sources
        if not (by_id.get(s.product_id) and by_id[s.product_id].disqualified)
    )
    return ProgressiveResponse(
        phase=ResponsePhase.FIRST_CARDS,
        message=message,
        cards=cards,
        diagnostics={"card_count": len(cards), "finance_included": False},
    )


def build_finance_enriched_phase(
    sources: Sequence[CardSourceProduct],
    ranked: Sequence[RankedProduct] = (),
    *,
    message: Optional[str] = None,
) -> ProgressiveResponse:
    by_id = {r.product_id: r for r in ranked}
    cards = tuple(
        build_product_card(s, ranking=by_id.get(s.product_id), include_finance=True)
        for s in sources
        if not (by_id.get(s.product_id) and by_id[s.product_id].disqualified)
    )
    return ProgressiveResponse(
        phase=ResponsePhase.FINANCE_ENRICHED,
        message=message,
        cards=cards,
        diagnostics={"card_count": len(cards), "finance_included": True},
    )


def card_to_public_dict(card: ProductCard) -> dict[str, Any]:
    """Serialize for API — never expose raw merchant image source URLs."""

    return {
        "product_id": card.product_id,
        "display_name": card.display_name,
        "brand_model": card.brand_model,
        "merchant": {
            "display_name": card.merchant_display_name,
            "logo_cdn_url": card.merchant_logo_cdn_url,
        },
        "price": card.price,
        "list_price": card.list_price,
        "currency": card.currency,
        "stock_status": card.stock_status,
        "image": {
            "status": card.image.status,
            "thumbnail_cdn_url": card.image.thumbnail_cdn_url,
            "variant_code": card.image.variant_code,
        },
        "ranking_label": card.ranking_label,
        "best_finance": None
        if card.best_finance is None
        else {
            "institution_display_name": card.best_finance.institution_display_name,
            "institution_logo_cdn_url": card.best_finance.institution_logo_cdn_url,
            "term_months": card.best_finance.term_months,
            "monthly_payment": card.best_finance.monthly_payment,
            "total_repayment": card.best_finance.total_repayment,
            "fees_total": card.best_finance.fees_total,
            "display_label": card.best_finance.display_label,
            "campaign_ends_at": card.best_finance.campaign_ends_at,
            "payment_calculation_id": card.best_finance.payment_calculation_id,
            "rate_snapshot_id": card.best_finance.rate_snapshot_id,
            "campaign_version_id": card.best_finance.campaign_version_id,
            "merchant_finance_agreement_id": card.best_finance.merchant_finance_agreement_id,
            "rate_type": card.best_finance.rate_type,
        },
        "price_checked_at": card.price_checked_at,
        "campaign_checked_at": card.campaign_checked_at,
        "product_url": card.product_url,
        "tags": list(card.tags),
        "price_snapshot_id": card.price_snapshot_id,
        "stock_snapshot_id": card.stock_snapshot_id,
    }


__all__ = [
    "CardSourceProduct",
    "ProductCard",
    "ProductCardFinanceSummary",
    "ProductCardImage",
    "ProgressiveResponse",
    "ResponsePhase",
    "build_finance_enriched_phase",
    "build_first_cards_phase",
    "build_product_card",
    "build_searching_phase",
    "card_to_public_dict",
]
