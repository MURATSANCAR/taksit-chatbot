"""Product data-quality scoring (ADR-010 §58–62).

QUARANTINED products must not be shown in chatbot cards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence


class DataQualityStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ProductQualitySignals:
    has_external_id: bool = False
    has_display_name: bool = False
    has_price: bool = False
    price_positive: bool = False
    has_currency: bool = False
    has_stock_status: bool = False
    stock_known: bool = False
    has_primary_image: bool = False
    image_cdn_ready: bool = False
    has_source_reference: bool = False
    price_fresh: bool = False
    parse_failed: bool = False
    schema_invalid: bool = False
    duplicate_suspected: bool = False
    forbidden_hotlink_image: bool = False


@dataclass(frozen=True)
class ProductQualityVerdict:
    status: DataQualityStatus
    score: float  # 0..1
    reasons: tuple[str, ...]
    chatbot_visible: bool
    diagnostics: Mapping[str, float] = field(default_factory=dict)


_STOCK_KNOWN = frozenset({"AVAILABLE", "LIMITED", "OUT_OF_STOCK"})


def score_product_quality(signals: ProductQualitySignals) -> ProductQualityVerdict:
    """Deterministic quality gate — no merchant-specific rules."""

    if signals.parse_failed or signals.schema_invalid:
        return ProductQualityVerdict(
            status=DataQualityStatus.REJECTED,
            score=0.0,
            reasons=(
                *(["parse_failed"] if signals.parse_failed else []),
                *(["schema_invalid"] if signals.schema_invalid else []),
            ),
            chatbot_visible=False,
        )

    if signals.forbidden_hotlink_image:
        return ProductQualityVerdict(
            status=DataQualityStatus.QUARANTINED,
            score=0.2,
            reasons=("forbidden_hotlink_image",),
            chatbot_visible=False,
        )

    hard_missing: list[str] = []
    if not signals.has_external_id:
        hard_missing.append("missing_external_id")
    if not signals.has_display_name:
        hard_missing.append("missing_display_name")
    if not signals.has_price or not signals.price_positive:
        hard_missing.append("missing_or_invalid_price")

    if hard_missing:
        return ProductQualityVerdict(
            status=DataQualityStatus.QUARANTINED,
            score=0.15,
            reasons=tuple(hard_missing),
            chatbot_visible=False,
        )

    soft: list[str] = []
    points = 0.0
    weights = {
        "identity": 0.25,
        "price": 0.25,
        "stock": 0.15,
        "image": 0.20,
        "provenance": 0.10,
        "freshness": 0.05,
    }

    points += weights["identity"]  # hard gates already passed
    if signals.has_currency:
        points += weights["price"]
    else:
        soft.append("missing_currency")
        points += weights["price"] * 0.5

    if signals.stock_known:
        points += weights["stock"]
    elif signals.has_stock_status:
        soft.append("stock_unknown")
        points += weights["stock"] * 0.4
    else:
        soft.append("missing_stock")

    if signals.has_primary_image and signals.image_cdn_ready:
        points += weights["image"]
    elif signals.has_primary_image:
        soft.append("image_not_cdn_ready")
        points += weights["image"] * 0.3
    else:
        soft.append("missing_primary_image")

    if signals.has_source_reference:
        points += weights["provenance"]
    else:
        soft.append("missing_source_reference")

    if signals.price_fresh:
        points += weights["freshness"]
    else:
        soft.append("price_not_fresh")

    if signals.duplicate_suspected:
        soft.append("duplicate_suspected")
        points *= 0.85

    score = max(0.0, min(1.0, points))

    if score >= 0.90 and not soft:
        status = DataQualityStatus.READY
    elif score >= 0.55 and "missing_primary_image" not in soft:
        # Image missing → still PARTIAL but chatbot may show IMAGE_UNAVAILABLE
        status = DataQualityStatus.PARTIAL
    elif score >= 0.55:
        status = DataQualityStatus.PARTIAL
    else:
        status = DataQualityStatus.QUARANTINED

    chatbot_visible = status in {DataQualityStatus.READY, DataQualityStatus.PARTIAL}
    return ProductQualityVerdict(
        status=status,
        score=round(score, 4),
        reasons=tuple(soft),
        chatbot_visible=chatbot_visible,
        diagnostics=dict(weights),
    )


def signals_from_normalized(
    *,
    external_product_id: Optional[str],
    display_name: Optional[str],
    price: Optional[float],
    currency: Optional[str],
    stock_status: Optional[str],
    has_primary_image: bool,
    image_cdn_ready: bool,
    source_reference: Optional[str],
    price_fresh: bool = False,
    parse_failed: bool = False,
    schema_invalid: bool = False,
    duplicate_suspected: bool = False,
    forbidden_hotlink_image: bool = False,
) -> ProductQualitySignals:
    stock = (stock_status or "").upper()
    return ProductQualitySignals(
        has_external_id=bool((external_product_id or "").strip()),
        has_display_name=bool((display_name or "").strip()),
        has_price=price is not None,
        price_positive=price is not None and float(price) > 0,
        has_currency=bool((currency or "").strip()),
        has_stock_status=bool(stock),
        stock_known=stock in _STOCK_KNOWN,
        has_primary_image=has_primary_image,
        image_cdn_ready=image_cdn_ready,
        has_source_reference=bool((source_reference or "").strip()),
        price_fresh=price_fresh,
        parse_failed=parse_failed,
        schema_invalid=schema_invalid,
        duplicate_suspected=duplicate_suspected,
        forbidden_hotlink_image=forbidden_hotlink_image,
    )


def filter_chatbot_visible(
    verdicts: Sequence[tuple[str, ProductQualityVerdict]],
) -> tuple[str, ...]:
    return tuple(pid for pid, v in verdicts if v.chatbot_visible)


__all__ = [
    "DataQualityStatus",
    "ProductQualitySignals",
    "ProductQualityVerdict",
    "filter_chatbot_visible",
    "score_product_quality",
    "signals_from_normalized",
]
