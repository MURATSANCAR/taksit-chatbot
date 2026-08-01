"""Rebuildable catalog search / entity / quality projections (ADR-010).

Source of truth: products, product_offers, merchants, brands, categories, media.
Projection tables are truncated and rebuilt — never mutate source rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from taksitlio.data_quality import (
    DataQualityStatus,
    ProductQualitySignals,
    ProductQualityVerdict,
    score_product_quality,
)
from taksitlio.entity_resolution import EntityCandidate
from taksitlio.semantic_matching.turkish_normalize import normalize_turkish


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return normalize_turkish(str(text)).value or ""


def _gtin_valid(raw: str | None) -> bool:
    if raw is None or not str(raw).strip():
        return True  # absence is not invalid format
    digits = "".join(c for c in str(raw) if c.isdigit())
    return len(digits) in {8, 12, 13, 14} and digits == str(raw).strip()


@dataclass(frozen=True)
class ProjectionRebuildStats:
    search_rows: int = 0
    entity_rows: int = 0
    quality_rows: int = 0
    ready: int = 0
    partial: int = 0
    quarantined: int = 0
    rejected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_rows": self.search_rows,
            "entity_rows": self.entity_rows,
            "quality_rows": self.quality_rows,
            "ready": self.ready,
            "partial": self.partial,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
        }


class CatalogProjectionRepository:
    """Postgres-backed rebuild + read helpers for V027 projections."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def rebuild_all(self, *, catalog_revision: int = 1) -> ProjectionRebuildStats:
        quality = await self.rebuild_quality_projection()
        search = await self.rebuild_search_projection(catalog_revision=catalog_revision)
        entities = await self.rebuild_entity_index(catalog_revision=catalog_revision)
        return ProjectionRebuildStats(
            search_rows=search,
            entity_rows=entities,
            quality_rows=quality["total"],
            ready=quality["ready"],
            partial=quality["partial"],
            quarantined=quality["quarantined"],
            rejected=quality["rejected"],
        )

    async def rebuild_search_projection(self, *, catalog_revision: int = 1) -> int:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("TRUNCATE product_search_projection")
                rows = await conn.execute(
                    """
                    INSERT INTO product_search_projection (
                        product_id, offer_id, merchant_id, merchant_name,
                        merchant_alias_document, brand_id, brand_name,
                        category_id, category_path, product_name,
                        normalized_product_name, search_document,
                        current_price, list_price, currency, stock_status,
                        primary_image_url, product_url, attribute_document,
                        price_updated_at, stock_updated_at, product_updated_at,
                        data_quality_status, price_freshness, catalog_revision, rebuilt_at
                    )
                    SELECT
                        p.id,
                        o.id,
                        p.merchant_id,
                        COALESCE(NULLIF(m.display_name, ''), m.merchant_code),
                        COALESCE((
                            SELECT string_agg(DISTINCT a.alias_text, ' ')
                            FROM merchant_aliases a
                            WHERE a.merchant_id = m.id AND a.status = 'ACTIVE'
                        ), ''),
                        p.brand_id,
                        b.display_name,
                        p.category_id,
                        c.display_name,
                        p.display_name,
                        COALESCE(p.normalized_name, lower(p.display_name)),
                        trim(concat_ws(
                            ' ',
                            p.display_name,
                            p.normalized_name,
                            p.model_number,
                            b.display_name,
                            c.display_name,
                            m.display_name,
                            m.merchant_code,
                            p.attributes::text
                        )),
                        o.current_price,
                        o.list_price,
                        COALESCE(o.currency, 'TRY'),
                        COALESCE(o.stock_status, 'UNKNOWN'),
                        CASE
                            WHEN ma.status = 'READY' THEN ma.cdn_url
                            ELSE NULL
                        END,
                        p.source_url,
                        COALESCE(p.attributes::text, '{}'),
                        o.updated_at,
                        o.updated_at,
                        p.updated_at,
                        COALESCE(q.data_quality_status, p.data_quality_status, 'PARTIAL'),
                        COALESCE(o.freshness_status, 'UNVERIFIED'),
                        $1,
                        NOW()
                    FROM products p
                    JOIN merchants m ON m.id = p.merchant_id
                    LEFT JOIN brands b ON b.id = p.brand_id
                    LEFT JOIN categories c ON c.id = p.category_id
                    LEFT JOIN LATERAL (
                        SELECT *
                        FROM product_offers po
                        WHERE po.product_id = p.id
                        ORDER BY po.updated_at DESC NULLS LAST, po.id DESC
                        LIMIT 1
                    ) o ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT pml.media_asset_id
                        FROM product_media_links pml
                        WHERE pml.product_id = p.id AND pml.is_primary = TRUE
                        ORDER BY pml.id ASC
                        LIMIT 1
                    ) pml ON TRUE
                    LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                    LEFT JOIN product_data_quality_projection q ON q.product_id = p.id
                    WHERE p.status = 'ACTIVE'
                    """,
                    catalog_revision,
                )
                # asyncpg execute returns status like "INSERT 0 N"
                return int(str(rows).split()[-1])

    async def rebuild_entity_index(self, *, catalog_revision: int = 1) -> int:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("TRUNCATE entity_search_index RESTART IDENTITY")
                total = 0
                # Merchants
                total += await self._insert_entities(
                    conn,
                    """
                    SELECT 'MERCHANT'::text, m.id::text, m.display_name, m.merchant_code,
                           COALESCE(a.alias_text, ''), m.status
                    FROM merchants m
                    LEFT JOIN merchant_aliases a
                      ON a.merchant_id = m.id AND a.status = 'ACTIVE'
                    WHERE m.status = 'ACTIVE'
                    """,
                    catalog_revision,
                    derive_code_aliases=True,
                )
                # Brands
                total += await self._insert_entities(
                    conn,
                    """
                    SELECT 'BRAND'::text, b.id::text, b.display_name, b.brand_code,
                           COALESCE(a.alias_text, ''), b.status
                    FROM brands b
                    LEFT JOIN brand_aliases a
                      ON a.brand_id = b.id AND a.status = 'ACTIVE'
                    WHERE b.status = 'ACTIVE'
                    """,
                    catalog_revision,
                )
                # Categories — synonyms[] on `categories` (V003); catalog_categories aliases are UUID-scoped
                cat_rows = await conn.fetch(
                    """
                    SELECT id::text AS entity_id, display_name, category_code, synonyms, status
                    FROM categories
                    WHERE status = 'ACTIVE'
                    """
                )
                cat_payload: list[tuple[Any, ...]] = []
                cat_seen: set[tuple[str, str]] = set()
                for crow in cat_rows:
                    canonical = str(crow["display_name"] or crow["category_code"])
                    norm_name = _norm(canonical)
                    aliases = {str(crow["category_code"] or "")}
                    for syn in crow["synonyms"] or []:
                        if syn:
                            aliases.add(str(syn))
                    aliases.add("")
                    for al in aliases:
                        norm_alias = _norm(al) if al else ""
                        key = (str(crow["entity_id"]), norm_alias)
                        if key in cat_seen:
                            continue
                        cat_seen.add(key)
                        cat_payload.append(
                            (
                                "CATEGORY",
                                str(crow["entity_id"]),
                                canonical,
                                norm_name,
                                al,
                                norm_alias,
                                catalog_revision,
                                "ACTIVE",
                            )
                        )
                if cat_payload:
                    await conn.executemany(
                        """
                        INSERT INTO entity_search_index (
                            entity_type, entity_id, canonical_name, normalized_name,
                            alias, normalized_alias, catalog_revision, status, rebuilt_at
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8, NOW())
                        ON CONFLICT (entity_type, entity_id, normalized_alias) DO UPDATE SET
                            canonical_name = EXCLUDED.canonical_name,
                            normalized_name = EXCLUDED.normalized_name,
                            alias = EXCLUDED.alias,
                            catalog_revision = EXCLUDED.catalog_revision,
                            status = EXCLUDED.status,
                            rebuilt_at = NOW()
                        """,
                        cat_payload,
                    )
                    total += len(cat_payload)
                # Financial institutions
                total += await self._insert_entities(
                    conn,
                    """
                    SELECT 'FINANCIAL_INSTITUTION'::text, f.id::text, f.display_name,
                           f.institution_code, COALESCE(a.alias_text, ''), f.status
                    FROM financial_institutions f
                    LEFT JOIN financial_institution_aliases a
                      ON a.institution_id = f.id AND a.status = 'ACTIVE'
                    WHERE f.status = 'ACTIVE'
                    """,
                    catalog_revision,
                )
                # Products (canonical name only — aliases empty; keeps index bounded)
                inserted = await conn.execute(
                    """
                    INSERT INTO entity_search_index (
                        entity_type, entity_id, canonical_name, normalized_name,
                        alias, normalized_alias, catalog_revision, status, rebuilt_at
                    )
                    SELECT
                        'PRODUCT',
                        p.id::text,
                        p.display_name,
                        lower(COALESCE(p.normalized_name, p.display_name)),
                        '',
                        '',
                        $1,
                        'ACTIVE',
                        NOW()
                    FROM products p
                    WHERE p.status = 'ACTIVE'
                      AND COALESCE(
                            (SELECT q.data_quality_status
                             FROM product_data_quality_projection q
                             WHERE q.product_id = p.id),
                            p.data_quality_status,
                            'PARTIAL'
                          ) IN ('READY', 'PARTIAL')
                    """,
                    catalog_revision,
                )
                total += int(str(inserted).split()[-1])
                return total

    async def _insert_entities(
        self,
        conn: Any,
        select_sql: str,
        catalog_revision: int,
        *,
        derive_code_aliases: bool = False,
    ) -> int:
        rows = await conn.fetch(select_sql)
        if not rows:
            return 0
        payload = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            entity_type, entity_id, display, code, alias, status = row
            if status not in ("ACTIVE",):
                continue
            canonical = str(display or code or entity_id)
            norm_name = _norm(canonical)
            aliases = {str(alias or "").strip(), str(code or "").strip()}
            if derive_code_aliases:
                code_s = str(code or "").strip()
                if code_s.lower().startswith("m-") and len(code_s) > 2:
                    aliases.add(code_s[2:])  # m-teknosa → teknosa (catalog-derived)
                if canonical.lower().startswith("m-") and len(canonical) > 2:
                    aliases.add(canonical[2:])
            aliases.discard("")
            aliases.add("")  # canonical-only row
            for al in aliases:
                norm_alias = _norm(al) if al else ""
                key = (str(entity_type), str(entity_id), norm_alias)
                if key in seen:
                    continue
                seen.add(key)
                payload.append(
                    (
                        str(entity_type),
                        str(entity_id),
                        canonical,
                        norm_name,
                        al,
                        norm_alias,
                        catalog_revision,
                        "ACTIVE",
                    )
                )
        if not payload:
            return 0
        await conn.executemany(
            """
            INSERT INTO entity_search_index (
                entity_type, entity_id, canonical_name, normalized_name,
                alias, normalized_alias, catalog_revision, status, rebuilt_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8, NOW())
            ON CONFLICT (entity_type, entity_id, normalized_alias) DO UPDATE SET
                canonical_name = EXCLUDED.canonical_name,
                normalized_name = EXCLUDED.normalized_name,
                alias = EXCLUDED.alias,
                catalog_revision = EXCLUDED.catalog_revision,
                status = EXCLUDED.status,
                rebuilt_at = NOW()
            """,
            payload,
        )
        return len(payload)

    async def rebuild_quality_projection(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            products = await conn.fetch(
                """
                SELECT
                    p.id AS product_id,
                    p.merchant_id,
                    p.external_product_id,
                    p.merchant_sku,
                    p.gtin,
                    p.ean,
                    p.brand_id,
                    p.category_id,
                    p.display_name,
                    p.source_url,
                    p.status AS product_status,
                    p.data_quality_status AS existing_dq,
                    o.id AS offer_id,
                    o.current_price,
                    o.currency,
                    o.stock_status,
                    o.freshness_status,
                    o.updated_at AS offer_updated_at,
                    o.last_verified_at AS offer_verified_at,
                    ma.id AS media_id,
                    ma.status AS media_status,
                    ma.cdn_url,
                    ma.width,
                    ma.height,
                    ma.mime_type
                FROM products p
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM product_offers po
                    WHERE po.product_id = p.id
                    ORDER BY po.updated_at DESC NULLS LAST, po.id DESC
                    LIMIT 1
                ) o ON TRUE
                LEFT JOIN LATERAL (
                    SELECT pml.media_asset_id
                    FROM product_media_links pml
                    WHERE pml.product_id = p.id AND pml.is_primary = TRUE
                    ORDER BY pml.id ASC
                    LIMIT 1
                ) pml ON TRUE
                LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
                """
            )
            # Duplicate detection maps
            ext_counts: dict[tuple[int, str], int] = {}
            sku_counts: dict[tuple[int, str], int] = {}
            for row in products:
                mid = int(row["merchant_id"])
                ext = str(row["external_product_id"] or "")
                sku = str(row["merchant_sku"] or "").strip()
                ext_counts[(mid, ext)] = ext_counts.get((mid, ext), 0) + 1
                if sku:
                    sku_counts[(mid, sku)] = sku_counts.get((mid, sku), 0) + 1

            await conn.execute("TRUNCATE product_data_quality_projection")
            counts = {"total": 0, "ready": 0, "partial": 0, "quarantined": 0, "rejected": 0}
            batch: list[tuple[Any, ...]] = []
            for row in products:
                verdict, flags = _audit_row(
                    row,
                    dup_external=ext_counts.get(
                        (int(row["merchant_id"]), str(row["external_product_id"] or "")),
                        0,
                    )
                    > 1,
                    dup_sku=(
                        bool(str(row["merchant_sku"] or "").strip())
                        and sku_counts.get(
                            (
                                int(row["merchant_id"]),
                                str(row["merchant_sku"] or "").strip(),
                            ),
                            0,
                        )
                        > 1
                    ),
                )
                counts["total"] += 1
                counts[verdict.status.value.lower()] += 1
                batch.append(
                    (
                        int(row["product_id"]),
                        int(row["offer_id"]) if row["offer_id"] is not None else None,
                        verdict.status.value,
                        float(verdict.score),
                        bool(verdict.chatbot_visible),
                        list(verdict.reasons),
                        flags["empty_name"],
                        flags["missing_merchant"],
                        flags["missing_category"],
                        flags["missing_brand"],
                        flags["invalid_price"],
                        flags["invalid_currency"],
                        flags["invalid_url_format"],
                        flags["missing_primary_image"],
                        flags["image_below_min_size"],
                        flags["duplicate_external_id"],
                        flags["duplicate_merchant_sku"],
                        flags["invalid_gtin"],
                        flags["active_without_offer"],
                        flags["in_stock_without_price"],
                        flags["missing_price_updated_at"],
                        json.dumps(dict(verdict.diagnostics)),
                    )
                )
            if batch:
                await conn.executemany(
                    """
                    INSERT INTO product_data_quality_projection (
                        product_id, offer_id, data_quality_status, score, chatbot_visible,
                        reasons, empty_name, missing_merchant, missing_category, missing_brand,
                        invalid_price, invalid_currency, invalid_url_format,
                        missing_primary_image, image_below_min_size,
                        duplicate_external_id, duplicate_merchant_sku, invalid_gtin,
                        active_without_offer, in_stock_without_price,
                        missing_price_updated_at, diagnostics, audited_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22::jsonb, NOW()
                    )
                    """,
                    batch,
                )
            return counts

    async def load_entity_candidates(
        self, entity_type: str, *, limit: int = 5000
    ) -> tuple[EntityCandidate, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT entity_id, canonical_name,
                       array_agg(DISTINCT NULLIF(alias, ''))
                         FILTER (WHERE alias IS NOT NULL AND alias <> '') AS aliases
                FROM entity_search_index
                WHERE entity_type = $1 AND status = 'ACTIVE'
                GROUP BY entity_id, canonical_name
                ORDER BY canonical_name
                LIMIT $2
                """,
                entity_type,
                limit,
            )
        out: list[EntityCandidate] = []
        for row in rows:
            aliases = tuple(a for a in (row["aliases"] or []) if a)
            out.append(
                EntityCandidate(
                    entity_id=str(row["entity_id"]),
                    display_name=str(row["canonical_name"]),
                    canonical_name=str(row["canonical_name"]),
                    aliases=aliases,
                    entity_type=entity_type.lower(),
                )
            )
        return tuple(out)

    async def search_products(
        self,
        *,
        name_terms: Sequence[str] = (),
        merchant_id: Optional[int] = None,
        brand_id: Optional[int] = None,
        category_id: Optional[int] = None,
        max_price: Optional[float] = None,
        limit: int = 50,
    ) -> Sequence[Mapping[str, Any]]:
        clauses = [
            "data_quality_status IN ('READY', 'PARTIAL')",
            "current_price IS NOT NULL",
            "current_price > 0",
        ]
        args: list[Any] = []
        if merchant_id is not None:
            args.append(merchant_id)
            clauses.append(f"merchant_id = ${len(args)}")
        if brand_id is not None:
            args.append(brand_id)
            clauses.append(f"brand_id = ${len(args)}")
        if category_id is not None:
            args.append(category_id)
            clauses.append(f"category_id = ${len(args)}")
        if max_price is not None:
            args.append(max_price)
            clauses.append(f"current_price <= ${len(args)}")
        terms = [t.strip() for t in name_terms if t and str(t).strip()]
        if terms:
            term_parts = []
            for t in terms:
                args.append(f"%{t}%")
                term_parts.append(
                    f"(product_name ILIKE ${len(args)} OR search_document ILIKE ${len(args)})"
                )
            clauses.append("(" + " OR ".join(term_parts) + ")")
        args.append(limit)
        sql = f"""
            SELECT *
            FROM product_search_projection
            WHERE {' AND '.join(clauses)}
            ORDER BY
              CASE WHEN primary_image_url IS NOT NULL THEN 0 ELSE 1 END,
              current_price ASC NULLS LAST,
              product_id ASC
            LIMIT ${len(args)}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return tuple(dict(r) for r in rows)


def _audit_row(
    row: Mapping[str, Any],
    *,
    dup_external: bool,
    dup_sku: bool,
) -> tuple[Any, dict[str, bool]]:
    name = str(row.get("display_name") or "").strip()
    empty_name = not name
    missing_merchant = row.get("merchant_id") is None
    missing_category = row.get("category_id") is None
    missing_brand = row.get("brand_id") is None
    price = row.get("current_price")
    invalid_price = price is None or float(price) <= 0
    currency = str(row.get("currency") or "")
    invalid_currency = currency not in {"TRY", "USD", "EUR"}
    url = str(row.get("source_url") or "").strip()
    invalid_url = bool(url) and not (
        url.startswith("http://") or url.startswith("https://")
    )
    missing_primary = row.get("media_id") is None or not row.get("cdn_url")
    width = row.get("width")
    height = row.get("height")
    image_small = False
    if width is not None and height is not None:
        image_small = int(width) < 600 or int(height) < 600
    gtin = row.get("gtin") or row.get("ean")
    invalid_gtin = not _gtin_valid(str(gtin) if gtin is not None else None)
    active_no_offer = (
        str(row.get("product_status")) == "ACTIVE" and row.get("offer_id") is None
    )
    stock = str(row.get("stock_status") or "UNKNOWN")
    in_stock_no_price = stock == "AVAILABLE" and invalid_price
    missing_price_ts = row.get("offer_updated_at") is None and row.get("offer_id") is not None

    signals = ProductQualitySignals(
        has_external_id=bool(row.get("external_product_id")),
        has_display_name=not empty_name,
        has_price=price is not None,
        price_positive=not invalid_price,
        has_currency=not invalid_currency,
        has_stock_status=bool(row.get("stock_status")),
        stock_known=stock in {"AVAILABLE", "LIMITED", "OUT_OF_STOCK"},
        has_primary_image=not missing_primary,
        image_cdn_ready=str(row.get("media_status") or "") == "READY"
        and bool(row.get("cdn_url")),
        has_source_reference=bool(url),
        price_fresh=str(row.get("freshness_status") or "") == "FRESH",
        duplicate_suspected=dup_external or dup_sku,
        schema_invalid=invalid_url or invalid_gtin or invalid_currency,
    )
    verdict = score_product_quality(signals)
    # Soft flags for projection — do not escalate PARTIAL solely for missing brand/category
    if empty_name or missing_merchant or invalid_price or active_no_offer:
        if verdict.status == DataQualityStatus.READY:
            verdict = ProductQualityVerdict(
                status=DataQualityStatus.QUARANTINED,
                score=min(verdict.score, 0.2),
                reasons=tuple(
                    dict.fromkeys(
                        list(verdict.reasons)
                        + (["empty_name"] if empty_name else [])
                        + (["missing_merchant"] if missing_merchant else [])
                        + (["invalid_price"] if invalid_price else [])
                        + (["active_without_offer"] if active_no_offer else [])
                    )
                ),
                chatbot_visible=False,
                diagnostics=verdict.diagnostics,
            )

    flags = {
        "empty_name": empty_name,
        "missing_merchant": missing_merchant,
        "missing_category": missing_category,
        "missing_brand": missing_brand,
        "invalid_price": invalid_price,
        "invalid_currency": invalid_currency,
        "invalid_url_format": invalid_url,
        "missing_primary_image": missing_primary,
        "image_below_min_size": image_small,
        "duplicate_external_id": dup_external,
        "duplicate_merchant_sku": dup_sku,
        "invalid_gtin": invalid_gtin and gtin is not None,
        "active_without_offer": active_no_offer,
        "in_stock_without_price": in_stock_no_price,
        "missing_price_updated_at": missing_price_ts,
    }
    return verdict, flags
