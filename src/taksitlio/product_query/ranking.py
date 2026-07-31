"""Deterministic product ranking modes (ADR-010 §55–56).

No merchant/bank/category name hardcoding — weights come from policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence


class RankingMode(str, Enum):
    CHEAPEST_PRODUCT_PRICE = "CHEAPEST_PRODUCT_PRICE"
    LOWEST_MONTHLY_PAYMENT = "LOWEST_MONTHLY_PAYMENT"
    LOWEST_TOTAL_REPAYMENT = "LOWEST_TOTAL_REPAYMENT"
    LONGEST_TERM = "LONGEST_TERM"
    BEST_ATTRIBUTE_MATCH = "BEST_ATTRIBUTE_MATCH"
    BEST_OVERALL_VALUE = "BEST_OVERALL_VALUE"


@dataclass(frozen=True)
class RankingWeights:
    query_relevance: float = 0.25
    attribute_coverage: float = 0.15
    budget_compatibility: float = 0.15
    stock: float = 0.10
    price: float = 0.10
    finance: float = 0.10
    total_repayment: float = 0.10
    freshness: float = 0.05


@dataclass(frozen=True)
class RankableProduct:
    product_id: str
    price: float
    stock_status: str
    price_freshness: str
    has_primary_image: bool
    query_relevance: float = 0.0
    attribute_coverage: float = 0.0
    budget_ok: bool = True
    best_monthly_payment: Optional[float] = None
    best_total_repayment: Optional[float] = None
    best_term_months: Optional[int] = None
    finance_active: bool = False
    rate_fresh: bool = False
    campaign_active: bool = True
    metadata: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedProduct:
    product_id: str
    score: float
    label: str
    disqualified: bool
    disqualify_reasons: tuple[str, ...]


def safety_disqualify(
    item: RankableProduct,
    *,
    require_finance: bool = True,
    require_image: bool = True,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if item.stock_status != "AVAILABLE":
        reasons.append("stock_not_available")
    if item.price_freshness != "FRESH":
        reasons.append("stale_or_unverified_price")
    if require_image and not item.has_primary_image:
        reasons.append("primary_image_unavailable")
    if require_finance:
        if not item.finance_active:
            reasons.append("finance_inactive")
        if not item.rate_fresh:
            reasons.append("rate_not_fresh")
        if not item.campaign_active:
            reasons.append("campaign_inactive")
    return tuple(reasons)


def rank_products(
    items: Sequence[RankableProduct],
    *,
    mode: RankingMode = RankingMode.BEST_OVERALL_VALUE,
    weights: Optional[RankingWeights] = None,
    min_comparison_count_for_best_label: int = 3,
) -> tuple[RankedProduct, ...]:
    w = weights or RankingWeights()
    scored: list[RankedProduct] = []
    require_finance = mode in {
        RankingMode.LOWEST_MONTHLY_PAYMENT,
        RankingMode.LOWEST_TOTAL_REPAYMENT,
        RankingMode.BEST_OVERALL_VALUE,
        RankingMode.LONGEST_TERM,
    }
    # Catalog browse (cheapest / attribute) may surface IMAGE_UNAVAILABLE cards.
    require_image = require_finance

    eligible_for_best = [
        i
        for i in items
        if not safety_disqualify(
            i, require_finance=require_finance, require_image=require_image
        )
        and i.best_monthly_payment is not None
    ]

    for item in items:
        reasons = safety_disqualify(
            item, require_finance=require_finance, require_image=require_image
        )
        if mode in {
            RankingMode.LOWEST_MONTHLY_PAYMENT,
            RankingMode.LOWEST_TOTAL_REPAYMENT,
            RankingMode.BEST_OVERALL_VALUE,
            RankingMode.LONGEST_TERM,
        } and reasons:
            scored.append(
                RankedProduct(
                    product_id=item.product_id,
                    score=float("-inf"),
                    label="excluded",
                    disqualified=True,
                    disqualify_reasons=reasons,
                )
            )
            continue

        if mode is RankingMode.CHEAPEST_PRODUCT_PRICE:
            # Still block unknown stock / stale / missing image from topping.
            if reasons:
                score = float("-inf")
                label = "excluded"
                scored.append(
                    RankedProduct(item.product_id, score, label, True, reasons)
                )
                continue
            score = -item.price
            label = "En düşük ürün fiyatı"
        elif mode is RankingMode.LOWEST_MONTHLY_PAYMENT:
            pay = item.best_monthly_payment
            score = float("-inf") if pay is None else -pay
            label = "En düşük aylık ödeme"
        elif mode is RankingMode.LOWEST_TOTAL_REPAYMENT:
            tot = item.best_total_repayment
            score = float("-inf") if tot is None else -tot
            label = "En düşük toplam geri ödeme"
        elif mode is RankingMode.LONGEST_TERM:
            term = item.best_term_months
            score = float("-inf") if term is None else float(term)
            label = "En uzun vade"
        elif mode is RankingMode.BEST_ATTRIBUTE_MATCH:
            if reasons:
                scored.append(
                    RankedProduct(
                        item.product_id, float("-inf"), "excluded", True, reasons
                    )
                )
                continue
            score = item.attribute_coverage
            label = "Kriterlerinize en yakın seçenek"
        else:
            # BEST_OVERALL_VALUE
            price_score = 1.0 / (1.0 + max(item.price, 0.0) / 10000.0)
            monthly_score = (
                0.0
                if item.best_monthly_payment is None
                else 1.0 / (1.0 + item.best_monthly_payment / 1000.0)
            )
            repay_score = (
                0.0
                if item.best_total_repayment is None
                else 1.0 / (1.0 + item.best_total_repayment / 10000.0)
            )
            score = (
                w.query_relevance * item.query_relevance
                + w.attribute_coverage * item.attribute_coverage
                + w.budget_compatibility * (1.0 if item.budget_ok else 0.0)
                + w.stock * (1.0 if item.stock_status == "AVAILABLE" else 0.0)
                + w.price * price_score
                + w.finance * monthly_score
                + w.total_repayment * repay_score
                + w.freshness * (1.0 if item.price_freshness == "FRESH" else 0.0)
            )
            # ADR-012 §13: "En uygun" only when comparison bar + safety clear.
            if len(eligible_for_best) >= min_comparison_count_for_best_label:
                label = "En uygun"
            else:
                label = "Kriterlerinize en yakın seçenek"

        scored.append(
            RankedProduct(
                product_id=item.product_id,
                score=score,
                label=label,
                disqualified=False,
                disqualify_reasons=(),
            )
        )

    scored.sort(key=lambda r: r.score, reverse=True)
    return tuple(scored)


__all__ = [
    "RankableProduct",
    "RankedProduct",
    "RankingMode",
    "RankingWeights",
    "rank_products",
    "safety_disqualify",
]
