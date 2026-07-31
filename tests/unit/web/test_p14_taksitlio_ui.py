"""P14 — guest UI wires to /v1/chat cards (no invented DEMO offers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.merchant.directory import MerchantDirectoryEntry
from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert

ROOT = Path(__file__).resolve().parents[3]
UI_HTML = ROOT / "web" / "taksitlio" / "index.html"
UI_JS = ROOT / "web" / "taksitlio" / "js" / "chat-cards.js"


def test_ui_assets_wire_chat_api_without_demo_offers() -> None:
    html = UI_HTML.read_text(encoding="utf-8")
    js = UI_JS.read_text(encoding="utf-8")
    assert "js/chat-cards.js" in html
    assert "js/search-session" in html or "/v1/search-sessions" in html
    assert "Galaxy A56" not in html
    assert "Yapı Kredi" not in html
    assert "MediaMarkt" not in html
    assert "const DEMO" not in html
    assert "cardToDeal" in js
    assert "dealsFromChatPayload" in js
    assert "thumbnail_cdn_url" in js
    assert "Tahmini aylık ödeme" in js or "display_label" in js


@pytest.mark.asyncio
async def test_taksitlio_static_and_chat_cards_roundtrip() -> None:
    container = build_in_memory_container()
    directory = container.extras["merchant_directory"]
    await directory.upsert(
        MerchantDirectoryEntry(id=1, merchant_code="m1", display_name="Catalog Merchant")
    )
    catalog = container.extras["product_catalog"]
    p = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="T1", display_name="Tablet Air")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=p.id,
        plan=plan_offer_upsert(
            NormalizedOffer(
                external_product_id="T1", current_price=12000, currency="TRY"
            ),
            NormalizedStock(external_product_id="T1", stock_status="AVAILABLE"),
        ),
    )
    await catalog.attach_primary_media(
        p.id,
        cdn_url="https://cdn.test/tablet.webp",
        sha256="abc",
        status="READY",
        source_url="https://merchant.example/t.jpg",
    )

    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ui = await client.get("/taksitlio/")
        assert ui.status_code == 200
        assert b"js/chat-cards.js" in ui.content
        assert b"js/search-session" in ui.content
        assert b"const DEMO" not in ui.content

        js = await client.get("/taksitlio/js/chat-cards.js")
        assert js.status_code == 200
        assert b"dealsFromChatPayload" in js.content

        search_js = await client.get("/taksitlio/js/search-session/client.js")
        assert search_js.status_code == 200
        assert b"/v1/search-sessions" in search_js.content

        chat = await client.post(
            "/v1/chat",
            json={
                "session_id": "ui-p14",
                "message": "Tablet Air bakıyorum",
                "product_phase": "FIRST_CARDS",
            },
        )
        assert chat.status_code == 200
        body = chat.json()
        assert body["diagnostics"].get("product_path") is True
        assert body["cards"]
        card = body["cards"][0]
        assert card["display_name"] == "Tablet Air"
        assert card["image"]["thumbnail_cdn_url"] == "https://cdn.test/tablet.webp"
        assert card["best_finance"] is None  # FIRST_CARDS omits finance
