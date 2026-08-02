#!/usr/bin/env python3
"""Enforce catalog integrity: READY CDN image + category_id required.

1) Seed/expand taxonomy synonyms
2) Re-resolve categories for ACTIVE products missing category_id
3) QUARANTINE products missing READY primary image OR category_id
4) Restore READY/PARTIAL when both present again
5) Rebuild search projections

Never invents images/categories — unresolved rows are hidden from chatbot.

  set -a && . ./.env.runtime && set +a
  .venv/bin/python -u scripts/enforce_catalog_integrity.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def seed_taxonomy(conn: Any) -> dict[str, int]:
    from taksitlio.product.resolution import ensure_taxonomy_seed_categories
    from taksitlio.product.taxonomy import merge_synonym

    seeded = 0
    merged = 0
    for row in ensure_taxonomy_seed_categories():
        merge_into = row.get("merge_into_code")
        if merge_into:
            existing = await conn.fetchrow(
                "SELECT id, synonyms FROM categories WHERE category_code=$1",
                merge_into,
            )
            if existing:
                syns = merge_synonym(tuple(existing["synonyms"] or ()), *row["synonyms"])
                await conn.execute(
                    "UPDATE categories SET synonyms=$2::text[], updated_at=NOW() WHERE id=$1",
                    int(existing["id"]),
                    list(syns),
                )
                merged += 1
            continue
        if not row.get("display_name"):
            continue
        await conn.execute(
            """
            INSERT INTO categories (category_code, display_name, description, synonyms, status)
            VALUES ($1,$2,$3,$4::text[],'ACTIVE')
            ON CONFLICT (category_code) DO UPDATE SET
              synonyms = (
                SELECT array_agg(DISTINCT s)
                FROM unnest(
                  COALESCE(categories.synonyms,'{}'::text[]) || EXCLUDED.synonyms
                ) AS s
              ),
              display_name = EXCLUDED.display_name,
              status='ACTIVE',
              updated_at=NOW()
            """,
            row["category_code"],
            row["display_name"],
            row.get("description"),
            row["synonyms"],
        )
        seeded += 1
    return {"seeded": seeded, "merged": merged}


async def resolve_missing_categories(conn: Any) -> dict[str, int]:
    from taksitlio.product.normalize import normalize_display_name
    from taksitlio.product.resolution import resolve_category_for_product

    cats = [
        dict(r)
        for r in await conn.fetch(
            "SELECT id, category_code, display_name, synonyms FROM categories WHERE status='ACTIVE'"
        )
    ]
    brand_names = {
        normalize_display_name(str(x["normalized_name"] or x["display_name"] or ""))
        for x in await conn.fetch(
            "SELECT display_name, normalized_name FROM brands WHERE status='ACTIVE'"
        )
    }
    synonym_index: list[tuple[int, str]] = []
    for row in cats:
        labels = [str(row.get("display_name") or ""), *[str(s) for s in (row.get("synonyms") or ())]]
        for label in labels:
            lab = label.casefold().strip()
            if len(lab) < 3:
                continue
            if normalize_display_name(lab) in brand_names and " " not in lab and ">" not in lab:
                continue
            synonym_index.append((int(row["id"]), lab))

    rows = await conn.fetch(
        """
        SELECT id, display_name,
               COALESCE(full_description, short_description, '') AS description,
               attributes, category_id,
               COALESCE(source_url, '') AS source_url
        FROM products
        WHERE status = 'ACTIVE' AND category_id IS NULL
        ORDER BY id
        """
    )
    set_n = 0
    for i, r in enumerate(rows, 1):
        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        if isinstance(r["attributes"], str):
            try:
                attrs = json.loads(r["attributes"] or "{}")
            except Exception:
                attrs = {}
        cres = resolve_category_for_product(
            product_id=int(r["id"]),
            title=str(r["display_name"] or ""),
            description=str(r["description"] or "")[:500],
            attributes=attrs,
            categories=cats,
            synonym_index=synonym_index,
            source_url=str(r["source_url"] or ""),
        )
        if cres.resolved_category_id is not None:
            await conn.execute(
                "UPDATE products SET category_id=$1, updated_at=NOW() WHERE id=$2",
                int(cres.resolved_category_id),
                int(r["id"]),
            )
            set_n += 1
        if i % 25000 == 0:
            print(f"  category resolve {i}/{len(rows)} set={set_n}", flush=True)
    return {"missing_before": len(rows), "category_set": set_n}


def _n(status: str) -> int:
    try:
        return int(str(status).split()[-1])
    except Exception:
        return 0


async def enforce_quarantine(conn: Any) -> dict[str, int]:
    """Hide incomplete products; restore when image+category OK."""

    q = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'QUARANTINED',
          metadata = COALESCE(p.metadata, '{}'::jsonb)
            || jsonb_build_object(
                 'integrity_block',
                 CASE
                   WHEN p.category_id IS NULL
                        AND NOT (
                          p.metadata->>'primary_media_status' = 'READY'
                          OR EXISTS (
                            SELECT 1 FROM product_media_links pml
                            JOIN media_assets ma ON ma.id = pml.media_asset_id
                            WHERE pml.product_id = p.id AND pml.is_primary
                              AND ma.status = 'READY' AND coalesce(ma.cdn_url,'') <> ''
                          )
                        ) THEN 'missing_category_and_image'
                   WHEN p.category_id IS NULL THEN 'missing_category'
                   ELSE 'missing_ready_image'
                 END
               ),
          updated_at = NOW()
        WHERE p.status = 'ACTIVE'
          AND (
            p.category_id IS NULL
            OR NOT (
              EXISTS (
                SELECT 1 FROM product_media_links pml
                JOIN media_assets ma ON ma.id = pml.media_asset_id
                WHERE pml.product_id = p.id AND pml.is_primary
                  AND ma.status = 'READY'
                  AND ma.cdn_url IS NOT NULL
                  AND length(ma.cdn_url) > 0
              )
            )
          )
        """
    )

    restore = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'READY',
          metadata = (COALESCE(p.metadata, '{}'::jsonb) - 'integrity_block'),
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
    return {"quarantined_or_kept": _n(q), "restored_ready": _n(restore)}


