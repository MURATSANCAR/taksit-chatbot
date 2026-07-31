"""P12 — finance option sync, Postgres mapping, admin rebuild API."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from taksitlio.campaign_catalog.models import RateSnapshotRecord, RateType
from taksitlio.product_query.finance_index import (
    InMemoryFinanceOptionIndex,
    InMemoryInstitutionLabelLoader,
    load_institution_labels,
)
from taksitlio.product_query.finance_projection import (
    InstitutionTermOption,
    OfferFinanceContext,
)
from taksitlio.product_query.finance_sync import sync_finance_options_for_product
from taksitlio.product_query.postgres_finance import (
    PostgresFinanceOptionIndex,
    _row_from_db,
)


@pytest.mark.asyncio
async def test_sync_finance_options_puts_eligible_rows() -> None:
    index = InMemoryFinanceOptionIndex()
    offer = OfferFinanceContext(
        product_offer_id="10",
        merchant_id="1",
        merchant_code="m1",
        purchase_price=12000,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
    )
    rate = RateSnapshotRecord(
        financial_product_code="fp",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
    )
    rows = await sync_finance_options_for_product(
        index,
        product_id="42",
        offer=offer,
        term_options=(
            InstitutionTermOption(
                institution_id="7",
                financial_product_code="fp",
                term_months=12,
                rate_snapshot=rate,
            ),
        ),
    )
    assert len(rows) == 1
    assert rows[0].eligibility_status == "ELIGIBLE"
    assert rows[0].monthly_payment == 1000.0
    listed = await index.list_for_product("42")
    assert len(listed) == 1
    assert listed[0].institution_id == "7"


@pytest.mark.asyncio
async def test_load_institution_labels_from_loader() -> None:
    loader = InMemoryInstitutionLabelLoader({"9": "Catalog Bank"})
    resolver = await load_institution_labels(loader)
    assert resolver.label_for("9") == "Catalog Bank"
    assert resolver.label_for("missing") == "institution:missing"


def test_row_from_db_maps_metadata() -> None:
    class FakeRow(dict):
        def keys(self):  # type: ignore[override]
            return super().keys()

    row = FakeRow(
        {
            "product_offer_id": 10,
            "merchant_id": 1,
            "institution_id": 7,
            "term_months": 12,
            "monthly_payment": 1000.0,
            "total_repayment": 12000.0,
            "fees_total": 0,
            "eligibility_status": "ELIGIBLE",
            "plan_kind": "CALCULATED_ESTIMATE",
            "freshness_status": "FRESH",
            "campaign_id": None,
            "rate_snapshot_id": 3,
            "metadata": {
                "display_label": "Tahmini aylık ödeme",
                "ineligible_reasons": [],
            },
        }
    )
    mapped = _row_from_db(row)
    assert mapped.institution_id == "7"
    assert mapped.display_label == "Tahmini aylık ödeme"
    assert mapped.monthly_payment == 1000.0


class _FakeConn:
    def __init__(self) -> None:
        self.offer_by_product: dict[int, int] = {42: 100}
        self.rows: list[dict[str, Any]] = []
        self.deleted_offer_ids: list[int] = []

    def transaction(self) -> Any:
        return self

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def fetchval(self, sql: str, *args: Any) -> Optional[int]:
        if "product_offers" in sql:
            return self.offer_by_product.get(int(args[0]))
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        pid = int(args[0])
        offer_id = self.offer_by_product.get(pid)
        if offer_id is None:
            return []
        return [r for r in self.rows if r["product_offer_id"] == offer_id]

    async def execute(self, sql: str, *args: Any) -> str:
        if sql.strip().startswith("DELETE"):
            offer_id = int(args[0])
            self.deleted_offer_ids.append(offer_id)
            self.rows = [r for r in self.rows if r["product_offer_id"] != offer_id]
            return "DELETE 1"
        if "INSERT INTO product_finance_options" in sql:
            self.rows.append(
                {
                    "product_offer_id": args[0],
                    "merchant_id": args[1],
                    "institution_id": args[2],
                    "campaign_id": args[3],
                    "term_months": args[4],
                    "monthly_payment": args[5],
                    "total_repayment": args[6],
                    "fees_total": args[7],
                    "eligibility_status": args[8],
                    "plan_kind": args[9],
                    "rate_snapshot_id": args[10],
                    "freshness_status": args[11],
                    "metadata": args[12],
                }
            )
            return "INSERT 1"
        return "OK"


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        return self._conn


@pytest.mark.asyncio
async def test_postgres_finance_index_put_and_list() -> None:
    conn = _FakeConn()
    index = PostgresFinanceOptionIndex(_FakePool(conn))
    offer = OfferFinanceContext(
        product_offer_id="100",
        merchant_id="1",
        merchant_code="m1",
        purchase_price=12000,
        stock_status="AVAILABLE",
        price_freshness="FRESH",
    )
    rate = RateSnapshotRecord(
        financial_product_code="fp",
        rate_type=RateType.ZERO_RATE,
        freshness_status="FRESH",
    )
    await sync_finance_options_for_product(
        index,
        product_id="42",
        offer=offer,
        term_options=(
            InstitutionTermOption(
                institution_id="7",
                financial_product_code="fp",
                term_months=12,
                rate_snapshot=rate,
                rate_snapshot_id="3",
            ),
        ),
    )
    listed = await index.list_for_product("42")
    assert len(listed) == 1
    assert listed[0].eligibility_status == "ELIGIBLE"
    assert listed[0].institution_id == "7"
    assert listed[0].display_label is not None


@pytest.mark.asyncio
async def test_admin_finance_rebuild_and_list() -> None:
    from httpx import ASGITransport, AsyncClient

    from taksitlio.api.app import create_app
    from taksitlio.app.container import build_in_memory_container

    container = build_in_memory_container()
    loader = container.extras["institution_label_loader"]
    loader.set_labels({"7": "Institution Seven"})
    app = create_app(container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rebuild = await client.post(
            "/v1/admin/finance-options/rebuild",
            json={
                "product_id": "42",
                "product_offer_id": "100",
                "merchant_id": "1",
                "merchant_code": "m1",
                "purchase_price": 12000,
                "term_options": [
                    {
                        "institution_id": "7",
                        "term_months": 12,
                        "rate_snapshot": {
                            "rate_type": "ZERO_RATE",
                            "freshness_status": "FRESH",
                        },
                    }
                ],
            },
        )
        assert rebuild.status_code == 200, rebuild.text
        body = rebuild.json()
        assert body["eligible_count"] == 1
        assert body["options"][0]["monthly_payment"] == 1000.0

        listed = await client.get("/v1/admin/finance-options/42")
        assert listed.status_code == 200
        assert len(listed.json()["options"]) == 1

        reload = await client.post("/v1/admin/institutions/reload-labels")
        assert reload.status_code == 200
        assert reload.json()["label_count"] == 1
