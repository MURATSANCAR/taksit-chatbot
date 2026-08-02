#!/usr/bin/env python3
"""Sync live-feed brand/category/attributes into products + backfill gaps (ADR-010).

Does NOT invent prices/partners. Only copies fields present in src-m-*.json and
runs brand/category resolution + image backfill orchestration hints.

  set -a && . ./.env.runtime && set +a
  .venv/bin/python -u scripts/complete_catalog_gaps.py --sync-feeds --resolve --rescore
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))


def _load_feed_index() -> dict[tuple[str, str], dict[str, Any]]:
    """(merchant_code, external_id) -> feed product row."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(LIVE.glob("src-m-*.json")):
        mcode = path.stem.replace("src-", "", 1)
        try:
            products = json.loads(path.read_text(encoding="utf-8")).get("products") or []
        except Exception as exc:
            print(f"skip feed {path.name}: {exc}", flush=True)
            continue
        for p in products:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            out[(mcode, str(p["id"]))] = p
    return out


async def sync_feeds(conn) -> dict[str, int]:
    print("loading feed index…", flush=True)
    feed = _load_feed_index()
    print(f"feed_rows={len(feed)}", flush=True)

    rows = await conn.fetch(
        """
        SELECT p.id, p.external_product_id, m.merchant_code,
               p.attributes, p.brand_id, p.category_id, p.manufacturer_name
        FROM products p
        JOIN merchants m ON m.id = p.merchant_id
        WHERE p.status = 'ACTIVE'
        """
    )
    stats = {
        "seen": 0,
        "attrs_updated": 0,
        "manufacturer_set": 0,
        "pending_image_set": 0,
        "no_feed": 0,
    }
    batch_attr: list[tuple[str, int]] = []
    batch_mfr: list[tuple[str, int]] = []
    batch_pending: list[tuple[str, int]] = []

    for r in rows:
        stats["seen"] += 1
        key = (r["merchant_code"], str(r["external_product_id"]))
        fp = feed.get(key)
        if not fp:
            stats["no_feed"] += 1
            continue
        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        if isinstance(r["attributes"], str):
            try:
                attrs = json.loads(r["attributes"] or "{}")
            except Exception:
                attrs = {}
        changed = False
        for k in ("brand", "category", "model", "color", "size", "gender"):
            val = fp.get(k)
            if val is None or str(val).strip() == "" or str(val).lower() == "null":
                continue
            if k == "category" and (val is None or str(val).strip() == ""):
                continue
            if attrs.get(k) != val:
                attrs[k] = val
                changed = True
        # merge feed attributes dict if present
        fa = fp.get("attributes")
        if isinstance(fa, dict):
            for k, v in fa.items():
                if v is None or str(v).strip() == "":
                    continue
                if attrs.get(k) != v:
                    attrs[k] = v
                    changed = True
        if changed:
            batch_attr.append((json.dumps(attrs, ensure_ascii=False), int(r["id"])))
            stats["attrs_updated"] += 1
        brand = (fp.get("brand") or "").strip()
        if brand and not (r["manufacturer_name"] or "").strip():
            batch_mfr.append((brand[:256], int(r["id"])))
            stats["manufacturer_set"] += 1
        img = (fp.get("image_url") or "").strip()
        if img.startswith("http"):
            batch_pending.append((img, int(r["id"])))
            stats["pending_image_set"] += 1

        if len(batch_attr) >= 500:
            await conn.executemany(
                "UPDATE products SET attributes=$1::jsonb, updated_at=NOW() WHERE id=$2",
                batch_attr,
            )
            batch_attr.clear()
        if len(batch_mfr) >= 500:
            await conn.executemany(
                "UPDATE products SET manufacturer_name=$1, updated_at=NOW() WHERE id=$2",
                batch_mfr,
            )
            batch_mfr.clear()
        if len(batch_pending) >= 500:
            await conn.executemany(
                """
                UPDATE products SET metadata = COALESCE(metadata,'{}'::jsonb)
                  || jsonb_build_object('pending_source_image_url', $1::text),
                  updated_at=NOW()
                WHERE id=$2
                  AND coalesce(metadata->>'primary_media_status','') IS DISTINCT FROM 'READY'
                """,
                batch_pending,
            )
            batch_pending.clear()
        if stats["seen"] % 20000 == 0:
            print(f"  sync progress {stats}", flush=True)

    if batch_attr:
        await conn.executemany(
            "UPDATE products SET attributes=$1::jsonb, updated_at=NOW() WHERE id=$2",
            batch_attr,
        )
    if batch_mfr:
        await conn.executemany(
            "UPDATE products SET manufacturer_name=$1, updated_at=NOW() WHERE id=$2",
            batch_mfr,
        )
    if batch_pending:
        await conn.executemany(
            """
            UPDATE products SET metadata = COALESCE(metadata,'{}'::jsonb)
              || jsonb_build_object('pending_source_image_url', $1::text),
              updated_at=NOW()
            WHERE id=$2
              AND coalesce(metadata->>'primary_media_status','') IS DISTINCT FROM 'READY'
            """,
            batch_pending,
        )
    return stats


