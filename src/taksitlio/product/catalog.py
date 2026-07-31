"""Apply product/offer upsert plans to a catalog store (ADR-010 P8).

No demo seed — only rows produced from verified ingestion snapshots.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

from taksitlio.data_quality import DataQualityStatus, ProductQualityVerdict
from taksitlio.ingestion.runner import IngestionRunResult
from taksitlio.product.models import ProductStatus
from taksitlio.product.upsert import (
    OfferUpsertResult,
    ProductUpsertPlan,
    plan_offer_upsert,
    plan_product_upsert,
)


_PRIVATE_CDN_PREFIXES = (
    "http://127.0.0.1:9000/taksitlio-media/",
    "http://localhost:9000/taksitlio-media/",
    "http://127.0.0.1:9000/taksitlio-media",
    "http://localhost:9000/taksitlio-media",
)


def publicize_cdn_url(
    url: Optional[str],
    *,
    storage_key: Optional[str] = None,
) -> Optional[str]:
    """Map private MinIO/localhost URLs to CDN_BASE_URL for browser clients."""

    base = (os.environ.get("CDN_BASE_URL") or "").rstrip("/")
    if storage_key and base:
        return f"{base}/{storage_key.lstrip('/')}"
    if not url:
        return None
    if not base:
        return url
    for priv in _PRIVATE_CDN_PREFIXES:
        if url.startswith(priv):
            rest = url[len(priv) :].lstrip("/")
            return f"{base}/{rest}" if rest else base
    if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
        marker = "/taksitlio-media/"
        if marker in url:
            return f"{base}/{url.split(marker, 1)[1]}"
    return url


def _attrs_dict(attrs: Any) -> dict[str, Any]:
    if attrs is None:
        return {}
    if isinstance(attrs, dict):
        return dict(attrs)
    if isinstance(attrs, (bytes, bytearray)):
        attrs = attrs.decode("utf-8")
    if isinstance(attrs, str):
        text = attrs.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return dict(attrs)


@dataclass(frozen=True)
class StoredProduct:
    id: int
    merchant_id: int
    external_product_id: str
    display_name: str
    content_hash: Optional[str]
    data_quality_status: str
    status: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    primary_cdn_url: Optional[str] = None
    pending_source_image_url: Optional[str] = None
    primary_media_status: Optional[str] = None


def _row_to_stored_product(row: Any) -> StoredProduct:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    return StoredProduct(
        id=int(row["id"]),
        merchant_id=int(row["merchant_id"]),
        external_product_id=str(row["external_product_id"]),
        display_name=str(row["display_name"]),
        content_hash=row["content_hash"],
        data_quality_status=str(row["data_quality_status"]),
        status=str(row["status"]),
        attributes=_attrs_dict(row["attributes"]),
        primary_cdn_url=publicize_cdn_url(
            row["primary_cdn_url"] if "primary_cdn_url" in keys else None,
            storage_key=row["storage_key"] if "storage_key" in keys else None,
        ),
        pending_source_image_url=(
            row["pending_source_image_url"] if "pending_source_image_url" in keys else None
        ),
        primary_media_status=(
            row["primary_media_status"] if "primary_media_status" in keys else None
        ),
    )


@dataclass(frozen=True)
class StoredOffer:
    id: int
    product_id: int
    merchant_id: int
    current_price: float
    currency: str
    stock_status: str
    content_hash: Optional[str]
    freshness_status: str


@dataclass(frozen=True)
class ApplyItemResult:
    external_product_id: str
    product_action: str  # UPSERT | SKIP_UNCHANGED | SKIP_QUARANTINED | SKIP_NO_PRODUCT
    offer_action: Optional[str]
    product_id: Optional[int]
    offer_id: Optional[int]
    quality_status: str


@dataclass(frozen=True)
class CatalogApplyResult:
    merchant_id: int
    upserted_products: int
    skipped_unchanged: int
    skipped_quarantined: int
    upserted_offers: int
    items: tuple[ApplyItemResult, ...]
    finance_rebuild: Optional[Any] = None


class ProductCatalogRepository(Protocol):
    async def get_product_hash(
        self, *, merchant_id: int, external_product_id: str
    ) -> Optional[str]: ...

    async def get_offer_hash(self, *, product_id: int) -> Optional[str]: ...

    async def get_offer(self, product_id: int) -> Optional[StoredOffer]: ...

    async def upsert_product(
        self,
        *,
        merchant_id: int,
        plan: ProductUpsertPlan,
        data_quality_status: str,
        status: str,
    ) -> StoredProduct: ...

    async def upsert_offer(
        self,
        *,
        merchant_id: int,
        product_id: int,
        plan: OfferUpsertResult,
    ) -> StoredOffer: ...

    async def list_products(
        self, *, merchant_id: Optional[int] = None, limit: int = 100
    ) -> Sequence[StoredProduct]: ...

    async def list_products_matching(
        self,
        *,
        name_terms: Sequence[str],
        merchant_id: Optional[int] = None,
        limit: int = 100,
    ) -> Sequence[StoredProduct]: ...

    async def get_product(self, product_id: int) -> Optional[StoredProduct]: ...

    async def set_pending_source_image(
        self, product_id: int, source_url: str
    ) -> None: ...

    async def attach_primary_media(
        self,
        product_id: int,
        *,
        cdn_url: Optional[str],
        sha256: str,
        status: str,
        source_url: str,
        storage_key: Optional[str] = None,
        mime_type: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        file_size: Optional[int] = None,
    ) -> None: ...

    async def mark_offer_stale(self, product_id: int) -> None: ...

    async def refresh_offer_price(
        self,
        product_id: int,
        *,
        price: float,
        currency: str,
        stock_status: str,
        list_price: Optional[float] = None,
    ) -> Optional[StoredOffer]: ...


class InMemoryProductCatalogRepository:
    def __init__(self) -> None:
        self._products: dict[tuple[int, str], StoredProduct] = {}
        self._by_id: dict[int, tuple[int, str]] = {}
        self._offers: dict[int, StoredOffer] = {}  # product_id → offer
        self._next_product = 1
        self._next_offer = 1

    async def get_product_hash(
        self, *, merchant_id: int, external_product_id: str
    ) -> Optional[str]:
        row = self._products.get((merchant_id, external_product_id))
        return None if row is None else row.content_hash

    async def get_offer_hash(self, *, product_id: int) -> Optional[str]:
        row = self._offers.get(product_id)
        return None if row is None else row.content_hash

    async def get_offer(self, product_id: int) -> Optional[StoredOffer]:
        return self._offers.get(product_id)

    async def upsert_product(
        self,
        *,
        merchant_id: int,
        plan: ProductUpsertPlan,
        data_quality_status: str,
        status: str,
    ) -> StoredProduct:
        key = (merchant_id, plan.external_product_id)
        existing = self._products.get(key)
        pid = existing.id if existing else self._next_product
        if existing is None:
            self._next_product += 1
        stored = StoredProduct(
            id=pid,
            merchant_id=merchant_id,
            external_product_id=plan.external_product_id,
            display_name=plan.display_name,
            content_hash=plan.content_hash,
            data_quality_status=data_quality_status,
            status=status,
            attributes=dict(plan.attributes),
            primary_cdn_url=None if existing is None else existing.primary_cdn_url,
            pending_source_image_url=(
                None if existing is None else existing.pending_source_image_url
            ),
            primary_media_status=(
                None if existing is None else existing.primary_media_status
            ),
        )
        self._products[key] = stored
        self._by_id[pid] = key
        return stored

    async def upsert_offer(
        self,
        *,
        merchant_id: int,
        product_id: int,
        plan: OfferUpsertResult,
    ) -> StoredOffer:
        existing = self._offers.get(product_id)
        oid = existing.id if existing else self._next_offer
        if existing is None:
            self._next_offer += 1
        stored = StoredOffer(
            id=oid,
            product_id=product_id,
            merchant_id=merchant_id,
            current_price=plan.current_price,
            currency=plan.currency,
            stock_status=plan.stock_status.value,
            content_hash=plan.content_hash,
            freshness_status=plan.freshness_status.value,
        )
        self._offers[product_id] = stored
        return stored

    async def list_products(
        self, *, merchant_id: Optional[int] = None, limit: int = 100
    ) -> Sequence[StoredProduct]:
        rows = list(self._products.values())
        if merchant_id is not None:
            rows = [r for r in rows if r.merchant_id == merchant_id]
        rows.sort(key=lambda r: r.id)
        return tuple(rows[:limit])

    async def list_products_matching(
        self,
        *,
        name_terms: Sequence[str],
        merchant_id: Optional[int] = None,
        limit: int = 100,
    ) -> Sequence[StoredProduct]:
        terms = [t.casefold() for t in name_terms if t and str(t).strip()]
        rows = list(self._products.values())
        if merchant_id is not None:
            rows = [r for r in rows if r.merchant_id == merchant_id]
        if terms:
            rows = [
                r
                for r in rows
                if any(t in (r.display_name or "").casefold() for t in terms)
            ]
        rows.sort(key=lambda r: r.id)
        return tuple(rows[:limit])

    async def get_product(self, product_id: int) -> Optional[StoredProduct]:
        key = self._by_id.get(product_id)
        if key is None:
            return None
        return self._products.get(key)

    async def set_pending_source_image(
        self, product_id: int, source_url: str
    ) -> None:
        product = await self.get_product(product_id)
        if product is None:
            return
        key = self._by_id[product_id]
        self._products[key] = StoredProduct(
            **{
                **product.__dict__,
                "pending_source_image_url": source_url,
            }
        )

    async def attach_primary_media(
        self,
        product_id: int,
        *,
        cdn_url: Optional[str],
        sha256: str,
        status: str,
        source_url: str,
        storage_key: Optional[str] = None,
        mime_type: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        file_size: Optional[int] = None,
    ) -> None:
        _ = (sha256, source_url, storage_key, mime_type, width, height, file_size)
        product = await self.get_product(product_id)
        if product is None:
            return
        key = self._by_id[product_id]
        self._products[key] = StoredProduct(
            **{
                **product.__dict__,
                "primary_cdn_url": cdn_url,
                "primary_media_status": status,
                "pending_source_image_url": None,
            }
        )

    async def mark_offer_stale(self, product_id: int) -> None:
        offer = self._offers.get(product_id)
        if offer is None:
            return
        self._offers[product_id] = StoredOffer(
            **{**offer.__dict__, "freshness_status": "STALE"}
        )

    async def refresh_offer_price(
        self,
        product_id: int,
        *,
        price: float,
        currency: str,
        stock_status: str,
        list_price: Optional[float] = None,
    ) -> Optional[StoredOffer]:
        _ = list_price
        offer = self._offers.get(product_id)
        if offer is None:
            return None
        updated = StoredOffer(
            **{
                **offer.__dict__,
                "current_price": float(price),
                "currency": currency,
                "stock_status": stock_status,
                "freshness_status": "FRESH",
            }
        )
        self._offers[product_id] = updated
        return updated


class PostgresProductCatalogRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get_product_hash(
        self, *, merchant_id: int, external_product_id: str
    ) -> Optional[str]:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT content_hash FROM products
                WHERE merchant_id = $1 AND external_product_id = $2
                """,
                merchant_id,
                external_product_id,
            )

    async def get_offer_hash(self, *, product_id: int) -> Optional[str]:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT content_hash FROM product_offers
                WHERE product_id = $1
                ORDER BY id DESC LIMIT 1
                """,
                product_id,
            )

    async def get_offer(self, product_id: int) -> Optional[StoredOffer]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM product_offers
                WHERE product_id = $1
                ORDER BY id DESC LIMIT 1
                """,
                product_id,
            )
        if row is None:
            return None
        return StoredOffer(
            id=int(row["id"]),
            product_id=product_id,
            merchant_id=int(row["merchant_id"]),
            current_price=float(row["current_price"]),
            currency=str(row["currency"]),
            stock_status=str(row["stock_status"]),
            content_hash=row["content_hash"],
            freshness_status=str(row["freshness_status"]),
        )

    async def upsert_product(
        self,
        *,
        merchant_id: int,
        plan: ProductUpsertPlan,
        data_quality_status: str,
        status: str,
    ) -> StoredProduct:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO products (
                    merchant_id, external_product_id, merchant_sku, gtin, ean, mpn,
                    model_number, display_name, normalized_name,
                    short_description, full_description, status, data_quality_status,
                    source_url, content_hash, source_reference, attributes,
                    last_seen_at, last_verified_at, updated_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb,
                    NOW(), NOW(), NOW()
                )
                ON CONFLICT (merchant_id, external_product_id) DO UPDATE SET
                    merchant_sku = EXCLUDED.merchant_sku,
                    gtin = EXCLUDED.gtin,
                    ean = EXCLUDED.ean,
                    mpn = EXCLUDED.mpn,
                    model_number = EXCLUDED.model_number,
                    display_name = EXCLUDED.display_name,
                    normalized_name = EXCLUDED.normalized_name,
                    short_description = EXCLUDED.short_description,
                    full_description = EXCLUDED.full_description,
                    status = EXCLUDED.status,
                    data_quality_status = EXCLUDED.data_quality_status,
                    source_url = EXCLUDED.source_url,
                    content_hash = EXCLUDED.content_hash,
                    source_reference = EXCLUDED.source_reference,
                    attributes = EXCLUDED.attributes,
                    last_seen_at = NOW(),
                    last_verified_at = NOW(),
                    updated_at = NOW()
                RETURNING id, merchant_id, external_product_id, display_name,
                          content_hash, data_quality_status, status, attributes
                """,
                merchant_id,
                plan.external_product_id,
                plan.merchant_sku,
                plan.gtin,
                plan.ean,
                plan.mpn,
                plan.model_number,
                plan.display_name,
                plan.normalized_name,
                plan.short_description,
                plan.full_description,
                status,
                data_quality_status,
                plan.source_url,
                plan.content_hash,
                plan.source_reference,
                json.dumps(dict(plan.attributes or {})),
            )
        attrs = row["attributes"]
        if isinstance(attrs, str):
            attrs = json.loads(attrs)
        return StoredProduct(
            id=int(row["id"]),
            merchant_id=int(row["merchant_id"]),
            external_product_id=str(row["external_product_id"]),
            display_name=str(row["display_name"]),
            content_hash=row["content_hash"],
            data_quality_status=str(row["data_quality_status"]),
            status=str(row["status"]),
            attributes=dict(attrs or {}),
        )

    async def upsert_offer(
        self,
        *,
        merchant_id: int,
        product_id: int,
        plan: OfferUpsertResult,
    ) -> StoredOffer:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing_id = await conn.fetchval(
                    """
                    SELECT id FROM product_offers
                    WHERE product_id = $1
                    ORDER BY id DESC LIMIT 1
                    """,
                    product_id,
                )
                if existing_id is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO product_offers (
                            product_id, merchant_id, current_price, list_price, currency,
                            stock_status, checkout_url, freshness_status,
                            content_hash, source_reference, last_verified_at
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                        RETURNING *
                        """,
                        product_id,
                        merchant_id,
                        plan.current_price,
                        plan.list_price,
                        plan.currency,
                        plan.stock_status.value,
                        plan.checkout_url,
                        plan.freshness_status.value,
                        plan.content_hash,
                        plan.source_reference,
                    )
                    offer_id = int(row["id"])
                else:
                    row = await conn.fetchrow(
                        """
                        UPDATE product_offers SET
                            current_price = $2,
                            list_price = $3,
                            currency = $4,
                            stock_status = $5,
                            checkout_url = $6,
                            freshness_status = $7,
                            content_hash = $8,
                            source_reference = $9,
                            last_verified_at = NOW(),
                            updated_at = NOW()
                        WHERE id = $1
                        RETURNING *
                        """,
                        existing_id,
                        plan.current_price,
                        plan.list_price,
                        plan.currency,
                        plan.stock_status.value,
                        plan.checkout_url,
                        plan.freshness_status.value,
                        plan.content_hash,
                        plan.source_reference,
                    )
                    offer_id = int(row["id"])
                if plan.snapshot_required:
                    await conn.execute(
                        """
                        INSERT INTO product_offer_snapshots (
                            offer_id, price, list_price, stock_status, currency,
                            content_hash, source_reference
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                        """,
                        offer_id,
                        plan.current_price,
                        plan.list_price,
                        plan.stock_status.value,
                        plan.currency,
                        plan.content_hash,
                        plan.source_reference,
                    )
                    await conn.execute(
                        """
                        INSERT INTO product_price_history (
                            offer_id, price, list_price, currency,
                            content_hash, source_reference
                        ) VALUES ($1,$2,$3,$4,$5,$6)
                        """,
                        offer_id,
                        plan.current_price,
                        plan.list_price,
                        plan.currency,
                        plan.content_hash,
                        plan.source_reference,
                    )
        return StoredOffer(
            id=int(row["id"]),
            product_id=product_id,
            merchant_id=merchant_id,
            current_price=float(row["current_price"]),
            currency=str(row["currency"]),
            stock_status=str(row["stock_status"]),
            content_hash=row["content_hash"],
            freshness_status=str(row["freshness_status"]),
        )

    async def list_products(
        self, *, merchant_id: Optional[int] = None, limit: int = 100
    ) -> Sequence[StoredProduct]:
        async with self._pool.acquire() as conn:
            if merchant_id is None:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.merchant_id, p.external_product_id, p.display_name,
                           p.content_hash, p.data_quality_status, p.status, p.attributes,
                           COALESCE(p.metadata->>'primary_cdn_url', ma.cdn_url) AS primary_cdn_url,
                           COALESCE(p.metadata->>'primary_media_status', ma.status)
                             AS primary_media_status,
                           p.metadata->>'pending_source_image_url' AS pending_source_image_url,
                           ma.storage_key AS storage_key
                    FROM products p
                    LEFT JOIN product_media_links pml
                      ON pml.product_id = p.id AND pml.is_primary = TRUE
                    LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                    ORDER BY
                      CASE WHEN EXISTS (
                        SELECT 1
                        FROM product_offers po
                        JOIN product_finance_options pfo ON pfo.product_offer_id = po.id
                        WHERE po.product_id = p.id
                          AND pfo.eligibility_status = 'ELIGIBLE'
                          AND pfo.monthly_payment IS NOT NULL
                      ) THEN 0 ELSE 1 END,
                      CASE WHEN COALESCE(p.metadata->>'primary_media_status', ma.status) = 'READY'
                           THEN 0 ELSE 1 END,
                      p.id ASC
                    LIMIT $1
                    """,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.merchant_id, p.external_product_id, p.display_name,
                           p.content_hash, p.data_quality_status, p.status, p.attributes,
                           COALESCE(p.metadata->>'primary_cdn_url', ma.cdn_url) AS primary_cdn_url,
                           COALESCE(p.metadata->>'primary_media_status', ma.status)
                             AS primary_media_status,
                           p.metadata->>'pending_source_image_url' AS pending_source_image_url,
                           ma.storage_key AS storage_key
                    FROM products p
                    LEFT JOIN product_media_links pml
                      ON pml.product_id = p.id AND pml.is_primary = TRUE
                    LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                    WHERE p.merchant_id = $1
                    ORDER BY p.id ASC
                    LIMIT $2
                    """,
                    merchant_id,
                    limit,
                )
        return tuple(_row_to_stored_product(row) for row in rows)

    async def list_products_matching(
        self,
        *,
        name_terms: Sequence[str],
        merchant_id: Optional[int] = None,
        limit: int = 100,
    ) -> Sequence[StoredProduct]:
        terms = [str(t).strip() for t in name_terms if t and str(t).strip()]
        if not terms:
            return await self.list_products(merchant_id=merchant_id, limit=limit)
        # ILIKE any term; prefer finance-ready + media, then id.
        patterns = [f"%{t}%" for t in terms]
        async with self._pool.acquire() as conn:
            if merchant_id is None:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.merchant_id, p.external_product_id, p.display_name,
                           p.content_hash, p.data_quality_status, p.status, p.attributes,
                           COALESCE(p.metadata->>'primary_cdn_url', ma.cdn_url) AS primary_cdn_url,
                           COALESCE(p.metadata->>'primary_media_status', ma.status)
                             AS primary_media_status,
                           p.metadata->>'pending_source_image_url' AS pending_source_image_url,
                           ma.storage_key AS storage_key
                    FROM products p
                    LEFT JOIN product_media_links pml
                      ON pml.product_id = p.id AND pml.is_primary = TRUE
                    LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                    WHERE p.status = 'ACTIVE'
                      AND (
                        SELECT bool_or(p.display_name ILIKE x)
                        FROM unnest($1::text[]) AS x
                      )
                    ORDER BY
                      CASE WHEN EXISTS (
                        SELECT 1
                        FROM product_offers po
                        JOIN product_finance_options pfo ON pfo.product_offer_id = po.id
                        WHERE po.product_id = p.id
                          AND pfo.eligibility_status = 'ELIGIBLE'
                          AND pfo.monthly_payment IS NOT NULL
                      ) THEN 0 ELSE 1 END,
                      CASE WHEN COALESCE(p.metadata->>'primary_media_status', ma.status) = 'READY'
                           THEN 0 ELSE 1 END,
                      p.id ASC
                    LIMIT $2
                    """,
                    patterns,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.merchant_id, p.external_product_id, p.display_name,
                           p.content_hash, p.data_quality_status, p.status, p.attributes,
                           COALESCE(p.metadata->>'primary_cdn_url', ma.cdn_url) AS primary_cdn_url,
                           COALESCE(p.metadata->>'primary_media_status', ma.status)
                             AS primary_media_status,
                           p.metadata->>'pending_source_image_url' AS pending_source_image_url,
                           ma.storage_key AS storage_key
                    FROM products p
                    LEFT JOIN product_media_links pml
                      ON pml.product_id = p.id AND pml.is_primary = TRUE
                    LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                    WHERE p.merchant_id = $1
                      AND p.status = 'ACTIVE'
                      AND (
                        SELECT bool_or(p.display_name ILIKE x)
                        FROM unnest($2::text[]) AS x
                      )
                    ORDER BY p.id ASC
                    LIMIT $3
                    """,
                    merchant_id,
                    patterns,
                    limit,
                )
        return tuple(_row_to_stored_product(row) for row in rows)

    async def get_product(self, product_id: int) -> Optional[StoredProduct]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.id, p.merchant_id, p.external_product_id, p.display_name,
                       p.content_hash, p.data_quality_status, p.status, p.attributes,
                       COALESCE(p.metadata->>'primary_cdn_url', ma.cdn_url) AS primary_cdn_url,
                       COALESCE(p.metadata->>'primary_media_status', ma.status)
                         AS primary_media_status,
                       p.metadata->>'pending_source_image_url' AS pending_source_image_url,
                       ma.storage_key AS storage_key
                FROM products p
                LEFT JOIN product_media_links pml
                  ON pml.product_id = p.id AND pml.is_primary = TRUE
                LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                WHERE p.id = $1
                """,
                product_id,
            )
        if row is None:
            return None
        return _row_to_stored_product(row)

    async def set_pending_source_image(
        self, product_id: int, source_url: str
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE products
                SET metadata = COALESCE(metadata, '{}'::jsonb)
                    || jsonb_build_object('pending_source_image_url', $2::text),
                    updated_at = NOW()
                WHERE id = $1
                """,
                product_id,
                source_url,
            )

    async def attach_primary_media(
        self,
        product_id: int,
        *,
        cdn_url: Optional[str],
        sha256: str,
        status: str,
        source_url: str,
        storage_key: Optional[str] = None,
        mime_type: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        file_size: Optional[int] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                asset_id = await conn.fetchval(
                    "SELECT id FROM media_assets WHERE sha256 = $1",
                    sha256,
                )
                if asset_id is None:
                    asset_id = await conn.fetchval(
                        """
                        INSERT INTO media_assets (
                            source_url, storage_key, cdn_url, mime_type,
                            width, height, file_size, sha256, status,
                            last_verified_at
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
                        RETURNING id
                        """,
                        source_url,
                        storage_key,
                        cdn_url,
                        mime_type,
                        width,
                        height,
                        file_size,
                        sha256,
                        status,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE media_assets SET
                            storage_key = COALESCE($2, storage_key),
                            cdn_url = COALESCE($3, cdn_url),
                            status = $4,
                            last_verified_at = NOW(),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        asset_id,
                        storage_key,
                        cdn_url,
                        status,
                    )
                await conn.execute(
                    """
                    UPDATE product_media_links
                    SET is_primary = FALSE
                    WHERE product_id = $1 AND is_primary = TRUE
                    """,
                    product_id,
                )
                await conn.execute(
                    """
                    INSERT INTO product_media_links (
                        product_id, media_asset_id, media_role,
                        display_order, is_primary
                    ) VALUES ($1,$2,'PRIMARY',0,TRUE)
                    ON CONFLICT (product_id, media_asset_id, media_role)
                    DO UPDATE SET is_primary = TRUE
                    """,
                    product_id,
                    asset_id,
                )
                await conn.execute(
                    """
                    UPDATE products
                    SET metadata = (COALESCE(metadata, '{}'::jsonb)
                        - 'pending_source_image_url')
                        || jsonb_build_object(
                            'primary_cdn_url', to_jsonb($2::text),
                            'primary_media_status', to_jsonb($3::text)
                        ),
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    product_id,
                    cdn_url,
                    status,
                )

    async def mark_offer_stale(self, product_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE product_offers
                SET freshness_status = 'STALE', updated_at = NOW()
                WHERE product_id = $1
                """,
                product_id,
            )

    async def refresh_offer_price(
        self,
        product_id: int,
        *,
        price: float,
        currency: str,
        stock_status: str,
        list_price: Optional[float] = None,
    ) -> Optional[StoredOffer]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE product_offers SET
                    current_price = $2,
                    list_price = COALESCE($3, list_price),
                    currency = $4,
                    stock_status = $5,
                    freshness_status = 'FRESH',
                    last_verified_at = NOW(),
                    updated_at = NOW()
                WHERE id = (
                    SELECT id FROM product_offers
                    WHERE product_id = $1
                    ORDER BY id DESC LIMIT 1
                )
                RETURNING *
                """,
                product_id,
                price,
                list_price,
                currency,
                stock_status,
            )
        if row is None:
            return None
        return StoredOffer(
            id=int(row["id"]),
            product_id=product_id,
            merchant_id=int(row["merchant_id"]),
            current_price=float(row["current_price"]),
            currency=str(row["currency"]),
            stock_status=str(row["stock_status"]),
            content_hash=row["content_hash"],
            freshness_status=str(row["freshness_status"]),
        )


def _status_for_quality(verdict: ProductQualityVerdict) -> tuple[str, str]:
    """Return (data_quality_status, product.status)."""

    dq = verdict.status.value
    if verdict.status is DataQualityStatus.REJECTED:
        return dq, ProductStatus.REJECTED.value
    if verdict.status is DataQualityStatus.QUARANTINED:
        return dq, ProductStatus.QUARANTINED.value
    if verdict.status is DataQualityStatus.PARTIAL:
        return dq, ProductStatus.ACTIVE.value
    return dq, ProductStatus.ACTIVE.value


async def apply_ingestion_to_catalog(
    result: IngestionRunResult,
    *,
    merchant_id: int,
    catalog: ProductCatalogRepository,
    only_chatbot_visible: bool = True,
    finance_deps: Optional[Any] = None,
    merchant_code: Optional[str] = None,
) -> CatalogApplyResult:
    """Plan + apply products/offers from a dry-run or live ingestion result.

    When ``finance_deps`` is set, offer upserts automatically rebuild
    ``product_finance_options`` for chatbot ``best_finance``.
    """

    items: list[ApplyItemResult] = []
    upserted_products = 0
    skipped_unchanged = 0
    skipped_quarantined = 0
    upserted_offers = 0
    finance_pairs: list[tuple[int, int]] = []

    for row in result.items:
        if row.product is None:
            items.append(
                ApplyItemResult(
                    external_product_id=row.external_product_id,
                    product_action="SKIP_NO_PRODUCT",
                    offer_action=None,
                    product_id=None,
                    offer_id=None,
                    quality_status=row.quality.status.value,
                )
            )
            continue

        if only_chatbot_visible and not row.quality.chatbot_visible:
            skipped_quarantined += 1
            items.append(
                ApplyItemResult(
                    external_product_id=row.external_product_id,
                    product_action="SKIP_QUARANTINED",
                    offer_action=None,
                    product_id=None,
                    offer_id=None,
                    quality_status=row.quality.status.value,
                )
            )
            continue

        prev_hash = await catalog.get_product_hash(
            merchant_id=merchant_id,
            external_product_id=row.product.external_product_id,
        )
        product_plan = plan_product_upsert(
            row.product, previous_content_hash=prev_hash
        )
        dq, status = _status_for_quality(row.quality)

        if product_plan.action == "SKIP_UNCHANGED" and prev_hash:
            # Still refresh last_seen via upsert for visibility; treat as skip count.
            stored = await catalog.upsert_product(
                merchant_id=merchant_id,
                plan=product_plan,
                data_quality_status=dq,
                status=status,
            )
            skipped_unchanged += 1
            product_action = "SKIP_UNCHANGED"
        else:
            stored = await catalog.upsert_product(
                merchant_id=merchant_id,
                plan=product_plan,
                data_quality_status=dq,
                status=status,
            )
            upserted_products += 1
            product_action = "UPSERT"

        offer_action = None
        offer_id = None
        if row.offers:
            stock = row.stock[0] if row.stock else None
            prev_offer = await catalog.get_offer_hash(product_id=stored.id)
            offer_plan = plan_offer_upsert(
                row.offers[0], stock, previous_content_hash=prev_offer
            )
            if offer_plan.action == "SKIP_UNCHANGED":
                offer_action = "SKIP_UNCHANGED"
                # Still refresh finance if offer exists (campaign may have changed).
                finance_pairs.append((stored.id, merchant_id))
            else:
                offer = await catalog.upsert_offer(
                    merchant_id=merchant_id,
                    product_id=stored.id,
                    plan=offer_plan,
                )
                offer_id = offer.id
                upserted_offers += 1
                offer_action = "UPSERT"
                finance_pairs.append((stored.id, merchant_id))

        items.append(
            ApplyItemResult(
                external_product_id=row.external_product_id,
                product_action=product_action,
                offer_action=offer_action,
                product_id=stored.id,
                offer_id=offer_id,
                quality_status=row.quality.status.value,
            )
        )

    finance_stats = None
    if finance_deps is not None and finance_pairs:
        from taksitlio.product_query.auto_finance import rebuild_finance_for_products

        finance_stats = await rebuild_finance_for_products(
            finance_deps,
            product_ids=finance_pairs,
            merchant_code=merchant_code,
        )

    return CatalogApplyResult(
        merchant_id=merchant_id,
        upserted_products=upserted_products,
        skipped_unchanged=skipped_unchanged,
        skipped_quarantined=skipped_quarantined,
        upserted_offers=upserted_offers,
        items=tuple(items),
        finance_rebuild=finance_stats,
    )


__all__ = [
    "ApplyItemResult",
    "CatalogApplyResult",
    "InMemoryProductCatalogRepository",
    "PostgresProductCatalogRepository",
    "ProductCatalogRepository",
    "StoredOffer",
    "StoredProduct",
    "apply_ingestion_to_catalog",
]
