"""Policy-driven merchant selection for search-ready expansion (no hardcoded names)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class MerchantSelectionPolicy:
    weights: Mapping[str, float]
    minimums: Mapping[str, float]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "MerchantSelectionPolicy":
        data = dict(payload or {})
        return cls(
            weights=dict(data.get("weights") or {}),
            minimums=dict(data.get("minimums") or {}),
        )


def score_merchant_row(row: Mapping[str, Any], policy: MerchantSelectionPolicy) -> float:
    """Score a readiness snapshot row using versioned weights (0..1 coverages preferred)."""

    score = 0.0
    total_w = 0.0
    for key, weight in policy.weights.items():
        w = float(weight)
        total_w += w
        if key == "active_products_norm":
            active = float(row.get("active_products") or 0)
            # Soft normalize without merchant-specific caps — log-ish bound.
            norm = min(1.0, active / 5000.0) if active > 0 else 0.0
            score += w * norm
        else:
            score += w * float(row.get(key) or 0.0)
    if total_w <= 0:
        return 0.0
    return score / total_w


def meets_minimums(row: Mapping[str, Any], policy: MerchantSelectionPolicy) -> bool:
    for key, minimum in policy.minimums.items():
        if float(row.get(key) or 0.0) < float(minimum):
            return False
    return True


def select_merchant_candidates(
    readiness_rows: Sequence[Mapping[str, Any]],
    *,
    policy: MerchantSelectionPolicy,
    prefer_finance: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return ranked merchant candidates. Does not mutate cohorts."""

    scored: list[dict[str, Any]] = []
    for row in readiness_rows:
        item = dict(row)
        item["selection_score"] = score_merchant_row(row, policy)
        item["meets_minimums"] = meets_minimums(row, policy)
        item["prefer_finance"] = prefer_finance and float(row.get("finance_coverage") or 0) > 0
        scored.append(item)
    scored.sort(
        key=lambda r: (
            1 if r.get("meets_minimums") else 0,
            1 if r.get("prefer_finance") else 0,
            float(r.get("selection_score") or 0),
        ),
        reverse=True,
    )
    return scored[: max(1, int(limit))]


__all__ = [
    "MerchantSelectionPolicy",
    "meets_minimums",
    "score_merchant_row",
    "select_merchant_candidates",
]
