"""Global crawl product cap helpers (ADR-010 storage guard)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_live_merchant_feeds import (  # noqa: E402
    count_live_feed_products,
    global_room_for_source,
    merchant_absolute_cap,
    set_global_product_cap,
)


@pytest.fixture()
def feed_dir(tmp_path: Path) -> Path:
    def _write(name: str, n: int) -> None:
        products = [
            {
                "id": f"{name}-{i}",
                "name": f"P{i}",
                "price": 1.0,
                "url": f"https://example.test/{name}/{i}",
            }
            for i in range(n)
        ]
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"products": products, "count": n}),
            encoding="utf-8",
        )

    _write("src-m-a", 400_000)
    _write("src-m-b", 500_000)
    _write("src-m-c", 50_000)
    return tmp_path


def test_count_and_room(feed_dir: Path) -> None:
    set_global_product_cap(1_000_000)
    assert count_live_feed_products(feed_dir=feed_dir) == 950_000
    assert global_room_for_source("src-m-c", feed_dir=feed_dir) == 100_000
    # c may grow until others (900k) leave 100k room → abs cap 100k
    assert merchant_absolute_cap("src-m-c", 0, feed_dir=feed_dir) == 100_000
    assert merchant_absolute_cap("src-m-c", 20_000, feed_dir=feed_dir) == 20_000


def test_cap_reached_blocks_new_merchant(feed_dir: Path) -> None:
    set_global_product_cap(1_000_000)
    (feed_dir / "src-m-c.json").write_text(
        json.dumps({"products": [], "count": 100_000}),
        encoding="utf-8",
    )
    assert count_live_feed_products(feed_dir=feed_dir) == 1_000_000
    assert merchant_absolute_cap("src-m-new", 0, feed_dir=feed_dir) == 0


def test_global_cap_disabled(feed_dir: Path) -> None:
    set_global_product_cap(0)
    assert merchant_absolute_cap("src-m-a", 0, feed_dir=feed_dir) is None
    assert merchant_absolute_cap("src-m-a", 10, feed_dir=feed_dir) == 10
