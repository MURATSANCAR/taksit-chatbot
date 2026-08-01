"""Champion/challenger ranking adaptation with deterministic safety floor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from taksitlio.product_query.ranking import (
    RankableProduct,
    RankingWeights,
    RankedProduct,
    rank_products,
    safety_disqualify,
)


@dataclass(frozen=True)
class RankingPolicyVersion:
    policy_code: str
    version: int
    role: str  # CHAMPION | CHALLENGER
    weights: RankingWeights
    status: str = "ACTIVE"
    traffic_pct: float = 0.0

    @classmethod
    def from_weight_map(
        cls,
        *,
        policy_code: str,
        version: int,
        role: str,
        weights: Mapping[str, float],
        status: str = "ACTIVE",
        traffic_pct: float = 0.0,
    ) -> "RankingPolicyVersion":
        return cls(
            policy_code=policy_code,
            version=version,
            role=role,
            status=status,
            traffic_pct=traffic_pct,
            weights=RankingWeights(
                query_relevance=float(weights.get("query_relevance", 0.25)),
                attribute_coverage=float(weights.get("attribute_coverage", 0.15)),
                budget_compatibility=float(weights.get("budget_compatibility", 0.15)),
                stock=float(weights.get("stock", 0.10)),
                price=float(weights.get("price", 0.10)),
                finance=float(weights.get("finance", 0.10)),
                total_repayment=float(weights.get("total_repayment", 0.10)),
                freshness=float(weights.get("freshness", 0.05)),
            ),
        )


@dataclass(frozen=True)
class RankingGateResult:
    promote: bool
    reasons: tuple[str, ...]
    quality_regression: bool
    wrong_finance_result: bool
    negative_constraint_leakage: bool
    latency_ok: bool


@dataclass(frozen=True)
class RankingFeedbackEvent:
    event_type: str
    polarity: str
    product_id: str
    position: Optional[int]
    ranking_policy_version: str
    query_version: str


def assert_safety_floor_preserved(
    *,
    eligible_before: Sequence[RankableProduct],
    ranked_after: Sequence[RankedProduct],
) -> tuple[bool, tuple[str, ...]]:
    """Adaptive ranking may only reorder eligible candidates — never resurrect."""

    eligible_ids = {
        i.product_id
        for i in eligible_before
        if not safety_disqualify(i, require_finance=True, require_image=True)
    }
    reasons: list[str] = []
    for r in ranked_after:
        if r.disqualified:
            continue
        if r.product_id not in eligible_ids:
            reasons.append(f"resurrected:{r.product_id}")
    return (not reasons), tuple(reasons)


def evaluate_promotion_gate(
    *,
    quality_regression: bool,
    wrong_finance_result: bool,
    negative_constraint_leakage: bool,
    latency_p95_ms: float,
    latency_target_ms: float = 50.0,
    min_feedback_samples: int = 100,
    feedback_sample_count: int = 0,
) -> RankingGateResult:
    reasons: list[str] = []
    if feedback_sample_count < min_feedback_samples:
        reasons.append("insufficient_feedback_samples")
    if quality_regression:
        reasons.append("quality_regression")
    if wrong_finance_result:
        reasons.append("wrong_finance_result")
    if negative_constraint_leakage:
        reasons.append("negative_constraint_leakage")
    latency_ok = latency_p95_ms < latency_target_ms
    if not latency_ok:
        reasons.append("latency_target_missed")
    return RankingGateResult(
        promote=not reasons,
        reasons=tuple(reasons),
        quality_regression=quality_regression,
        wrong_finance_result=wrong_finance_result,
        negative_constraint_leakage=negative_constraint_leakage,
        latency_ok=latency_ok,
    )


def shadow_compare(
    items: Sequence[RankableProduct],
    champion: RankingPolicyVersion,
    challenger: RankingPolicyVersion,
) -> dict[str, object]:
    """Run both policies; challenger never affects user-facing order here."""

    champ = rank_products(items, weights=champion.weights)
    chall = rank_products(items, weights=challenger.weights)
    floor_ok, floor_reasons = assert_safety_floor_preserved(
        eligible_before=items, ranked_after=chall
    )
    return {
        "champion_version": champion.version,
        "challenger_version": challenger.version,
        "champion_top": [r.product_id for r in champ if not r.disqualified][:10],
        "challenger_top": [r.product_id for r in chall if not r.disqualified][:10],
        "safety_floor_ok": floor_ok,
        "safety_floor_reasons": list(floor_reasons),
        "mode": "SHADOW",
    }


__all__ = [
    "RankingFeedbackEvent",
    "RankingGateResult",
    "RankingPolicyVersion",
    "assert_safety_floor_preserved",
    "evaluate_promotion_gate",
    "shadow_compare",
]
