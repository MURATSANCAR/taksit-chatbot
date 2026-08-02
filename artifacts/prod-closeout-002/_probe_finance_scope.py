#!/usr/bin/env python3
"""PROD-CLOSEOUT-002 DB probes — product readiness for finance merchants."""
from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    out: dict = {"merchants": {}}
    try:
        for mid, name in [(20, "Trendyol"), (40, "Evofone"), (8, "MediaMarkt"), (11, "Teknosa")]:
            row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS active_offers,
                  COUNT(*) FILTER (WHERE p.brand_id IS NOT NULL AND p.category_id IS NOT NULL)::int AS branded_catted,
                  COUNT(*) FILTER (
                    WHERE p.brand_id IS NOT NULL AND p.category_id IS NOT NULL
                      AND o.current_price IS NOT NULL AND o.current_price > 0
                      AND o.freshness_status = 'FRESH'
                      AND o.checkout_url IS NOT NULL AND length(o.checkout_url) > 5
                      AND EXISTS (
                        SELECT 1 FROM product_media_links pml
                        JOIN media_assets ma ON ma.id = pml.media_asset_id
                        WHERE pml.product_id = p.id AND pml.is_primary AND ma.status = 'READY'
                      )
                      AND EXISTS (
                        SELECT 1 FROM product_finance_options f
                        WHERE f.product_offer_id = o.id AND f.eligibility_status = 'ELIGIBLE'
                      )
                  )::int AS search_finance_candidate,
                  COUNT(*) FILTER (WHERE p.brand_id IS NULL)::int AS null_brand,
                  COUNT(*) FILTER (WHERE p.category_id IS NULL)::int AS null_cat,
                  COUNT(*) FILTER (
                    WHERE p.brand_id IS NULL AND COALESCE(p.manufacturer_name, '') <> ''
                  )::int AS null_brand_with_mfg,
                  COUNT(*) FILTER (
                    WHERE p.category_id IS NULL
                      AND COALESCE(p.metadata->>'source_category', p.metadata->>'category', '') <> ''
                  )::int AS null_cat_with_source
                FROM products p
                JOIN product_offers o ON o.product_id = p.id
                WHERE o.merchant_id = $1 AND p.status = 'ACTIVE'
                """,
                mid,
            )
            samples = await conn.fetch(
                """
                SELECT LEFT(p.display_name, 80) AS dn,
                       LEFT(COALESCE(p.manufacturer_name, ''), 40) AS mfg,
                       LEFT(COALESCE(p.metadata->>'source_category', p.metadata->>'category', ''), 60) AS src_cat,
                       p.brand_id, p.category_id
                FROM products p
                JOIN product_offers o ON o.product_id = p.id
                WHERE o.merchant_id = $1 AND p.status = 'ACTIVE'
                  AND (p.brand_id IS NULL OR p.category_id IS NULL)
                LIMIT 8
                """,
                mid,
            )
            cats = await conn.fetch(
                """
                SELECT c.display_name, count(*)::int AS n
                FROM products p
                JOIN product_offers o ON o.product_id = p.id
                JOIN categories c ON c.id = p.category_id
                WHERE o.merchant_id = $1 AND p.brand_id IS NOT NULL AND p.category_id IS NOT NULL
                GROUP BY 1 ORDER BY n DESC LIMIT 8
                """,
                mid,
            )
            # brand evidence from title tokens for null-brand products with finance
            title_brand_hints = await conn.fetch(
                """
                SELECT LEFT(p.display_name, 100) AS dn, LEFT(COALESCE(p.manufacturer_name,''),40) AS mfg
                FROM products p
                JOIN product_offers o ON o.product_id = p.id
                WHERE o.merchant_id = $1 AND p.status = 'ACTIVE' AND p.brand_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM product_finance_options f
                    WHERE f.product_offer_id = o.id AND f.eligibility_status = 'ELIGIBLE'
                  )
                LIMIT 10
                """,
                mid,
            )
            out["merchants"][name] = {
                "merchant_id": mid,
                "stats": dict(row),
                "null_samples": [dict(s) for s in samples],
                "top_cats": [dict(x) for x in cats],
                "finance_null_brand_samples": [dict(x) for x in title_brand_hints],
            }

        # search-ready attribute sample
        attrs = await conn.fetch(
            """
            SELECT p.id, LEFT(p.display_name, 80) dn,
                   LEFT(COALESCE(p.attributes::text, ''), 200) attrs
            FROM search_ready_product_projection s
            JOIN products p ON p.id = s.product_id
            WHERE p.attributes IS NOT NULL AND p.attributes::text NOT IN ('{}', 'null', '[]')
            LIMIT 5
            """
        )
        out["search_ready_attr_samples"] = [dict(r) for r in attrs]
        attr_n = await conn.fetchval(
            """
            SELECT COUNT(*) FROM search_ready_product_projection s
            JOIN products p ON p.id = s.product_id
            WHERE p.attributes IS NOT NULL AND p.attributes::text NOT IN ('{}','null','[]')
            """
        )
        out["search_ready_with_attrs"] = int(attr_n or 0)

        # READY merchants today
        ready = await conn.fetch(
            """
            SELECT DISTINCT ON (merchant_id) merchant_id, status
            FROM merchant_readiness_snapshots
            ORDER BY merchant_id, evaluated_at DESC
            """
        )
        out["latest_readiness"] = [
            {"merchant_id": int(r["merchant_id"]), "status": r["status"]}
            for r in ready
            if r["status"] == "READY"
        ]
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
