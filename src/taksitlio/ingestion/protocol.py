"""Merchant product source adapter protocol (ADR-010 §33).

Concrete adapters are registered by opaque ``adapter_code``. Merchant display
names are never hardcoded in production mapping code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Optional, Protocol, Sequence

from taksitlio.ingestion.capabilities import IngestionCapability


@dataclass(frozen=True)
class DiscoveredProductRef:
    external_product_id: str
    source_url: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedProduct:
    external_product_id: str
    display_name: str
    merchant_sku: Optional[str] = None
    gtin: Optional[str] = None
    ean: Optional[str] = None
    mpn: Optional[str] = None
    brand_name: Optional[str] = None
    model_number: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    source_url: Optional[str] = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None


@dataclass(frozen=True)
class NormalizedOffer:
    external_product_id: str
    current_price: float
    currency: str = "TRY"
    list_price: Optional[float] = None
    checkout_url: Optional[str] = None
    location_code: Optional[str] = None
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None


@dataclass(frozen=True)
class NormalizedStock:
    external_product_id: str
    stock_status: str  # AVAILABLE | LIMITED | OUT_OF_STOCK | UNKNOWN
    location_code: Optional[str] = None
    quantity: Optional[int] = None
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None


@dataclass(frozen=True)
class NormalizedMediaRef:
    external_product_id: str
    source_url: str
    media_role: str = "PRIMARY"  # PRIMARY | GALLERY | THUMBNAIL | ...
    display_order: int = 0
    source_reference: Optional[str] = None


class MerchantProductSourceAdapter(Protocol):
    """Per-merchant ingestion adapter — no shared brittle scraper."""

    adapter_code: str

    def capabilities(self) -> Sequence[IngestionCapability]: ...

    def discover_products(
        self, *, cursor: Optional[str] = None
    ) -> AsyncIterator[DiscoveredProductRef]: ...

    async def fetch_product(self, external_product_id: str) -> NormalizedProduct: ...

    async def fetch_offers(self, external_product_id: str) -> Sequence[NormalizedOffer]: ...

    async def fetch_stock(self, external_product_id: str) -> Sequence[NormalizedStock]: ...

    async def fetch_media(
        self, external_product_id: str
    ) -> Sequence[NormalizedMediaRef]: ...

    async def fetch_finance_metadata(
        self, external_product_id: str
    ) -> Mapping[str, Any]: ...


__all__ = [
    "DiscoveredProductRef",
    "MerchantProductSourceAdapter",
    "NormalizedMediaRef",
    "NormalizedOffer",
    "NormalizedProduct",
    "NormalizedStock",
]
