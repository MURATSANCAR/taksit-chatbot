#!/usr/bin/env python3
"""Backfill missing product images into media_assets + CDN (ADR-010, no hotlink).

For products without a READY primary media link, download merchant ``image_url`` /
``pending_source_image_url``, store via OBJECT_STORAGE_BACKEND, link + update
``products.metadata.primary_*``.

Run on nanobase only::

  set -a && . ./.env.runtime && set +a
  nohup .venv/bin/python -u scripts/backfill_product_images.py \\
    --concurrency 8 --limit 0 > /tmp/backfill-product-images.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))

# Listing thumbs are often <600px; still usable for cards.
LISTING_POLICY_KW = dict(
    min_width=250,
    min_height=250,
    preferred_width=600,
    aspect_min=0.5,
    aspect_max=2.0,
)


def _feed_image_index() -> dict[tuple[str, str], str]:
    """(merchant_code, external_product_id) -> image_url."""
    out: dict[tuple[str, str], str] = {}
    for path in sorted(LIVE.glob("src-m-*.json")):
        mcode = path.stem.replace("src-", "", 1)  # m-flo
        try:
            products = json.loads(path.read_text(encoding="utf-8")).get("products") or []
        except Exception:
            continue
        for p in products:
            if not isinstance(p, dict):
                continue
            eid = p.get("id")
            url = p.get("image_url")
            if eid and url and str(url).startswith("http"):
                out[(mcode, str(eid))] = str(url)
    return out


async def main() -> None:
    import asyncpg

    from taksitlio.media.pipeline import download_image, ingest_image_bytes
    from taksitlio.media.quality import MediaQualityPolicy
    from taksitlio.media.s3_storage import build_object_storage_from_env
    from taksitlio.product.catalog import PostgresProductCatalogRepository

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0, help="0 = all missing")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument(
        "--merchants",
        default="",
        help="comma merchant_codes e.g. m-vatan,m-flo (empty = all)",
    )
    args = p.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    storage = build_object_storage_from_env(
        default_local_root=os.environ.get(
            "MEDIA_STORAGE_ROOT", str(ROOT / "var" / "media")
        )
    )
    policy = MediaQualityPolicy(**LISTING_POLICY_KW)

    print("building feed image index…", flush=True)
    feed_idx = _feed_image_index()
    print(f"feed_image_urls={len(feed_idx)}", flush=True)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=max(4, args.concurrency + 2))
    catalog = PostgresProductCatalogRepository(pool)
    merchants = {m.strip() for m in args.merchants.split(",") if m.strip()}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS product_id,
                   p.external_product_id,
                   m.merchant_code,
                   p.metadata->>'pending_source_image_url' AS pending_url,
                   ma.status AS media_status
            FROM products p
            JOIN merchants m ON m.id = p.merchant_id
            LEFT JOIN product_media_links pml
              ON pml.product_id = p.id AND pml.is_primary
            LEFT JOIN media_assets ma ON ma.id = pml.media_asset_id
            WHERE COALESCE(ma.status, p.metadata->>'primary_media_status', '') <> 'READY'
            ORDER BY p.id
            """
        )
    if merchants:
        rows = [r for r in rows if r["merchant_code"] in merchants]
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    print(f"todo={len(rows)} concurrency={args.concurrency}", flush=True)

    known: set[str] = set()
    async with pool.acquire() as conn:
        for r in await conn.fetch("SELECT sha256 FROM media_assets"):
            known.add(r["sha256"])

    stats = {
        "ready": 0,
        "quarantined": 0,
        "failed": 0,
        "no_url": 0,
        "done": 0,
    }
    sem = asyncio.Semaphore(max(1, args.concurrency))
    lock = asyncio.Lock()

    async def one(row: Any) -> None:
        pid = int(row["product_id"])
        mcode = row["merchant_code"]
        eid = str(row["external_product_id"])
        url = (row["pending_url"] or "").strip() or feed_idx.get((mcode, eid), "")
        if not url.startswith("http"):
            async with lock:
                stats["no_url"] += 1
                stats["done"] += 1
            return
        async with sem:
            try:
                await catalog.set_pending_source_image(pid, url)
                data = await download_image(url)
                async with lock:
                    known_snap = set(known)
                outcome = await asyncio.to_thread(
                    ingest_image_bytes,
                    data,
                    source_url=url,
                    storage=storage,
                    known_sha256=known_snap,
                    policy=policy,
                    source_reference=mcode,
                )
                draft = outcome.draft
                status = (
                    draft.status.value
                    if hasattr(draft.status, "value")
                    else str(draft.status)
                )
                async with pool.acquire() as conn:
                    mid = await conn.fetchval(
                        """
                        INSERT INTO media_assets (
                          source_url, storage_key, cdn_url, mime_type, width, height,
                          file_size, sha256, perceptual_hash, quality_score, status,
                          source_reference
                        ) VALUES (
                          $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12
                        )
                        ON CONFLICT (sha256) DO UPDATE SET
                          updated_at = NOW(),
                          last_verified_at = NOW(),
                          status = EXCLUDED.status,
                          quality_score = EXCLUDED.quality_score,
                          cdn_url = COALESCE(EXCLUDED.cdn_url, media_assets.cdn_url),
                          storage_key = COALESCE(EXCLUDED.storage_key, media_assets.storage_key)
                        RETURNING id
                        """,
                        draft.source_url,
                        draft.storage_key,
                        draft.cdn_url,
                        draft.mime_type,
                        draft.width,
                        draft.height,
                        draft.file_size,
                        draft.sha256,
                        draft.perceptual_hash,
                        draft.quality_score,
                        status,
                        draft.source_reference,
                    )
                    for v in draft.variants or ():
                        await conn.execute(
                            """
                            INSERT INTO media_variants (
                              media_asset_id, variant_code, width, height, mime_type,
                              storage_key, cdn_url, file_size
                            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                            ON CONFLICT (media_asset_id, variant_code) DO NOTHING
                            """,
                            mid,
                            v["variant_code"],
                            v["width"],
                            v.get("height"),
                            v["mime_type"],
                            v["storage_key"],
                            v["cdn_url"],
                            v.get("file_size"),
                        )
                    await conn.execute(
                        """
                        INSERT INTO product_media_links (
                          product_id, media_asset_id, media_role, display_order, is_primary
                        ) VALUES ($1,$2,'PRIMARY',0,TRUE)
                        ON CONFLICT (product_id, media_asset_id, media_role) DO UPDATE
                          SET is_primary = TRUE
                        """,
                        pid,
                        mid,
                    )
                await catalog.attach_primary_media(
                    pid,
                    cdn_url=draft.cdn_url,
                    sha256=draft.sha256,
                    status=status,
                    source_url=url,
                    storage_key=draft.storage_key,
                    mime_type=draft.mime_type,
                    width=draft.width,
                    height=draft.height,
                    file_size=draft.file_size,
                )
                async with lock:
                    known.add(draft.sha256)
                    if status == "READY":
                        stats["ready"] += 1
                    else:
                        stats["quarantined"] += 1
                    stats["done"] += 1
                    if stats["done"] % 50 == 0:
                        print(dict(stats), flush=True)
            except Exception as exc:  # noqa: BLE001
                async with lock:
                    stats["failed"] += 1
                    stats["done"] += 1
                    if stats["failed"] <= 20 or stats["failed"] % 100 == 0:
                        print(f"FAIL {mcode}/{eid}: {exc}", flush=True)

    await asyncio.gather(*(one(r) for r in rows))
    print({"final": stats}, flush=True)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
