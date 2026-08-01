"""Catalog domain event types and selective projection planning (P2-LIVE)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Mapping, Optional, Sequence


class CatalogEventType(str, Enum):
    PRODUCT_DISCOVERED = "PRODUCT_DISCOVERED"
    PRODUCT_CHANGED = "PRODUCT_CHANGED"
    PRODUCT_STATUS_CHANGED = "PRODUCT_STATUS_CHANGED"
    OFFER_CHANGED = "OFFER_CHANGED"
    PRICE_CHANGED = "PRICE_CHANGED"
    STOCK_CHANGED = "STOCK_CHANGED"
    MEDIA_DISCOVERED = "MEDIA_DISCOVERED"
    MEDIA_CHANGED = "MEDIA_CHANGED"
    SOURCE_CATEGORY_DISCOVERED = "SOURCE_CATEGORY_DISCOVERED"
    SOURCE_CATEGORY_CHANGED = "SOURCE_CATEGORY_CHANGED"
    BRAND_CHANGED = "BRAND_CHANGED"
    ATTRIBUTE_CHANGED = "ATTRIBUTE_CHANGED"
    ENTITY_RESOLUTION_CHANGED = "ENTITY_RESOLUTION_CHANGED"
    CATEGORY_MAPPING_CHANGED = "CATEGORY_MAPPING_CHANGED"
    ATTRIBUTE_MAPPING_CHANGED = "ATTRIBUTE_MAPPING_CHANGED"
    FINANCE_RULE_CHANGED = "FINANCE_RULE_CHANGED"
    CAMPAIGN_CHANGED = "CAMPAIGN_CHANGED"
    RATE_CHANGED = "RATE_CHANGED"
    RANKING_POLICY_CHANGED = "RANKING_POLICY_CHANGED"
    MERCHANT_READINESS_RECALCULATION_REQUESTED = (
        "MERCHANT_READINESS_RECALCULATION_REQUESTED"
    )


class ProjectionKind(str, Enum):
    SEARCH = "SEARCH"
    FINANCE = "FINANCE"
    MEDIA = "MEDIA"
    RANKING_FEATURES = "RANKING_FEATURES"
    MERCHANT_READINESS = "MERCHANT_READINESS"
    ENTITY_INDEX = "ENTITY_INDEX"
    RELEASE_SCOPE = "RELEASE_SCOPE"


_EVENT_PROJECTIONS: Mapping[CatalogEventType, FrozenSet[ProjectionKind]] = {
    CatalogEventType.PRODUCT_DISCOVERED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.MEDIA,
            ProjectionKind.RANKING_FEATURES,
            ProjectionKind.MERCHANT_READINESS,
            ProjectionKind.ENTITY_INDEX,
        }
    ),
    CatalogEventType.PRODUCT_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.RANKING_FEATURES,
            ProjectionKind.MERCHANT_READINESS,
        }
    ),
    CatalogEventType.OFFER_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.FINANCE,
            ProjectionKind.RANKING_FEATURES,
            ProjectionKind.MERCHANT_READINESS,
        }
    ),
    CatalogEventType.PRICE_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.FINANCE,
            ProjectionKind.RANKING_FEATURES,
            ProjectionKind.MERCHANT_READINESS,
        }
    ),
    CatalogEventType.STOCK_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.RANKING_FEATURES,
            ProjectionKind.MERCHANT_READINESS,
        }
    ),
    CatalogEventType.MEDIA_DISCOVERED: frozenset(
        {ProjectionKind.MEDIA, ProjectionKind.RANKING_FEATURES, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.MEDIA_CHANGED: frozenset(
        {ProjectionKind.MEDIA, ProjectionKind.RANKING_FEATURES, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.SOURCE_CATEGORY_DISCOVERED: frozenset(
        {ProjectionKind.ENTITY_INDEX, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.ENTITY_RESOLUTION_CHANGED: frozenset(
        {ProjectionKind.SEARCH, ProjectionKind.ENTITY_INDEX, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.CATEGORY_MAPPING_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.ENTITY_INDEX,
            ProjectionKind.MERCHANT_READINESS,
            ProjectionKind.RELEASE_SCOPE,
        }
    ),
    CatalogEventType.ATTRIBUTE_MAPPING_CHANGED: frozenset(
        {ProjectionKind.SEARCH, ProjectionKind.RANKING_FEATURES}
    ),
    CatalogEventType.FINANCE_RULE_CHANGED: frozenset(
        {ProjectionKind.FINANCE, ProjectionKind.RANKING_FEATURES, ProjectionKind.RELEASE_SCOPE}
    ),
    CatalogEventType.CAMPAIGN_CHANGED: frozenset(
        {ProjectionKind.FINANCE, ProjectionKind.RANKING_FEATURES}
    ),
    CatalogEventType.RATE_CHANGED: frozenset(
        {ProjectionKind.FINANCE, ProjectionKind.RANKING_FEATURES}
    ),
    CatalogEventType.RANKING_POLICY_CHANGED: frozenset({ProjectionKind.RANKING_FEATURES}),
    CatalogEventType.MERCHANT_READINESS_RECALCULATION_REQUESTED: frozenset(
        {ProjectionKind.MERCHANT_READINESS, ProjectionKind.RELEASE_SCOPE}
    ),
    CatalogEventType.PRODUCT_STATUS_CHANGED: frozenset(
        {
            ProjectionKind.SEARCH,
            ProjectionKind.MERCHANT_READINESS,
            ProjectionKind.RELEASE_SCOPE,
        }
    ),
    CatalogEventType.SOURCE_CATEGORY_CHANGED: frozenset(
        {ProjectionKind.ENTITY_INDEX, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.BRAND_CHANGED: frozenset(
        {ProjectionKind.ENTITY_INDEX, ProjectionKind.MERCHANT_READINESS}
    ),
    CatalogEventType.ATTRIBUTE_CHANGED: frozenset(
        {ProjectionKind.SEARCH, ProjectionKind.RANKING_FEATURES}
    ),
}


@dataclass(frozen=True)
class CatalogDomainEvent:
    event_type: CatalogEventType
    source_id: Optional[str] = None
    source_revision: Optional[str] = None
    source_item_id: Optional[str] = None
    ingestion_run_id: Optional[str] = None
    content_hash: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    merchant_id: Optional[int] = None
    product_id: Optional[int] = None
    offer_id: Optional[int] = None
    catalog_revision: Optional[str] = None
    payload: Mapping[str, object] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> Optional[tuple[str, str, str, str]]:
        if not all(
            [self.source_id, self.source_item_id, self.source_revision, self.content_hash]
        ):
            return None
        return (
            str(self.source_id),
            str(self.source_item_id),
            str(self.source_revision),
            str(self.content_hash),
        )


@dataclass(frozen=True)
class SelectiveRefreshPlan:
    projections: FrozenSet[ProjectionKind]
    product_ids: FrozenSet[int]
    merchant_ids: FrozenSet[int]
    full_catalog_rebuild: bool = False


def plan_selective_refresh(
    events: Sequence[CatalogDomainEvent],
) -> SelectiveRefreshPlan:
    """Affected IDs only — never rebuild entire catalog for one product change."""

    projections: set[ProjectionKind] = set()
    product_ids: set[int] = set()
    merchant_ids: set[int] = set()
    for ev in events:
        projections |= set(_EVENT_PROJECTIONS.get(ev.event_type, frozenset()))
        if ev.product_id is not None:
            product_ids.add(int(ev.product_id))
        if ev.merchant_id is not None:
            merchant_ids.add(int(ev.merchant_id))
    return SelectiveRefreshPlan(
        projections=frozenset(projections),
        product_ids=frozenset(product_ids),
        merchant_ids=frozenset(merchant_ids),
        full_catalog_rebuild=False,
    )


def projections_for(event_type: CatalogEventType) -> FrozenSet[ProjectionKind]:
    return _EVENT_PROJECTIONS.get(event_type, frozenset())


__all__ = [
    "CatalogDomainEvent",
    "CatalogEventType",
    "ProjectionKind",
    "SelectiveRefreshPlan",
    "plan_selective_refresh",
    "projections_for",
]
