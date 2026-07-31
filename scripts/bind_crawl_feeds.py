#!/usr/bin/env python3
"""Bind StormCrawler feed outputs to ADR-010 ingestion (ops registry driven).

Does not hardcode merchant/bank display names in application logic — reads
``crawler/ops/crawl-registry.yaml`` and calls admin APIs / local adapters.

Examples:
  python scripts/bind_crawl_feeds.py --registry crawler/ops/crawl-registry.yaml --coverage
  python scripts/bind_crawl_feeds.py --fixtures --dry-run
  python scripts/bind_crawl_feeds.py --api http://127.0.0.1:8000 --bind-merchants --limit 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def load_registry(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"invalid registry: {path}")
    return data


def coverage_report(registry: Mapping[str, Any]) -> dict[str, Any]:
    merchants = list(registry.get("merchants") or [])
    banks = list(registry.get("banks") or [])
    return {
        "merchant_count": len(merchants),
        "bank_count": len(banks),
        "merchant_codes": [m.get("merchant_code") for m in merchants],
        "institution_codes": [b.get("institution_code") for b in banks],
        "meets_min_merchants_20": len(merchants) >= 20,
        "feeds_expected": [
            *(m.get("source_code") for m in merchants),
            *(b.get("source_code") for b in banks),
        ],
    }


def feed_url_for(source_code: str, *, feed_base: str, fixtures: bool) -> str:
    if fixtures:
        return ""  # path used instead
    return f"{feed_base.rstrip('/')}/feeds/{source_code}.json"


def feed_path_for(source_code: str, *, fixtures_dir: Path) -> Path:
    return fixtures_dir / f"{source_code}.json"


async def dry_run_merchant_fixture(path: Path) -> dict[str, Any]:
    from taksitlio.ingestion.binding import SourceBinding
    from taksitlio.ingestion.runner import run_ingestion_dry

    binding = SourceBinding(
        source_code=path.stem,
        adapter_code="generic.json_feed.v1",
        merchant_id="ops-local",
        config={"feed_path": str(path)},
    )
    result = await run_ingestion_dry(binding, limit=50)
    return {
        "channel": "PRODUCT",
        "source_code": path.stem,
        "discovered": result.discovered,
        "succeeded": result.succeeded,
        "products": len(result.items),
        "adapter_code": result.adapter_code,
    }


async def dry_run_campaign_fixture(path: Path, institution_code: str) -> dict[str, Any]:
    from taksitlio.campaign_catalog.feed_apply import (
        InMemoryCampaignCatalog,
        apply_campaign_feed_result,
    )
    from taksitlio.ingestion.adapters.generic_campaign_feed import (
        GenericCampaignFeedAdapter,
        run_campaign_feed_dry,
    )

    adapter = GenericCampaignFeedAdapter(
        feed_path=path,
        default_institution_code=institution_code,
        source_reference=str(path),
    )
    result = await run_campaign_feed_dry(adapter)
    catalog = InMemoryCampaignCatalog()
    applied = apply_campaign_feed_result(catalog, result)
    return {
        "channel": "CAMPAIGN",
        "source_code": path.stem,
        "campaigns": len(result.campaigns),
        "rates": len(result.rates),
        "rates_skipped_no_explicit_rate": result.rates_skipped_no_explicit_rate,
        "applied": applied,
    }


async def bind_via_api(
    *,
    api: str,
    registry: Mapping[str, Any],
    feed_base: str,
    limit_merchants: Optional[int],
    limit_banks: Optional[int],
    fixtures: bool,
    fixtures_dir: Path,
) -> list[dict[str, Any]]:
    import httpx

    out: list[dict[str, Any]] = []
    merchants = list(registry.get("merchants") or [])
    banks = list(registry.get("banks") or [])
    if limit_merchants is not None:
        merchants = merchants[:limit_merchants]
    if limit_banks is not None:
        banks = banks[:limit_banks]

    async with httpx.AsyncClient(base_url=api.rstrip("/"), timeout=60.0) as client:
        for m in merchants:
            code = m["merchant_code"]
            display = m["display_name"]
            source_code = m["source_code"]
            r = await client.post(
                "/v1/admin/merchants",
                json={"merchant_code": code, "display_name": display},
            )
            r.raise_for_status()
            merchant_id = r.json().get("id") or r.json().get("merchant_id")
            cfg: dict[str, Any]
            if fixtures:
                path = feed_path_for(source_code, fixtures_dir=fixtures_dir)
                if not path.exists():
                    out.append({"source_code": source_code, "skipped": "no fixture"})
                    continue
                cfg = {"feed_path": str(path)}
            else:
                cfg = {
                    "feed_url": feed_url_for(
                        source_code, feed_base=feed_base, fixtures=False
                    )
                }
            dry = await client.post(
                "/v1/admin/ingestion/dry-run",
                json={
                    "source_code": source_code,
                    "adapter_code": m.get("adapter_code") or "generic.json_feed.v1",
                    "merchant_id": str(merchant_id),
                    "config": cfg,
                    "limit": 20,
                },
            )
            out.append(
                {
                    "merchant_code": code,
                    "merchant_id": merchant_id,
                    "status_code": dry.status_code,
                    "body": dry.json() if dry.headers.get("content-type", "").startswith("application/json") else dry.text,
                }
            )

        for b in banks:
            # Campaign bind is local/file based when API campaign route absent;
            # still register opaque institution label via finance reload if available.
            source_code = b["source_code"]
            if fixtures:
                path = feed_path_for(source_code, fixtures_dir=fixtures_dir)
                if path.exists():
                    local = await dry_run_campaign_fixture(
                        path, str(b["institution_code"])
                    )
                    out.append({"bank": b["institution_code"], **local})
                else:
                    out.append({"bank": b["institution_code"], "skipped": "no fixture"})
            else:
                out.append(
                    {
                        "bank": b["institution_code"],
                        "feed_url": feed_url_for(
                            source_code, feed_base=feed_base, fixtures=False
                        ),
                        "note": "await StormCrawler feed; use --fixtures for local E2E",
                    }
                )
    return out


def inject_seeds_plan(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build URLFrontier seed payloads (metadata for JsonFeedIndexerBolt)."""

    seeds: list[dict[str, Any]] = []
    for m in registry.get("merchants") or []:
        for url in m.get("seed_urls") or []:
            seeds.append(
                {
                    "url": url,
                    "metadata": {
                        "taksitlio.source_code": m["source_code"],
                        "taksitlio.channel": "PRODUCT",
                        "taksitlio.merchant_code": m["merchant_code"],
                    },
                }
            )
    for b in registry.get("banks") or []:
        for url in b.get("seed_urls") or []:
            seeds.append(
                {
                    "url": url,
                    "metadata": {
                        "taksitlio.source_code": b["source_code"],
                        "taksitlio.channel": "CAMPAIGN",
                        "taksitlio.institution_code": b["institution_code"],
                    },
                }
            )
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "crawler/ops/crawl-registry.yaml",
    )
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--fixtures", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed-plan", action="store_true")
    parser.add_argument("--write-seed-plan", type=Path, default=None)
    parser.add_argument("--api", default=None, help="API base for bind")
    parser.add_argument("--bind-merchants", action="store_true")
    parser.add_argument("--feed-base", default="http://127.0.0.1:8091")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=ROOT / "crawler/feeds/fixtures",
    )
    args = parser.parse_args()

    registry = load_registry(args.registry)
    report = coverage_report(registry)
    if args.coverage or not any(
        [args.dry_run, args.seed_plan, args.write_seed_plan, args.bind_merchants]
    ):
        print(json.dumps({"coverage": report}, indent=2, ensure_ascii=False))

    if args.seed_plan or args.write_seed_plan:
        plan = inject_seeds_plan(registry)
        payload = {"count": len(plan), "seeds": plan}
        if args.write_seed_plan:
            args.write_seed_plan.parent.mkdir(parents=True, exist_ok=True)
            args.write_seed_plan.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"wrote {args.write_seed_plan} ({len(plan)} seeds)")
        else:
            print(json.dumps({"seed_count": len(plan)}, indent=2))

    if args.dry_run and args.fixtures:
        async def _run() -> None:
            results = []
            prod = args.fixtures_dir / "src-m-teknosa.json"
            camp = args.fixtures_dir / "src-b-fibabanka.json"
            if prod.exists():
                results.append(await dry_run_merchant_fixture(prod))
            if camp.exists():
                results.append(
                    await dry_run_campaign_fixture(camp, "fi-fibabanka")
                )
            print(json.dumps({"dry_run": results}, indent=2, ensure_ascii=False))

        asyncio.run(_run())

    if args.bind_merchants and args.api:
        async def _bind() -> None:
            out = await bind_via_api(
                api=args.api,
                registry=registry,
                feed_base=args.feed_base,
                limit_merchants=args.limit,
                limit_banks=args.limit,
                fixtures=args.fixtures,
                fixtures_dir=args.fixtures_dir,
            )
            print(json.dumps({"bind": out}, indent=2, ensure_ascii=False))

        asyncio.run(_bind())

    if not report["meets_min_merchants_20"]:
        raise SystemExit("registry has fewer than 20 merchants")


if __name__ == "__main__":
    main()
