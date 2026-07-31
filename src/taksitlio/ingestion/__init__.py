"""ADR-010 ingestion framework (P0 skeleton)."""

from taksitlio.ingestion.capabilities import IngestionCapability
from taksitlio.ingestion.errors import (
    AuthFailed,
    CampaignParseFailed,
    IngestionError,
    MediaFetchFailed,
    ProductParseFailed,
    RateLimited,
    RateUnavailable,
    SourceBlocked,
    SourceSchemaChanged,
    SourceTimeout,
)
from taksitlio.ingestion.protocol import (
    DiscoveredProductRef,
    MerchantProductSourceAdapter,
    NormalizedOffer,
    NormalizedProduct,
    NormalizedStock,
)
from taksitlio.ingestion.registry import AdapterRegistry

__all__ = [
    "AdapterRegistry",
    "AuthFailed",
    "CampaignParseFailed",
    "DiscoveredProductRef",
    "IngestionCapability",
    "IngestionError",
    "MediaFetchFailed",
    "MerchantProductSourceAdapter",
    "NormalizedOffer",
    "NormalizedProduct",
    "NormalizedStock",
    "ProductParseFailed",
    "RateLimited",
    "RateUnavailable",
    "SourceBlocked",
    "SourceSchemaChanged",
    "SourceTimeout",
]
