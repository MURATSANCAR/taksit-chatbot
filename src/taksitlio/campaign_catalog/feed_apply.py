"""Apply campaign feed dry results into an in-memory catalog (no rate invent)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from taksitlio.campaign_catalog.models import FinanceCampaignRecord, RateSnapshotRecord

if TYPE_CHECKING:
    from taksitlio.ingestion.adapters.generic_campaign_feed import CampaignFeedDryResult


@dataclass
class InMemoryCampaignCatalog:
    campaigns_by_code: Dict[str, FinanceCampaignRecord] = field(default_factory=dict)
    rates: List[RateSnapshotRecord] = field(default_factory=list)
    institutions: Dict[str, str] = field(default_factory=dict)  # code → display_name

    def upsert_institution(self, institution_code: str, display_name: str) -> None:
        code = institution_code.strip()
        if code:
            self.institutions[code] = display_name.strip() or code

    def apply_feed(self, result: "CampaignFeedDryResult") -> dict:
        applied = 0
        for camp in result.campaigns:
            self.campaigns_by_code[camp.campaign_code] = camp
            applied += 1
        self.rates.extend(result.rates)
        return {
            "campaigns_applied": applied,
            "rates_applied": len(result.rates),
            "rates_skipped_no_explicit_rate": result.rates_skipped_no_explicit_rate,
            "institutions_known": len(self.institutions),
        }


def apply_campaign_feed_result(
    catalog: InMemoryCampaignCatalog,
    result: "CampaignFeedDryResult",
    *,
    institution_display_name: Optional[str] = None,
) -> dict:
    for camp in result.campaigns:
        catalog.upsert_institution(
            camp.institution_code,
            institution_display_name or camp.institution_code,
        )
    return catalog.apply_feed(result)


__all__ = [
    "InMemoryCampaignCatalog",
    "apply_campaign_feed_result",
]
