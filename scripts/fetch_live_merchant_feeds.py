#!/usr/bin/env python3
"""Expand live merchant feeds for publicly verified alışveriş-kredisi partners.

IMPORTANT: Fibabanka/Taksitlio claim 60+ brands, but the named roster is NOT
published on the open web (mobile-app / partner CRM only). This script only
crawls merchants verified from public bank/merchant pages — never invents
partner membership or prices/rates.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "crawler" / "feeds" / "live"
UA = "TaksitlioBot/0.1 (+ADR-010 polite catalog; ops research)"

# Allow `from browser_fetch import ...` when run as scripts/fetch_*.py
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def parse_tr_price(raw: str) -> Optional[float]:
    text = raw.strip().replace("TL", "").replace("₺", "").strip()
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except ValueError:
        return None


def parse_jsonld_product(html: str, url: str) -> Optional[dict[str, Any]]:
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
        # unwrap @graph
        expanded: list[Any] = []
        for it in items:
            if isinstance(it, dict) and "@graph" in it:
                expanded.extend(it["@graph"] if isinstance(it["@graph"], list) else [])
            else:
                expanded.append(it)
        for it in expanded:
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            types = [str(x) for x in (t if isinstance(t, list) else [t]) if x]
            if not any(x.endswith("Product") or x == "Product" for x in types):
                continue
            offers = it.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            try:
                price = float(str(offers.get("price")).replace(",", "."))
            except (TypeError, ValueError):
                continue
            name = it.get("name")
            if not name:
                continue
            brand = it.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            avail = str(offers.get("availability") or "")
            stock = "UNKNOWN"
            if "InStock" in avail:
                stock = "AVAILABLE"
            elif "OutOfStock" in avail:
                stock = "OUT_OF_STOCK"
            img = it.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            attrs: dict[str, Any] = {}
            for ap in it.get("additionalProperty") or []:
                if isinstance(ap, dict) and ap.get("name") is not None:
                    attrs[str(ap["name"])] = ap.get("value")
            return {
                "id": str(
                    it.get("sku")
                    or it.get("productID")
                    or it.get("mpn")
                    or url.rstrip("/").rsplit("-", 1)[-1].replace(".html", "")
                ),
                "name": str(name).strip(),
                "sku": it.get("sku"),
                "gtin": it.get("gtin13") or it.get("gtin") or it.get("gtin14"),
                "ean": it.get("gtin13") or it.get("ean"),
                "mpn": it.get("mpn"),
                "brand": brand,
                "model": it.get("model"),
                "url": url,
                "price": price,
                "list_price": _opt_float(offers.get("highPrice") or offers.get("listPrice")),
                "currency": offers.get("priceCurrency") or "TRY",
                "stock_status": stock,
                "image_url": img,
                "attributes": attrs,
                "category": it.get("category"),
            }
    # OpenGraph + embedded price fallback (no invent)
    om = re.search(
        r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I
    ) or re.search(
        r'content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html, re.I
    )
    pm = re.search(r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)"?', html)
    img_m = re.search(
        r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I
    )
    if om and pm:
        price = float(pm.group(1).replace(",", "."))
        name = unescape(om.group(1)).strip()
        # strip site suffix noise
        name = re.sub(r"\s*[|·].*$", "", name).strip()
        pid = url.rstrip("/").rsplit("-", 1)[-1].replace(".html", "")
        return {
            "id": pid,
            "name": name,
            "url": url,
            "price": price,
            "currency": "TRY",
            "stock_status": "UNKNOWN",
            "image_url": img_m.group(1) if img_m else None,
            "attributes": {},
        }
    return None


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_vatan_listing(html: str) -> list[dict[str, Any]]:
    starts = [m.start() for m in re.finditer(r'<div class="product-list product-list--', html)]
    starts.append(len(html))
    products: list[dict[str, Any]] = []
    for i in range(len(starts) - 1):
        chunk = html[starts[i] : starts[i + 1]]
        name_m = re.search(r'product-list__product-name">\s*<h3>(.*?)</h3>', chunk, re.S)
        price_m = re.search(r"product-list__price[^>]*>\s*([^<\s]+)", chunk)
        if not name_m or not price_m:
            continue
        name = unescape(name_m.group(1)).strip()
        price = parse_tr_price(unescape(price_m.group(1)))
        if price is None:
            continue
        code_m = re.search(r'product-list__product-code">\s*([^<]+)\s*<', chunk)
        sku = unescape(code_m.group(1)).strip() if code_m else None
        href_m = re.search(
            r'href="(https://www\.vatanbilgisayar\.com/[^"]+\.html)"', chunk
        ) or re.search(
            r"data-href='(https://www\.vatanbilgisayar\.com/[^']+\.html)'", chunk
        )
        if not href_m:
            continue
        url = href_m.group(1)
        img_m = re.search(
            r'data-src="(https://cdn\.vatanbilgisayar\.com/Upload/PRODUCT/[^"]+)"',
            chunk,
        )
        img = img_m.group(1) if img_m else None
        brand = None
        if img:
            bm = re.search(r"/PRODUCT/([^/]+)/", img)
            if bm:
                brand = bm.group(1).replace("-", " ").title()
        pid = sku or url.rsplit("/", 1)[-1].removesuffix(".html")
        attrs: dict[str, Any] = {}
        ram = re.search(r"(\d+)\s*Gb", name, re.I)
        if ram:
            attrs["ram_gb_raw"] = int(ram.group(1))
        products.append(
            {
                "id": pid,
                "name": name,
                "sku": sku or pid,
                "brand": brand,
                "url": url,
                "price": price,
                "currency": "TRY",
                "stock_status": "UNKNOWN",
                "image_url": img,
                "attributes": attrs,
            }
        )
    return list({p["id"]: p for p in products}.values())


def _apply_limit(urls: list[str], limit: int) -> list[str]:
    """limit<=0 means no cap."""
    if limit and limit > 0:
        return urls[:limit]
    return urls


def fetch_listing_then_jsonld(
    client: httpx.Client,
    *,
    category_urls: list[str],
    product_href_re: str,
    delay: float,
    limit: int,
    base: str = "",
) -> list[dict[str, Any]]:
    product_urls: list[str] = []
    for cat in category_urls:
        r = client.get(cat)
        if r.status_code != 200:
            time.sleep(delay)
            continue
        found = re.findall(product_href_re, r.text)
        for u in found:
            if u.startswith("/"):
                u = urljoin(base or cat, u)
            if u not in product_urls:
                product_urls.append(u)
        time.sleep(delay)
    product_urls = _apply_limit(product_urls, limit)
    out: list[dict[str, Any]] = []
    for i, url in enumerate(product_urls, 1):
        r = client.get(url)
        if r.status_code == 200:
            p = parse_jsonld_product(r.text, url)
            if p:
                out.append(p)
        if i % 25 == 0:
            print(f"    ... {i}/{len(product_urls)} fetched, {len(out)} ok")
        time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


def fetch_vatan(client: httpx.Client, delay: float, limit: int = 0) -> list[dict[str, Any]]:
    cats = [
        "https://www.vatanbilgisayar.com/notebook/",
        "https://www.vatanbilgisayar.com/cep-telefonu-modelleri/",
        "https://www.vatanbilgisayar.com/tablet/",
        "https://www.vatanbilgisayar.com/televizyon/",
        "https://www.vatanbilgisayar.com/beyaz-esya/",
        "https://www.vatanbilgisayar.com/oyun-bilgisayari/",
        "https://www.vatanbilgisayar.com/fotograf-makinesi/",
        "https://www.vatanbilgisayar.com/yazici/",
        "https://www.vatanbilgisayar.com/monitor/",
        "https://www.vatanbilgisayar.com/kulaklik/",
    ]
    out: list[dict[str, Any]] = []
    for cat in cats:
        page = 1
        while True:
            url = cat if page == 1 else f"{cat}?page={page}"
            r = client.get(url)
            if r.status_code != 200:
                break
            batch = parse_vatan_listing(r.text)
            if not batch:
                break
            before = len(out)
            out.extend(batch)
            out = list({p["id"]: p for p in out}.values())
            added = len(out) - before
            print(f"  vatan {cat.split('.com/')[1]} page={page} +{added} total={len(out)}")
            if limit > 0 and len(out) >= limit:
                return out[:limit]
            if added == 0:
                break
            # discover max page from links
            pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", r.text)]
            max_page = max(pages) if pages else page
            page += 1
            if page > max_page:
                break
            time.sleep(delay)
        time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


def _mediamarkt_product_urls_from_sitemaps(
    client: httpx.Client, delay: float, limit: int
) -> list[str]:
    idx = client.get("https://www.mediamarkt.com.tr/sitemaps/sitemap-index.xml")
    if idx.status_code != 200:
        return []
    maps = [
        u
        for u in re.findall(r"<loc>([^<]+)</loc>", idx.text)
        if "sitemap-productdetailspages-" in u
    ]
    urls: list[str] = []
    for sm in maps:
        time.sleep(delay)
        r = client.get(sm)
        if r.status_code != 200:
            continue
        for u in re.findall(r"<loc>([^<]+)</loc>", r.text):
            if "/tr/product/" in u and u not in urls:
                urls.append(u)
                if limit > 0 and len(urls) >= limit:
                    return urls
        print(f"  mediamarkt sitemap {sm.rsplit('/', 1)[-1]} urls={len(urls)}")
    return urls


def fetch_mediamarkt(client: httpx.Client, delay: float, limit: int) -> list[dict[str, Any]]:
    product_urls = _mediamarkt_product_urls_from_sitemaps(client, delay, limit)
    if not product_urls:
        # fallback: category listing
        cats = [
            "https://www.mediamarkt.com.tr/tr/category/laptop-504926.html",
            "https://www.mediamarkt.com.tr/tr/category/cep-telefonlari-504171.html",
            "https://www.mediamarkt.com.tr/tr/category/tabletler-639520.html",
            "https://www.mediamarkt.com.tr/tr/category/oyuncu-laptop-878043.html",
        ]
        for cat in cats:
            r = client.get(cat)
            if r.status_code != 200:
                time.sleep(delay)
                continue
            links = re.findall(
                r'href="(https://www\.mediamarkt\.com\.tr/tr/product/[^"#?]+)"', r.text
            )
            links += [
                "https://www.mediamarkt.com.tr" + u
                for u in re.findall(r'href="(/tr/product/[^"#?]+)"', r.text)
            ]
            for u in links:
                if u not in product_urls:
                    product_urls.append(u)
            time.sleep(delay)
        product_urls = _apply_limit(product_urls, limit)
    out: list[dict[str, Any]] = []
    for i, url in enumerate(product_urls, 1):
        r = client.get(url)
        if r.status_code == 200:
            p = parse_jsonld_product(r.text, url)
            if p:
                out.append(p)
        if i % 50 == 0:
            print(f"    ... {i}/{len(product_urls)} fetched, {len(out)} ok")
            # checkpoint so long unlimited runs are durable
            write_feed(
                "src-m-mediamarkt",
                list({p["id"]: p for p in out}.values()),
                "mediamarkt live capture (checkpoint)",
            )
        time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


def fetch_koctas(client: httpx.Client, delay: float, limit: int) -> list[dict[str, Any]]:
    """Koçtaş: PDP/category often Akamai-denied; use Playwright homepage + allowed pages.

    Full catalog needs FlareSolverr/residential proxy or merchant feed.
    Sitemap product URLs are public but PDP HTML is blocked from many IPs.
    """
    from browser_fetch import fetch_html_playwright

    html = fetch_html_playwright("https://www.koctas.com.tr/", wait_ms=5000)
    if not html:
        print("  koctas: browser blocked — set FLARESOLVERR_URL or provide partner feed")
        return []
    # Extract from DOM-like structure in rendered HTML via JS-equivalent regex cleanup
    products: list[dict[str, Any]] = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(locale="tr-TR").new_page()
            page.set_content(html, wait_until="domcontentloaded")
            rows = page.evaluate(
                """() => {
                const out=[]; const seen=new Set();
                document.querySelectorAll('a[href*="/p/"]').forEach(a=>{
                  const href=a.getAttribute('href')||'';
                  const m=href.match(/\\/p\\/(\\d+)/);
                  if(!m || seen.has(m[1])) return;
                  let root=a.closest('[data-price], .product-item, [class*="product-card"], li, article');
                  if(!root) root=a.parentElement?.parentElement || a.parentElement;
                  const priceAttr=root?.getAttribute?.('data-price')
                    || root?.querySelector?.('[data-price]')?.getAttribute('data-price');
                  const pm=(root?.innerText||'').match(/(\\d{1,3}(?:\\.\\d{3})*,\\d{2}|\\d+,\\d{2})\\s*TL/);
                  let name=(a.getAttribute('title')||a.getAttribute('aria-label')||'').trim();
                  if(!name){
                    name=root?.querySelector?.('h2,h3,[class*="name"],[class*="title"]')?.textContent?.trim()||'';
                  }
                  if(!name || name.length<6) return;
                  if(/next slide|previous|sepete|çok satan|alışverişe başla|indirim fırsatı/i.test(name)) return;
                  let price=null;
                  if(priceAttr){
                    const raw=String(priceAttr);
                    price=parseFloat(raw.includes(',') ? raw.replace(/\\./g,'').replace(',','.') : raw);
                  } else if(pm){
                    price=parseFloat(pm[1].replace(/\\./g,'').replace(',','.'));
                  }
                  if(!(price>0)) return;
                  let img=root?.querySelector('img[src^="http"]')?.getAttribute('src')
                    || root?.querySelector('img[data-src^="http"]')?.getAttribute('data-src') || null;
                  if(img && img.startsWith('data:')) img=null;
                  const url=href.startsWith('http') ? href.split('?')[0]
                    : ('https://www.koctas.com.tr'+href.split('?')[0]);
                  seen.add(m[1]);
                  out.push({id:m[1], name:name.slice(0,200), url, price, image_url:img,
                            currency:'TRY', stock_status:'UNKNOWN', attributes:{}});
                });
                return out;
            }"""
            )
            browser.close()
            products = list(rows or [])
    except Exception as exc:
        print(f"  koctas extract fail: {exc}")
        return []
    products = _apply_limit(products, limit)
    time.sleep(delay)
    return list({p["id"]: p for p in products}.values())


def _dr_product_urls_from_sitemaps(client: httpx.Client, delay: float, limit: int) -> list[str]:
    idx = client.get("https://www.dr.com.tr/sitemaps/products.xml")
    if idx.status_code != 200:
        return []
    maps = re.findall(r"<loc>([^<]+)</loc>", idx.text)
    urls: list[str] = []
    for sm in maps:
        time.sleep(delay)
        r = client.get(sm)
        if r.status_code != 200:
            continue
        for u in re.findall(r"<loc>([^<]+)</loc>", r.text):
            if "dr.com.tr" in u and u not in urls:
                urls.append(u)
                if limit > 0 and len(urls) >= limit:
                    return urls
        print(f"  dr sitemap {sm.rsplit('/', 1)[-1]} urls={len(urls)}")
    return urls


def fetch_dr(client: httpx.Client, delay: float, limit: int) -> list[dict[str, Any]]:
    """D&R: official product sitemaps + JSON-LD (category HTML has almost no PDP links)."""
    product_urls = _dr_product_urls_from_sitemaps(client, delay, limit)
    out: list[dict[str, Any]] = []
    for i, url in enumerate(product_urls, 1):
        r = client.get(url)
        if r.status_code == 200:
            p = parse_jsonld_product(r.text, url)
            if p:
                out.append(p)
        if i % 50 == 0:
            print(f"    ... {i}/{len(product_urls)} fetched, {len(out)} ok")
            write_feed(
                "src-m-dr",
                list({p["id"]: p for p in out}.values()),
                "dr live capture (checkpoint)",
            )
        time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


def fetch_teknosa(client: httpx.Client, delay: float, limit: int) -> list[dict[str, Any]]:
    """Teknosa: Cloudflare blocks plain HTTP; try Playwright, then FlareSolverr."""
    from browser_fetch import fetch_html, fetch_html_flaresolverr

    seeds = [
        "https://www.teknosa.com/",
        "https://www.teknosa.com/laptop-c-100001",
        "https://www.teknosa.com/telefon-c-100002",
    ]
    product_urls: list[str] = []
    for seed in seeds:
        html = fetch_html(seed) or fetch_html_flaresolverr(seed)
        if not html:
            print(f"  teknosa blocked: {seed}")
            continue
        found = re.findall(r'href="(https://www\.teknosa\.com/[^"#?]+\-p\-\d+[^"#?]*)"', html)
        found += [
            "https://www.teknosa.com" + u
            for u in re.findall(r'href="(/[^"#?]+\-p\-\d+[^"#?]*)"', html)
        ]
        for u in found:
            if u not in product_urls:
                product_urls.append(u.split("?")[0])
        time.sleep(delay)
    product_urls = _apply_limit(product_urls, limit)
    out: list[dict[str, Any]] = []
    for i, url in enumerate(product_urls, 1):
        html = fetch_html(url)
        if html:
            p = parse_jsonld_product(html, url)
            if p:
                out.append(p)
        if i % 20 == 0:
            print(f"    ... {i}/{len(product_urls)} fetched, {len(out)} ok")
        time.sleep(delay)
    return list({p["id"]: p for p in out}.values())


def write_feed(source_code: str, products: list[dict[str, Any]], source: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{source_code}.json"
    # Drop incomplete rows (no invent)
    clean = [p for p in products if p.get("name") and p.get("price") is not None and p.get("url")]
    path.write_text(
        json.dumps(
            {
                "products": clean,
                "source": source,
                "count": len(clean),
                "quality": "live_polite_capture",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


FETCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "vatan": lambda c, d, lim: fetch_vatan(c, d, lim),
    "mediamarkt": lambda c, d, lim: fetch_mediamarkt(c, d, lim),
    "koctas": lambda c, d, lim: fetch_koctas(c, d, lim),
    "dr": lambda c, d, lim: fetch_dr(c, d, lim),
    "teknosa": lambda c, d, lim: fetch_teknosa(c, d, lim),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--merchants",
        default="vatan,mediamarkt,koctas,dr,teknosa",
        help="comma list: vatan,mediamarkt,koctas,dr,teknosa",
    )
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="max products per merchant; 0 = no limit",
    )
    args = p.parse_args()
    wanted = [m.strip() for m in args.merchants.split(",") if m.strip()]

    with httpx.Client(timeout=40.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for code in wanted:
            if code not in FETCHERS:
                print(f"skip unknown {code}")
                continue
            print(f"fetch {code} ...")
            try:
                products = FETCHERS[code](client, args.delay, args.limit)
                path = write_feed(f"src-m-{code}", products, f"{code} live capture")
                print(f"  {len(products)} -> {path}")
            except Exception as exc:
                print(f"  FAIL {exc}")


if __name__ == "__main__":
    main()
