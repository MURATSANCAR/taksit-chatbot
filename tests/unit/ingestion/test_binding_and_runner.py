"""Source binding + dry-run ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taksitlio.ingestion.binding import SourceBinding, instantiate_adapter
from taksitlio.ingestion.runner import run_ingestion_dry


@pytest.fixture()
def feed_path(tmp_path: Path) -> Path:
    path = tmp_path / "feed.json"
    path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "SKU-1",
                        "name": "Test Laptop",
                        "price": 15000.0,
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                        "image_url": "https://cdn.example.test/a.jpg",
                        "url": "https://shop.example.test/p/1",
                    },
                    {
                        "id": "SKU-2",
                        "name": "Broken",
                        # missing price → quarantine after fetch offers empty?
                        "currency": "TRY",
                        "stock_status": "AVAILABLE",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_dry_run_scores_items(feed_path: Path) -> None:
    binding = SourceBinding(
        source_code="src-demo-1",
        adapter_code="generic.json_feed.v1",
        merchant_id="merchant-opaque-1",
        config={"feed_path": str(feed_path)},
    )
    result = await run_ingestion_dry(binding, limit=10)
    assert result.discovered == 2
    assert result.succeeded == 2
    assert result.chatbot_visible >= 1
    statuses = {i.external_product_id: i.quality.status.value for i in result.items}
    assert statuses["SKU-1"] in {"PARTIAL", "READY"}
    assert statuses["SKU-2"] == "QUARANTINED"


def test_inline_secret_rejected_when_credential_ref() -> None:
    binding = SourceBinding(
        source_code="s",
        adapter_code="generic.json_feed.v1",
        merchant_id="m1",
        credential_ref="env://FEED_TOKEN",
        config={"feed_path": "/tmp/x", "authorization": "Bearer x"},
    )
    with pytest.raises(ValueError, match="inline"):
        instantiate_adapter(binding)
