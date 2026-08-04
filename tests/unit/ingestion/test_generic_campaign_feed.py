"""Unit tests for generic.campaign_feed.v1 — no invented rates."""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.campaign_catalog.feed_apply import (
    InMemoryCampaignCatalog,
    apply_campaign_feed_result,
)
from taksitlio.campaign_catalog.models import RateType
from taksitlio.ingestion.adapters.generic_campaign_feed import (
    ADAPTER_CODE,
    GenericCampaignFeedAdapter,
    run_campaign_feed_dry,
)
from taksitlio.ingestion.binding import (
    SourceBinding,
    build_default_registry,
    instantiate_campaign_adapter,
)


FIXTURES = (
    Path(__file__).resolve().parents[3] / "crawler" / "feeds" / "fixtures"
)
FIXTURE = FIXTURES / "src-b-fibabanka.json"
ALBARAKA_DRAFT = FIXTURES / "src-b-albaraka.json"
KUVEYTTURK_DRAFT = FIXTURES / "src-b-kuveytturk.json"


@pytest.mark.asyncio
async def test_campaign_feed_fixture_no_invented_rates() -> None:
    assert FIXTURE.exists()
    adapter = GenericCampaignFeedAdapter(
        feed_path=FIXTURE,
        default_institution_code="fi-fibabanka",
    )
    result = await run_campaign_feed_dry(adapter)
    assert result.adapter_code == ADAPTER_CODE
    assert len(result.campaigns) == 2
    # One campaign has empty terms → no rates; one has explicit zero rate.
    assert len(result.rates) == 1
    assert result.rates[0].rate_type is RateType.ZERO_RATE
    assert result.rates[0].annual_cost_rate == 0.0

    catalog = InMemoryCampaignCatalog()
    applied = apply_campaign_feed_result(
        catalog, result, institution_display_name="Ops Label"
    )
    assert applied["campaigns_applied"] == 2
    assert applied["rates_applied"] == 1
    assert "fi-fibabanka" in catalog.institutions


@pytest.mark.asyncio
async def test_instantiate_campaign_adapter_from_binding() -> None:
    binding = SourceBinding(
        source_code="src-b-fibabanka",
        adapter_code=ADAPTER_CODE,
        merchant_id="ops-platform",
        config={
            "feed_path": str(FIXTURE),
            "institution_code": "fi-fibabanka",
        },
    )
    adapter = instantiate_campaign_adapter(binding)
    camps = await adapter.load_campaigns()
    assert len(camps) >= 1


def test_registry_lists_campaign_adapter() -> None:
    codes = build_default_registry().known_codes()
    assert "generic.json_feed.v1" in codes
    assert ADAPTER_CODE in codes


def test_campaign_adapter_rejects_inline_secrets() -> None:
    binding = SourceBinding(
        source_code="x",
        adapter_code=ADAPTER_CODE,
        merchant_id="m",
        config={"feed_path": str(FIXTURE), "api_key": "nope"},
    )
    with pytest.raises(ValueError, match="credential_ref"):
        instantiate_campaign_adapter(binding)


@pytest.mark.asyncio
async def test_albaraka_draft_fixture_parses_without_merchant_invent() -> None:
    """Fehmi CMS 9502 draft: explicit monthly rates, empty merchants, DB category codes."""
    assert ALBARAKA_DRAFT.exists()
    adapter = GenericCampaignFeedAdapter(
        feed_path=ALBARAKA_DRAFT,
        default_institution_code="fi-albaraka",
    )
    loaded = await adapter.load_campaigns()
    assert len(loaded) == 3
    assert all(c.institution_code == "fi-albaraka" for c in loaded)
    assert all(c.merchant_codes == () for c in loaded)
    codes = {c.external_campaign_id: c for c in loaded}
    assert codes["alb-9502-phone-le-20k"].category_codes == ("MOBILE_PHONE",)
    assert codes["alb-9502-phone-gt-20k"].max_amount == 150000.0
    assert codes["alb-9502-tablet-laptop"].category_codes == ("TABLET", "LAPTOP")

    result = await run_campaign_feed_dry(adapter)
    assert len(result.campaigns) == 3
    # monthly_rate_pct present → rate snapshots (no invent)
    assert len(result.rates) == 3
    assert all(r.monthly_rate == pytest.approx(0.0199) for r in result.rates)
    assert all(c.status.value == "DRAFT" for c in result.campaigns)
    assert all(not c.eligible_merchant_codes for c in result.campaigns)
    phone = next(c for c in result.campaigns if c.campaign_code == "alb-9502-phone-le-20k")
    assert phone.eligible_category_codes == ("MOBILE_PHONE",)
    tablet = next(c for c in result.campaigns if c.campaign_code == "alb-9502-tablet-laptop")
    assert tablet.eligible_category_codes == ("TABLET", "LAPTOP")


@pytest.mark.asyncio
async def test_kuveytturk_draft_fixture_parses_without_scope_invent() -> None:
    assert KUVEYTTURK_DRAFT.exists()
    adapter = GenericCampaignFeedAdapter(
        feed_path=KUVEYTTURK_DRAFT,
        default_institution_code="fi-kuveytturk",
    )
    result = await run_campaign_feed_dry(adapter)
    assert len(result.campaigns) == 1
    camp = result.campaigns[0]
    assert camp.campaign_code == "kuv-7802-new-customer"
    assert camp.eligible_merchant_codes == ()
    assert camp.eligible_category_codes == ()
    assert len(result.rates) == 2
    assert all(r.monthly_rate == pytest.approx(0.0299) for r in result.rates)
