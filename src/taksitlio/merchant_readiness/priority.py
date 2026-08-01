"""Policy-driven merchant activation priority (no merchant-name hardcoding)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class MerchantPriorityWeights:
    searchable_product_potential: float = 0.20
    category_coverage: float = 0.20
    media_coverage: float = 0.15
    price_freshness: float = 0.10
    finance_coverage: float = 0.10
    payment_plan_coverage: float = 0.05
    user_query_demand: float = 0.10
    unresolved_product_penalty: float = 0.05
    drift_risk_penalty: float = 0.03
    critical_error_penalty: float = 0.02

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "MerchantPriorityWeights":
        return cls(
            searchable_product_potential=float(
                data.get("searchable_product_potential", 0.20) or 0.20
            ),
            category_coverage=float(data.get("category_coverage", 0.20) or 0.20),
            media_coverage=float(data.get("media_coverage", 0.15) or 0.15),
            price_freshness=float(data.get("price_freshness", 0.10) or 0.10),
            finance_coverage=float(data.get("finance_coverage", 0.10) or 0.10),
            payment_plan_coverage=float(
                data.get("payment_plan_coverage", 0.05) or 0.05
            ),
            user_query_demand=float(data.get("user_query_demand", 0.10) or 0.10),
            unresolved_product_penalty=float(
                data.get("unresolved_product_penalty", 0.05) or 0.05
            ),
            drift_risk_penalty=float(data.get("drift_risk_penalty", 0.03) or 0.03),
            critical_error_penalty=float(
                data.get("critical_error_penalty", 0.02) or 0.02
            ),
        )


@dataclass(frozen=True)
class MerchantPrioritySignals:
    merchant_id: int
    active_products: int
    category_coverage: float
    media_coverage: float
    price_freshness: float
    finance_coverage: float
    payment_plan_coverage: float
    user_query_demand: float = 0.0
    unresolved_product_count: int = 0
    drift_risk: float = 0.0
    critical_error_count: int = 0
    # Optional display for reports only — never used in branching
    merchant_code: str = ""


@dataclass(frozen=True)
class MerchantPriorityScore:
    merchant_id: int
    score: float
    components: Mapping[str, float]
    merchant_code: str = ""


def score_merchant(
    signals: MerchantPrioritySignals,
    weights: MerchantPriorityWeights,
    *,
    max_products_norm: int,
) -> MerchantPriorityScore:
    pot = min(1.0, signals.active_products / max(max_products_norm, 1))
    unresolved_ratio = signals.unresolved_product_count / max(signals.active_products, 1)
    components = {
        "searchable_product_potential": weights.searchable_product_potential * pot,
        "category_coverage": weights.category_coverage * signals.category_coverage,
        "media_coverage": weights.media_coverage * signals.media_coverage,
        "price_freshness": weights.price_freshness * signals.price_freshness,
        "finance_coverage": weights.finance_coverage * signals.finance_coverage,
        "payment_plan_coverage": weights.payment_plan_coverage
        * signals.payment_plan_coverage,
        "user_query_demand": weights.user_query_demand * signals.user_query_demand,
        "unresolved_product_penalty": -weights.unresolved_product_penalty
        * unresolved_ratio,
        "drift_risk_penalty": -weights.drift_risk_penalty * signals.drift_risk,
        "critical_error_penalty": -weights.critical_error_penalty
        * min(1.0, signals.critical_error_count / 10.0),
    }
    return MerchantPriorityScore(
        merchant_id=signals.merchant_id,
        score=round(sum(components.values()), 6),
        components=components,
        merchant_code=signals.merchant_code,
    )


def top_priority_merchants(
    signals: Sequence[MerchantPrioritySignals],
    weights: MerchantPriorityWeights,
    *,
    limit: int = 5,
) -> tuple[MerchantPriorityScore, ...]:
    max_n = max((s.active_products for s in signals), default=1)
    scored = [score_merchant(s, weights, max_products_norm=max_n) for s in signals]
    scored.sort(key=lambda s: s.score, reverse=True)
    return tuple(scored[:limit])


__all__ = [
    "MerchantPriorityScore",
    "MerchantPrioritySignals",
    "MerchantPriorityWeights",
    "score_merchant",
    "top_priority_merchants",
]
