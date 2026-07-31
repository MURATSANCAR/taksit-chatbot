#!/usr/bin/env python3
"""Teknosa full-catalog via crawl4ai (Cloudflare bypass).

cloudscraper often gets 'Just a moment'; Playwright cookie transfer to httpx
fails on CF TLS fingerprint. crawl4ai real browser works for sitemap + PDP.

  .venv-crawl/bin/python -u scripts/fetch_teknosa_crawl4ai.py --limit 0 --concurrency 3

Resumes from crawler/feeds/live/src-m-teknosa.json. Does not invent data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_live_merchant_feeds import (  # noqa: E402
    _load_existing_feed,
    parse_jsonld_product,
    write_feed,
)

OUT_CODE = "src-m-teknosa"
INDEX = "https://www.teknosa.com/siteharitasi.xml"


async def crawl_text(url: str, *, wait_s: float = 2.0) -> tuple[bool, str]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=120000,
        delay_before_return_html=wait_s,
    )
    async with AsyncWebCrawler(config=browser) as crawler:
        result = await crawler.arun(url=url, config=run)
    html = result.html or ""
    ok = bool(result.success) and len(html) > 500 and "Just a moment" not in html[:2000]
    return ok, html


async def collect_product_urls() -> list[str]:
    ok, html = await crawl_text(INDEX, wait_s=2.5)
    if not ok:
        print("  sitemap index blocked")
        return []
    children = re.findall(r"<loc>([^<]+)</loc>", html)
    maps = [
        u
        for u in children
        if "Product-tr-TRY" in u or "OutletCanonicalised-tr-TRY" in u
    ]
    print(f"  maps={len(maps)}")
    urls: list[str] = []
    seen: set[str] = set()
    for sm in maps:
        print(f"  fetch map {sm.rsplit('/', 1)[-1]} ...", flush=True)
        ok, body = await crawl_text(sm, wait_s=1.0)
        if not ok:
            print(f"    fail map {sm}")
            continue
        batch = 0
        for u in re.findall(r"<loc>([^<]+)</loc>", body):
            u = u.split("?")[0]
            if u.endswith(".xml"):
                continue
            if u not in seen:
                seen.add(u)
                urls.append(u)
                batch += 1
        print(f"    +{batch} total={len(urls)}", flush=True)
    return urls


async def fetch_one(url: str, sem: asyncio.Semaphore) -> Optional[dict[str, Any]]:
    async with sem:
        ok, html = await crawl_text(url, wait_s=1.2)
        if not ok:
            return None
        return parse_jsonld_product(html, url)


async def amain() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=3)
    args = p.parse_args()

    by_id = _load_existing_feed(OUT_CODE)
    print(f"resume {len(by_id)}")
    product_urls = await collect_product_urls()
    done = {str(x.get("url")) for x in by_id.values() if x.get("url")}
    todo = [u for u in product_urls if u not in done]
    if args.limit > 0:
        todo = todo[: args.limit]
    print(f"todo={len(todo)} catalog={len(product_urls)} concurrency={args.concurrency}")

    sem = asyncio.Semaphore(max(1, args.concurrency))
    fail = 0
    done_n = 0
    # Process in chunks to checkpoint
    chunk = 100
    for start in range(0, len(todo), chunk):
        batch = todo[start : start + chunk]
        print(f"batch {start}-{start + len(batch) - 1}", flush=True)
        results = await asyncio.gather(*[fetch_one(u, sem) for u in batch])
        for p_row in results:
            done_n += 1
            if p_row and p_row.get("id"):
                by_id[str(p_row["id"])] = p_row
            else:
                fail += 1
        write_feed(
            OUT_CODE,
            list(by_id.values()),
            "teknosa crawl4ai sitemap+pdp (checkpoint)",
        )
        print(
            f"  ... {done_n}/{len(todo)} ok={len(by_id)} fail={fail}",
            flush=True,
        )

    path = write_feed(
        OUT_CODE,
        list(by_id.values()),
        "teknosa crawl4ai sitemap+pdp",
    )
    print(f"done {len(by_id)} -> {path}")


if __name__ == "__main__":
    asyncio.run(amain())
