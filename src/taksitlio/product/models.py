"""Product / offer domain records (ADR-010 §36 / §41)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class StockStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    UNVERIFIED = "UNVERIFIED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class ProductStatus(str, Enum):
    ACTIVE = "ACTIVE"
    UNAVAILABLE = "UNAVAILABLE"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    DRAFT = "DRAFT"


@dataclass(frozen=True)
class ProductRecord:
    merchant_code: str
    external_product_id: str
    display_name: str
    merchant_sku: Optional[str] = None
    gtin: Optional[str] = None
    ean: Optional[str] = None
    mpn: Optional[str] = None
    brand_name: Optional[str] = None
    model_number: Optional[str] = None
    normalized_name: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    source_url: Optional[str] = None
    status: ProductStatus = ProductStatus.ACTIVE
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductOfferRecord:
    merchant_code: str
    external_product_id: str
    current_price: float
    currency: str = "TRY"
    list_price: Optional[float] = None
    stock_status: StockStatus = StockStatus.UNKNOWN
    checkout_url: Optional[str] = None
    freshness_status: FreshnessStatus = FreshnessStatus.UNVERIFIED
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None


__all__ = [
    "FreshnessStatus",
    "ProductOfferRecord",
    "ProductRecord",
    "ProductStatus",
    "StockStatus",
]
