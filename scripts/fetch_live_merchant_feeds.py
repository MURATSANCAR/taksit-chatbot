#!/usr/bin/env python3
"""Polite live merchant HTML/JSON-LD → ADR-010 product feeds.

- Honors crawl delay (default 2s)
- Does not invent price/stock
- Writes ``crawler/feeds/live/{source_code}.json``

This is an ops tool for building feeds when merchant APIs are unavailable.
Production chatbot never calls this synchronously.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "crawler" / "feeds" / "live"
UA = "TaksitlioBot/0.1 (+ADR-010 catalog research; polite; ops)"


def parse_tr_price(raw: str) -> Optional[float]:
    text = raw.strip().replace("TL", "").strip()
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except ValueError:
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
    uniq = {p["id"]: p for p in products}
    return list(uniq.values())


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
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" not in types:
                continue
            offers = it.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            try:
                price = float(offers.get("price"))
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
                    or url.rsplit("-", 1)[-1].replace(".html", "")
                ),
                "name": name,
                "sku": it.get("sku"),
                "gtin": it.get("gtin13") or it.get("gtin"),
                "mpn": it.get("mpn"),
                "brand": brand,
                "url": url,
                "price": price,
                "currency": offers.get("priceCurrency") or "TRY",
                "stock_status": stock,
                "image_url": img,
                "attributes": attrs,
            }
    return None


def fetch_vatan(client: httpx.Client, delay: float) -> list[dict[str, Any]]:
    categories = [
        "https://www.vatanbilgisayar.com/notebook/",
        "https://www.vatanbilgisayar.com/cep-telefonu-modelleri/",
    ]
    out: list[dict[str, Any]] = []
    for url in categories:
        r = client.get(url)
        r.raise_for_status()
        out.extend(parse_vatan_listing(r.text))
        time.sleep(delay)
    uniq = {p["id"]: p for p in out}
    return list(uniq.values())


def fetch_mediamarkt(client: httpx.Client, delay: float, limit: int) -> list[dict[str, Any]]:
    cats = [
        "https://www.mediamarkt.com.tr/tr/category/laptop-504926.html",
        "https://www.mediamarkt.com.tr/tr/category/cep-telefonlari-504171.html",
        "https://www.mediamarkt.com.tr/tr/category/tabletler-639520.html",
    ]
    product_urls: list[str] = []
    for cat in cats:
        r = client.get(cat)
        r.raise_for_status()
        links = re.findall(
            r'href="(https://www\.mediamarkt\.com\.tr/tr/product/[^"#?]+)"', r.text
        )
        links += [
            urljoin("https://www.mediamarkt.com.tr", u)
            for u in re.findall(r'href="(/tr/product/[^"#?]+)"', r.text)
        ]
        for u in links:
            if u not in product_urls:
                product_urls.append(u)
        time.sleep(delay)
    product_urls = product_urls[:limit]
    products: list[dict[str, Any]] = []
    for url in product_urls:
        r = client.get(url)
        if r.status_code != 200:
            time.sleep(delay)
            continue
        parsed = parse_jsonld_product(r.text, url)
        if parsed:
            products.append(parsed)
        time.sleep(delay)
    uniq = {p["id"]: p for p in products}
    return list(uniq.values())


def write_feed(source_code: str, products: list[dict[str, Any]], source: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{source_code}.json"
    payload = {
        "products": products,
        "source": source,
        "count": len(products),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merchant", choices=["vatan", "mediamarkt", "all"], default="all")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--mm-limit", type=int, default=45)
    args = parser.parse_args()

    with httpx.Client(timeout=40.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        if args.merchant in {"vatan", "all"}:
            products = fetch_vatan(client, args.delay)
            path = write_feed("src-m-vatan", products, "vatanbilgisayar.com listing HTML")
            print(f"vatan: {len(products)} -> {path}")
        if args.merchant in {"mediamarkt", "all"}:
            products = fetch_mediamarkt(client, args.delay, args.mm_limit)
            path = write_feed(
                "src-m-mediamarkt", products, "mediamarkt.com.tr category+PDP JSON-LD"
            )
            print(f"mediamarkt: {len(products)} -> {path}")


if __name__ == "__main__":
    main()
