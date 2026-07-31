"""Scheduler domain types (ADR-010 §63)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SchedulerQueue(str, Enum):
    PRODUCT_DISCOVERY = "PRODUCT_DISCOVERY"
    PRODUCT_DETAIL = "PRODUCT_DETAIL"
    PRICE_REFRESH = "PRICE_REFRESH"
    STOCK_REFRESH = "STOCK_REFRESH"
    MEDIA_FETCH = "MEDIA_FETCH"
    CAMPAIGN_REFRESH = "CAMPAIGN_REFRESH"
    RATE_REFRESH = "RATE_REFRESH"
    FAILED_ITEM_RETRY = "FAILED_ITEM_RETRY"


# Lower number = higher priority
PRIORITY_USER_SEARCH_STALE = 10
PRIORITY_POPULAR_ACTIVE = 20
PRIORITY_PRICE_CHANGED = 30
PRIORITY_CAMPAIGN = 40
PRIORITY_UNVERIFIED_LONG = 80
PRIORITY_DEFAULT = 100


@dataclass(frozen=True)
class FreshnessTtlPolicy:
    price_ttl_seconds: int = 3600
    stock_ttl_seconds: int = 3600
    product_ttl_seconds: int = 86400
    image_ttl_seconds: int = 604800
    campaign_ttl_seconds: int = 3600
    bank_terms_ttl_seconds: int = 3600


@dataclass(frozen=True)
class FreshnessVerdict:
    status: str  # FRESH | STALE | EXPIRED | UNVERIFIED
    age_seconds: Optional[float]
    show_as_current_offer: bool
    enqueue_refresh: bool
    queue: Optional[SchedulerQueue]
    priority: int


@dataclass(frozen=True)
class SchedulerJobSpec:
    queue_name: SchedulerQueue
    priority: int
    source_id: Optional[str] = None
    product_id: Optional[str] = None
    external_item_id: Optional[str] = None
    payload: Optional[dict] = None


__all__ = [
    "FreshnessTtlPolicy",
    "FreshnessVerdict",
    "PRIORITY_CAMPAIGN",
    "PRIORITY_DEFAULT",
    "PRIORITY_POPULAR_ACTIVE",
    "PRIORITY_PRICE_CHANGED",
    "PRIORITY_UNVERIFIED_LONG",
    "PRIORITY_USER_SEARCH_STALE",
    "SchedulerJobSpec",
    "SchedulerQueue",
]
