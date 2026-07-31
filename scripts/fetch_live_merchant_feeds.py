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

# Hard stop across all src-m-* live feeds (MinIO/DB size guard). 0 disables.
DEFAULT_GLOBAL_PRODUCT_CAP = 1_000_000
_ACTIVE_GLOBAL_CAP = DEFAULT_GLOBAL_PRODUCT_CAP

# Allow `from browser_fetch import ...` when run as scripts/fetch_*.py
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def set_global_product_cap(cap: int) -> None:
    """Set process-wide live-feed product ceiling (0 = disabled)."""

    global _ACTIVE_GLOBAL_CAP
    _ACTIVE_GLOBAL_CAP = max(0, int(cap))


def active_global_product_cap() -> int:
    return _ACTIVE_GLOBAL_CAP


def count_live_feed_products(
    *,
    exclude_source: Optional[str] = None,
    feed_dir: Optional[Path] = None,
) -> int:
    """Sum `count` (or products length) across live merchant feed JSON files."""

    root = feed_dir or OUT
    if not root.is_dir():
        return 0
    total = 0
    exclude = (exclude_source or "").removesuffix(".json")
    for path in sorted(root.glob("src-m-*.json")):
        stem = path.stem
        if exclude and stem == exclude:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        n = data.get("count")
        if not isinstance(n, int):
            n = len(data.get("products") or [])
        total += max(0, int(n))
    return total


def global_room_for_source(
    source_code: str,
    *,
    global_cap: Optional[int] = None,
    feed_dir: Optional[Path] = None,
) -> int:
    """How many products this source may hold before the global cap is hit."""

    cap = active_global_product_cap() if global_cap is None else max(0, int(global_cap))
    if cap <= 0:
        return 10**18
    others = count_live_feed_products(exclude_source=source_code, feed_dir=feed_dir)
    return max(0, cap - others)


def merchant_absolute_cap(
    source_code: str,
    per_merchant_limit: int,
    *,
    global_cap: Optional[int] = None,
    feed_dir: Optional[Path] = None,
) -> Optional[int]:
    """Absolute max ``len(by_id)`` for this merchant. ``None`` = uncapped."""

    caps: list[int] = []
    if per_merchant_limit and per_merchant_limit > 0:
        caps.append(int(per_merchant_limit))
    g = active_global_product_cap() if global_cap is None else max(0, int(global_cap))
    if g > 0:
        caps.append(global_room_for_source(source_code, global_cap=g, feed_dir=feed_dir))
    if not caps:
        return None
    return max(0, min(caps))


