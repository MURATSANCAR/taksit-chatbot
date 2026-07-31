"""P11 — merchant directory, finance enrich, S3 storage factory."""

from __future__ import annotations

import os

import pytest

from taksitlio.merchant.directory import (
    InMemoryMerchantDirectory,
    MerchantDirectoryEntry,
    resolve_merchant_display_name,
)
from taksitlio.media.s3_storage import S3CompatibleObjectStorage, build_object_storage_from_env
from taksitlio.media.storage import LocalObjectStorage
from taksitlio.product_query.finance_index import (
    InMemoryFinanceOptionIndex,
    InstitutionLabelResolver,
    enrich_candidate_with_finance,
)
from taksitlio.product_query.finance_projection import ProductFinanceOptionRow
from taksitlio.product_query.search import SearchProductCandidate


@pytest.mark.asyncio
async def test_merchant_display_from_directory() -> None:
    directory = InMemoryMerchantDirectory(
        (
            MerchantDirectoryEntry(
                id=9, merchant_code="m-opaque", display_name="Catalog Merchant Nine"
            ),
        )
    )
    assert await resolve_merchant_display_name(9, directory) == "Catalog Merchant Nine"
    assert await resolve_merchant_display_name(99, directory) == "merchant:99"


@pytest.mark.asyncio
async def test_finance_enrich_enables_finance_fields() -> None:
    cand = SearchProductCandidate(
        product_id="1",
        display_name="Phone",
        brand_model=None,
        merchant_id="1",
        merchant_display_name="merchant:1",
        price=12000,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
        has_primary_image=True,
    )
    row = ProductFinanceOptionRow(
        product_offer_id="o1",
        merchant_id="1",
        institution_id="inst-a",
        term_months=12,
        monthly_payment=1100.0,
        total_repayment=13200.0,
        fees_total=0.0,
        eligibility_status="ELIGIBLE",
        plan_kind="CALCULATED_ESTIMATE",
        freshness_status="FRESH",
        campaign_id=None,
        rate_snapshot_id="r1",
        display_label="Tahmini aylık ödeme",
    )
    enriched = enrich_candidate_with_finance(
        cand,
        (row,),
        institutions=InstitutionLabelResolver(labels={"inst-a": "Institution A"}),
    )
    assert enriched.finance_active is True
    assert enriched.rate_fresh is True
    assert enriched.best_monthly_payment == 1100.0
    assert enriched.card_finance is not None
    assert enriched.card_finance.institution_display_name == "Institution A"


@pytest.mark.asyncio
async def test_catalog_search_uses_merchant_and_finance() -> None:
    from httpx import ASGITransport, AsyncClient

    from taksitlio.api.app import create_app
    from taksitlio.app.container import build_in_memory_container
    from taksitlio.ingestion.protocol import NormalizedOffer, NormalizedProduct, NormalizedStock
    from taksitlio.product.upsert import plan_offer_upsert, plan_product_upsert

    container = build_in_memory_container()
    directory = container.extras["merchant_directory"]
    await directory.upsert(
        MerchantDirectoryEntry(id=1, merchant_code="m1", display_name="Operator Merchant")
    )
    catalog = container.extras["product_catalog"]
    p = await catalog.upsert_product(
        merchant_id=1,
        plan=plan_product_upsert(
            NormalizedProduct(external_product_id="P1", display_name="Laptop Z")
        ),
        data_quality_status="PARTIAL",
        status="ACTIVE",
    )
    await catalog.upsert_offer(
        merchant_id=1,
        product_id=p.id,
        plan=plan_offer_upsert(
            NormalizedOffer(external_product_id="P1", current_price=10000),
            NormalizedStock(external_product_id="P1", stock_status="AVAILABLE"),
        ),
    )
    await catalog.attach_primary_media(
        p.id,
        cdn_url="https://cdn.test/p1.webp",
        sha256="sha",
        status="READY",
        source_url="https://src.example/p1.jpg",
    )
    index = container.extras["finance_option_index"]
    await index.put(
        str(p.id),
        (
            ProductFinanceOptionRow(
                product_offer_id="o",
                merchant_id="1",
                institution_id="i1",
                term_months=6,
                monthly_payment=1800.0,
                total_repayment=10800.0,
                fees_total=0.0,
                eligibility_status="ELIGIBLE",
                plan_kind="CALCULATED_ESTIMATE",
                freshness_status="FRESH",
                campaign_id=None,
                rate_snapshot_id=None,
                display_label="Tahmini aylık ödeme",
            ),
        ),
    )
    container.extras["institution_labels"] = InstitutionLabelResolver(
        labels={"i1": "Bank Label From Catalog"}
    )

    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/product-query/search",
            json={
                "utterance": "laptop",
                "ranking_mode": "LOWEST_MONTHLY_PAYMENT",
                "use_catalog": True,
                "use_popular_cache": False,
                "catalog_merchant_id": 1,
                "products": [],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cards"]
    assert body["cards"][0]["merchant_display_name"] == "Operator Merchant"
    assert body["cards"][0]["best_finance"]["institution_display_name"] == (
        "Bank Label From Catalog"
    )
    await container.aclose()


def test_build_storage_local_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CDN_BASE_URL", "https://cdn.test")
    storage = build_object_storage_from_env()
    assert isinstance(storage, LocalObjectStorage)
    key = storage.put("a/b.bin", b"hi", content_type="application/octet-stream")
    assert storage.cdn_url_for(key).startswith("https://cdn.test/")


def test_s3_storage_with_fake_client() -> None:
    calls = {}

    class FakeClient:
        def put_object(self, **kwargs):
            calls.update(kwargs)

    storage = S3CompatibleObjectStorage(
        bucket="b",
        cdn_base_url="https://cdn.s3.test",
        prefix="pref",
        client=FakeClient(),
    )
    key = storage.put("x/y.webp", b"img", content_type="image/webp")
    assert key == "pref/x/y.webp"
    assert calls["Bucket"] == "b"
    assert calls["Key"] == "pref/x/y.webp"
    assert storage.cdn_url_for(key) == "https://cdn.s3.test/pref/x/y.webp"