async def resolve_all(conn) -> dict[str, int]:
    from taksitlio.product.normalize import normalize_display_name
    from taksitlio.product.resolution import (
        resolve_brand_for_product,
        resolve_category_for_product,
    )
    from taksitlio.product.taxonomy_pg import ensure_brand

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
            if normalize_display_name(lab) in brand_names and " " not in lab:
                continue
            synonym_index.append((int(row["id"]), lab))

    aliases = [
        dict(r)
        for r in await conn.fetch(
            "SELECT brand_id, alias_text, normalized_alias FROM brand_aliases WHERE status='ACTIVE'"
        )
    ]
    brand_alias_map: dict[str, int] = {}
    for row in aliases:
        key = normalize_display_name(str(row.get("normalized_alias") or row.get("alias_text") or ""))
        if key:
            brand_alias_map[key] = int(row["brand_id"])

    rows = await conn.fetch(
        """
        SELECT id, display_name,
               COALESCE(full_description, short_description, '') AS description,
               attributes, brand_id, category_id, manufacturer_name,
               COALESCE(source_url, '') AS source_url
        FROM products WHERE status='ACTIVE'
        ORDER BY id
        """
    )
    stats = {"brand_set": 0, "brand_created": 0, "category_set": 0, "seen": 0}
    pending_new_brands: dict[str, list[int]] = defaultdict(list)

    for i, r in enumerate(rows):
        stats["seen"] += 1
        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        if isinstance(r["attributes"], str):
            try:
                attrs = json.loads(r["attributes"] or "{}")
            except Exception:
                attrs = {}
        if r["manufacturer_name"] and not attrs.get("brand"):
            attrs = {**attrs, "brand": r["manufacturer_name"]}

        if r["brand_id"] is None:
            bres = resolve_brand_for_product(
                product_id=int(r["id"]),
                title=str(r["display_name"] or ""),
                attributes=attrs,
                brand_alias_map=brand_alias_map,
            )
            if bres.brand_id is not None:
                await conn.execute(
                    "UPDATE products SET brand_id=$1, updated_at=NOW() WHERE id=$2",
                    int(bres.brand_id),
                    int(r["id"]),
                )
                stats["brand_set"] += 1
            elif bres.source_method == "structured_source_brand_unlinked" and bres.evidence_span:
                pending_new_brands[bres.evidence_span].append(int(r["id"]))

        if r["category_id"] is None:
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
                stats["category_set"] += 1

        if stats["seen"] % 25000 == 0:
            print(f"  resolve progress {stats}", flush=True)

    for name, pids in pending_new_brands.items():
        try:
            bid = await ensure_brand(conn, brand_name=name)
            stats["brand_created"] += 1
            for pid in pids:
                await conn.execute(
                    "UPDATE products SET brand_id=$1, updated_at=NOW() WHERE id=$2 AND brand_id IS NULL",
                    int(bid),
                    pid,
                )
                stats["brand_set"] += 1
            norm = normalize_display_name(name)
            if norm:
                brand_alias_map[norm] = int(bid)
        except Exception as exc:  # noqa: BLE001
            print(f"brand create fail {name!r}: {exc}", flush=True)

    return stats


async def rescore(conn) -> dict[str, int]:
    """Mark READY when CDN image + offer basics present; else keep PARTIAL."""
    # READY: has READY primary media metadata or linked asset + ACTIVE + has offer price
    r1 = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'READY',
          updated_at = NOW()
        WHERE p.status = 'ACTIVE'
          AND p.data_quality_status IS DISTINCT FROM 'READY'
          AND EXISTS (
            SELECT 1 FROM product_offers o
            WHERE o.product_id = p.id AND o.current_price > 0 AND o.currency IS NOT NULL
          )
          AND (
            p.metadata->>'primary_media_status' = 'READY'
            OR EXISTS (
              SELECT 1 FROM product_media_links pml
              JOIN media_assets ma ON ma.id = pml.media_asset_id
              WHERE pml.product_id = p.id AND pml.is_primary AND ma.status = 'READY'
            )
          )
        """
    )
    r2 = await conn.execute(
        """
        UPDATE products p SET
          data_quality_status = 'PARTIAL',
          updated_at = NOW()
        WHERE p.status = 'ACTIVE'
          AND p.data_quality_status = 'READY'
          AND NOT (
            p.metadata->>'primary_media_status' = 'READY'
            OR EXISTS (
              SELECT 1 FROM product_media_links pml
              JOIN media_assets ma ON ma.id = pml.media_asset_id
              WHERE pml.product_id = p.id AND pml.is_primary AND ma.status = 'READY'
            )
          )
        """
    )
    # asyncpg returns status string like UPDATE N
    def _n(status: str) -> int:
        try:
            return int(str(status).split()[-1])
        except Exception:
            return 0

    return {"marked_ready": _n(r1), "reverted_partial": _n(r2)}


async def main() -> None:
    import asyncpg

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sync-feeds", action="store_true")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        args.sync_feeds = args.resolve = args.rescore = True
    if not (args.sync_feeds or args.resolve or args.rescore):
        raise SystemExit("pass --all or one of --sync-feeds/--resolve/--rescore")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    report: dict[str, Any] = {}
    try:
        if args.sync_feeds:
            report["sync"] = await sync_feeds(conn)
            print("sync", report["sync"], flush=True)
        if args.resolve:
            report["resolve"] = await resolve_all(conn)
            print("resolve", report["resolve"], flush=True)
        if args.rescore:
            report["rescore"] = await rescore(conn)
            print("rescore", report["rescore"], flush=True)
    finally:
        await conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
