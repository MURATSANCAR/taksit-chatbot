"""Build InstitutionTermOption rows from a campaign catalog (ADR-010 §50).

Does not invent rates — only snapshots already present on the catalog are used.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    FinanceCampaignRecord,
    RateSnapshotRecord,
)
from taksitlio.product_query.finance_projection import InstitutionTermOption


def _terms_for_snapshot(
    snap: RateSnapshotRecord,
    campaign: Optional[FinanceCampaignRecord],
) -> tuple[int, ...]:
    terms: list[int] = []
    if snap.term_rates:
        terms.extend(int(t) for t in snap.term_rates.keys() if int(t) > 0)
    if snap.minimum_term is not None and snap.maximum_term is not None:
        if snap.minimum_term == snap.maximum_term and snap.minimum_term > 0:
            terms.append(int(snap.minimum_term))
    if campaign is not None:
        terms.extend(int(t) for t in campaign.eligible_terms if t > 0)
    # Preserve order, drop duplicates.
    seen: set[int] = set()
    out: list[int] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return tuple(out)


def build_term_options(
    *,
    campaigns: Sequence[FinanceCampaignRecord],
    rates: Sequence[RateSnapshotRecord],
    merchant_code: str,
    institution_ids: Optional[dict[str, str]] = None,
    require_active: bool = True,
    require_agreement: bool = True,
) -> tuple[InstitutionTermOption, ...]:
    """Assemble term options for a merchant from catalog records.

    ``institution_ids`` maps institution_code → opaque institution_id string
    used in projection rows (DB id or code).
    """

    by_code = {c.campaign_code: c for c in campaigns}
    id_map = institution_ids or {}
    options: list[InstitutionTermOption] = []

    for idx, snap in enumerate(rates):
        camp: Optional[FinanceCampaignRecord] = None
        if snap.campaign_code:
            camp = by_code.get(snap.campaign_code)

        if camp is not None:
            if require_active and camp.status is not CampaignStatus.ACTIVE:
                continue
            if require_agreement and not camp.agreement_active:
                continue
            if (
                camp.eligible_merchant_codes
                and merchant_code not in camp.eligible_merchant_codes
            ):
                continue
            institution_code = camp.institution_code
        else:
            # Rate without campaign — still usable for estimate if fresh.
            institution_code = snap.financial_product_code.rsplit("-", 1)[0]

        institution_id = id_map.get(institution_code, institution_code)
        for term in _terms_for_snapshot(snap, camp):
            options.append(
                InstitutionTermOption(
                    institution_id=str(institution_id),
                    financial_product_code=snap.financial_product_code,
                    term_months=term,
                    rate_snapshot=snap,
                    campaign=camp,
                    rate_snapshot_id=f"rate:{snap.campaign_code or 'na'}:{idx}:{term}",
                    campaign_id=camp.campaign_code if camp else None,
                )
            )
    return tuple(options)


def activate_campaign_for_projection(
    campaign: FinanceCampaignRecord,
    *,
    agreement_active: bool = True,
) -> FinanceCampaignRecord:
    """Return an ACTIVE copy suitable for estimate projection (not personal approval).

    Elevates UNVERIFIED → SOURCE_PROVIDED so eligibility matches the DB projection
    path (source file present). Does not claim VERIFIED / personal credit approval.
    """

    from taksitlio.campaign_catalog.models import VerificationStatus

    verification = campaign.verification_status
    if verification is VerificationStatus.UNVERIFIED:
        verification = VerificationStatus.SOURCE_PROVIDED
    return replace(
        campaign,
        status=CampaignStatus.ACTIVE,
        verification_status=verification,
        agreement_active=agreement_active,
    )


__all__ = [
    "activate_campaign_for_projection",
    "build_term_options",
]
