#!/usr/bin/env python3
"""Read-only P3.2 probe: DR/network source signals and post-uplift coverage."""
from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlparse

import asyncpg


async def main() -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    async with pool.acquire() as c:
        rows = await c.fetch(
            """
            SELECT m.merchant_code,
              COUNT(*) FILTER (WHERE p.status='ACTIVE') AS active,
              COUNT(*) FILTER (WHERE p.status='ACTIVE' AND p.category_id IS NOT NULL) AS with_cat,
              COUNT(*) FILTER (WHERE p.status='ACTIVE' AND COALESCE(p.brand,'')<>'') AS with_brand
            FROM products p JOIN merchants m ON m.id=p.merchant_id
            WHERE m.merchant_code = ANY($1::text[])
            GROUP BY 1 ORDER BY 1
            """,
            ["m-dr", "m-network", "m-vivense", "m-evofone", "m-civil", "m-hepsiburada", "m-vatan"],
        )
        print("coverage:")
        for r in rows:
            active = int(r["active"] or 0)
            cat = int(r["with_cat"] or 0)
            brand = int(r["with_brand"] or 0)
            print(
                f"  {r['merchant_code']}: active={active} cat={cat}"
                f" ({(cat/active if active else 0):.3f}) brand={brand}"
                f" ({(brand/active if active else 0):.3f})"
            )

        # URL path token histogram for DR / network products missing category
        for code in ("m-dr", "m-network"):
            samples = await c.fetch(
                """
                SELECT p.title, p.product_url, p.attributes::text AS attrs, p.brand, p.category_id
                FROM products p JOIN merchants m ON m.id=p.merchant_id
                WHERE m.merchant_code=$1 AND p.status='ACTIVE' AND p.category_id IS NULL
                ORDER BY p.id LIMIT 40
                """,
                code,
            )
            path_tokens: dict[str, int] = {}
            attr_cats: dict[str, int] = {}
            brand_from_attr = 0
            for s in samples:
                url = s["product_url"] or ""
                path = urlparse(url).path.strip("/").split("/")
                for tok in path[:3]:
                    if tok and len(tok) < 40:
                        path_tokens[tok] = path_tokens.get(tok, 0) + 1
                try:
                    attrs = json.loads(s["attrs"] or "{}")
                except Exception:
                    attrs = {}
                if isinstance(attrs, dict):
                    for k in ("category", "Category", "kategori", "product_type", "breadcrumb"):
                        v = attrs.get(k)
                        if v:
                            attr_cats[str(v)[:80]] = attr_cats.get(str(v)[:80], 0) + 1
                    if not (s["brand"] or "").strip():
                        if attrs.get("brand") or attrs.get("Brand") or attrs.get("marka"):
                            brand_from_attr += 1
            print(f"\n{code} missing-cat sample n={len(samples)}")
            print("  top path tokens:", sorted(path_tokens.items(), key=lambda x: -x[1])[:12])
            print("  attr category-like:", sorted(attr_cats.items(), key=lambda x: -x[1])[:12])
            print("  brand_from_attr among sample:", brand_from_attr)

        # Latest readiness for selected merchants
        cols = await c.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='merchant_readiness_snapshots'
            ORDER BY ordinal_position
            """
        )
        print("\nsnapshot columns:", [r["column_name"] for r in cols])
        if cols:
            latest = await c.fetch(
                """
                SELECT DISTINCT ON (m.merchant_code)
                  m.merchant_code, s.status, s.metrics, s.reasons, s.computed_at
                FROM merchant_readiness_snapshots s
                JOIN merchants m ON m.id = s.merchant_id
                WHERE m.merchant_code = ANY($1::text[])
                ORDER BY m.merchant_code, s.computed_at DESC
                """,
                ["m-dr", "m-network", "m-vatan", "m-hepsiburada"],
            )
            for r in latest:
                print(
                    f"  {r['merchant_code']}: {r['status']} metrics={r['metrics']} reasons={r['reasons']}"
                )
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
