"""Apply campaign feed dry results into an in-memory catalog (no rate invent)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    FinanceCampaignRecord,
    RateSnapshotRecord,
)
from taksitlio.campaign_catalog.term_options import activate_campaign_for_projection

if TYPE_CHECKING:
    from taksitlio.ingestion.adapters.generic_campaign_feed import CampaignFeedDryResult


@dataclass
class InMemoryCampaignCatalog:
    campaigns_by_code: Dict[str, FinanceCampaignRecord] = field(default_factory=dict)
    rates: List[RateSnapshotRecord] = field(default_factory=list)
    institutions: Dict[str, str] = field(default_factory=dict)  # code → display_name
    # merchant_code → institution_codes with ACTIVE agreements
    agreements: Dict[str, Set[str]] = field(default_factory=dict)

    def upsert_institution(self, institution_code: str, display_name: str) -> None:
        code = institution_code.strip()
        if code:
            self.institutions[code] = display_name.strip() or code

    def upsert_agreement(self, merchant_code: str, institution_code: str) -> None:
        m = merchant_code.strip()
        i = institution_code.strip()
        if not m or not i:
            return
        self.agreements.setdefault(m, set()).add(i)

    def has_agreement(self, merchant_code: str, institution_code: str) -> bool:
        return institution_code in self.agreements.get(merchant_code, set())

    def apply_feed(
        self,
        result: "CampaignFeedDryResult",
        *,
        activate: bool = False,
    ) -> dict:
        applied = 0
        agreements_created = 0
        activated = 0
        for camp in result.campaigns:
            record = camp
            if activate:
                # Merchant-scoped: agreement true when merchant_codes listed or
                # platform-wide (empty list) when activating for projection.
                record = activate_campaign_for_projection(
                    camp, agreement_active=True
                )
                activated += 1
            self.campaigns_by_code[record.campaign_code] = record
            for mcode in record.eligible_merchant_codes:
                self.upsert_agreement(mcode, record.institution_code)
                agreements_created += 1
            applied += 1
        self.rates.extend(result.rates)
        return {
            "campaigns_applied": applied,
            "rates_applied": len(result.rates),
            "rates_skipped_no_explicit_rate": result.rates_skipped_no_explicit_rate,
            "institutions_known": len(self.institutions),
            "agreements_created": agreements_created,
            "campaigns_activated": activated,
        }

    def activate_campaign(self, campaign_code: str) -> bool:
        camp = self.campaigns_by_code.get(campaign_code)
        if camp is None:
            return False
        active = activate_campaign_for_projection(camp, agreement_active=True)
        self.campaigns_by_code[campaign_code] = active
        for mcode in active.eligible_merchant_codes:
            self.upsert_agreement(mcode, active.institution_code)
        return True

    def campaigns_for_merchant(self, merchant_code: str) -> list[FinanceCampaignRecord]:
        out: list[FinanceCampaignRecord] = []
        for camp in self.campaigns_by_code.values():
            if camp.eligible_merchant_codes and merchant_code not in camp.eligible_merchant_codes:
                continue
            if camp.eligible_merchant_codes:
                # Scoped: require agreement row.
                if not self.has_agreement(merchant_code, camp.institution_code):
                    # Still allow if agreement_active was set at apply time.
                    if not camp.agreement_active:
                        continue
            out.append(camp)
        return out


def apply_campaign_feed_result(
    catalog: InMemoryCampaignCatalog,
    result: "CampaignFeedDryResult",
    *,
    institution_display_name: Optional[str] = None,
    activate: bool = False,
) -> dict:
    for camp in result.campaigns:
        catalog.upsert_institution(
            camp.institution_code,
            institution_display_name or camp.institution_code,
        )
    return catalog.apply_feed(result, activate=activate)


def mark_agreements_on_campaigns(
    catalog: InMemoryCampaignCatalog,
    *,
    merchant_code: str,
) -> int:
    """Set agreement_active on campaigns that have a merchant↔bank agreement."""

    updated = 0
    for code, camp in list(catalog.campaigns_by_code.items()):
        if not catalog.has_agreement(merchant_code, camp.institution_code):
            continue
        if camp.agreement_active:
            continue
        catalog.campaigns_by_code[code] = replace(camp, agreement_active=True)
        updated += 1
    return updated


__all__ = [
    "InMemoryCampaignCatalog",
    "apply_campaign_feed_result",
    "mark_agreements_on_campaigns",
]
