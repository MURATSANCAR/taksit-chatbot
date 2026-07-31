"""Schema drift / anomaly quarantine (ADR-012 §17)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class DriftAction(str, Enum):
    OK = "OK"
    QUARANTINED = "QUARANTINED"
    SOURCE_SCHEMA_CHANGED = "SOURCE_SCHEMA_CHANGED"


@dataclass(frozen=True)
class DriftSignals:
    price_drop_ratio: Optional[float] = None  # 0.9 = 90% drop
    product_count_drop_ratio: Optional[float] = None
    all_out_of_stock: bool = False
    image_count_zero: bool = False
    campaign_count_spike_ratio: Optional[float] = None
    term_months_jump: Optional[tuple[int, int]] = None  # (old, new)
    currency_changed: bool = False


@dataclass(frozen=True)
class DriftDecision:
    action: DriftAction
    reasons: tuple[str, ...]


def evaluate_schema_drift(signals: DriftSignals) -> DriftDecision:
    reasons: list[str] = []
    if signals.price_drop_ratio is not None and signals.price_drop_ratio >= 0.90:
        reasons.append("price_drop_gt_90pct")
    if signals.product_count_drop_ratio is not None and signals.product_count_drop_ratio >= 0.80:
        reasons.append("product_count_drop_gt_80pct")
    if signals.all_out_of_stock:
        reasons.append("all_out_of_stock")
    if signals.image_count_zero:
        reasons.append("image_count_zero")
    if signals.campaign_count_spike_ratio is not None and signals.campaign_count_spike_ratio >= 3.0:
        reasons.append("campaign_spike")
    if signals.term_months_jump is not None:
        old, new = signals.term_months_jump
        if old > 0 and new >= old * 5:
            reasons.append("term_months_anomaly")
    if signals.currency_changed:
        reasons.append("currency_changed")

    if not reasons:
        return DriftDecision(action=DriftAction.OK, reasons=())
    if "currency_changed" in reasons or "term_months_anomaly" in reasons:
        return DriftDecision(action=DriftAction.SOURCE_SCHEMA_CHANGED, reasons=tuple(reasons))
    return DriftDecision(action=DriftAction.QUARANTINED, reasons=tuple(reasons))


__all__ = [
    "DriftAction",
    "DriftDecision",
    "DriftSignals",
    "evaluate_schema_drift",
]
