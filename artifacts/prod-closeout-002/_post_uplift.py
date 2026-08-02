#!/usr/bin/env python3
import asyncio, asyncpg, os, json

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    for mid, name in [(8, "MM"), (11, "Tek"), (20, "TY"), (40, "Evo")]:
        row = await c.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE p.brand_id IS NOT NULL)::int AS with_brand,
              COUNT(*) FILTER (WHERE p.category_id IS NOT NULL)::int AS with_cat,
              COUNT(*)::int AS n,
              COUNT(*) FILTER (WHERE p.merchant_id = $1)::int AS p_mid_match,
              COUNT(*) FILTER (WHERE o.merchant_id = $1 AND p.merchant_id IS DISTINCT FROM o.merchant_id)::int AS mid_mismatch
            FROM products p
            JOIN product_offers o ON o.product_id = p.id
            WHERE o.merchant_id = $1 AND p.status = 'ACTIVE'
            """,
            mid,
        )
        snap = await c.fetchrow(
            """
            SELECT status, brand_coverage, category_coverage, attribute_coverage, evaluated_at
            FROM merchant_readiness_snapshots
            WHERE merchant_id=$1 ORDER BY evaluated_at DESC LIMIT 1
            """,
            mid,
        )
        print(name, dict(row), "snap", dict(snap) if snap else None)
    await c.close()

asyncio.run(main())
