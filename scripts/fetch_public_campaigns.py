#!/usr/bin/env python3
"""Fetch publicly published alışveriş-kredisi campaigns (ADR-010, no invented rates).

Taksitlio mobile app holds the full partner/campaign roster; open web only exposes
bank campaign detail pages (today: Fibabanka /kampanyalar/guncel-*). This script
scrapes those pages into ``generic.campaign_feed.v1`` JSON, including banner
``image_url`` when published. Rates/terms are taken only from explicit tables.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "crawler" / "feeds" / "live"
MEDIA = OUT / "campaign_media"
UA = "TaksitlioBot/0.1 (+ADR-010 polite catalog; ops research)"
FIBABANKA_LIST = "https://www.fibabanka.com.tr/kampanyalar"

# Merchant opaque codes — only when named on the public campaign page.
_MERCHANT_ALIASES = {
    "evofone": "m-evofone",
    "trendyol": "m-trendyol",
    "teknosa": "m-teknosa",
    "vatan": "m-vatan",
    "mediamarkt": "m-mediamarkt",
    "media markt": "m-mediamarkt",
    "koçtaş": "m-koctas",
    "koctas": "m-koctas",
    "idefix": "m-idefix",
}


def _html_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(text)


def _paras(html: str) -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in _html_text(html).split("\n")]
    return [p for p in paras if p]


def _og(html: str, prop: str) -> Optional[str]:
    m = re.search(
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)',
        html,
        re.I,
    ) or re.search(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(prop)}["\']',
        html,
        re.I,
    )
    return unescape(m.group(1)).strip() if m else None


def _title(html: str) -> str:
    t = _og(html, "og:title")
    if t:
        return t
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if m:
        return unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    m = re.search(r"<title>([^<]+)", html, re.I)
    return unescape(m.group(1)).strip() if m else ""


def _campaign_image(html: str) -> Optional[str]:
    imgs = re.findall(
        r"(https://cdn\.fibabanka\.com\.tr/mnresize/\d+/-/images/default-source/kampanyalar/[^\"'?]+\.(?:jpg|jpeg|png|webp))",
        html,
        re.I,
    )
    if not imgs:
        imgs = re.findall(
            r"(https://cdn\.fibabanka\.com\.tr/images/default-source/kampanyalar/[^\"'?]+\.(?:jpg|jpeg|png|webp))",
            html,
            re.I,
        )
    if not imgs:
        return None
    # Prefer largest mnresize width, then strip resize prefix for full asset.
    best = max(imgs, key=lambda u: int(re.search(r"/mnresize/(\d+)/", u).group(1)) if "/mnresize/" in u else 9999)
    full = re.sub(r"/mnresize/\d+/-", "/", best)
    return full.replace(".com.tr//", ".com.tr/")


def _parse_tr_number(raw: str) -> Optional[float]:
    text = raw.strip().replace("TL", "").replace("₺", "").replace("%", "").strip()
    text = text.replace(".", "").replace(",", ".") if re.search(r"\d\.\d{3}", text) else text.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except ValueError:
        return None


def _pct_to_float(raw: str) -> Optional[float]:
    m = re.search(r"%?\s*([\d]+(?:[.,]\d+)?)", raw)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _merchant_codes_from_text(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for alias, code in _MERCHANT_ALIASES.items():
        if alias in low and code not in found:
            found.append(code)
    return found


def _is_alisveris_kredisi_title(title: str) -> bool:
    low = title.lower()
    return "alışveriş kredisi" in low or "alisveris kredisi" in low


def parse_fibabanka_campaign(html: str, url: str) -> Optional[dict[str, Any]]:
    title = _title(html)
    # Only pages whose own title is alışveriş kredisi — never inherit sidebar copy.
    if not title or not _is_alisveris_kredisi_title(title):
        return None
    if title.strip().lower() == "güncel kampanyalar":
        return None

    paras = _paras(html)
    full_body = "\n".join(paras)
    # Prefer long descriptive paragraphs for summary.
    summary_candidates = [
        p
        for p in paras
        if "alışveriş kredisi" in p.lower()
        and ("müşterilerine özel" in p.lower() or "üst limit" in p.lower() or "fırsat" in p.lower())
        and len(p) > 100
    ]
    summary = summary_candidates[0] if summary_candidates else (_og(html, "og:description") or title)

    max_amount = None
    m = re.search(
        r"(?:üst limit(?:i)?|kadar)[^\d]{0,40}([\d.\s]+)\s*TL",
        full_body,
        re.I,
    )
    if m:
        max_amount = _parse_tr_number(m.group(1))

    months = None
    m = re.search(r"(?:azami vade|vadeli)[^\d]{0,20}(\d+)\s*ay", full_body, re.I)
    if m:
        months = int(m.group(1))

    annual = None
    monthly = None
    # Flexible örnek ödeme tablosu: amount, months, monthly %, installment, fee, monthly cost, annual cost
    m = re.search(
        r"([\d.\s]+)\s*TL\s+(\d+)\s+%([\d.,]+)\s+([\d.,]+)\s*TL\s+[^\n%]*%([\d.,]+)\s+%([\d.,]+)",
        full_body,
        re.I,
    )
    if m:
        months = months or int(m.group(2))
        monthly = _pct_to_float(m.group(3))
        annual = _pct_to_float(m.group(6))
    else:
        # Prefer explicit body rates (%0,99) over marketing title (%0 in slug/H1).
        rates = [
            _pct_to_float(x)
            for x in re.findall(
                r"%\s*([\d.,]+)\s*faizli\s+Alışveriş Kredisi",
                full_body,
                re.I,
            )
        ]
        rates = [r for r in rates if r is not None]
        # If both 0 and 0.99 appear, take the max non-title signal: prefer nonzero when present.
        if rates:
            nonzero = [r for r in rates if r > 0]
            monthly = nonzero[0] if nonzero else rates[0]

    terms: list[dict[str, Any]] = []
    if months is not None or annual is not None or monthly is not None:
        term: dict[str, Any] = {}
        if months is not None:
            term["months"] = months
        if annual is not None:
            term["rate_apr"] = annual
            if monthly is not None:
                term["monthly_rate_pct"] = monthly
        elif monthly is not None:
            term["monthly_rate_pct"] = monthly
            if monthly == 0.0:
                term["rate_apr"] = 0.0
        if term:
            terms.append(term)

    campaign_type = "INSTALLMENT"
    if any(t.get("rate_apr") == 0.0 for t in terms) and not any(
        (t.get("monthly_rate_pct") or 0) > 0 for t in terms
    ):
        campaign_type = "ZERO_RATE"

    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    image_url = _campaign_image(html)
    if image_url:
        image_url = image_url.replace(".com.tr//", ".com.tr/")
    merchants = _merchant_codes_from_text(f"{title}\n{summary}")

    return {
        "id": f"fibabanka-{slug}"[:64],
        "institution_code": "fi-fibabanka",
        "name": title,
        "campaign_type": campaign_type,
        "summary": summary,
        "valid_from": None,
        "valid_until": None,
        "min_amount": None,
        "max_amount": max_amount,
        "terms": terms,
        "merchant_codes": merchants,
        "category_codes": [],
        "source_url": url,
        "image_url": image_url,
    }


def list_guncel_campaign_urls(html: str, base: str) -> list[str]:
    urls = []
    for href in re.findall(r'href=["\']([^"\']*kampanyalar/guncel[^"\']+)["\']', html, re.I):
        if href.rstrip("/").endswith("guncel-kampanyalar") or href.rstrip("/").endswith(
            "guncel-ozel-kampanyalar"
        ):
            continue
        if "/gecmis-" in href:
            continue
        full = urljoin(base, href)
        if full not in urls:
            urls.append(full)
    return urls


def download_image(client: httpx.Client, url: str, dest_dir: Path) -> Optional[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.-]+", "_", urlparse(url).path.rsplit("/", 1)[-1]) or "img.jpg"
    path = dest_dir / name.split("?")[0]
    if path.exists() and path.stat().st_size > 0:
        return path
    r = client.get(url)
    if r.status_code != 200 or not r.content:
        return None
    path.write_bytes(r.content)
    return path


def fetch_fibabanka(client: httpx.Client, delay: float, download_images: bool) -> list[dict[str, Any]]:
    r = client.get(FIBABANKA_LIST)
    r.raise_for_status()
    urls = list_guncel_campaign_urls(r.text, FIBABANKA_LIST)
    out: list[dict[str, Any]] = []
    for url in urls:
        time.sleep(delay)
        resp = client.get(url)
        if resp.status_code != 200:
            continue
        parsed = parse_fibabanka_campaign(resp.text, url)
        if not parsed:
            continue
        if download_images and parsed.get("image_url"):
            local = download_image(client, str(parsed["image_url"]), MEDIA)
            if local:
                parsed["image_local_path"] = str(local.relative_to(ROOT))
        out.append(parsed)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--no-images", action="store_true")
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=40.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        campaigns = fetch_fibabanka(client, args.delay, download_images=not args.no_images)

    path = OUT / "src-b-fibabanka.json"
    path.write_text(
        json.dumps(
            {
                "campaigns": campaigns,
                "source": "fibabanka public /kampanyalar (alışveriş kredisi only)",
                "count": len(campaigns),
                "quality": "live_polite_capture",
                "note": "Taksitlio app roster not public; only bank-published pages.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(campaigns)} campaigns -> {path}")
    for c in campaigns:
        print(
            f"  - {c['id']}: type={c['campaign_type']} max={c.get('max_amount')} "
            f"terms={c.get('terms')} merchants={c.get('merchant_codes')} img={bool(c.get('image_url'))}"
        )


if __name__ == "__main__":
    main()
