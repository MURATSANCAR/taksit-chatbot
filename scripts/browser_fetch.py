#!/usr/bin/env python3
"""Optional browser / FlareSolverr helpers for WAF-challenged merchant pages.

Used by ``fetch_live_merchant_feeds.py`` when plain HTTP is blocked.

- Playwright: https://github.com/microsoft/playwright-python
- FlareSolverr (ops sidecar): https://github.com/FlareSolverr/FlareSolverr

Does not invent product data. If a challenge cannot be solved, returns None.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "").rstrip("/")


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
    """prefer: auto|playwright|flaresolverr"""
    if prefer in ("auto", "flaresolverr"):
        html = fetch_html_flaresolverr(url)
        if html:
            return html
        if prefer == "flaresolverr":
            return None
    return fetch_html_playwright(url)
