#!/usr/bin/env python3
"""Upsert live feeds into Postgres (ADR-010 products/offers + finance_campaigns).

Run on a host with DATABASE_URL and applied V015–V018 migrations.
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

LIVE = Path(os.environ.get("LIVE_FEED_DIR", str(ROOT / "crawler" / "feeds" / "live")))


async def ensure_merchant(conn, *, code: str, name: str) -> int:
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
    # V004 merchants may not have updated_at / status — fallback
    if row is None:
        row = await conn.fetchrow(
            "SELECT id FROM merchants WHERE merchant_code=$1", code
        )
    return int(row["id"])


async def ensure_institution(conn, *, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO financial_institutions (
          institution_code, display_name, normalized_name, status
        )
        VALUES ($1::text, $2::text, lower($2::text), 'ACTIVE')
        ON CONFLICT (institution_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
    )
    return int(row["id"])


async def attach_product_images(
    pool,
    *,
    ingestion,
    applied,
    catalog,
) -> dict[str, int]:
    """Download feed image_url → object storage → media_assets (no hotlink)."""
    import os

    from taksitlio.media.pipeline import download_image, ingest_image_bytes
    from taksitlio.media.quality import MediaQualityPolicy
    from taksitlio.media.s3_storage import build_object_storage_from_env

    storage = build_object_storage_from_env(
        default_local_root=os.environ.get(
            "MEDIA_STORAGE_ROOT", str(ROOT / "var" / "media")
        )
    )
    # Listing thumbs often <600px; keep usable for catalog cards.
    policy = MediaQualityPolicy(
        min_width=250,
        min_height=250,
        preferred_width=600,
        aspect_min=0.5,
        aspect_max=2.0,
    )

    by_ext = {
        i.external_product_id: i.product_id
        for i in applied.items
        if i.product_id is not None
    }
    stats = {"pending_set": 0, "downloaded": 0, "ready": 0, "failed": 0, "skipped": 0}
    async with pool.acquire() as conn:
        known = {
            r["sha256"]
            for r in await conn.fetch("SELECT sha256 FROM media_assets")
        }
        for row in ingestion.items:
            product_id = by_ext.get(row.external_product_id)
            if product_id is None or not row.media:
                stats["skipped"] += 1
                continue
            primary = next(
                (m for m in row.media if getattr(m, "media_role", None) == "PRIMARY"),
                row.media[0],
            )
            source_url = getattr(primary, "source_url", None)
            if not source_url:
                stats["skipped"] += 1
                continue
            await catalog.set_pending_source_image(product_id, source_url)
            stats["pending_set"] += 1
            try:
                data = await download_image(source_url)
                outcome = ingest_image_bytes(
                    data,
                    source_url=source_url,
                    storage=storage,
                    known_sha256=known,
                    policy=policy,
                    source_reference=ingestion.source_code,
                )
                draft = outcome.draft
                status = (
                    draft.status.value
                    if hasattr(draft.status, "value")
                    else str(draft.status)
                )
                if outcome.skipped_duplicate_sha:
                    mid = await conn.fetchval(
                        "SELECT id FROM media_assets WHERE sha256=$1", draft.sha256
                    )
                else:
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
                    known.add(draft.sha256)
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
                await catalog.attach_primary_media(
                    product_id,
                    cdn_url=draft.cdn_url,
                    sha256=draft.sha256,
                    status=status,
                    source_url=source_url,
                    storage_key=draft.storage_key,
                    mime_type=draft.mime_type,
                    width=draft.width,
                    height=draft.height,
                    file_size=draft.file_size,
                )
                stats["downloaded"] += 1
                if status == "READY":
                    stats["ready"] += 1
            except Exception:
                stats["failed"] += 1
    return stats


async def ingest_products(pool) -> list[dict[str, Any]]:
    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.product.catalog import (
        PostgresProductCatalogRepository,
        apply_ingestion_to_catalog,
    )

    # Opaque codes → display names from ops registry (not app hardcode maps).
    name_by_code = {
        "src-m-vatan": ("m-vatan", "Vatan Bilgisayar"),
        "src-m-mediamarkt": ("m-mediamarkt", "MediaMarkt"),
        "src-m-koctas": ("m-koctas", "Koçtaş"),
        "src-m-dr": ("m-dr", "D&R"),
    }
    reports = []
    catalog = PostgresProductCatalogRepository(pool)
    only = {
        x.strip()
        for x in (os.environ.get("_INGEST_ONLY") or "").split(",")
        if x.strip()
    }
    skip = {
        x.strip()
        for x in (os.environ.get("_INGEST_SKIP") or "").split(",")
        if x.strip()
    }
    skip_media = (os.environ.get("_INGEST_SKIP_MEDIA") or "").strip() in {
        "1",
        "true",
        "yes",
    }

    def _wanted(stem: str) -> bool:
        short = stem.replace("src-m-", "").replace("src-", "")
        keys = {stem, short, f"m-{short}" if not short.startswith("m-") else short}
        if only and not (keys & only):
            return False
        if skip and (keys & skip):
            return False
        return True

    async with pool.acquire() as conn:
        for path in sorted(LIVE.glob("src-m-*.json")):
            if not _wanted(path.stem):
                reports.append({"file": path.name, "skipped": "filter"})
                continue
            meta = name_by_code.get(path.stem)
            if meta is None:
                # Derive opaque code from stem: src-m-foo -> m-foo
                code = path.stem.replace("src-", "", 1)
                meta = (code, code)
            code, name = meta
            # Skip empty product feeds (blocked/unparseable sites)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not raw.get("products"):
                reports.append(
                    {"file": path.name, "skipped": "empty_products", "merchant_code": code}
                )
                continue
            merchant_id = await ensure_merchant(conn, code=code, name=name)
            binding = SourceBinding(
                source_code=path.stem,
                adapter_code="generic.json_feed.v1",
                merchant_id=str(merchant_id),
                config={"feed_path": str(path)},
            )
            # No artificial product cap for live upserts.
            result = await run_ingestion_dry(binding, limit=1_000_000)
            applied = await apply_ingestion_to_catalog(
                result, merchant_id=merchant_id, catalog=catalog
            )
            if skip_media:
                media_stats = {"skipped": "skip_media"}
            else:
                media_stats = await attach_product_images(
                    pool, ingestion=result, applied=applied, catalog=catalog
                )
            reports.append(
                {
                    "file": path.name,
                    "merchant_id": merchant_id,
                    "discovered": result.discovered,
                    "chatbot_visible": result.chatbot_visible,
                    "upserted_products": applied.upserted_products,
                    "upserted_offers": applied.upserted_offers,
                    "media": media_stats,
                }
            )
    return reports


async def ingest_campaigns(pool, *, activate: bool = False) -> list[dict[str, Any]]:
    from taksitlio.campaign_catalog.postgres import persist_campaign_feed
    from taksitlio.ingestion.adapters.generic_campaign_feed import (
        GenericCampaignFeedAdapter,
        run_campaign_feed_dry,
    )

    reports = []
    files = {
        "src-b-isbank.json": ("fi-isbank", "İş Bankası"),
        "src-b-fibabanka.json": ("fi-fibabanka", "Fibabanka"),
    }
    async with pool.acquire() as conn:
        for fname, (icode, iname) in files.items():
            path = LIVE / fname
            if not path.exists():
                continue
            institution_id = await ensure_institution(conn, code=icode, name=iname)
            adapter = GenericCampaignFeedAdapter(
                feed_path=path, default_institution_code=icode
            )
            raw_feed = json.loads(path.read_text(encoding="utf-8"))
            raw_by_id = {
                str(row.get("id")): row
                for row in (raw_feed.get("campaigns") or [])
                if isinstance(row, dict) and row.get("id")
            }
            result = await run_campaign_feed_dry(adapter)
            stats = await persist_campaign_feed(
                conn,
                result,
                institution_display_names={icode: iname},
                raw_by_campaign_code=raw_by_id,
                activate=activate,
            )
            reports.append(
                {
                    "file": fname,
                    "institution_id": institution_id,
                    "campaigns_applied": stats.campaigns_upserted,
                    "rates_upserted": stats.rates_upserted,
                    "agreements_upserted": stats.agreements_upserted,
                    "activated": stats.activated,
                }
            )
    return reports


async def main() -> None:
    import argparse
    import asyncpg

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--merchants",
        default="",
        help="comma stems or codes e.g. flo,src-m-vatan (empty=all)",
    )
    ap.add_argument(
        "--skip-merchants",
        default="",
        help="comma stems to skip e.g. flo",
    )
    ap.add_argument("--skip-campaigns", action="store_true")
    ap.add_argument("--skip-media", action="store_true", help="upsert products only")
    args = ap.parse_args()
    only = {x.strip() for x in args.merchants.split(",") if x.strip()}
    skip = {x.strip() for x in args.skip_merchants.split(",") if x.strip()}

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL required")
    # asyncpg wants postgresql:// not sqlalchemy style
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        # Stash filters for ingest_products via env (minimal change surface)
        os.environ["_INGEST_ONLY"] = ",".join(sorted(only))
        os.environ["_INGEST_SKIP"] = ",".join(sorted(skip))
        if args.skip_media:
            os.environ["_INGEST_SKIP_MEDIA"] = "1"
        product_reports = await ingest_products(pool)
        campaign_reports = (
            [] if args.skip_campaigns else await ingest_campaigns(pool, activate=True)
        )
        async with pool.acquire() as conn:
            counts = {
                "merchants": await conn.fetchval("SELECT count(*) FROM merchants"),
                "products": await conn.fetchval("SELECT count(*) FROM products"),
                "offers": await conn.fetchval("SELECT count(*) FROM product_offers"),
                "institutions": await conn.fetchval(
                    "SELECT count(*) FROM financial_institutions"
                ),
                "campaigns": await conn.fetchval("SELECT count(*) FROM finance_campaigns"),
            }
        print(
            json.dumps(
                {
                    "product_reports": product_reports,
                    "campaign_reports": campaign_reports,
                    "counts": counts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
