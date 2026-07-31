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


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "crawler"
    / "feeds"
    / "fixtures"
    / "src-b-fibabanka.json"
)


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
