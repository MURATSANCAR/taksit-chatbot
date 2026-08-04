"""Generic bank/merchant campaign JSON feed (ADR-010 StormCrawler bridge).

Opaque adapter_code: ``generic.campaign_feed.v1``.

Does not invent rates or terms — missing rate fields stay empty / rate rows skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx

from taksitlio.campaign_catalog.models import (
    CampaignStatus,
    CampaignType,
    FinanceCampaignRecord,
    RateSnapshotRecord,
    RateType,
    VerificationStatus,
)
from taksitlio.ingestion.errors import ProductParseFailed, SourceTimeout
from taksitlio.product.hashing import content_hash

ADAPTER_CODE = "generic.campaign_feed.v1"

_VALID_TYPES = {t.value for t in CampaignType}


@dataclass(frozen=True)
class NormalizedCampaignTerm:
    months: Optional[int] = None
    rate_apr: Optional[float] = None
    monthly_rate_pct: Optional[float] = None
    fee: Optional[float] = None


@dataclass(frozen=True)
class NormalizedCampaign:
    external_campaign_id: str
    institution_code: str
    display_name: str
    campaign_type: str
    summary: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    terms: tuple[NormalizedCampaignTerm, ...] = ()
    merchant_codes: tuple[str, ...] = ()
    category_codes: tuple[str, ...] = ()
    source_url: Optional[str] = None
    content_hash: Optional[str] = None
    source_reference: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class GenericCampaignFeedAdapter:
    """Reads ``{"campaigns":[...]}`` from URL or local path."""

    adapter_code = ADAPTER_CODE

    def __init__(
        self,
        *,
        feed_url: Optional[str] = None,
        feed_path: Optional[str | Path] = None,
        timeout_seconds: float = 30.0,
        source_reference: Optional[str] = None,
        request_headers: Optional[Mapping[str, str]] = None,
        default_institution_code: Optional[str] = None,
    ) -> None:
        if not feed_url and not feed_path:
            raise ValueError("feed_url or feed_path required")
        self._feed_url = feed_url
        self._feed_path = Path(feed_path) if feed_path else None
        self._timeout = timeout_seconds
        self._source_reference = source_reference or feed_url or str(feed_path)
        self._request_headers = dict(request_headers or {})
        self._default_institution = default_institution_code
        self._items: dict[str, NormalizedCampaign] | None = None

    def capabilities(self) -> Sequence[str]:
        return ("CAMPAIGN", "FINANCE_OPTION")

    async def load_campaigns(self) -> Sequence[NormalizedCampaign]:
        items = await self._load()
        return tuple(items.values())

    async def _load(self) -> dict[str, NormalizedCampaign]:
        if self._items is not None:
            return self._items
        if self._feed_path is not None:
            try:
                raw = json.loads(self._feed_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProductParseFailed(str(exc), detail=str(self._feed_path)) from exc
        else:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(
                        self._feed_url,  # type: ignore[arg-type]
                        headers=self._request_headers or None,
                    )
                    resp.raise_for_status()
                    raw = resp.json()
            except httpx.TimeoutException as exc:
                raise SourceTimeout(str(exc), detail=self._feed_url) from exc
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                raise ProductParseFailed(str(exc), detail=self._feed_url) from exc

        campaigns = raw.get("campaigns") if isinstance(raw, dict) else None
        if not isinstance(campaigns, list):
            raise ProductParseFailed(
                "feed missing campaigns[] array",
                detail=self._source_reference,
            )
        indexed: dict[str, NormalizedCampaign] = {}
        for row in campaigns:
            if not isinstance(row, dict):
                continue
            parsed = _parse_row(
                row,
                source_reference=self._source_reference,
                default_institution=self._default_institution,
            )
            if parsed is None:
                continue
            indexed[parsed.external_campaign_id] = parsed
        self._items = indexed
        return indexed


def _parse_row(
    row: Mapping[str, Any],
    *,
    source_reference: str,
    default_institution: Optional[str],
) -> Optional[NormalizedCampaign]:
    cid = str(row.get("id") or row.get("campaign_code") or "").strip()
    name = str(row.get("name") or row.get("display_name") or "").strip()
    institution = str(
        row.get("institution_code") or default_institution or ""
    ).strip()
    if not cid or not name or not institution:
        return None
    ctype = str(row.get("campaign_type") or "INSTALLMENT").strip().upper()
    if ctype not in _VALID_TYPES:
        ctype = "INSTALLMENT"

    terms: list[NormalizedCampaignTerm] = []
    raw_terms = row.get("terms")
    if isinstance(raw_terms, list):
        for t in raw_terms:
            if not isinstance(t, dict):
                continue
            months = _as_int(t.get("months") if "months" in t else t.get("term_months"))
            if "rate_apr" in t:
                rate = _as_float(t.get("rate_apr"))
            elif "annual_cost_rate" in t:
                rate = _as_float(t.get("annual_cost_rate"))
            else:
                rate = None
            monthly_pct = (
                _as_float(t.get("monthly_rate_pct"))
                if "monthly_rate_pct" in t
                else None
            )
            fee = _as_float(t.get("fee")) if "fee" in t else None
            # Keep term only if at least one explicit field present (no invent).
            if months is None and rate is None and monthly_pct is None and fee is None:
                continue
            terms.append(
                NormalizedCampaignTerm(
                    months=months,
                    rate_apr=rate,
                    monthly_rate_pct=monthly_pct,
                    fee=fee,
                )
            )

    merchants = _as_str_tuple(row.get("merchant_codes"))
    categories = _as_str_tuple(row.get("category_codes"))

    return NormalizedCampaign(
        external_campaign_id=cid,
        institution_code=institution,
        display_name=name,
        campaign_type=ctype,
        summary=_as_str(row.get("summary")),
        valid_from=_as_str(row.get("valid_from")),
        valid_until=_as_str(row.get("valid_until")),
        min_amount=_as_float(row.get("min_amount") or row.get("minimum_purchase_amount")),
        max_amount=_as_float(row.get("max_amount") or row.get("maximum_purchase_amount")),
        terms=tuple(terms),
        merchant_codes=merchants,
        category_codes=categories,
        source_url=_as_str(row.get("source_url") or row.get("url")),
        content_hash=content_hash(dict(row)),
        source_reference=source_reference,
        raw=dict(row),
    )


def to_finance_campaign_record(campaign: NormalizedCampaign) -> FinanceCampaignRecord:
    eligible_terms = tuple(
        t.months for t in campaign.terms if t.months is not None and t.months > 0
    )
    try:
        ctype = CampaignType(campaign.campaign_type)
    except ValueError:
        ctype = CampaignType.INSTALLMENT
    return FinanceCampaignRecord(
        campaign_code=campaign.external_campaign_id,
        institution_code=campaign.institution_code,
        display_name=campaign.display_name,
        campaign_type=ctype,
        status=CampaignStatus.DRAFT,
        verification_status=VerificationStatus.UNVERIFIED,
        valid_from=_parse_dt(campaign.valid_from),
        valid_until=_parse_dt(campaign.valid_until),
        minimum_purchase_amount=campaign.min_amount,
        maximum_purchase_amount=campaign.max_amount,
        eligible_terms=eligible_terms,
        eligible_merchant_codes=campaign.merchant_codes,
        eligible_category_codes=tuple(
            c.strip().upper() for c in campaign.category_codes if c and str(c).strip()
        ),
        agreement_active=False,
        source_reference=campaign.source_reference or campaign.source_url,
    )


def to_rate_snapshots(campaign: NormalizedCampaign) -> list[RateSnapshotRecord]:
    """Build rate snapshots only when source provided an explicit rate — never invent."""

    out: list[RateSnapshotRecord] = []
    name_l = (campaign.display_name or "").casefold()
    pesin_zero = "peşin fiyatına" in name_l or "pesin fiyatina" in name_l
    for term in campaign.terms:
        if (
            term.rate_apr is None
            and term.monthly_rate_pct is None
            and term.fee is None
            and not (pesin_zero and term.months)
            and campaign.campaign_type != "ZERO_RATE"
        ):
            continue

        is_zero = (
            campaign.campaign_type == "ZERO_RATE"
            or term.rate_apr == 0.0
            or term.monthly_rate_pct == 0.0
            or (pesin_zero and term.monthly_rate_pct is None and term.rate_apr is None)
        )
        if is_zero:
            out.append(
                RateSnapshotRecord(
                    financial_product_code=f"{campaign.institution_code}-default",
                    rate_type=RateType.ZERO_RATE,
                    annual_cost_rate=0.0,
                    monthly_rate=0.0,
                    minimum_term=term.months,
                    maximum_term=term.months,
                    term_rates={term.months: 0.0} if term.months else {},
                    freshness_status="FRESH",
                    verification_status=VerificationStatus.UNVERIFIED,
                    source_reference=campaign.source_reference or campaign.source_url,
                    campaign_code=campaign.external_campaign_id,
                )
            )
            continue

        if term.monthly_rate_pct is None:
            # Annual-only without monthly — do not invent a monthly conversion.
            continue

        monthly = float(term.monthly_rate_pct) / 100.0
        term_rates: dict[int, float] = {}
        if term.months is not None:
            term_rates[term.months] = monthly
        out.append(
            RateSnapshotRecord(
                financial_product_code=f"{campaign.institution_code}-default",
                rate_type=RateType.INTEREST,
                monthly_rate=monthly,
                annual_cost_rate=(
                    float(term.rate_apr) / 100.0 if term.rate_apr is not None else None
                ),
                minimum_term=term.months,
                maximum_term=term.months,
                term_rates=term_rates,
                freshness_status="FRESH",
                verification_status=VerificationStatus.UNVERIFIED,
                source_reference=campaign.source_reference or campaign.source_url,
                campaign_code=campaign.external_campaign_id,
            )
        )
    return out


@dataclass
class CampaignFeedDryResult:
    adapter_code: str
    source_reference: str
    campaigns: list[FinanceCampaignRecord]
    rates: list[RateSnapshotRecord]
    skipped_incomplete: int
    rates_skipped_no_explicit_rate: int


async def run_campaign_feed_dry(
    adapter: GenericCampaignFeedAdapter,
) -> CampaignFeedDryResult:
    loaded = await adapter.load_campaigns()
    campaigns: list[FinanceCampaignRecord] = []
    rates: list[RateSnapshotRecord] = []
    rates_skipped = 0
    for item in loaded:
        campaigns.append(to_finance_campaign_record(item))
        snaps = to_rate_snapshots(item)
        if not snaps and item.terms:
            # Terms without rates — kept on campaign eligible_terms only.
            rates_skipped += sum(1 for t in item.terms if t.rate_apr is None)
        rates.extend(snaps)
    return CampaignFeedDryResult(
        adapter_code=ADAPTER_CODE,
        source_reference=adapter._source_reference,
        campaigns=campaigns,
        rates=rates,
        skipped_incomplete=0,
        rates_skipped_no_explicit_rate=rates_skipped,
    )


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            out.append(text)
    return tuple(out)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


__all__ = [
    "ADAPTER_CODE",
    "CampaignFeedDryResult",
    "GenericCampaignFeedAdapter",
    "NormalizedCampaign",
    "NormalizedCampaignTerm",
    "run_campaign_feed_dry",
    "to_finance_campaign_record",
    "to_rate_snapshots",
]
