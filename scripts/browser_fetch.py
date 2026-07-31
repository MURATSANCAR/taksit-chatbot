#!/usr/bin/env python3
"""Optional browser / FlareSolverr / cloudscraper helpers for WAF pages.

Used by ``fetch_live_merchant_feeds.py`` when plain HTTP is blocked.

- cloudscraper: JS challenge bypass (preferred for Teknosa)
- Playwright: https://github.com/microsoft/playwright-python
- FlareSolverr (ops sidecar): https://github.com/FlareSolverr/FlareSolverr

Does not invent product data. If a challenge cannot be solved, returns None.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "").rstrip("/")


def curl_cffi_get(url: str, *, impersonate: str = "chrome124", timeout: float = 60.0) -> Optional[str]:
    """TLS-fingerprint impersonation (bypasses many Akamai/CF bot checks).

    https://github.com/lexiforest/curl_cffi
    """
    try:
        from curl_cffi import requests as creq
    except ImportError:
        print("  curl_cffi not installed — pip install curl_cffi")
        return None
    try:
        r = creq.get(url, impersonate=impersonate, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        print(f"  curl_cffi fail {url}: {exc}")
        return None
    if r.status_code != 200 or len(r.text or "") < 200:
        return None
    text = r.text or ""
    if "Just a moment" in text[:2000] or "Access Denied" in text[:2000]:
        return None
    if "Pardon Our Interruption" in text[:2000]:
        return None
    return text


def create_cloudscraper() -> Any:
    """Return a cloudscraper session (Cloudflare JS challenge)."""
    try:
        import cloudscraper
    except ImportError as exc:
        raise SystemExit(
            "cloudscraper required for WAF merchants — pip install cloudscraper"
        ) from exc
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False}
    )


def fetch_html_cloudscraper(url: str, *, timeout: float = 60.0, session: Any = None) -> Optional[str]:
    scraper = session or create_cloudscraper()
    try:
        r = scraper.get(url, timeout=timeout)
    except Exception as exc:
        print(f"  cloudscraper fail {url}: {exc}")
        return None
    if r.status_code != 200 or len(r.text) < 500:
        return None
    if "Just a moment" in r.text or "Attention Required" in r.text[:2000]:
        return None
    return r.text


def fetch_html_playwright(
    url: str,
    *,
    wait_ms: int = 4000,
    timeout_ms: int = 60000,
) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed — pip install playwright && playwright install chromium")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(locale="tr-TR").new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            title = page.title()
            browser.close()
    except Exception as exc:
        print(f"  playwright fail {url}: {exc}")
        return None
    if "Access Denied" in html or "Just a moment" in html or "Attention Required" in title:
        return None
    return html


def fetch_html_flaresolverr(url: str, *, timeout_ms: int = 60000) -> Optional[str]:
    """Solve Cloudflare-style challenges via FlareSolverr proxy (ops)."""
    if not FLARESOLVERR_URL:
        return None
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    try:
        r = httpx.post(
            f"{FLARESOLVERR_URL}/v1",
            json=payload,
            timeout=timeout_ms / 1000 + 10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"  flaresolverr fail {url}: {exc}")
        return None
    if data.get("status") != "ok":
        print(f"  flaresolverr status={data.get('status')} msg={data.get('message')}")
        return None
    solution = data.get("solution") or {}
    html = solution.get("response")
    return html if isinstance(html, str) and len(html) > 500 else None


def fetch_html(url: str, *, prefer: str = "auto") -> Optional[str]:
    """prefer: auto|cloudscraper|playwright|flaresolverr"""
    if prefer in ("auto", "cloudscraper"):
        html = fetch_html_cloudscraper(url)
        if html:
            return html
        if prefer == "cloudscraper":
            return None
    if prefer in ("auto", "flaresolverr"):
        html = fetch_html_flaresolverr(url)
        if html:
            return html
        if prefer == "flaresolverr":
            return None
    return fetch_html_playwright(url)


def create_cf_httpx_client(
    seed_url: str,
    *,
    timeout: float = 60.0,
    wait_ms: int = 4000,
) -> Optional[httpx.Client]:
    """Solve Cloudflare once with Playwright, return httpx.Client carrying cookies.

    Used when cloudscraper alone gets 'Just a moment...' (Teknosa sitemaps).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed for CF cookie session")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="tr-TR",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(seed_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(wait_ms)
            # Wait out challenge if still present
            for _ in range(8):
                title = page.title()
                if "Just a moment" not in title and "Attention Required" not in title:
                    break
                page.wait_for_timeout(2000)
            cookies = context.cookies()
            ua = context._impl_obj._options.get("user_agent") if False else None
            browser.close()
    except Exception as exc:
        print(f"  cf cookie session fail: {exc}")
        return None
    jar = {c["name"]: c["value"] for c in cookies}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    }
    client = httpx.Client(
        timeout=timeout,
        headers=headers,
        cookies=jar,
        follow_redirects=True,
    )
    # sanity
    try:
        probe = client.get(seed_url)
        if probe.status_code != 200 or "Just a moment" in probe.text[:2000]:
            print(f"  cf cookie probe still blocked status={probe.status_code}")
            client.close()
            return None
    except Exception as exc:
        print(f"  cf cookie probe fail: {exc}")
        client.close()
        return None
    print(f"  cf cookie session ok cookies={len(jar)}")
    return client