def resolve_run_limit(
    per_merchant_limit: int,
    *,
    source_code: str,
    global_cap: Optional[int] = None,
    feed_dir: Optional[Path] = None,
) -> int:
    """Effective fetcher limit for this run. ``0`` means stop (no room / no work).

    When per-merchant limit is 0 (historically uncapped), the global ceiling
    becomes the limit so crawls still stop at 1M total products.
    """

    abs_cap = merchant_absolute_cap(
        source_code,
        per_merchant_limit,
        global_cap=global_cap,
        feed_dir=feed_dir,
    )
    if abs_cap is None:
        return 0 if per_merchant_limit == 0 else int(per_merchant_limit)
    return int(abs_cap)


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
            if not isinstance(offers, dict):
                offers = {}
            try:
                price_raw = (
                    offers.get("price")
                    or offers.get("lowPrice")
                    or offers.get("highPrice")
                )
                price = float(str(price_raw).replace(",", "."))
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
            if stock == "UNKNOWN" and "AggregateOffer" in str(offers.get("@type") or ""):
                stock = "AVAILABLE"
            img = _normalize_image_url(it.get("image"))
            attrs: dict[str, Any] = {}
            for ap in it.get("additionalProperty") or []:
                if isinstance(ap, dict) and ap.get("name") is not None:
                    attrs[str(ap["name"])] = ap.get("value")
            clean_name = _clean_product_name(str(name).strip())
            pid = str(
                it.get("sku")
                or it.get("productID")
                or it.get("mpn")
                or it.get("gtin13")
                or url.rstrip("/").rsplit("-", 1)[-1].replace(".html", "")
            )
            # Prefer stable numeric id from -p- / trailing slug digits
            m_pid = re.search(r"-p-(\d+)\b", url) or re.search(
                r"-(\d{6,})\s*$", url.rstrip("/")
            )
            if m_pid and (not pid.isdigit() or len(pid) < 5):
                pid = m_pid.group(1)
            return {
                "id": pid,
                "name": clean_name,
                "sku": it.get("sku"),
                "gtin": it.get("gtin13") or it.get("gtin") or it.get("gtin14"),
                "ean": it.get("gtin13") or it.get("ean"),
                "mpn": it.get("mpn"),
                "brand": brand,
                "model": it.get("model"),
                "url": url,
                "price": price,
                "list_price": _opt_float(
                    offers.get("highPrice") or offers.get("listPrice")
                ),
                "currency": str(offers.get("priceCurrency") or "TRY").upper(),
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
    # n11 SPA payload: displayPriceNumber + groupId (URL trailing id)
    if "n11.com" in url:
        n11 = _parse_n11_embedded(html, url)
        if n11:
            return n11
    if om and pm:
        price = float(pm.group(1).replace(",", "."))
        name = _clean_product_name(unescape(om.group(1)).strip())
        clean_url = url.split("?")[0].split("#")[0]
        pid = _stable_product_id(html, clean_url)
        img = img_m.group(1) if img_m else None
        if img and img.startswith("/"):
            from urllib.parse import urljoin as _uj

            img = _uj(clean_url, img)
        return {
            "id": pid,
            "name": name,
            "url": clean_url,
            "price": price,
            "currency": "TRY",
            "stock_status": "UNKNOWN",
            "image_url": img,
            "attributes": {},
        }
    # title + displayPriceNumber generic fallback (n11-like)
    title_m = re.search(r"<title>([^<]+)", html, re.I) or re.search(
        r"<h1[^>]*>([^<]+)", html, re.I
    )
    dpn = re.search(r'"displayPriceNumber"\s*:\s*(\d+(?:\.\d+)?)', html)
    if title_m and dpn and "n11.com" in url:
        clean_url = url.split("?")[0].split("#")[0]
        return {
            "id": _stable_product_id(html, clean_url),
            "name": _clean_product_name(unescape(title_m.group(1)).strip()),
            "url": clean_url,
            "price": float(dpn.group(1)),
            "currency": "TRY",
            "stock_status": "UNKNOWN",
            "image_url": None,
            "attributes": {},
        }
    return None


def _parse_n11_embedded(html: str, url: str) -> Optional[dict[str, Any]]:
    clean_url = url.split("?")[0].split("#")[0]
    pid_m = re.search(r"-(\d+)\s*$", clean_url.rstrip("/"))
    if not pid_m:
        return None
    pid = pid_m.group(1)
    # Prefer price bound to this groupId
    m = re.search(
        rf'"displayPriceNumber"\s*:\s*(\d+(?:\.\d+)?)[\s\S]{{0,1500}}?"groupId"\s*:\s*{re.escape(pid)}\b',
        html,
    )
    if not m:
        m = re.search(
            rf'"groupId"\s*:\s*{re.escape(pid)}\b[\s\S]{{0,1500}}?"displayPriceNumber"\s*:\s*(\d+(?:\.\d+)?)',
            html,
        )
    if not m:
        return None
    price = float(m.group(1))
    name_m = re.search(r"<h1[^>]*>([^<]+)", html, re.I) or re.search(
        r"<title>([^<]+)", html, re.I
    )
    if not name_m:
        return None
    img = None
    img_m = re.search(
        rf'"groupId"\s*:\s*{re.escape(pid)}[\s\S]{{0,2500}}?"path"\s*:\s*"(https://n11scdn[^"]+)"',
        html,
    ) or re.search(r'(https://n11scdn[^"]+\.jpg)', html)
    if img_m:
        img = img_m.group(1).replace("{0}", "1000_1426")
    return {
        "id": pid,
        "name": _clean_product_name(unescape(name_m.group(1)).strip()),
        "url": clean_url,
        "price": price,
        "currency": "TRY",
        "stock_status": "UNKNOWN",
        "image_url": img,
        "attributes": {},
    }


def _stable_product_id(html: str, url: str) -> str:
    """Prefer merchant product codes over noisy URL slug tails."""
    clean_url = url.split("?")[0].split("#")[0]
    for pat in (
        r'data-product-code=["\']([^"\']+)',
        r'data-product-id=["\']([^"\']+)',
        r'"sku"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    for pat in (r"/_/R-p-(\d+)", r"-p-(\d+)\b", r"/(\d{6,})(?:/|$)"):
        m = re.search(pat, clean_url)
        if m:
            return m.group(1)
    return clean_url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")[:80]


def _curl_get(url: str, *, impersonate: str) -> Optional[str]:
    from browser_fetch import curl_cffi_get

    return curl_cffi_get(url, impersonate=impersonate, timeout=90.0)


def fetch_hybris_product_sitemap(
    *,
    source_code: str,
    sitemap_index: str,
    delay: float,
    limit: int,
    workers: int = 6,
    impersonate: str = "chrome124",
) -> list[dict[str, Any]]:
    """Arçelik/Beko-style Hybris PRODUCT-tr-TRY sitemap via curl_cffi."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from html import unescape as _ue

    idx_html = _curl_get(sitemap_index, impersonate=impersonate)
    if not idx_html:
        print(f"  {source_code} sitemap index blocked")
        return list(_load_existing_feed(source_code).values())
    maps = [
        _ue(u)
        for u in re.findall(r"<loc>([^<]+)</loc>", idx_html)
        if "PRODUCT" in u.upper()
    ]
    product_urls: list[str] = []
    seen: set[str] = set()
    for sm in maps:
        time.sleep(max(delay, 0.1))
        body = _curl_get(sm, impersonate=impersonate)
        if not body:
            print(f"  {source_code} product map fail")
            continue
        batch = 0
        for u in re.findall(r"<loc>([^<]+)</loc>", body):
            u = _ue(u).split("?")[0]
            if u not in seen:
                seen.add(u)
                product_urls.append(u)
                batch += 1
        print(f"  {source_code} sitemap +{batch} total={len(product_urls)}")

    by_id = _load_existing_feed(source_code)
    abs_cap = merchant_absolute_cap(source_code, limit)
    if abs_cap is not None and abs_cap <= 0:
        print(f"  {source_code} global product cap reached — skip")
        return list(by_id.values())
    if abs_cap is not None and len(by_id) >= abs_cap:
        print(f"  {source_code} at cap {abs_cap} — skip")
        return list(by_id.values())[:abs_cap]

    done = {str(p.get("url")) for p in by_id.values() if p.get("url")}
    todo = [u for u in product_urls if u not in done]
    if abs_cap is not None:
        room = max(0, abs_cap - len(by_id))
        todo = todo[:room]
    else:
        todo = _apply_limit(todo, limit)
    print(
        f"  {source_code} todo={len(todo)} catalog={len(product_urls)} "
        f"workers={workers} abs_cap={abs_cap}",
        flush=True,
    )

    lock = threading.Lock()
    fail = 0
    done_n = 0

    def _one(url: str) -> Optional[dict[str, Any]]:
        html = _curl_get(url, impersonate=impersonate)
        if not html:
            return None
        p = parse_jsonld_product(html, url)
        if p and p.get("image_url") and str(p["image_url"]).startswith("/"):
            p["image_url"] = urljoin(url, str(p["image_url"]))
        return p

    batch_size = 300
    for start in range(0, len(todo), batch_size):
        live_cap = merchant_absolute_cap(source_code, limit)
        if live_cap is not None and len(by_id) >= live_cap:
            print(f"  {source_code} stopped at global/merchant cap {live_cap}", flush=True)
            break
        batch = todo[start : start + batch_size]
        if live_cap is not None:
            batch = batch[: max(0, live_cap - len(by_id))]
        if not batch:
            break
        print(f"  {source_code} batch {start}-{start + len(batch) - 1}", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(_one, u): u for u in batch}
            for fut in as_completed(futs):
                try:
                    p = fut.result()
                except Exception:
                    p = None
                with lock:
                    done_n += 1
                    if p and p.get("id"):
                        if live_cap is None or len(by_id) < live_cap:
                            by_id[str(p["id"])] = p
                    else:
                        fail += 1
                    if done_n % 150 == 0 or done_n == len(todo):
                        write_feed(
                            source_code,
                            list(by_id.values()),
                            f"{source_code} curl_cffi sitemap (checkpoint)",
                        )
                        print(
                            f"    ... {done_n}/{len(todo)} ok={len(by_id)} fail={fail}",
                            flush=True,
                        )
        if delay > 0:
            time.sleep(delay)
    out = list(by_id.values())
    live_cap = merchant_absolute_cap(source_code, limit)
    if live_cap is not None:
        out = out[:live_cap]
    return out


def fetch_arcelik(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 6
) -> list[dict[str, Any]]:
    return fetch_hybris_product_sitemap(
        source_code="src-m-arcelik",
        sitemap_index="https://www.arcelik.com.tr/sitemap.xml",
        delay=delay,
        limit=limit,
        workers=workers,
        impersonate="chrome124",
    )


def fetch_beko(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 6
) -> list[dict[str, Any]]:
    return fetch_hybris_product_sitemap(
        source_code="src-m-beko",
        sitemap_index="https://www.beko.com.tr/sitemap.xml",
        delay=delay,
        limit=limit,
        workers=workers,
        impersonate="chrome124",
    )


def fetch_decathlon(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 6
) -> list[dict[str, Any]]:
    """Decathlon TR: category sitemaps → /p/... PDP via safari TLS impersonation."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from html import unescape as _ue

    impersonate = "safari18_0"
    idx = _curl_get("https://www.decathlon.com.tr/sitemap/index.xml", impersonate=impersonate)
    if not idx:
        print("  decathlon sitemap index blocked")
        return list(_load_existing_feed("src-m-decathlon").values())
    cat_maps = [
        _ue(u)
        for u in re.findall(r"<loc>([^<]+)</loc>", idx)
        if "category-product-listing" in u
    ]
    category_urls: list[str] = []
    for sm in cat_maps:
        body = _curl_get(sm, impersonate=impersonate)
        if not body:
            continue
        for u in re.findall(r"<loc>([^<]+)</loc>", body):
            category_urls.append(_ue(u).split("?")[0])
    print(f"  decathlon categories={len(category_urls)}")

    pdp_urls: list[str] = []
    seen: set[str] = set()
    for i, cat in enumerate(category_urls, 1):
        html = _curl_get(cat, impersonate=impersonate)
        if html:
            for href in re.findall(r'href="(/p/[^"?]+)"', html):
                full = urljoin("https://www.decathlon.com.tr", href.split("?")[0])
                # strip tracking query already; normalize R-p id path
                if full not in seen:
                    seen.add(full)
                    pdp_urls.append(full)
        if i % 50 == 0:
            print(f"    cats {i}/{len(category_urls)} pdps={len(pdp_urls)}", flush=True)
        time.sleep(max(delay, 0.05))

    by_id = _load_existing_feed("src-m-decathlon")
    done = {str(p.get("url")) for p in by_id.values() if p.get("url")}
    todo = [u for u in pdp_urls if u not in done]
    todo = _apply_limit(todo, limit)
    print(f"  decathlon todo={len(todo)} catalog={len(pdp_urls)} workers={workers}")

    lock = threading.Lock()
    fail = 0
    done_n = 0

    def _one(url: str) -> Optional[dict[str, Any]]:
        html = _curl_get(url, impersonate=impersonate)
        if not html:
            return None
        return parse_jsonld_product(html, url)

    batch_size = 250
    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        print(f"  decathlon batch {start}-{start + len(batch) - 1}", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(_one, u): u for u in batch}
            for fut in as_completed(futs):
                try:
                    p = fut.result()
                except Exception:
                    p = None
                with lock:
                    done_n += 1
                    if p and p.get("id"):
                        by_id[str(p["id"])] = p
                    else:
                        fail += 1
                    if done_n % 100 == 0 or done_n == len(todo):
                        write_feed(
                            "src-m-decathlon",
                            list(by_id.values()),
                            "decathlon curl_cffi category→pdp (checkpoint)",
                        )
                        print(
                            f"    ... {done_n}/{len(todo)} ok={len(by_id)} fail={fail}",
                            flush=True,
                        )
        if delay > 0:
            time.sleep(delay)
    return list(by_id.values())


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _normalize_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    if isinstance(img, list):
        return _normalize_image_url(img[0] if img else None)
    if isinstance(img, dict):
        for key in ("url", "contentUrl", "@id"):
            val = img.get(key)
            if isinstance(val, list) and val:
                return _normalize_image_url(val[0])
            if isinstance(val, str) and val.startswith("http"):
                return val
        return None
    if isinstance(img, str) and img.startswith("http"):
        return img
    return None


def _load_existing_feed(source_code: str) -> dict[str, dict[str, Any]]:
    path = OUT / f"{source_code}.json"
    if not path.exists():
        return {}
    try:
        prev = json.loads(path.read_text(encoding="utf-8")).get("products") or []
        return {str(p["id"]): p for p in prev if p.get("id")}
    except Exception as exc:
        print(f"  resume skip {source_code}: {exc}")
        return {}


def _collect_sitemap_locs(
    client: httpx.Client,
    *,
    index_url: Optional[str] = None,
    map_urls: Optional[list[str]] = None,
    map_filter: Optional[Callable[[str], bool]] = None,
    delay: float = 0.2,
) -> list[str]:
    """Collect product page URLs from sitemap index and/or direct sitemap list."""
    maps: list[str] = list(map_urls or [])
    if index_url:
        r = client.get(index_url)
        if r.status_code != 200:
            print(f"  sitemap index HTTP {r.status_code} {index_url}")
        else:
            children = re.findall(r"<loc>([^<]+)</loc>", r.text)
            for u in children:
                u = unescape(u)
                if map_filter and not map_filter(u):
                    continue
                if u not in maps:
                    maps.append(u)
    urls: list[str] = []
    seen: set[str] = set()
    for sm in maps:
        time.sleep(max(delay, 0.05))
        r = client.get(sm)
        if r.status_code != 200:
            print(f"  sitemap fail {sm} -> {r.status_code}")
            continue
        batch = 0
        for u in re.findall(r"<loc>([^<]+)</loc>", r.text):
            u = unescape(u).split("?")[0]
            if u.endswith(".xml"):
                continue
            if u not in seen:
                seen.add(u)
                urls.append(u)
                batch += 1
        print(f"  sitemap {sm.rsplit('/', 1)[-1][:60]} +{batch} total={len(urls)}")
    return urls


def fetch_sitemap_jsonld_catalog(
    client: httpx.Client,
    *,
    source_code: str,
    delay: float,
    limit: int,
    index_url: Optional[str] = None,
    map_urls: Optional[list[str]] = None,
    map_filter: Optional[Callable[[str], bool]] = None,
    workers: int = 6,
    use_cloudscraper: bool = False,
    use_curl_cffi: bool = False,
    curl_impersonate: str = "chrome124",
    url_filter: Optional[Callable[[str], bool]] = None,
) -> list[dict[str, Any]]:
    """Sitemap → PDP JSON-LD. Streams map-by-map (no multi-million URL lists)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from html import unescape as _ue

    maps: list[str] = list(map_urls or [])
    if index_url:
        if use_curl_cffi:
            idx = _curl_get(index_url, impersonate=curl_impersonate)
            if not idx:
                print(f"  {source_code} sitemap index blocked")
                return list(_load_existing_feed(source_code).values())
            children = re.findall(r"<loc>([^<]+)</loc>", idx)
        else:
            r = client.get(index_url)
            if r.status_code != 200:
                print(f"  {source_code} sitemap index HTTP {r.status_code}")
                return list(_load_existing_feed(source_code).values())
            children = re.findall(r"<loc>([^<]+)</loc>", r.text)
        for u in children:
            u = _ue(u)
            if map_filter and not map_filter(u):
                continue
            if u not in maps:
                maps.append(u)

    by_id = _load_existing_feed(source_code)
    if by_id:
        print(f"  {source_code} resume {len(by_id)} products")
    done_urls = {str(p.get("url")) for p in by_id.values() if p.get("url")}
    lock = threading.Lock()
    fail = 0
    fetched = 0
    tls = threading.local()

    def _budget() -> Optional[int]:
        return merchant_absolute_cap(source_code, limit)

    budget = _budget()
    if budget is not None and budget <= 0:
        print(f"  {source_code} global product cap reached — skip")
        return list(by_id.values())
    if budget is not None and len(by_id) >= budget:
        print(f"  {source_code} at cap {budget} — skip")
        return list(by_id.values())[:budget]
    print(
        f"  {source_code} maps={len(maps)} workers={workers} "
        f"curl={use_curl_cffi} abs_cap={budget}",
        flush=True,
    )

    def _session():
        if use_cloudscraper:
            s = getattr(tls, "scraper", None)
            if s is None:
                from browser_fetch import create_cloudscraper

                s = create_cloudscraper()
                tls.scraper = s
            return s
        return client

    def _one(url: str) -> Optional[dict[str, Any]]:
        if use_curl_cffi:
            html = _curl_get(url, impersonate=curl_impersonate)
            if not html:
                return None
            return parse_jsonld_product(html, url)
        local = _session()
        try:
            r = local.get(url, timeout=60)
        except Exception:
            return None
        code = getattr(r, "status_code", 0)
        if code == 429:
            time.sleep(2.0)
            return None
        if code != 200:
            return None
        try:
            return parse_jsonld_product(r.text, url)
        except Exception:
            return None

    def _process_urls(todo: list[str], label: str) -> bool:
        """Fetch PDPs; return False if limit / global budget exhausted."""
        nonlocal fail, fetched
        if not todo:
            return True
        print(f"  {source_code} {label} todo={len(todo)} have={len(by_id)}", flush=True)
        batch_size = 300
        for start in range(0, len(todo), batch_size):
            live = _budget()
            if live is not None and len(by_id) >= live:
                print(f"  {source_code} stopped at cap {live}", flush=True)
                return False
            batch = todo[start : start + batch_size]
            if live is not None:
                remain = live - len(by_id)
                if remain <= 0:
                    return False
                batch = batch[:remain]
            with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
                futs = {ex.submit(_one, u): u for u in batch}
                for fut in as_completed(futs):
                    try:
                        p = fut.result()
                    except Exception:
                        p = None
                    with lock:
                        fetched += 1
                        live_now = _budget()
                        if p and p.get("id"):
                            if live_now is None or len(by_id) < live_now:
                                by_id[str(p["id"])] = p
                                done_urls.add(str(p.get("url") or ""))
                        else:
                            fail += 1
                        if fetched % 150 == 0:
                            write_feed(
                                source_code,
                                list(by_id.values()),
                                f"{source_code} sitemap stream (checkpoint)",
                            )
                            print(
                                f"    ... fetched={fetched} ok={len(by_id)} fail={fail}",
                                flush=True,
                            )
            write_feed(
                source_code,
                list(by_id.values()),
                f"{source_code} sitemap stream (checkpoint)",
            )
            if delay > 0:
                time.sleep(delay)
        return True

    try:
        for mi, sm in enumerate(maps, 1):
            live = _budget()
            if live is not None and len(by_id) >= live:
                break
            time.sleep(max(delay, 0.05))
            if use_curl_cffi:
                body = _curl_get(sm, impersonate=curl_impersonate)
            else:
                r = client.get(sm)
                body = r.text if r.status_code == 200 else None
            if not body:
                print(f"  {source_code} map fail {sm.rsplit('/', 1)[-1][:50]}")
                continue
            urls: list[str] = []
            for u in re.findall(r"<loc>([^<]+)</loc>", body):
                u = _ue(u).split("?")[0]
                if u.endswith(".xml"):
                    continue
                if url_filter and not url_filter(u):
                    continue
                if u in done_urls:
                    continue
                urls.append(u)
            print(
                f"  {source_code} map {mi}/{len(maps)} {sm.rsplit('/', 1)[-1][:40]} "
                f"new={len(urls)}",
                flush=True,
            )
            if not _process_urls(urls, f"map{mi}"):
                break
    except Exception as exc:
        import traceback

        print(f"  {source_code} aborted: {exc}", flush=True)
        traceback.print_exc()
        write_feed(
            source_code,
            list(by_id.values()),
            f"{source_code} sitemap stream (aborted)",
        )

    out = list(by_id.values())
    live = _budget()
    if live is not None:
        out = out[:live]
    return out


def fetch_flo(client: httpx.Client, delay: float, limit: int, *, workers: int = 6) -> list[dict[str, Any]]:
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-flo",
        delay=delay,
        limit=limit,
        index_url="https://www.flo.com.tr/products.xml",
        workers=workers,
        url_filter=lambda u: "/urun/" in u,
    )


def fetch_evofone(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 4
) -> list[dict[str, Any]]:
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-evofone",
        delay=delay,
        limit=limit,
        map_urls=["https://evofone.com/sitemap/products/0.xml"],
        workers=workers,
    )


def fetch_network(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 6
) -> list[dict[str, Any]]:
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-network",
        delay=delay,
        limit=limit,
        index_url="https://www.network.com.tr/sitemap.xml",
        map_filter=lambda u: re.search(r"/product_\d+\.xml$", u) is not None,
        workers=workers,
        url_filter=lambda u: "-p-" in u and "/en/" not in u,
    )


def fetch_civil(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 4
) -> list[dict[str, Any]]:
    """Civil storefront is civilim.com (public Shopify)."""
    idx = client.get("https://www.civilim.com/sitemap.xml")
    maps: list[str] = []
    if idx.status_code == 200:
        for u in re.findall(r"<loc>([^<]+)</loc>", idx.text):
            u = unescape(u)
            if "sitemap_products_" in u:
                maps.append(u)
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-civil",
        delay=delay,
        limit=limit,
        map_urls=maps,
        workers=workers,
        url_filter=lambda u: "/products/" in u and u.rstrip("/") != "https://www.civilim.com",
    )


def fetch_trendyol(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 4
) -> list[dict[str, Any]]:
    """Trendyol TR product sitemaps. Prefer curl_cffi; httpx hits 429 quickly."""
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-trendyol",
        delay=max(delay, 0.25),
        limit=limit,
        index_url="https://www.trendyol.com/sitemap_index.xml",
        map_filter=lambda u: re.search(
            r"https://www\.trendyol\.com/sitemap_products\d+\.xml$", u
        )
        is not None,
        workers=min(workers, 4),
        use_curl_cffi=True,
        curl_impersonate="chrome124",
        url_filter=lambda u: "-p-" in u and "trendyol.com/" in u,
    )


def fetch_n11(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 6
) -> list[dict[str, Any]]:
    """n11 official product sitemap index (curl_cffi) + embedded price payload."""
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-n11",
        delay=delay,
        limit=limit,
        index_url="https://www.n11.com/sitemap/product/sitemap-index.xml",
        workers=workers,
        use_curl_cffi=True,
        curl_impersonate="chrome124",
        url_filter=lambda u: "/urun/" in u,
    )


def _clean_product_name(name: str) -> str:
    name = re.sub(r"\s*[|·].*$", "", name).strip()
    name = re.sub(
        r"\s*(Fiyatı ve Özellikleri|Fiyat ve Özellikleri|- Fiyat ve Özellikleri)\s*$",
        "",
        name,
        flags=re.I,
    ).strip()
    return name


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
    source_code = "src-m-vatan"
    abs_cap = merchant_absolute_cap(source_code, limit)
    if abs_cap is not None and abs_cap <= 0:
        print("  vatan global product cap reached — skip")
        return list(_load_existing_feed(source_code).values())
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
    out: list[dict[str, Any]] = list(_load_existing_feed(source_code).values())
    for cat in cats:
        page = 1
        while True:
            live = merchant_absolute_cap(source_code, limit)
            if live is not None and len(out) >= live:
                return out[:live]
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
            if live is not None:
                out = out[:live]
            added = len(out) - before
            print(f"  vatan {cat.split('.com/')[1]} page={page} +{added} total={len(out)}")
            if live is not None and len(out) >= live:
                return out[:live]
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
    live = merchant_absolute_cap(source_code, limit)
    out = list({p["id"]: p for p in out}.values())
    return out[:live] if live is not None else out


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


def fetch_mediamarkt(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 8
) -> list[dict[str, Any]]:
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-mediamarkt",
        delay=delay,
        limit=limit,
        index_url="https://www.mediamarkt.com.tr/sitemaps/sitemap-index.xml",
        map_filter=lambda u: "sitemap-productdetailspages-" in u,
        workers=workers,
        url_filter=lambda u: "/tr/product/" in u,
    )


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
    live = merchant_absolute_cap("src-m-koctas", limit)
    if live is not None:
        products = products[:live]
    elif limit and limit > 0:
        products = products[:limit]
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


def fetch_dr(
    client: httpx.Client, delay: float, limit: int, *, workers: int = 8
) -> list[dict[str, Any]]:
    """D&R: official product sitemaps + JSON-LD (parallel)."""
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-dr",
        delay=delay,
        limit=limit,
        index_url="https://www.dr.com.tr/sitemaps/products.xml",
        workers=workers,
        url_filter=lambda u: "dr.com.tr" in u,
    )


def fetch_teknosa(
    client: httpx.Client,
    delay: float,
    limit: int,
    *,
    workers: int = 8,
) -> list[dict[str, Any]]:
    """Teknosa full catalog via siteharitasi + curl_cffi (Cloudflare bypass)."""
    return fetch_sitemap_jsonld_catalog(
        client,
        source_code="src-m-teknosa",
        delay=delay,
        limit=limit,
        index_url="https://www.teknosa.com/siteharitasi.xml",
        map_filter=lambda u: (
            "Product-tr-TRY" in u or "OutletCanonicalised-tr-TRY" in u
        ),
        workers=workers,
        use_curl_cffi=True,
        curl_impersonate="chrome124",
        url_filter=lambda u: "teknosa.com" in u and "-p-" in u,
    )


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
    "vatan": lambda c, d, lim, **kw: fetch_vatan(c, d, lim),
    "mediamarkt": lambda c, d, lim, **kw: fetch_mediamarkt(
        c, d, lim, workers=int(kw.get("workers", 8))
    ),
    "koctas": lambda c, d, lim, **kw: fetch_koctas(c, d, lim),
    "dr": lambda c, d, lim, **kw: fetch_dr(
        c, d, lim, workers=int(kw.get("workers", 8))
    ),
    "teknosa": lambda c, d, lim, **kw: fetch_teknosa(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "flo": lambda c, d, lim, **kw: fetch_flo(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "evofone": lambda c, d, lim, **kw: fetch_evofone(
        c, d, lim, workers=int(kw.get("workers", 4))
    ),
    "network": lambda c, d, lim, **kw: fetch_network(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "civil": lambda c, d, lim, **kw: fetch_civil(
        c, d, lim, workers=int(kw.get("workers", 4))
    ),
    "trendyol": lambda c, d, lim, **kw: fetch_trendyol(
        c, d, lim, workers=int(kw.get("workers", 4))
    ),
    "n11": lambda c, d, lim, **kw: fetch_n11(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "arcelik": lambda c, d, lim, **kw: fetch_arcelik(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "beko": lambda c, d, lim, **kw: fetch_beko(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
    "decathlon": lambda c, d, lim, **kw: fetch_decathlon(
        c, d, lim, workers=int(kw.get("workers", 6))
    ),
}


def main() -> None:
    import os

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--merchants",
        default="vatan,mediamarkt,koctas,dr,teknosa",
        help="comma list: vatan,mediamarkt,koctas,dr,teknosa,flo,evofone,network,civil,trendyol,arcelik,beko,decathlon",
    )
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="max products per merchant; 0 = no per-merchant limit (global cap still applies)",
    )
    p.add_argument(
        "--global-cap",
        type=int,
        default=int(os.environ.get("CRAWL_GLOBAL_PRODUCT_CAP", str(DEFAULT_GLOBAL_PRODUCT_CAP))),
        help=(
            f"stop when sum(src-m-*.json counts) reaches this "
            f"(default {DEFAULT_GLOBAL_PRODUCT_CAP}; 0 disables; env CRAWL_GLOBAL_PRODUCT_CAP)"
        ),
    )
    p.add_argument(
        "--workers",
        type=int,
        default=6,
        help="concurrent PDP workers (teknosa / WAF merchants)",
    )
    args = p.parse_args()
    set_global_product_cap(args.global_cap)
    wanted = [m.strip() for m in args.merchants.split(",") if m.strip()]

    total = count_live_feed_products()
    cap = active_global_product_cap()
    print(f"live feeds total={total} global_cap={cap or 'disabled'}")
    if cap > 0 and total >= cap:
        print(f"global product cap reached ({total}>={cap}) — crawl stopped")
        return

    with httpx.Client(timeout=40.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for code in wanted:
            if code not in FETCHERS:
                print(f"skip unknown {code}")
                continue
            total = count_live_feed_products()
            if cap > 0 and total >= cap:
                print(f"global product cap reached ({total}>={cap}) — stopping remaining merchants")
                break
            print(f"fetch {code} ... (live_total={total})")
            try:
                products = FETCHERS[code](
                    client, args.delay, args.limit, workers=args.workers
                )
                if not products:
                    print(f"  {code}: empty result — keeping existing feed if any")
                    continue
                path = write_feed(f"src-m-{code}", products, f"{code} live capture")
                print(f"  {len(products)} -> {path} (live_total={count_live_feed_products()})")
            except Exception as exc:
                print(f"  FAIL {exc}")


if __name__ == "__main__":
    main()
