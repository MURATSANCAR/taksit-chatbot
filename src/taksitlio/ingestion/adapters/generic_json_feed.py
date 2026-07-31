"""Generic JSON product feed adapter (ADR-010 P1).

Opaque adapter_code: ``generic.json_feed.v1``.
Merchant identity comes from ingestion_sources.merchant_id — never hardcoded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Optional, Sequence

import httpx

from taksitlio.ingestion.capabilities import IngestionCapability
from taksitlio.ingestion.errors import ProductParseFailed, SourceTimeout
from taksitlio.ingestion.protocol import (
    DiscoveredProductRef,
    NormalizedMediaRef,
    NormalizedOffer,
    NormalizedProduct,
    NormalizedStock,
)
from taksitlio.product.hashing import content_hash

ADAPTER_CODE = "generic.json_feed.v1"

_CAPABILITIES = (
    IngestionCapability.PRODUCT_DISCOVERY,
    IngestionCapability.PRODUCT_DETAIL,
    IngestionCapability.PRICE,
    IngestionCapability.STOCK,
    IngestionCapability.MEDIA,
    IngestionCapability.ATTRIBUTE,
)


class GenericJsonFeedAdapter:
    """Reads a JSON feed from URL or local path.

    Expected shape::

        {
          "products": [
            {
              "id": "SKU-1",
              "name": "...",
              "sku": "...",
              "gtin": "...",
              "ean": "...",
              "mpn": "...",
              "brand": "...",
              "model": "...",
              "url": "https://...",
              "price": 1000.0,
              "list_price": 1200.0,
              "currency": "TRY",
              "stock_status": "AVAILABLE",
              "image_url": "https://...",
              "attributes": {"ram_gb": 16}
            }
          ]
        }
    """

    adapter_code = ADAPTER_CODE

    def __init__(
        self,
        *,
        feed_url: Optional[str] = None,
        feed_path: Optional[str | Path] = None,
        timeout_seconds: float = 30.0,
        source_reference: Optional[str] = None,
    ) -> None:
        if not feed_url and not feed_path:
            raise ValueError("feed_url or feed_path required")
        self._feed_url = feed_url
        self._feed_path = Path(feed_path) if feed_path else None
        self._timeout = timeout_seconds
        self._source_reference = source_reference or feed_url or str(feed_path)
        self._items: dict[str, dict[str, Any]] | None = None

    def capabilities(self) -> Sequence[IngestionCapability]:
        return _CAPABILITIES

    async def _load(self) -> dict[str, dict[str, Any]]:
        if self._items is not None:
            return self._items
        if self._feed_path is not None:
            try:
                raw = json.loads(self._feed_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProductParseFailed(str(exc), detail=str(self._feed_path)) from exc
        else:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(self._feed_url)  # type: ignore[arg-type]
                    resp.raise_for_status()
                    raw = resp.json()
            except httpx.TimeoutException as exc:
                raise SourceTimeout(str(exc), detail=self._feed_url) from exc
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                raise ProductParseFailed(str(exc), detail=self._feed_url) from exc

        products = raw.get("products") if isinstance(raw, dict) else None
        if not isinstance(products, list):
            raise ProductParseFailed(
                "feed missing products[] array",
                detail=self._source_reference,
            )
        indexed: dict[str, dict[str, Any]] = {}
        for row in products:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or row.get("sku") or "").strip()
            if not pid:
                continue
            indexed[pid] = row
        if not indexed:
            raise ProductParseFailed("feed has no valid product ids", detail=self._source_reference)
        self._items = indexed
        return indexed

    async def discover_products(
        self, *, cursor: Optional[str] = None
    ) -> AsyncIterator[DiscoveredProductRef]:
        _ = cursor
        items = await self._load()
        for pid, row in items.items():
            yield DiscoveredProductRef(
                external_product_id=pid,
                source_url=_as_str(row.get("url")),
                content_hash=content_hash(row),
                metadata={},
            )

    async def fetch_product(self, external_product_id: str) -> NormalizedProduct:
        row = await self._require(external_product_id)
        name = _as_str(row.get("name") or row.get("title")) or external_product_id
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        return NormalizedProduct(
            external_product_id=external_product_id,
            display_name=name,
            merchant_sku=_as_str(row.get("sku")) or external_product_id,
            gtin=_as_str(row.get("gtin")),
            ean=_as_str(row.get("ean")),
            mpn=_as_str(row.get("mpn")),
            brand_name=_as_str(row.get("brand")),
            model_number=_as_str(row.get("model")),
            short_description=_as_str(row.get("short_description")),
            full_description=_as_str(row.get("description")),
            source_url=_as_str(row.get("url")),
            attributes=attrs,
            content_hash=content_hash(row),
            source_reference=self._source_reference,
        )

    async def fetch_offers(self, external_product_id: str) -> Sequence[NormalizedOffer]:
        row = await self._require(external_product_id)
        price = row.get("price")
        if price is None:
            return ()
        try:
            current = float(price)
        except (TypeError, ValueError) as exc:
            raise ProductParseFailed(
                f"invalid price for {external_product_id}",
                detail=str(price),
            ) from exc
        list_price = row.get("list_price")
        list_val = None
        if list_price is not None:
            try:
                list_val = float(list_price)
            except (TypeError, ValueError):
                list_val = None
        return (
            NormalizedOffer(
                external_product_id=external_product_id,
                current_price=current,
                currency=_as_str(row.get("currency")) or "TRY",
                list_price=list_val,
                checkout_url=_as_str(row.get("url")),
                content_hash=content_hash(
                    {"price": current, "list_price": list_val, "currency": row.get("currency")}
                ),
                source_reference=self._source_reference,
            ),
        )

    async def fetch_stock(self, external_product_id: str) -> Sequence[NormalizedStock]:
        row = await self._require(external_product_id)
        status = (_as_str(row.get("stock_status")) or "UNKNOWN").upper()
        if status not in {"AVAILABLE", "LIMITED", "OUT_OF_STOCK", "UNKNOWN"}:
            status = "UNKNOWN"
        qty = row.get("quantity")
        quantity = int(qty) if isinstance(qty, int) else None
        return (
            NormalizedStock(
                external_product_id=external_product_id,
                stock_status=status,
                quantity=quantity,
                source_reference=self._source_reference,
            ),
        )

    async def fetch_media(
        self, external_product_id: str
    ) -> Sequence[NormalizedMediaRef]:
        row = await self._require(external_product_id)
        url = _as_str(row.get("image_url") or row.get("primary_image"))
        if not url:
            return ()
        return (
            NormalizedMediaRef(
                external_product_id=external_product_id,
                source_url=url,
                media_role="PRIMARY",
                source_reference=self._source_reference,
            ),
        )

    async def fetch_finance_metadata(
        self, external_product_id: str
    ) -> Mapping[str, Any]:
        await self._require(external_product_id)
        return {}

    async def _require(self, external_product_id: str) -> dict[str, Any]:
        items = await self._load()
        row = items.get(external_product_id)
        if row is None:
            raise ProductParseFailed(
                f"product not found: {external_product_id}",
                detail=self._source_reference,
            )
        return row


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def register_generic_json_feed(
    registry,
    *,
    feed_url: Optional[str] = None,
    feed_path: Optional[str | Path] = None,
) -> None:
    """Register ``generic.json_feed.v1`` bound to a concrete feed location."""

    registry.register(
        ADAPTER_CODE,
        lambda: GenericJsonFeedAdapter(feed_url=feed_url, feed_path=feed_path),
    )


__all__ = [
    "ADAPTER_CODE",
    "GenericJsonFeedAdapter",
    "register_generic_json_feed",
]
