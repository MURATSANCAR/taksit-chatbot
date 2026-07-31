"""Quality circuit breaker — source-scoped, not whole chatbot (ADR-012 §24)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BreakerScope(str, Enum):
    MERCHANT_PRICE = "MERCHANT_PRICE"
    BANK_CAMPAIGN = "BANK_CAMPAIGN"
    IMAGE_SOURCE = "IMAGE_SOURCE"


class BreakerAction(str, Enum):
    NONE = "NONE"
    DISABLE_PRICE_RESULTS = "DISABLE_PRICE_RESULTS"
    DISABLE_CAMPAIGN_RESULTS = "DISABLE_CAMPAIGN_RESULTS"
    FALLBACK_IMAGE_SOURCE = "FALLBACK_IMAGE_SOURCE"


@dataclass
class QualityCircuitBreaker:
    broken_price_rate: float = 0.0
    campaign_mismatch_count: int = 0
    broken_image_rate: float = 0.0
    price_threshold: float = 0.05
    image_threshold: float = 0.03
    disabled: set[BreakerAction] = field(default_factory=set)

    def evaluate(self) -> tuple[BreakerAction, ...]:
        actions: list[BreakerAction] = []
        if self.broken_price_rate > self.price_threshold:
            actions.append(BreakerAction.DISABLE_PRICE_RESULTS)
        if self.campaign_mismatch_count > 0:
            actions.append(BreakerAction.DISABLE_CAMPAIGN_RESULTS)
        if self.broken_image_rate > self.image_threshold:
            actions.append(BreakerAction.FALLBACK_IMAGE_SOURCE)
        self.disabled = set(actions)
        return tuple(actions)

    def is_price_disabled(self) -> bool:
        return BreakerAction.DISABLE_PRICE_RESULTS in self.disabled

    def is_campaign_disabled(self) -> bool:
        return BreakerAction.DISABLE_CAMPAIGN_RESULTS in self.disabled


def decide_breaker(
    *,
    scope: BreakerScope,
    broken_rate: float = 0.0,
    mismatch_count: int = 0,
) -> BreakerAction:
    if scope is BreakerScope.MERCHANT_PRICE and broken_rate > 0.05:
        return BreakerAction.DISABLE_PRICE_RESULTS
    if scope is BreakerScope.BANK_CAMPAIGN and mismatch_count > 0:
        return BreakerAction.DISABLE_CAMPAIGN_RESULTS
    if scope is BreakerScope.IMAGE_SOURCE and broken_rate > 0.03:
        return BreakerAction.FALLBACK_IMAGE_SOURCE
    return BreakerAction.NONE


__all__ = [
    "BreakerAction",
    "BreakerScope",
    "QualityCircuitBreaker",
    "decide_breaker",
]
