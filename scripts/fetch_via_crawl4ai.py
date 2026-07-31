#!/usr/bin/env python3
"""Fetch merchant feeds via vendored open-source crawlers (ADR-010).

Uses:
  - crawl4ai (https://github.com/unclecode/crawl4ai) — Playwright stealth crawl
  - FlareSolverr patterns (https://github.com/FlareSolverr/FlareSolverr) via optional
    undetected-chromedriver when crawl4ai is blocked
  - Selector hints from BerkayKOCAK/e-commerce-crawler (vendor/e-commerce-crawler)

Run with the crawl venv (Python ≥3.10):
  .venv-crawl/bin/python scripts/fetch_via_crawl4ai.py --merchants teknosa,koctas,dr
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "crawler" / "feeds" / "live"
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_live_merchant_feeds import parse_jsonld_product, parse_tr_price, write_feed  # noqa: E402


def _clean_name(name: str) -> Optional[str]:
    name = unescape(name).strip()
    if len(name) < 4:
        return None
    if re.search(
        r"next slide|previous|sepete|çok satan|alışverişe başla|indirim fırsatı|attention required",
        name,
        re.I,
    ):
        return None
    return name[:200]


async def crawl_html(url: str, *, wait_ms: int = 2000) -> tuple[bool, str, Optional[int]]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser = BrowserConfig(headless=True, verbose=False)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=60000,
        delay_before_return_html=wait_ms / 1000.0,
    )
    async with AsyncWebCrawler(config=browser) as crawler:
        result = await crawler.arun(url=url, config=run)
    html = result.html or ""
    ok = bool(result.success) and len(html) > 1000 and "Access Denied" not in html
    if "Just a moment" in html or "Attention Required" in html:
        ok = False
    return ok, html, getattr(result, "status_code", None)


def fetch_html_undetected(url: str, *, wait_s: float = 5.0) -> Optional[str]:
    """FlareSolverr-style path: undetected-chromedriver + real Chrome."""
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return None
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=tr-TR")
    driver = None
    try:
        driver = uc.Chrome(options=opts)
        driver.set_page_load_timeout(60)
        driver.get(url)
        time.sleep(wait_s)
        html = driver.page_source
        if "Access Denied" in html or "Just a moment" in html:
            return None
        return html
    except Exception as exc:
        print(f"  undetected-chrome fail {url}: {exc}")
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def extract_teknosa_listing(html: str, base: str) -> list[dict[str, Any]]:
    """Parse Teknosa listing / PDP HTML into feed rows (no invent)."""
    products: list[dict[str, Any]] = []
    # Prefer Product JSON-LD blocks (often embedded on listing pages)
    for block in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            # ItemList
            if str(it.get("@type", "")).endswith("ItemList"):
                for el in it.get("itemListElement") or []:
                    if isinstance(el, dict) and isinstance(el.get("item"), dict):
                        items.append(el["item"])
                continue
            t = it.get("@type")
            types = [str(x) for x in (t if isinstance(t, list) else [t]) if x]
            if not any(x.endswith("Product") or x == "Product" for x in types):
                continue
            url = it.get("url") or it.get("@id") or base
            parsed = parse_jsonld_product(json.dumps(it), str(url))
            # parse_jsonld_product expects full html; rebuild mini html
            mini = (
                f'<script type="application/ld+json">{json.dumps(it)}</script>'
            )
            parsed = parse_jsonld_product(mini, str(url))
            if parsed:
                products.append(parsed)

    # Product detail links for follow-up
    hrefs = re.findall(r'href="(https://www\.teknosa\.com/[^"#?]+\-p\-\d+[^"#?]*)"', html)
    hrefs += [
        urljoin("https://www.teknosa.com", u)
        for u in re.findall(r'href="(/[^"#?]+\-p\-\d+[^"#?]*)"', html)
    ]
    # Listing cards (BerkayKOCAK hint: product-item-inner)
    for m in re.finditer(
        r'class="[^"]*product-item[^"]*"[\s\S]{0,2500}?href="([^"]+\-p\-\d+[^"]*)"[\s\S]{0,1500}?',
        html,
        re.I,
    ):
        chunk = m.group(0)
        href = urljoin("https://www.teknosa.com", m.group(1).split("?")[0])
        name_m = re.search(r'product-name[^>]*>([^<]+)', chunk, re.I) or re.search(
            r'title="([^"]+)"', chunk
        )
        price_m = re.search(r'([\d.]+,\d{2})\s*(?:TL|₺)', chunk) or re.search(
            r'class="[^"]*price[^"]*"[^>]*>\s*([^<]+)', chunk, re.I
        )
        if not name_m or not price_m:
            continue
        name = _clean_name(name_m.group(1))
        price = parse_tr_price(unescape(price_m.group(1)))
        if not name or price is None:
            continue
        pid = re.search(r'-p-(\d+)', href)
        img_m = re.search(r'(?:data-src|src)="(https://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', chunk, re.I)
        products.append(
            {
                "id": pid.group(1) if pid else href.rsplit("/", 1)[-1],
                "name": name,
                "url": href,
                "price": price,
                "currency": "TRY",
                "stock_status": "UNKNOWN",
                "image_url": img_m.group(1) if img_m else None,
                "attributes": {},
            }
        )
    # Ensure unique
    return list({p["id"]: p for p in products if p.get("id")}.values()), list(
        dict.fromkeys(u.split("?")[0] for u in hrefs)
    )


def extract_koctas_cards(html: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for m in re.finditer(r'href="([^"]+/p/(\d+)[^"]*)"', html):
        href, pid = m.group(1), m.group(2)
        start, end = max(0, m.start() - 800), min(len(html), m.end() + 1200)
        chunk = html[start:end]
        name = None
        for pat in (
            r'title="([^"]{6,160})"',
            r'aria-label="([^"]{6,160})"',
            r'<h[23][^>]*>([^<]{6,160})</h[23]>',
        ):
            nm = re.search(pat, chunk, re.I)
            if nm and _clean_name(nm.group(1)):
                name = _clean_name(nm.group(1))
                break
        if not name:
            continue
        price = None
        pm = re.search(r'data-price="([^"]+)"', chunk)
        if pm:
            price = parse_tr_price(pm.group(1))
        if price is None:
            pm = re.search(r'([\d.]+,\d{2})\s*TL', chunk)
            if pm:
                price = parse_tr_price(pm.group(1))
        if price is None:
            continue
        img_m = re.search(
            r'(?:data-src|src)="(https://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
            chunk,
            re.I,
        )
        url = urljoin("https://www.koctas.com.tr", href.split("?")[0])
        products.append(
            {
                "id": pid,
                "name": name,
                "url": url,
                "price": price,
                "currency": "TRY",
                "stock_status": "UNKNOWN",
                "image_url": img_m.group(1) if img_m else None,
                "attributes": {},
            }
        )
    return list({p["id"]: p for p in products}.values())


async def fetch_teknosa(limit: int, delay: float) -> list[dict[str, Any]]:
    # Categories inspired by vendor/e-commerce-crawler + live site
    seeds = [
        "https://www.teknosa.com/",
        "https://www.teknosa.com/laptop-c-100001",
        "https://www.teknosa.com/telefon-c-100002",
        "https://www.teknosa.com/tablet-c-100003",
        "https://www.teknosa.com/televizyon-c-100004",
        "https://www.teknosa.com/beyaz-esya-c-100005",
    ]
    products: list[dict[str, Any]] = []
    pdp_urls: list[str] = []
    for seed in seeds:
        print(f"  crawl4ai {seed}")
        ok, html, status = await crawl_html(seed, wait_ms=2500)
        if not ok:
            print(f"    blocked status={status}; trying undetected-chrome")
            html2 = fetch_html_undetected(seed)
            if not html2:
                continue
            html = html2
        batch, hrefs = extract_teknosa_listing(html, seed)
        print(f"    listing products={len(batch)} pdp_links={len(hrefs)}")
        products.extend(batch)
        for u in hrefs:
            if u not in pdp_urls:
                pdp_urls.append(u)
        await asyncio.sleep(delay)

    # Enrich via PDP JSON-LD until limit
    need = max(0, (limit if limit > 0 else 200) - len({p["id"]: p for p in products}))
    for i, url in enumerate(pdp_urls[: max(need, 0) if limit > 0 else min(len(pdp_urls), 150)]):
        ok, html, _ = await crawl_html(url, wait_ms=1500)
        if not ok:
            continue
        p = parse_jsonld_product(html, url)
        if p:
            products.append(p)
        if (i + 1) % 20 == 0:
            print(f"    pdp {i+1} ok={len({x['id']: x for x in products})}")
            write_feed(
                "src-m-teknosa",
                list({x["id"]: x for x in products}.values()),
                "teknosa crawl4ai checkpoint",
            )
        await asyncio.sleep(delay)
    out = list({p["id"]: p for p in products}.values())
    if limit > 0:
        out = out[:limit]
    return out


async def fetch_koctas(limit: int, delay: float) -> list[dict[str, Any]]:
    seeds = ["https://www.koctas.com.tr/"]
    # Try a few category URLs from public sitemap (may be Akamai-denied)
    import httpx

    try:
        idx = httpx.get(
            "https://www.koctas.com.tr/koctasSitemaps/category-tr-try.xml",
            timeout=40,
            headers={"User-Agent": "TaksitlioBot/0.1"},
            follow_redirects=True,
        )
        if idx.status_code == 200:
            cats = re.findall(r"<loc>([^<]+)</loc>", idx.text)[:12]
            seeds.extend(cats)
    except Exception:
        pass

    products: list[dict[str, Any]] = []
    for seed in seeds:
        print(f"  crawl4ai {seed}")
        ok, html, status = await crawl_html(seed, wait_ms=3000)
        if not ok:
            print(f"    blocked status={status}; trying undetected-chrome")
            html2 = fetch_html_undetected(seed, wait_s=6)
            if not html2:
                continue
            html = html2
            ok = True
        batch = extract_koctas_cards(html)
        print(f"    cards={len(batch)}")
        products.extend(batch)
        await asyncio.sleep(delay)
    out = list({p["id"]: p for p in products}.values())
    if limit > 0:
        out = out[:limit]
    return out


async def fetch_dr(limit: int, delay: float) -> list[dict[str, Any]]:
    """D&R still best via official sitemap + HTTP; crawl4ai optional for blocked PDPs."""
    import httpx

    from fetch_live_merchant_feeds import _dr_product_urls_from_sitemaps

    UA = "TaksitlioBot/0.1 (+ADR-010 polite catalog; ops research)"
    with httpx.Client(timeout=40, headers={"User-Agent": UA}, follow_redirects=True) as client:
        urls = _dr_product_urls_from_sitemaps(client, delay, limit if limit > 0 else 0)
        out: list[dict[str, Any]] = []
        for i, url in enumerate(urls, 1):
            r = client.get(url)
            html = r.text if r.status_code == 200 else ""
            p = parse_jsonld_product(html, url) if html else None
            if not p:
                ok, html2, _ = await crawl_html(url, wait_ms=1000)
                if ok:
                    p = parse_jsonld_product(html2, url)
            if p:
                out.append(p)
            if i % 50 == 0:
                print(f"    ... {i}/{len(urls)} ok={len(out)}")
                write_feed("src-m-dr", list({x["id"]: x for x in out}.values()), "dr crawl4ai/http")
            time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


async def amain() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--merchants", default="teknosa,koctas,dr")
    p.add_argument("--delay", type=float, default=0.6)
    p.add_argument("--limit", type=int, default=100, help="0 = uncapped where supported")
    args = p.parse_args()
    wanted = [m.strip() for m in args.merchants.split(",") if m.strip()]
    OUT.mkdir(parents=True, exist_ok=True)

    fetchers = {
        "teknosa": fetch_teknosa,
        "koctas": fetch_koctas,
        "dr": fetch_dr,
    }
    for code in wanted:
        if code not in fetchers:
            print(f"skip unknown {code}")
            continue
        print(f"fetch {code} via crawl4ai/vendor ...")
        products = await fetchers[code](args.limit, args.delay)
        path = write_feed(f"src-m-{code}", products, f"{code} via crawl4ai/vendor bridge")
        print(f"  {len(products)} -> {path}")


if __name__ == "__main__":
    asyncio.run(amain())
