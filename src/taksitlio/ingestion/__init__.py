"""ADR-010 ingestion framework (P0 skeleton)."""

from taksitlio.ingestion.binding import SourceBinding, build_default_registry, instantiate_adapter
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
from taksitlio.ingestion.runner import IngestionRunResult, run_ingestion_dry

__all__ = [
    "AdapterRegistry",
    "AuthFailed",
    "CampaignParseFailed",
    "DiscoveredProductRef",
    "IngestionCapability",
    "IngestionError",
    "IngestionRunResult",
    "MediaFetchFailed",
    "MerchantProductSourceAdapter",
    "NormalizedOffer",
    "NormalizedProduct",
    "NormalizedStock",
    "ProductParseFailed",
    "RateLimited",
    "RateUnavailable",
    "SourceBinding",
    "SourceBlocked",
    "SourceSchemaChanged",
    "SourceTimeout",
    "build_default_registry",
    "instantiate_adapter",
    "run_ingestion_dry",
]
