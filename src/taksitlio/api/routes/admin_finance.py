"""Admin finance projection sync (ADR-010 P12).

Rebuilds product_finance_options into the search finance index.
Does not open personalized Campaign Gate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-finance"])


class RateSnapshotIn(BaseModel):
    financial_product_code: str = "fp"
    rate_type: str = "ZERO_RATE"
    monthly_rate: Optional[float] = None
    annual_cost_rate: Optional[float] = None
    freshness_status: str = "FRESH"


class CampaignIn(BaseModel):
    campaign_code: str
    institution_code: str
    display_name: str
    campaign_type: str = "ZERO_RATE"
    status: str = "ACTIVE"
    agreement_active: bool = True
    merchant_codes: List[str] = Field(default_factory=list)
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    allowed_terms: List[int] = Field(default_factory=list)


class TermOptionIn(BaseModel):
    institution_id: str
    financial_product_code: str = "fp"
    term_months: int = Field(..., gt=0)
    rate_snapshot: RateSnapshotIn
    campaign: Optional[CampaignIn] = None
    rate_snapshot_id: Optional[str] = None
    campaign_id: Optional[str] = None


class RebuildFinanceIn(BaseModel):
    product_id: str = Field(..., min_length=1)
    product_offer_id: str
    merchant_id: str
    merchant_code: str
    purchase_price: float = Field(..., gt=0)
    stock_status: str = "AVAILABLE"
    price_freshness: str = "FRESH"
    category_id: Optional[int] = None
    term_options: List[TermOptionIn] = Field(default_factory=list)


@router.post("/finance-options/rebuild")
async def rebuild_finance_options_endpoint(
    payload: RebuildFinanceIn, request: Request
) -> Dict[str, Any]:
    """Deterministic rebuild + index put. No invented rates."""

    from taksitlio.campaign_catalog.models import (
        CampaignStatus,
        CampaignType,
        FinanceCampaignRecord,
        RateSnapshotRecord,
        RateType,
    )
    from taksitlio.product_query.finance_projection import (
        InstitutionTermOption,
        OfferFinanceContext,
    )
    from taksitlio.product_query.finance_sync import sync_finance_options_for_product

    container = container_from(request)
    index = container.extras.get("finance_option_index")
    if index is None:
        raise HTTPException(status_code=501, detail="finance_option_index not configured")

    options: list[InstitutionTermOption] = []
    for opt in payload.term_options:
        try:
            rt = RateType(opt.rate_snapshot.rate_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid rate_type") from exc
        snap = RateSnapshotRecord(
            financial_product_code=opt.rate_snapshot.financial_product_code,
            rate_type=rt,
            monthly_rate=opt.rate_snapshot.monthly_rate,
            annual_cost_rate=opt.rate_snapshot.annual_cost_rate,
            freshness_status=opt.rate_snapshot.freshness_status,
        )
        campaign = None
        if opt.campaign is not None:
            try:
                ctype = CampaignType(opt.campaign.campaign_type)
                cstatus = CampaignStatus(opt.campaign.status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="invalid campaign_type or status"
                ) from exc
            campaign = FinanceCampaignRecord(
                campaign_code=opt.campaign.campaign_code,
                institution_code=opt.campaign.institution_code,
                display_name=opt.campaign.display_name,
                campaign_type=ctype,
                status=cstatus,
                agreement_active=opt.campaign.agreement_active,
                eligible_merchant_codes=tuple(opt.campaign.merchant_codes),
                minimum_purchase_amount=opt.campaign.min_amount,
                maximum_purchase_amount=opt.campaign.max_amount,
                eligible_terms=tuple(opt.campaign.allowed_terms),
            )
        options.append(
            InstitutionTermOption(
                institution_id=opt.institution_id,
                financial_product_code=opt.financial_product_code,
                term_months=opt.term_months,
                rate_snapshot=snap,
                campaign=campaign,
                rate_snapshot_id=opt.rate_snapshot_id,
                campaign_id=opt.campaign_id,
            )
        )

    offer = OfferFinanceContext(
        product_offer_id=payload.product_offer_id,
        merchant_id=payload.merchant_id,
        merchant_code=payload.merchant_code,
        purchase_price=payload.purchase_price,
        stock_status=payload.stock_status,
        price_freshness=payload.price_freshness,
        category_id=payload.category_id,
    )
    try:
        rows = await sync_finance_options_for_product(
            index,
            product_id=payload.product_id,
            offer=offer,
            term_options=options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "product_id": payload.product_id,
        "option_count": len(rows),
        "eligible_count": sum(1 for r in rows if r.eligibility_status == "ELIGIBLE"),
        "options": [
            {
                "institution_id": r.institution_id,
                "term_months": r.term_months,
                "monthly_payment": r.monthly_payment,
                "total_repayment": r.total_repayment,
                "eligibility_status": r.eligibility_status,
                "freshness_status": r.freshness_status,
                "display_label": r.display_label,
                "ineligible_reasons": list(r.ineligible_reasons),
            }
            for r in rows
        ],
    }


@router.get("/finance-options/{product_id}")
async def list_finance_options(product_id: str, request: Request) -> Dict[str, Any]:
    container = container_from(request)
    index = container.extras.get("finance_option_index")
    if index is None:
        raise HTTPException(status_code=501, detail="finance_option_index not configured")
    rows = await index.list_for_product(product_id)
    return {
        "product_id": product_id,
        "options": [
            {
                "institution_id": r.institution_id,
                "term_months": r.term_months,
                "monthly_payment": r.monthly_payment,
                "total_repayment": r.total_repayment,
                "eligibility_status": r.eligibility_status,
                "freshness_status": r.freshness_status,
                "display_label": r.display_label,
            }
            for r in rows
        ],
    }


@router.post("/institutions/reload-labels")
async def reload_institution_labels(request: Request) -> Dict[str, Any]:
    container = container_from(request)
    loader = container.extras.get("institution_label_loader")
    if loader is None:
        raise HTTPException(
            status_code=501, detail="institution_label_loader not configured"
        )
    from taksitlio.product_query.finance_index import load_institution_labels

    resolver = await load_institution_labels(loader)
    container.extras["institution_labels"] = resolver
    return {"label_count": len(resolver.labels)}
