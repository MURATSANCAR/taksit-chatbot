"""Pure upsert planning for products/offers (no DB I/O) — ADR-010 P1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.product.canonical import CanonicalKey, resolve_canonical_key
from taksitlio.product.hashing import content_hash
from taksitlio.product.models import FreshnessStatus, StockStatus
from taksitlio.product.normalize import normalize_display_name


@dataclass(frozen=True)
class ProductUpsertPlan:
    external_product_id: str
    display_name: str
    normalized_name: str
    merchant_sku: Optional[str]
    gtin: Optional[str]
    ean: Optional[str]
    mpn: Optional[str]
    brand_name: Optional[str]
    model_number: Optional[str]
    short_description: Optional[str]
    full_description: Optional[str]
    source_url: Optional[str]
    attributes: dict
    content_hash: str
    source_reference: Optional[str]
    canonical: Optional[CanonicalKey]
    action: str  # UPSERT | SKIP_UNCHANGED


@dataclass(frozen=True)
class OfferUpsertResult:
    external_product_id: str
    current_price: float
    list_price: Optional[float]
    currency: str
    stock_status: StockStatus
    checkout_url: Optional[str]
    content_hash: str
    source_reference: Optional[str]
    freshness_status: FreshnessStatus
    snapshot_required: bool
    action: str  # UPSERT | SKIP_UNCHANGED


def plan_product_upsert(
    product: NormalizedProduct,
    *,
    previous_content_hash: Optional[str] = None,
) -> ProductUpsertPlan:
    payload = {
        "external_product_id": product.external_product_id,
        "display_name": product.display_name,
        "merchant_sku": product.merchant_sku,
        "gtin": product.gtin,
        "ean": product.ean,
        "mpn": product.mpn,
        "brand_name": product.brand_name,
        "model_number": product.model_number,
        "short_description": product.short_description,
        "full_description": product.full_description,
        "source_url": product.source_url,
        "attributes": dict(product.attributes or {}),
    }
    digest = product.content_hash or content_hash(payload)
    action = "SKIP_UNCHANGED" if previous_content_hash and previous_content_hash == digest else "UPSERT"
    canonical = resolve_canonical_key(
        gtin=product.gtin,
        ean=product.ean,
        mpn=product.mpn,
        brand_name=product.brand_name,
        model_number=product.model_number,
        display_name=product.display_name,
    )
    return ProductUpsertPlan(
        external_product_id=product.external_product_id,
        display_name=product.display_name,
        normalized_name=normalize_display_name(product.display_name),
        merchant_sku=product.merchant_sku,
        gtin=product.gtin,
        ean=product.ean,
        mpn=product.mpn,
        brand_name=product.brand_name,
        model_number=product.model_number,
        short_description=product.short_description,
        full_description=product.full_description,
        source_url=product.source_url,
        attributes=dict(product.attributes or {}),
        content_hash=digest,
        source_reference=product.source_reference,
        canonical=canonical,
        action=action,
    )


def plan_offer_upsert(
    offer: NormalizedOffer,
    stock: Optional[NormalizedStock] = None,
    *,
    previous_content_hash: Optional[str] = None,
) -> OfferUpsertResult:
    status_raw = (stock.stock_status if stock else "UNKNOWN") or "UNKNOWN"
    try:
        stock_status = StockStatus(status_raw)
    except ValueError:
        stock_status = StockStatus.UNKNOWN

    payload = {
        "external_product_id": offer.external_product_id,
        "current_price": float(offer.current_price),
        "list_price": offer.list_price,
        "currency": offer.currency or "TRY",
        "stock_status": stock_status.value,
        "checkout_url": offer.checkout_url,
        "location_code": offer.location_code,
    }
    digest = offer.content_hash or content_hash(payload)
    unchanged = previous_content_hash is not None and previous_content_hash == digest
    return OfferUpsertResult(
        external_product_id=offer.external_product_id,
        current_price=float(offer.current_price),
        list_price=offer.list_price,
        currency=offer.currency or "TRY",
        stock_status=stock_status,
        checkout_url=offer.checkout_url,
        content_hash=digest,
        source_reference=offer.source_reference or (stock.source_reference if stock else None),
        freshness_status=FreshnessStatus.FRESH,
        snapshot_required=not unchanged,
        action="SKIP_UNCHANGED" if unchanged else "UPSERT",
    )


__all__ = [
    "OfferUpsertResult",
    "ProductUpsertPlan",
    "plan_offer_upsert",
    "plan_product_upsert",
]
