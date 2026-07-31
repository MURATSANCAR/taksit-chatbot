"""P13 — chat pipeline → catalog progressive cards."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.campaign_catalog.models import RateSnapshotRecord, RateType
from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
from taksitlio.merchant.directory import MerchantDirectoryEntry
from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert
from taksitlio.product_query.chat_bridge import (
    budget_max_price,
    infer_ranking_mode,
    need_profile_to_search_request,
)
from taksitlio.product_query.finance_projection import (
    InstitutionTermOption,
    OfferFinanceContext,
)
from taksitlio.product_query.finance_sync import sync_finance_options_for_product
from taksitlio.product_query.ranking import RankingMode


def test_need_profile_maps_budget_and_cheapest_default() -> None:
    profile = {
        "need_description": "laptop arıyorum",
        "budget": {
            "type": "APPROXIMATE",
            "value": 25000,
            "maximum": None,
            "monthly_payment": None,
        },
        "entities": [],
        "preferences": [{"concept": "installment", "importance": 0.9}],
    }
    assert budget_max_price(profile) == 25000.0
    assert infer_ranking_mode(profile) is RankingMode.CHEAPEST_PRODUCT_PRICE
    req = need_profile_to_search_request("msg", profile, phase="FIRST_CARDS")
    assert req.max_price == 25000.0
    assert req.phase == "FIRST_CARDS"


def test_monthly_budget_selects_finance_ranking() -> None:
    profile = {
        "need_description": "telefon",
        "budget": {"type": "MONTHLY_PAYMENT", "monthly_payment": 1500, "value": None},
        "entities": [],
    }
    assert infer_ranking_mode(profile) is RankingMode.LOWEST_MONTHLY_PAYMENT


@pytest.mark.asyncio
async def test_chat_returns_catalog_cards_when_products_exist() -> None:
    container = build_in_memory_container()
    directory = container.extras["merchant_directory"]
    await directory.upsert(
        MerchantDirectoryEntry(id=1, merchant_code="m1", display_name="Catalog Merchant")
    )
    catalog = container.extras["product_catalog"]
    p = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(
                external_product_id="PHONE1",
                display_name="Mobil cihaz Pro",
            )
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=p.id,
        plan=plan_offer_upsert(
            NormalizedOffer(
                external_product_id="PHONE1",
                current_price=19999,
                currency="TRY",
            ),
            NormalizedStock(
                external_product_id="PHONE1",
                stock_status="AVAILABLE",
            ),
        ),
    )
    await catalog.attach_primary_media(
        p.id,
        cdn_url="https://cdn.test/phone.webp",
        sha256="abc",
        status="READY",
        source_url="https://merchant.example/phone.jpg",
    )

    finance_index = container.extras["finance_option_index"]
    await sync_finance_options_for_product(
        finance_index,
        product_id=str(p.id),
        offer=OfferFinanceContext(
            product_offer_id="1",
            merchant_id="1",
            merchant_code="m1",
            purchase_price=19999,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
        ),
        term_options=(
            InstitutionTermOption(
                institution_id="7",
                financial_product_code="fp",
                term_months=12,
                rate_snapshot=RateSnapshotRecord(
                    financial_product_code="fp",
                    rate_type=RateType.ZERO_RATE,
                    freshness_status="FRESH",
                ),
            ),
        ),
    )

    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat",
            json={
                "session_id": "p13",
                "message": "Mobil cihaz bakıyorum, 40 bin civarı.",
                "product_phase": "FINANCE_ENRICHED",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["diagnostics"].get("product_path") is True
        assert body["phase"] == "FINANCE_ENRICHED"
        assert body["cards"]
        assert body["cards"][0]["display_name"] == "Mobil cihaz Pro"
        assert body["cards"][0]["merchant"]["display_name"] == "Catalog Merchant"
        assert body["cards"][0]["image"]["thumbnail_cdn_url"] == "https://cdn.test/phone.webp"
        assert body["cards"][0]["best_finance"] is not None
        assert "Tahmini" in (body["cards"][0]["best_finance"]["display_label"] or "")
        assert not body["campaigns"]


@pytest.mark.asyncio
async def test_chat_falls_back_to_campaigns_when_catalog_empty() -> None:
    container = build_in_memory_container()
    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat",
            json={
                "session_id": "legacy",
                "message": "Telefon bakıyoruz, 40 bin civarı.",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["diagnostics"].get("product_path") is False
        assert body["campaigns"]
        assert body["cards"] == []
