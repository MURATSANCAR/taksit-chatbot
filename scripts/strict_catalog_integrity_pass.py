#!/usr/bin/env python3
"""Strict integrity pass: READY only with category + real CDN READY asset."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def main() -> None:
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    q = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'QUARANTINED',
          metadata = COALESCE(p.metadata, '{}'::jsonb)
            || jsonb_build_object(
                 'integrity_block',
                 CASE WHEN p.category_id IS NULL THEN 'missing_category'
                      ELSE 'missing_ready_image' END
               ),
          updated_at = NOW()
        WHERE p.status = 'ACTIVE'
          AND (
            p.category_id IS NULL
            OR NOT EXISTS (
              SELECT 1 FROM product_media_links pml
              JOIN media_assets ma ON ma.id = pml.media_asset_id
              WHERE pml.product_id = p.id AND pml.is_primary
                AND ma.status = 'READY'
                AND ma.cdn_url IS NOT NULL
                AND length(ma.cdn_url) > 0
            )
          )
        """
    )
    r = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'READY',
          metadata = COALESCE(p.metadata, '{}'::jsonb) - 'integrity_block',
          updated_at = NOW()
        WHERE p.status = 'ACTIVE'
          AND p.category_id IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id = pml.media_asset_id
            WHERE pml.product_id = p.id AND pml.is_primary
              AND ma.status = 'READY'
              AND ma.cdn_url IS NOT NULL
              AND length(ma.cdn_url) > 0
          )
        """
    )
    print(q, r, flush=True)
    row = await conn.fetchrow(
        """
        SELECT
          count(*) FILTER (WHERE data_quality_status='READY') AS ready,
          count(*) FILTER (WHERE data_quality_status='QUARANTINED') AS quarantined,
          count(*) FILTER (WHERE data_quality_status='PARTIAL') AS partial,
          count(*) FILTER (WHERE data_quality_status='READY' AND category_id IS NULL) AS ready_no_cat
        FROM products WHERE status='ACTIVE'
        """
    )
    print(dict(row), flush=True)
    eligible = await conn.fetchval(
        """
        SELECT count(*) FROM products p
        WHERE p.status='ACTIVE' AND p.data_quality_status='READY'
          AND p.category_id IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM product_media_links pml
            JOIN media_assets ma ON ma.id = pml.media_asset_id
            WHERE pml.product_id = p.id AND pml.is_primary
              AND ma.status='READY' AND ma.cdn_url IS NOT NULL AND length(ma.cdn_url)>0
          )
        """
    )
    print({"strict_eligible": int(eligible)}, flush=True)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
