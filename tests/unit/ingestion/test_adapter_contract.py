"""ADR-010 P0 — adapter contract + registry tests."""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Optional, Sequence

import pytest

from taksitlio.campaign_catalog import PACKAGE_STATUS as CAMPAIGN_CATALOG_STATUS
from taksitlio.ingestion import (
    AdapterRegistry,
    DiscoveredProductRef,
    IngestionCapability,
    MerchantProductSourceAdapter,
    NormalizedOffer,
    NormalizedProduct,
    NormalizedStock,
    SourceTimeout,
)
from taksitlio.ingestion.protocol import NormalizedMediaRef
from taksitlio.merchant import MerchantActivationGate, MerchantRecord
from taksitlio.payment_plan import PACKAGE_STATUS as PAYMENT_PLAN_STATUS
from taksitlio.payment_plan import PaymentPlanKind


class _StubAdapter:
    adapter_code = "stub.feed.v1"

    def capabilities(self) -> Sequence[IngestionCapability]:
        return (
            IngestionCapability.PRODUCT_DISCOVERY,
            IngestionCapability.PRODUCT_DETAIL,
            IngestionCapability.PRICE,
            IngestionCapability.STOCK,
            IngestionCapability.MEDIA,
        )

    async def discover_products(
        self, *, cursor: Optional[str] = None
    ) -> AsyncIterator[DiscoveredProductRef]:
        yield DiscoveredProductRef(external_product_id="ext-1", content_hash="abc")

    async def fetch_product(self, external_product_id: str) -> NormalizedProduct:
        return NormalizedProduct(
            external_product_id=external_product_id,
            display_name="Example Product",
            merchant_sku="SKU-1",
        )

    async def fetch_offers(self, external_product_id: str) -> Sequence[NormalizedOffer]:
        return (
            NormalizedOffer(
                external_product_id=external_product_id,
                current_price=42999.0,
                currency="TRY",
            ),
        )

    async def fetch_stock(self, external_product_id: str) -> Sequence[NormalizedStock]:
        return (
            NormalizedStock(
                external_product_id=external_product_id,
                stock_status="AVAILABLE",
            ),
        )

    async def fetch_media(
        self, external_product_id: str
    ) -> Sequence[NormalizedMediaRef]:
        return (
            NormalizedMediaRef(
                external_product_id=external_product_id,
                source_url="https://example.test/img.jpg",
                media_role="PRIMARY",
            ),
        )

    async def fetch_finance_metadata(
        self, external_product_id: str
    ) -> Mapping[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_adapter_registry_and_contract() -> None:
    registry = AdapterRegistry()
    registry.register("stub.feed.v1", _StubAdapter)

    adapter: MerchantProductSourceAdapter = registry.get("stub.feed.v1")
    assert adapter.adapter_code == "stub.feed.v1"
    assert IngestionCapability.PRICE in adapter.capabilities()

    refs = [ref async for ref in adapter.discover_products()]
    assert refs[0].external_product_id == "ext-1"

    product = await adapter.fetch_product("ext-1")
    assert product.display_name == "Example Product"
    offers = await adapter.fetch_offers("ext-1")
    assert offers[0].current_price == 42999.0
    stock = await adapter.fetch_stock("ext-1")
    assert stock[0].stock_status == "AVAILABLE"
    media = await adapter.fetch_media("ext-1")
    assert media[0].media_role == "PRIMARY"
    assert await adapter.fetch_finance_metadata("ext-1") == {}


def test_adapter_registry_rejects_duplicate_and_unknown() -> None:
    registry = AdapterRegistry()
    registry.register("stub.feed.v1", _StubAdapter)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("stub.feed.v1", _StubAdapter)
    with pytest.raises(KeyError, match="unknown adapter_code"):
        registry.get("missing.adapter")


def test_typed_ingestion_error_codes() -> None:
    err = SourceTimeout("deadline", detail="upstream")
    assert err.code == "SOURCE_TIMEOUT"
    assert err.detail == "upstream"


def test_merchant_activation_default_blocked() -> None:
    m = MerchantRecord(merchant_code="m1", display_name="Example Merchant")
    assert m.activation_gate is MerchantActivationGate.BLOCKED


def test_p0_stub_packages_importable() -> None:
    assert CAMPAIGN_CATALOG_STATUS == "STUB_P0"
    assert PAYMENT_PLAN_STATUS == "STUB_P0"
    assert PaymentPlanKind.CALCULATED_ESTIMATE.value == "CALCULATED_ESTIMATE"
