"""Ingestion scheduler priorities (ADR-010 §63–64).

User requests never wait on crawlers — enqueue high-priority refresh instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def classify_freshness(
    *,
    last_verified_at: Optional[datetime],
    ttl_seconds: int,
    stale_after_ratio: float = 1.0,
    expire_after_ratio: float = 3.0,
    now: Optional[datetime] = None,
    queue_on_stale: SchedulerQueue = SchedulerQueue.PRICE_REFRESH,
    user_search_driven: bool = False,
) -> FreshnessVerdict:
    """Decide freshness label and whether to background-refresh.

    Critical EXPIRED offers must not be shown as current.
    STALE may be shown with a label while a refresh is enqueued.
    """

    clock = now or datetime.now(timezone.utc)
    if last_verified_at is None:
        return FreshnessVerdict(
            status="UNVERIFIED",
            age_seconds=None,
            show_as_current_offer=False,
            enqueue_refresh=True,
            queue=queue_on_stale,
            priority=PRIORITY_USER_SEARCH_STALE if user_search_driven else PRIORITY_UNVERIFIED_LONG,
        )

    verified = last_verified_at
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    age = (clock - verified).total_seconds()
    if age <= ttl_seconds * stale_after_ratio:
        return FreshnessVerdict(
            status="FRESH",
            age_seconds=age,
            show_as_current_offer=True,
            enqueue_refresh=False,
            queue=None,
            priority=PRIORITY_DEFAULT,
        )
    if age <= ttl_seconds * expire_after_ratio:
        return FreshnessVerdict(
            status="STALE",
            age_seconds=age,
            show_as_current_offer=True,  # labeled stale; not removed
            enqueue_refresh=True,
            queue=queue_on_stale,
            priority=PRIORITY_USER_SEARCH_STALE if user_search_driven else PRIORITY_POPULAR_ACTIVE,
        )
    return FreshnessVerdict(
        status="EXPIRED",
        age_seconds=age,
        show_as_current_offer=False,
        enqueue_refresh=True,
        queue=queue_on_stale,
        priority=PRIORITY_USER_SEARCH_STALE if user_search_driven else PRIORITY_PRICE_CHANGED,
    )


def enqueue_search_driven_refresh(
    *,
    product_id: str,
    verdict: FreshnessVerdict,
    source_id: Optional[str] = None,
    external_item_id: Optional[str] = None,
) -> Optional[SchedulerJobSpec]:
    if not verdict.enqueue_refresh or verdict.queue is None:
        return None
    return SchedulerJobSpec(
        queue_name=verdict.queue,
        priority=verdict.priority,
        source_id=source_id,
        product_id=product_id,
        external_item_id=external_item_id,
        payload={"reason": "search_driven_freshness", "freshness": verdict.status},
    )


def lease_sort_key(job: SchedulerJobSpec) -> tuple:
    """Lower priority value first, then FIFO-friendly product id."""

    return (job.priority, job.product_id or "", job.external_item_id or "")


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
    "classify_freshness",
    "enqueue_search_driven_refresh",
    "lease_sort_key",
]
