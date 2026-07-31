#!/usr/bin/env python3
"""Deploy smoke: product upsert with brand/category taxonomy (no media)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))
LIMIT = int(os.environ.get("TAXONOMY_BACKFILL_LIMIT", "200"))


async def ensure_merchant(conn, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO merchants (merchant_code, display_name, status)
        VALUES ($1, $2, 'ACTIVE')
        ON CONFLICT (merchant_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
    )
    return int(row["id"])


async def main() -> None:
    import asyncpg
    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.product.catalog import (
        PostgresProductCatalogRepository,
        apply_ingestion_to_catalog,
    )

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    catalog = PostgresProductCatalogRepository(pool)
    async with pool.acquire() as conn:
        for path in sorted(LIVE.glob("src-m-*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            products = raw.get("products") or []
            if not products:
                print({"file": path.name, "skipped": "empty"})
                continue
            code = path.stem.replace("src-", "", 1)
            mid = await ensure_merchant(conn, code, code)
            binding = SourceBinding(
                source_code=path.stem,
                adapter_code="generic.json_feed.v1",
                merchant_id=str(mid),
                config={"feed_path": str(path)},
            )
            result = await run_ingestion_dry(binding, limit=LIMIT)
            applied = await apply_ingestion_to_catalog(
                result, merchant_id=mid, catalog=catalog
            )
            sample = next(
                (p for p in products if p.get("brand") or p.get("category")),
                products[0],
            )
            print(
                {
                    "file": path.name,
                    "merchant_id": mid,
                    "upserted": applied.upserted_products,
                    "sample_brand": sample.get("brand"),
                    "sample_category": sample.get("category"),
                },
                flush=True,
            )

    brands = await pool.fetchval("SELECT count(*) FROM brands")
    with_brand = await pool.fetchval(
        "SELECT count(*) FROM products WHERE brand_id IS NOT NULL"
    )
    with_cat = await pool.fetchval(
        "SELECT count(*) FROM products WHERE category_id IS NOT NULL"
    )
    cats = await pool.fetch(
        "SELECT category_code, display_name FROM categories WHERE status='ACTIVE' ORDER BY id"
    )
    print(
        {
            "summary": True,
            "brands": brands,
            "with_brand": with_brand,
            "with_category": with_cat,
            "categories": [dict(c) for c in cats],
        },
        flush=True,
    )
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
