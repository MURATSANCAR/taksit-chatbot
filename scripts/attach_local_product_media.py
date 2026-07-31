#!/usr/bin/env python3
"""Attach locally downloaded product images into Postgres media tables.

Vatan CDN blocks some datacenter IPs (403). Ops downloads on an allowed host
into ``crawler/feeds/live/product_media`` + manifest, then this script upserts
media_assets / product_media_links without hotlinking.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))
MANIFEST = LIVE / "product_media_manifest.json"


async def main() -> None:
    import asyncpg

    from taksitlio.media.pipeline import ingest_image_bytes
    from taksitlio.media.quality import MediaQualityPolicy
    from taksitlio.media.storage import LocalObjectStorage

    if not MANIFEST.exists():
        raise SystemExit(f"missing {MANIFEST}")

    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ok_rows = [r for r in rows if r.get("ok") and r.get("local_path")]
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    root = os.environ.get("MEDIA_STORAGE_ROOT", "/tmp/taksitlio-media")
    cdn = os.environ.get("CDN_BASE_URL", "http://127.0.0.1:8000/cdn")
    storage = LocalObjectStorage(root, cdn_base_url=cdn)
    # Listing feeds often ship ~300px thumbs; keep them usable for catalog cards.
    policy = MediaQualityPolicy(
        min_width=250,
        min_height=250,
        preferred_width=600,
        aspect_min=0.5,
        aspect_max=2.0,
    )

    conn = await asyncpg.connect(dsn)
    known = {r["sha256"] for r in await conn.fetch("SELECT sha256 FROM media_assets")}
    stats = {"ready": 0, "linked": 0, "missing_product": 0, "failed": 0, "quarantined": 0}

    # Map merchant source stem -> merchant_id via products join later by external id only
    # (external ids are unique per merchant; resolve via merchants.merchant_code)
    source_to_mcode = {
        "src-m-vatan": "m-vatan",
        "src-m-mediamarkt": "m-mediamarkt",
        "src-m-koctas": "m-koctas",
        "src-m-dr": "m-dr",
    }

    try:
        for i, row in enumerate(ok_rows, 1):
            try:
                path = ROOT / row["local_path"]
                if not path.is_file():
                    # remote host: path may already be relative under LIVE
                    alt = LIVE.parent.parent.parent / row["local_path"]
                    path = alt if alt.is_file() else Path(row["local_path"])
                if not path.is_file():
                    # try under LIVE/product_media from sha path
                    path = LIVE / "product_media" / Path(row["local_path"]).name
                    if not path.is_file():
                        # sha layout
                        sha = row["sha256"]
                        for ext in (".jpg", ".png", ".webp"):
                            cand = LIVE / "product_media" / sha[:2] / f"{sha}{ext}"
                            if cand.is_file():
                                path = cand
                                break
                data = path.read_bytes()
                outcome = ingest_image_bytes(
                    data,
                    source_url=row.get("fetched_url") or row["source_url"],
                    storage=storage,
                    known_sha256=None,  # always re-evaluate status with listing policy
                    policy=policy,
                    source_reference=row.get("source"),
                )
                draft = outcome.draft
                status = draft.status.value if hasattr(draft.status, "value") else str(draft.status)
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
                      storage_key = COALESCE(EXCLUDED.storage_key, media_assets.storage_key),
                      width = COALESCE(EXCLUDED.width, media_assets.width),
                      height = COALESCE(EXCLUDED.height, media_assets.height)
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
                if status == "READY":
                    stats["ready"] += 1
                else:
                    stats["quarantined"] += 1

                mcode = source_to_mcode.get(row["source"])
                if mcode:
                    pid = await conn.fetchval(
                        """
                        SELECT p.id FROM products p
                        JOIN merchants m ON m.id = p.merchant_id
                        WHERE m.merchant_code=$1 AND p.external_product_id=$2
                        """,
                        mcode,
                        str(row["external_product_id"]),
                    )
                else:
                    pid = await conn.fetchval(
                        "SELECT id FROM products WHERE external_product_id=$1 LIMIT 1",
                        str(row["external_product_id"]),
                    )
                if pid is None:
                    stats["missing_product"] += 1
                    continue
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
                await conn.execute(
                    """
                    UPDATE products SET metadata =
                      COALESCE(metadata,'{}'::jsonb) - 'pending_source_image_url',
                      updated_at = NOW()
                    WHERE id=$1
                    """,
                    pid,
                )
                stats["linked"] += 1
            except Exception as exc:
                stats["failed"] += 1
                if stats["failed"] <= 5:
                    print("fail", row.get("external_product_id"), type(exc).__name__, exc)
            if i % 100 == 0:
                print(f"progress {i}/{len(ok_rows)} {stats}")
    finally:
        await conn.close()
    print(json.dumps({"total": len(ok_rows), **stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
