"""Inventory + data-quality audit against an existing production catalog.

Read-only by default. Use --write-projections only against staging or after
explicit approval — never mutates products / offers / media source rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def _inventory(conn: Any) -> dict[str, Any]:
    counts = await conn.fetch(
        """
        SELECT 'merchants' AS k, count(*)::bigint AS v FROM merchants
        UNION ALL SELECT 'merchants_active', count(*) FROM merchants WHERE status='ACTIVE'
        UNION ALL SELECT 'products', count(*) FROM products
        UNION ALL SELECT 'products_active', count(*) FROM products WHERE status='ACTIVE'
        UNION ALL SELECT 'product_offers', count(*) FROM product_offers
        UNION ALL SELECT 'brands', count(*) FROM brands WHERE status='ACTIVE'
        UNION ALL SELECT 'categories', count(*) FROM categories WHERE status='ACTIVE'
        UNION ALL SELECT 'financial_institutions', count(*) FROM financial_institutions WHERE status='ACTIVE'
        UNION ALL SELECT 'finance_campaigns', count(*) FROM finance_campaigns
        UNION ALL SELECT 'media_assets', count(*) FROM media_assets
        UNION ALL SELECT 'product_media_links', count(*) FROM product_media_links
        UNION ALL SELECT 'media_variants', count(*) FROM media_variants
        UNION ALL SELECT 'brand_aliases', count(*) FROM brand_aliases WHERE status='ACTIVE'
        UNION ALL SELECT 'merchant_aliases', count(*) FROM merchant_aliases WHERE status='ACTIVE'
        """
    )
    base = {str(r["k"]): int(r["v"]) for r in counts}

    cov = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE p.status='ACTIVE') AS active_products,
          count(*) FILTER (WHERE p.status='ACTIVE' AND p.brand_id IS NOT NULL) AS with_brand,
          count(*) FILTER (WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL) AS with_category,
          count(*) FILTER (
            WHERE p.status='ACTIVE'
              AND p.source_url IS NOT NULL
              AND p.source_url ~ '^https?://'
          ) AS with_valid_url,
          count(*) FILTER (
            WHERE p.status='ACTIVE'
              AND p.attributes IS NOT NULL
              AND p.attributes::text NOT IN ('{}', 'null')
          ) AS with_attributes,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND o.stock_status='AVAILABLE'
          ) AS in_stock,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND o.freshness_status='FRESH'
          ) AS fresh_price,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND o.current_price > 0
          ) AS positive_price,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND pml.is_primary AND ma.cdn_url IS NOT NULL
          ) AS primary_image,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND pml.is_primary AND ma.width >= 600 AND ma.height >= 600
          ) AS primary_ge600,
          count(DISTINCT p.id) FILTER (
            WHERE p.status='ACTIVE' AND EXISTS (
              SELECT 1 FROM product_media_links g
              WHERE g.product_id = p.id AND g.is_primary = FALSE
            )
          ) AS with_gallery
        FROM products p
        LEFT JOIN LATERAL (
          SELECT * FROM product_offers po
          WHERE po.product_id = p.id
          ORDER BY po.updated_at DESC NULLS LAST, po.id DESC
          LIMIT 1
        ) o ON TRUE
        LEFT JOIN product_media_links pml
          ON pml.product_id = p.id AND pml.is_primary = TRUE
        LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
        """
    )
    active = max(int(cov["active_products"] or 0), 1)

    quality_flags = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE display_name IS NULL OR length(trim(display_name))=0) AS empty_name,
          count(*) FILTER (WHERE merchant_id IS NULL) AS no_merchant,
          count(*) FILTER (WHERE brand_id IS NULL) AS no_brand,
          count(*) FILTER (WHERE category_id IS NULL) AS no_category,
          count(*) FILTER (WHERE source_url IS NULL OR source_url !~ '^https?://') AS bad_or_missing_url,
          count(*) FILTER (
            WHERE gtin IS NOT NULL
              AND length(regexp_replace(gtin, '[^0-9]', '', 'g')) NOT IN (8,12,13,14)
          ) AS invalid_gtin_format
        FROM products
        WHERE status='ACTIVE'
        """
    )
    offer_flags = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE current_price <= 0) AS nonpositive_price,
          count(*) FILTER (WHERE currency IS NULL OR currency NOT IN ('TRY','USD','EUR')) AS bad_currency,
          count(*) FILTER (WHERE stock_status='AVAILABLE' AND current_price IS NULL) AS stock_no_price
        FROM product_offers
        """
    )
    media_flags = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE status='QUARANTINED' OR status='FAILED' OR status='REJECTED') AS broken_or_quarantined,
          count(*) FILTER (WHERE mime_type IS NULL OR mime_type NOT ILIKE 'image/%') AS non_image_mime,
          count(*) FILTER (WHERE width IS NOT NULL AND height IS NOT NULL AND (width < 600 OR height < 600)) AS below_600
        FROM media_assets
        """
    )
    merchants = await conn.fetch(
        """
        SELECT m.id, m.merchant_code, m.display_name, m.status, count(p.id)::bigint AS products
        FROM merchants m
        LEFT JOIN products p ON p.merchant_id = m.id AND p.status='ACTIVE'
        GROUP BY m.id
        ORDER BY products DESC, m.id
        """
    )

    def _pct(n: int) -> float:
        return round(100.0 * n / active, 2)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": base,
        "coverage": {
            "primary_image_pct": _pct(int(cov["primary_image"] or 0)),
            "primary_image_ge600_pct": _pct(int(cov["primary_ge600"] or 0)),
            "gallery_image_pct": _pct(int(cov["with_gallery"] or 0)),
            "valid_product_url_pct": _pct(int(cov["with_valid_url"] or 0)),
            "fresh_price_pct": _pct(int(cov["fresh_price"] or 0)),
            "stock_available_pct": _pct(int(cov["in_stock"] or 0)),
            "brand_pct": _pct(int(cov["with_brand"] or 0)),
            "category_pct": _pct(int(cov["with_category"] or 0)),
            "attribute_pct": _pct(int(cov["with_attributes"] or 0)),
            "positive_price_pct": _pct(int(cov["positive_price"] or 0)),
        },
        "raw_coverage_counts": {k: int(cov[k] or 0) for k in cov.keys()},
        "quality_scan": {
            "products": {k: int(quality_flags[k] or 0) for k in quality_flags.keys()},
            "offers": {k: int(offer_flags[k] or 0) for k in offer_flags.keys()},
            "media": {k: int(media_flags[k] or 0) for k in media_flags.keys()},
        },
        "merchants": [
            {
                "id": int(r["id"]),
                "merchant_code": r["merchant_code"],
                "display_name": r["display_name"],
                "status": r["status"],
                "active_products": int(r["products"]),
            }
            for r in merchants
        ],
        "field_mapping": [
            {
                "concept": "product_id",
                "table": "products",
                "column": "id",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "merchant_id",
                "table": "products",
                "column": "merchant_id",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "external_product_id",
                "table": "products",
                "column": "external_product_id",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "merchant_sku",
                "table": "products",
                "column": "merchant_sku",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "gtin/ean",
                "table": "products",
                "column": "gtin / ean",
                "quality": "SPARSE",
                "gap": "Most rows null",
                "suggested_change": "Backfill from feeds when present; no invent",
            },
            {
                "concept": "brand",
                "table": "products / brands",
                "column": "brand_id",
                "quality": "LOW_COVERAGE",
                "gap": "~11% linked",
                "suggested_change": "Taxonomy bridge + brand upsert from attributes",
            },
            {
                "concept": "category",
                "table": "products / categories",
                "column": "category_id",
                "quality": "VERY_LOW",
                "gap": "<1% linked",
                "suggested_change": "Feed taxonomy → categories mapping (no hardcode)",
            },
            {
                "concept": "product title",
                "table": "products",
                "column": "display_name",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "description",
                "table": "products",
                "column": "short_description / full_description",
                "quality": "UNKNOWN",
                "gap": "Not required for first-card path",
                "suggested_change": None,
            },
            {
                "concept": "attributes",
                "table": "products",
                "column": "attributes JSONB",
                "quality": "PARTIAL",
                "gap": "~14% non-empty",
                "suggested_change": "Normalize into product_attribute_values later",
            },
            {
                "concept": "current price",
                "table": "product_offers",
                "column": "current_price",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "list price",
                "table": "product_offers",
                "column": "list_price",
                "quality": "SPARSE",
                "gap": "Often null",
                "suggested_change": None,
            },
            {
                "concept": "stock status",
                "table": "product_offers",
                "column": "stock_status",
                "quality": "MOSTLY_UNKNOWN",
                "gap": "Majority UNKNOWN",
                "suggested_change": "Feed stock capability; do not invent AVAILABLE",
            },
            {
                "concept": "product URL",
                "table": "products",
                "column": "source_url",
                "quality": "OK_FORMAT",
                "gap": "Live HTTP reachability not batch-probed in this pass",
                "suggested_change": "Optional async URL probe job (separate task)",
            },
            {
                "concept": "primary image",
                "table": "product_media_links + media_assets",
                "column": "is_primary / cdn_url",
                "quality": "GOOD",
                "gap": "~24% missing primary; many <600px",
                "suggested_change": "Quality projection flags; no re-download in this task",
            },
            {
                "concept": "gallery images",
                "table": "product_media_links",
                "column": "is_primary=false",
                "quality": "SPARSE",
                "gap": "Few non-primary links",
                "suggested_change": None,
            },
            {
                "concept": "source updated time",
                "table": "products",
                "column": "source_updated_at",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
            {
                "concept": "last verified time",
                "table": "products / product_offers",
                "column": "last_verified_at",
                "quality": "OK",
                "gap": None,
                "suggested_change": None,
            },
        ],
    }


async def _run(args: argparse.Namespace) -> int:
    dsn = args.database_url or os.environ.get("DATABASE_URL") or os.environ.get("PGVECTOR_URL")
    if not dsn:
        print("DATABASE_URL required", file=sys.stderr)
        return 2

    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        report = await _inventory(conn)
        report["mode"] = "read_only"
        report["write_projections"] = False

        if args.write_projections:
            if not args.allow_write:
                print(
                    "Refusing projection write without --allow-write "
                    "(projection tables only; source catalog untouched).",
                    file=sys.stderr,
                )
                return 3
            # Use a pool for repository helpers
            from taksitlio.catalog_projection import CatalogProjectionRepository

            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
            try:
                repo = CatalogProjectionRepository(pool)
                stats = await repo.rebuild_all(catalog_revision=int(args.catalog_revision))
                report["mode"] = "projections_rebuilt"
                report["write_projections"] = True
                report["projection_stats"] = stats.to_dict()
                dq = await pool.fetch(
                    """
                    SELECT data_quality_status, count(*)::bigint AS n
                    FROM product_data_quality_projection
                    GROUP BY 1 ORDER BY 1
                    """
                )
                report["quality_projection_counts"] = {
                    str(r["data_quality_status"]): int(r["n"]) for r in dq
                }
            finally:
                await pool.close()
    finally:
        await conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "mode": report["mode"]}, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=None)
    p.add_argument(
        "--out",
        default=str(ROOT / "evaluation" / "reports" / "adr010-011-prod-inventory.json"),
    )
    p.add_argument(
        "--write-projections",
        action="store_true",
        help="Rebuild product_search_projection / entity_search_index / quality projection",
    )
    p.add_argument(
        "--allow-write",
        action="store_true",
        help="Required together with --write-projections (staging/controlled)",
    )
    p.add_argument("--catalog-revision", type=int, default=1)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
