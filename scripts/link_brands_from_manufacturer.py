#!/usr/bin/env python3
"""Link products.brand_id from manufacturer_name via ensure_brand."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def main() -> None:
    import asyncpg
    from taksitlio.product.taxonomy_pg import ensure_brand

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        """
        SELECT manufacturer_name, count(*)::int AS n
        FROM products
        WHERE brand_id IS NULL
          AND manufacturer_name IS NOT NULL
          AND length(trim(manufacturer_name)) > 0
        GROUP BY manufacturer_name
        ORDER BY n DESC
        """
    )
    print(f"distinct_mfr={len(rows)}", flush=True)
    linked = 0
    created = 0
    for i, row in enumerate(rows, 1):
        name = row["manufacturer_name"]
        bid = await ensure_brand(conn, brand_name=name)
        if bid is None:
            continue
        created += 1
        res = await conn.execute(
            """
            UPDATE products
            SET brand_id = $1, updated_at = NOW()
            WHERE brand_id IS NULL AND manufacturer_name = $2
            """,
            int(bid),
            name,
        )
        linked += int(str(res).split()[-1])
        if i % 100 == 0:
            print({"i": i, "created": created, "linked": linked}, flush=True)
    left = await conn.fetchval("SELECT count(*) FROM products WHERE brand_id IS NULL")
    print({"final_created": created, "linked": linked, "no_brand_left": left}, flush=True)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
