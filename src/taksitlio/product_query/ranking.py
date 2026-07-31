"""Deterministic product ranking modes (ADR-010 §55–56).

No merchant/bank/category name hardcoding — weights come from policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


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
    sponsored: Optional[Sequence[Any]] = None,
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

    if sponsored:
        from taksitlio.recommendation_safety.feedback import (
            SponsoredPlacement,
            apply_sponsored_isolation,
        )

        placements: list[SponsoredPlacement] = []
        for sp in sponsored:
            if isinstance(sp, SponsoredPlacement):
                placements.append(sp)
            elif isinstance(sp, Mapping):
                placements.append(
                    SponsoredPlacement(
                        product_id=str(sp.get("product_id") or ""),
                        weight=float(sp.get("weight") or 0.0),
                    )
                )
            else:
                continue
        placements = [p for p in placements if p.product_id]
        if placements:
            by_id = {r.product_id: r for r in scored}
            organic_ids = [r.product_id for r in scored if not r.disqualified]
            best_ids = {r.product_id for r in scored if r.label == "En uygun"}
            stale_ids = {
                i.product_id
                for i in items
                if i.price_freshness != "FRESH" or i.stock_status != "AVAILABLE"
            }
            isolated = apply_sponsored_isolation(
                organic_ids,
                placements,
                eligible_ids=set(organic_ids),
                stale_ids=stale_ids,
                best_label_ids=best_ids,
            )
            # Keep disqualified at the end; reorder active results by isolation.
            active = [by_id[pid] for pid in isolated if pid in by_id]
            seen = set(isolated)
            rest = [r for r in scored if r.product_id not in seen]
            scored = active + rest

    return tuple(scored)


def rank_products_with_sponsored_isolation(
    items: Sequence[RankableProduct],
    *,
    mode: RankingMode = RankingMode.BEST_OVERALL_VALUE,
    weights: Optional[RankingWeights] = None,
    min_comparison_count_for_best_label: int = 3,
    sponsored_product_ids: Sequence[str] = (),
    sponsored_weights: Optional[Mapping[str, float]] = None,
) -> tuple[RankedProduct, ...]:
    """Rank then apply ADR-012 sponsored isolation (never steals 'en uygun')."""

    ranked = rank_products(
        items,
        mode=mode,
        weights=weights,
        min_comparison_count_for_best_label=min_comparison_count_for_best_label,
    )
    if not sponsored_product_ids:
        return ranked

    from taksitlio.recommendation_safety.feedback import (
        SponsoredPlacement,
        apply_sponsored_isolation,
    )

    organic_ids = [r.product_id for r in ranked if not r.disqualified]
    best_ids = {r.product_id for r in ranked if r.label in {"En uygun", "En uygun değer", "En uygun ürün"}}
    stale_ids = {
        i.product_id for i in items if i.price_freshness != "FRESH"
    }
    eligible = {
        i.product_id
        for i in items
        if not safety_disqualify(i, require_finance=False, require_image=False)
    }
    weights_map = sponsored_weights or {}
    sponsored = [
        SponsoredPlacement(pid, float(weights_map.get(pid, 0.0)))
        for pid in sponsored_product_ids
    ]
    order = apply_sponsored_isolation(
        organic_ids,
        sponsored,
        eligible_ids=eligible,
        stale_ids=stale_ids,
        best_label_ids=best_ids,
    )
    by_id = {r.product_id: r for r in ranked}
    reordered: list[RankedProduct] = []
    for pid in order:
        item = by_id.get(pid)
        if item is None:
            continue
        if pid in sponsored_product_ids and item.label in {
            "En uygun",
            "En uygun değer",
            "En uygun ürün",
        }:
            reordered.append(
                RankedProduct(
                    product_id=item.product_id,
                    score=item.score,
                    label="Sponsorlu seçenek",
                    disqualified=item.disqualified,
                    disqualify_reasons=item.disqualify_reasons,
                )
            )
        else:
            reordered.append(item)
    # Append any disqualified that were dropped from organic order
    seen = {r.product_id for r in reordered}
    for r in ranked:
        if r.product_id not in seen:
            reordered.append(r)
    return tuple(reordered)


__all__ = [
    "RankableProduct",
    "RankedProduct",
    "RankingMode",
    "RankingWeights",
    "rank_products",
    "rank_products_with_sponsored_isolation",
    "safety_disqualify",
]