async def main() -> None:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    report: dict[str, Any] = {}
    try:
        print("seed taxonomy…", flush=True)
        report["taxonomy"] = await seed_taxonomy(conn)
        print(report["taxonomy"], flush=True)
        print("resolve categories…", flush=True)
        report["resolve"] = await resolve_missing_categories(conn)
        print(report["resolve"], flush=True)
        print("enforce quarantine…", flush=True)
        report["enforce"] = await enforce_quarantine(conn)
        print(report["enforce"], flush=True)

        # counts
        row = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE status='ACTIVE') AS active,
              count(*) FILTER (WHERE status='ACTIVE' AND data_quality_status='READY') AS ready,
              count(*) FILTER (WHERE status='ACTIVE' AND data_quality_status='QUARANTINED') AS quarantined,
              count(*) FILTER (WHERE status='ACTIVE' AND category_id IS NULL) AS no_cat,
              count(*) FILTER (
                WHERE status='ACTIVE' AND data_quality_status='READY' AND category_id IS NOT NULL
              ) AS visible_candidate
            FROM products
            """
        )
        report["counts"] = {k: int(row[k]) for k in row.keys()}
    finally:
        await conn.close()

    # rebuild projections
    try:
        from taksitlio.catalog_projection import CatalogProjectionRepository

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        try:
            stats = await CatalogProjectionRepository(pool).rebuild_all(catalog_revision=1)
            report["projection"] = stats.to_dict()
        finally:
            await pool.close()
    except Exception as exc:  # noqa: BLE001
        report["projection_error"] = str(exc)

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
