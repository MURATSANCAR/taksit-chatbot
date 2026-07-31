"""Apply product/offer upsert plans to a catalog store (ADR-010 P8).

No demo seed — only rows produced from verified ingestion snapshots.
"""

from __future__ import annotations

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


class ProductCatalogRepository(Protocol):
    async def get_product_hash(
        self, *, merchant_id: int, external_product_id: str
    ) -> Optional[str]: ...

    async def get_offer_hash(self, *, product_id: int) -> Optional[str]: ...

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


class InMemoryProductCatalogRepository:
    def __init__(self) -> None:
        self._products: dict[tuple[int, str], StoredProduct] = {}
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
        )
        self._products[key] = stored
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
                dict(plan.attributes),
            )
        attrs = row["attributes"]
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
                    SELECT id, merchant_id, external_product_id, display_name,
                           content_hash, data_quality_status, status, attributes
                    FROM products ORDER BY id ASC LIMIT $1
                    """,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, merchant_id, external_product_id, display_name,
                           content_hash, data_quality_status, status, attributes
                    FROM products WHERE merchant_id = $1
                    ORDER BY id ASC LIMIT $2
                    """,
                    merchant_id,
                    limit,
                )
        out = []
        for row in rows:
            attrs = row["attributes"]
            out.append(
                StoredProduct(
                    id=int(row["id"]),
                    merchant_id=int(row["merchant_id"]),
                    external_product_id=str(row["external_product_id"]),
                    display_name=str(row["display_name"]),
                    content_hash=row["content_hash"],
                    data_quality_status=str(row["data_quality_status"]),
                    status=str(row["status"]),
                    attributes=dict(attrs or {}),
                )
            )
        return tuple(out)


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
) -> CatalogApplyResult:
    """Plan + apply products/offers from a dry-run or live ingestion result."""

    items: list[ApplyItemResult] = []
    upserted_products = 0
    skipped_unchanged = 0
    skipped_quarantined = 0
    upserted_offers = 0

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
            else:
                offer = await catalog.upsert_offer(
                    merchant_id=merchant_id,
                    product_id=stored.id,
                    plan=offer_plan,
                )
                offer_id = offer.id
                upserted_offers += 1
                offer_action = "UPSERT"

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

    return CatalogApplyResult(
        merchant_id=merchant_id,
        upserted_products=upserted_products,
        skipped_unchanged=skipped_unchanged,
        skipped_quarantined=skipped_quarantined,
        upserted_offers=upserted_offers,
        items=tuple(items),
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
