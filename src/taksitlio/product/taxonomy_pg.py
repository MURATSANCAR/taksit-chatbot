"""Postgres ensure helpers for brands / categories from feed labels."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from taksitlio.product.normalize import normalize_display_name
from taksitlio.product.taxonomy import merge_synonym, pick_existing_category, taxonomy_code


async def ensure_brand(conn: Any, brand_name: Optional[str]) -> Optional[int]:
    """Upsert brands + brand_aliases; return brand id."""

    name = (brand_name or "").strip()
    if not name:
        return None
    code = taxonomy_code(name)
    normalized = normalize_display_name(name)
    row = await conn.fetchrow(
        """
        INSERT INTO brands (brand_code, display_name, normalized_name, status)
        VALUES ($1, $2, $3, 'ACTIVE')
        ON CONFLICT (brand_code) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            normalized_name = EXCLUDED.normalized_name,
            status = 'ACTIVE',
            updated_at = NOW()
        RETURNING id
        """,
        code,
        name[:256],
        normalized[:256],
    )
    brand_id = int(row["id"])
    await conn.execute(
        """
        INSERT INTO brand_aliases (brand_id, alias_text, normalized_alias, locale, status)
        VALUES ($1, $2, $3, 'tr-TR', 'ACTIVE')
        ON CONFLICT (normalized_alias, locale) DO UPDATE SET
            brand_id = EXCLUDED.brand_id,
            alias_text = EXCLUDED.alias_text,
            status = 'ACTIVE'
        """,
        brand_id,
        name[:256],
        normalized[:256],
    )
    return brand_id


async def ensure_category(conn: Any, category_name: Optional[str]) -> Optional[int]:
    """Map feed category label onto categories (+ synonym), creating when needed."""

    label = (category_name or "").strip()
    if not label:
        return None

    existing_rows = await conn.fetch(
        """
        SELECT id, category_code, display_name, synonyms, description
        FROM categories
        WHERE status = 'ACTIVE'
        ORDER BY id
        """
    )
    mapped = [
        {
            "id": int(r["id"]),
            "category_code": r["category_code"],
            "display_name": r["display_name"],
            "synonyms": tuple(r["synonyms"] or ()),
            "description": r["description"],
        }
        for r in existing_rows
    ]
    hit = pick_existing_category(label, categories=mapped)
    if hit is not None:
        cat_id = int(hit["id"])
        syns = merge_synonym(hit.get("synonyms") or (), label, hit.get("display_name"))
        await conn.execute(
            """
            UPDATE categories
            SET synonyms = $2::text[],
                updated_at = NOW()
            WHERE id = $1
            """,
            cat_id,
            list(syns),
        )
        return cat_id

    code = taxonomy_code(label)
    # Avoid colliding with a reserved/seed code that didn't match via synonyms.
    clash = await conn.fetchval(
        "SELECT id FROM categories WHERE category_code = $1",
        code,
    )
    if clash is not None:
        syns_row = await conn.fetchval(
            "SELECT synonyms FROM categories WHERE id = $1",
            int(clash),
        )
        syns = merge_synonym(tuple(syns_row or ()), label)
        await conn.execute(
            """
            UPDATE categories
            SET synonyms = $2::text[], status = 'ACTIVE', updated_at = NOW()
            WHERE id = $1
            """,
            int(clash),
            list(syns),
        )
        return int(clash)

    row = await conn.fetchrow(
        """
        INSERT INTO categories (
            category_code, display_name, description, synonyms, status
        ) VALUES ($1, $2, $3, $4::text[], 'ACTIVE')
        RETURNING id
        """,
        code,
        label[:128],
        f"Merchant feed category: {label}"[:512],
        [label],
    )
    return int(row["id"])


async def resolve_product_taxonomy_ids(
    conn: Any,
    *,
    brand_name: Optional[str],
    category_name: Optional[str],
) -> tuple[Optional[int], Optional[int]]:
    brand_id = await ensure_brand(conn, brand_name)
    category_id = await ensure_category(conn, category_name)
    return brand_id, category_id


__all__ = [
    "ensure_brand",
    "ensure_category",
    "resolve_product_taxonomy_ids",
]
