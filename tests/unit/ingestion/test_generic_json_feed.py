"""ADR-010 P1 — generic.json_feed.v1 adapter tests (no merchant hardcode)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.ingestion import AdapterRegistry, IngestionCapability
from taksitlio.ingestion.adapters import (
    GENERIC_JSON_FEED_V1,
    GenericJsonFeedAdapter,
    register_generic_json_feed,
)


@pytest.fixture
def feed_path(tmp_path: Path) -> Path:
    path = tmp_path / "feed.json"
    path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "SKU-100",
                        "name": "Example Laptop 16GB",
                        "sku": "SKU-100",
                        "gtin": "8680009990001",
                        "brand": "ExampleBrand",
                        "model": "EL-16",
                        "url": "https://example.test/p/SKU-100",
                        "price": 42999.0,
                        "list_price": 45999.0,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                        "image_url": "https://example.test/img/SKU-100.jpg",
                        "attributes": {"ram_gb": 16},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_generic_json_feed_adapter_roundtrip(feed_path: Path) -> None:
    adapter = GenericJsonFeedAdapter(feed_path=feed_path)
    assert adapter.adapter_code == GENERIC_JSON_FEED_V1
    assert IngestionCapability.PRODUCT_DISCOVERY in adapter.capabilities()

    refs = [r async for r in adapter.discover_products()]
    assert len(refs) == 1
    assert refs[0].external_product_id == "SKU-100"

    product = await adapter.fetch_product("SKU-100")
    assert product.display_name == "Example Laptop 16GB"
    assert product.gtin == "8680009990001"
    assert product.attributes.get("ram_gb") == 16

    offers = await adapter.fetch_offers("SKU-100")
    assert offers[0].current_price == 42999.0
    stock = await adapter.fetch_stock("SKU-100")
    assert stock[0].stock_status == "AVAILABLE"
    media = await adapter.fetch_media("SKU-100")
    assert media[0].media_role == "PRIMARY"
    assert await adapter.fetch_finance_metadata("SKU-100") == {}


def test_register_generic_json_feed(feed_path: Path) -> None:
    registry = AdapterRegistry()
    register_generic_json_feed(registry, feed_path=feed_path)
    adapter = registry.get(GENERIC_JSON_FEED_V1)
    assert adapter.adapter_code == GENERIC_JSON_FEED_V1


def test_adapter_code_is_vendor_neutral() -> None:
    code = GENERIC_JSON_FEED_V1.lower()
    for banned in ("teknosa", "mediamarkt", "vatan", "fibabanka", "kuveyt"):
        assert banned not in code

