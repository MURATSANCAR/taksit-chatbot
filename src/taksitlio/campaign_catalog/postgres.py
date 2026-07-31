"""Postgres persist for finance campaigns, rates, and merchant–bank agreements.

Promoted from ops script logic — no rate invent; activation is explicit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from taksitlio.campaign_catalog.models import FinanceCampaignRecord, RateSnapshotRecord
from taksitlio.ingestion.adapters.generic_campaign_feed import CampaignFeedDryResult


@dataclass(frozen=True)
class CampaignPersistStats:
    institutions_upserted: int
    campaigns_upserted: int
    terms_upserted: int
    merchants_linked: int
    agreements_upserted: int
    rates_upserted: int
    financial_products_upserted: int
    activated: int


def _normalize_name(name: str) -> str:
    return name.strip().casefold()


async def ensure_institution(conn: Any, *, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO financial_institutions (
          institution_code, display_name, normalized_name, status
        )
        VALUES ($1::text, $2::text, $3::text, 'ACTIVE')
        ON CONFLICT (institution_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              normalized_name = EXCLUDED.normalized_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
        _normalize_name(name),
    )
    return int(row["id"])


async def ensure_merchant(conn: Any, *, code: str, name: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO merchants (merchant_code, display_name, status)
        VALUES ($1, $2, 'ACTIVE')
        ON CONFLICT (merchant_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        code,
        name,
    )
    if row is None:
        row = await conn.fetchrow(
            "SELECT id FROM merchants WHERE merchant_code=$1", code
        )
    return int(row["id"])


async def ensure_financial_product(
    conn: Any,
    *,
    institution_id: int,
    product_code: str,
    display_name: str,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO financial_products (
          institution_id, product_code, display_name, product_type, status
        )
        VALUES ($1, $2, $3, 'INSTALLMENT', 'ACTIVE')
        ON CONFLICT (institution_id, product_code) DO UPDATE
          SET display_name = EXCLUDED.display_name,
              status = 'ACTIVE',
              updated_at = NOW()
        RETURNING id
        """,
        institution_id,
        product_code,
        display_name,
    )
    return int(row["id"])


async def upsert_merchant_agreement(
    conn: Any,
    *,
    merchant_id: int,
    institution_id: int,
    financial_product_id: int,
    source_reference: Optional[str],
) -> None:
    await conn.execute(
        """
        INSERT INTO merchant_financial_agreements (
          merchant_id, institution_id, financial_product_id,
          status, source_reference
        )
        VALUES ($1, $2, $3, 'ACTIVE', $4)
        ON CONFLICT (merchant_id, institution_id, financial_product_id) DO UPDATE
          SET status = 'ACTIVE',
              source_reference = COALESCE(EXCLUDED.source_reference, merchant_financial_agreements.source_reference),
              updated_at = NOW()
        """,
        merchant_id,
        institution_id,
        financial_product_id,
        source_reference,
    )


async def persist_campaign_feed(
    conn: Any,
    result: CampaignFeedDryResult,
    *,
    institution_display_names: Optional[Mapping[str, str]] = None,
    raw_by_campaign_code: Optional[Mapping[str, Mapping[str, Any]]] = None,
    activate: bool = False,
) -> CampaignPersistStats:
    """Write dry-run campaigns + explicit rates into V018 tables.

    ``activate=True`` marks campaigns ACTIVE for estimate projection
    (still not personalized credit approval).
    """

    names = dict(institution_display_names or {})
    raw_map = dict(raw_by_campaign_code or {})
    inst_ids: dict[str, int] = {}
    product_ids: dict[str, int] = {}
    institutions_upserted = 0
    campaigns_upserted = 0
    terms_upserted = 0
    merchants_linked = 0
    agreements_upserted = 0
    rates_upserted = 0
    products_upserted = 0
    activated = 0

    for camp in result.campaigns:
        iname = names.get(camp.institution_code) or camp.institution_code
        if camp.institution_code not in inst_ids:
            inst_ids[camp.institution_code] = await ensure_institution(
                conn, code=camp.institution_code, name=iname
            )
            institutions_upserted += 1
        institution_id = inst_ids[camp.institution_code]

        fp_code = f"{camp.institution_code}-default"
        if fp_code not in product_ids:
            product_ids[fp_code] = await ensure_financial_product(
                conn,
                institution_id=institution_id,
                product_code=fp_code,
                display_name=f"{iname} Installment",
            )
            products_upserted += 1
        financial_product_id = product_ids[fp_code]

        raw = raw_map.get(camp.campaign_code, {})
        summary = raw.get("summary") if isinstance(raw, Mapping) else None
        metadata = {
            k: raw.get(k)
            for k in ("image_url", "image_local_path", "source_url", "terms")
            if isinstance(raw, Mapping) and raw.get(k) is not None
        }
        status = "ACTIVE" if activate else "DRAFT"
        if activate:
            activated += 1

        await conn.execute(
            """
            INSERT INTO finance_campaigns (
              institution_id, financial_product_id, campaign_code, display_name,
              summary, campaign_type, status, verification_status,
              minimum_purchase_amount, maximum_purchase_amount,
              valid_from, valid_until, source_reference, metadata
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, 'UNVERIFIED', $8, $9, $10, $11, $12,
              $13::jsonb
            )
            ON CONFLICT (campaign_code) DO UPDATE SET
              financial_product_id = EXCLUDED.financial_product_id,
              display_name = EXCLUDED.display_name,
              summary = EXCLUDED.summary,
              campaign_type = EXCLUDED.campaign_type,
              status = EXCLUDED.status,
              minimum_purchase_amount = EXCLUDED.minimum_purchase_amount,
              maximum_purchase_amount = EXCLUDED.maximum_purchase_amount,
              valid_from = EXCLUDED.valid_from,
              valid_until = EXCLUDED.valid_until,
              source_reference = EXCLUDED.source_reference,
              metadata = EXCLUDED.metadata,
              updated_at = NOW()
            """,
            institution_id,
            financial_product_id,
            camp.campaign_code,
            camp.display_name,
            summary,
            camp.campaign_type.value,
            status,
            camp.minimum_purchase_amount,
            camp.maximum_purchase_amount,
            camp.valid_from,
            camp.valid_until,
            camp.source_reference,
            json.dumps(metadata, ensure_ascii=False),
        )
        crow = await conn.fetchrow(
            "SELECT id FROM finance_campaigns WHERE campaign_code=$1",
            camp.campaign_code,
        )
        cid = int(crow["id"])
        campaigns_upserted += 1

        for months in camp.eligible_terms:
            await conn.execute(
                """
                INSERT INTO campaign_terms (campaign_id, term_months, included)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (campaign_id, term_months) DO NOTHING
                """,
                cid,
                months,
            )
            terms_upserted += 1

        for mcode in camp.eligible_merchant_codes:
            mid = await ensure_merchant(
                conn, code=mcode, name=mcode.removeprefix("m-").title()
            )
            await conn.execute(
                """
                INSERT INTO campaign_merchants (campaign_id, merchant_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                cid,
                mid,
            )
            merchants_linked += 1
            await upsert_merchant_agreement(
                conn,
                merchant_id=mid,
                institution_id=institution_id,
                financial_product_id=financial_product_id,
                source_reference=camp.source_reference,
            )
            agreements_upserted += 1

        if isinstance(raw, Mapping) and raw:
            await conn.execute(
                """
                INSERT INTO campaign_source_snapshots (
                  campaign_id, content_hash, payload, source_reference
                ) VALUES ($1, $2, $3::jsonb, $4)
                """,
                cid,
                str(raw.get("content_hash") or raw.get("id") or camp.campaign_code),
                json.dumps(dict(raw), ensure_ascii=False),
                raw.get("source_url") or camp.source_reference,
            )

    # Rates — only those produced by adapter (explicit / zero-rate rules).
    camp_id_by_code: dict[str, int] = {}
    for camp in result.campaigns:
        row = await conn.fetchrow(
            "SELECT id FROM finance_campaigns WHERE campaign_code=$1",
            camp.campaign_code,
        )
        if row:
            camp_id_by_code[camp.campaign_code] = int(row["id"])

    for snap in result.rates:
        institution_code = snap.financial_product_code.rsplit("-", 1)[0]
        if institution_code not in inst_ids:
            inst_ids[institution_code] = await ensure_institution(
                conn, code=institution_code, name=names.get(institution_code) or institution_code
            )
            institutions_upserted += 1
        institution_id = inst_ids[institution_code]
        if snap.financial_product_code not in product_ids:
            product_ids[snap.financial_product_code] = await ensure_financial_product(
                conn,
                institution_id=institution_id,
                product_code=snap.financial_product_code,
                display_name=f"{institution_code} Installment",
            )
            products_upserted += 1
        fp_id = product_ids[snap.financial_product_code]
        campaign_id = (
            camp_id_by_code.get(snap.campaign_code) if snap.campaign_code else None
        )
        rate_row = await conn.fetchrow(
            """
            INSERT INTO finance_rate_snapshots (
              financial_product_id, campaign_id,
              minimum_amount, maximum_amount, minimum_term, maximum_term,
              monthly_rate, annual_cost_rate, profit_rate, rate_type,
              verification_status, freshness_status, valid_from, valid_until,
              source_reference, metadata
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
              $16::jsonb
            )
            RETURNING id
            """,
            fp_id,
            campaign_id,
            snap.minimum_amount,
            snap.maximum_amount,
            snap.minimum_term,
            snap.maximum_term,
            snap.monthly_rate,
            snap.annual_cost_rate,
            snap.profit_rate,
            snap.rate_type.value,
            snap.verification_status.value,
            snap.freshness_status,
            snap.valid_from,
            snap.valid_until,
            snap.source_reference,
            json.dumps({"campaign_code": snap.campaign_code}, ensure_ascii=False),
        )
        rate_id = int(rate_row["id"])
        rates_upserted += 1
        for term_months, monthly in (snap.term_rates or {}).items():
            await conn.execute(
                """
                INSERT INTO finance_rate_tiers (
                  rate_snapshot_id, term_months, monthly_rate
                ) VALUES ($1, $2, $3)
                ON CONFLICT (rate_snapshot_id, term_months) DO UPDATE
                  SET monthly_rate = EXCLUDED.monthly_rate
                """,
                rate_id,
                int(term_months),
                float(monthly),
            )

    return CampaignPersistStats(
        institutions_upserted=institutions_upserted,
        campaigns_upserted=campaigns_upserted,
        terms_upserted=terms_upserted,
        merchants_linked=merchants_linked,
        agreements_upserted=agreements_upserted,
        rates_upserted=rates_upserted,
        financial_products_upserted=products_upserted,
        activated=activated,
    )


async def load_term_option_inputs_for_merchant(
    conn: Any,
    *,
    merchant_code: str,
) -> tuple[list[FinanceCampaignRecord], list[RateSnapshotRecord], dict[str, str]]:
    """Load ACTIVE campaigns + FRESH rates + institution id map for a merchant."""

    from datetime import datetime

    from taksitlio.campaign_catalog.models import (
        CampaignStatus,
        CampaignType,
        RateType,
        VerificationStatus,
    )

    merchant = await conn.fetchrow(
        "SELECT id FROM merchants WHERE merchant_code=$1", merchant_code
    )
    if merchant is None:
        return [], [], {}

    merchant_id = int(merchant["id"])
    camp_rows = await conn.fetch(
        """
        SELECT
          c.id, c.campaign_code, c.display_name, c.campaign_type, c.status,
          c.verification_status, c.valid_from, c.valid_until,
          c.minimum_purchase_amount, c.maximum_purchase_amount,
          c.source_reference, i.institution_code, i.id AS institution_id,
          EXISTS (
            SELECT 1 FROM merchant_financial_agreements mfa
            WHERE mfa.merchant_id = $1
              AND mfa.institution_id = c.institution_id
              AND mfa.status = 'ACTIVE'
          ) AS agreement_active,
          COALESCE(
            (SELECT array_agg(ct.term_months ORDER BY ct.term_months)
             FROM campaign_terms ct
             WHERE ct.campaign_id = c.id AND ct.included),
            '{}'::int[]
          ) AS eligible_terms,
          COALESCE(
            (SELECT array_agg(m.merchant_code ORDER BY m.merchant_code)
             FROM campaign_merchants cm
             JOIN merchants m ON m.id = cm.merchant_id
             WHERE cm.campaign_id = c.id),
            '{}'::text[]
          ) AS eligible_merchant_codes
        FROM finance_campaigns c
        JOIN financial_institutions i ON i.id = c.institution_id
        WHERE c.status = 'ACTIVE'
          AND (
            NOT EXISTS (
              SELECT 1 FROM campaign_merchants cm WHERE cm.campaign_id = c.id
            )
            OR EXISTS (
              SELECT 1 FROM campaign_merchants cm
              WHERE cm.campaign_id = c.id AND cm.merchant_id = $1
            )
          )
        """,
        merchant_id,
    )

    campaigns: list[FinanceCampaignRecord] = []
    institution_ids: dict[str, str] = {}
    for row in camp_rows:
        institution_ids[str(row["institution_code"])] = str(row["institution_id"])
        terms = tuple(int(t) for t in (row["eligible_terms"] or []))
        merchants = tuple(str(m) for m in (row["eligible_merchant_codes"] or []))
        campaigns.append(
            FinanceCampaignRecord(
                campaign_code=str(row["campaign_code"]),
                institution_code=str(row["institution_code"]),
                display_name=str(row["display_name"]),
                campaign_type=CampaignType(str(row["campaign_type"])),
                status=CampaignStatus(str(row["status"])),
                verification_status=VerificationStatus(str(row["verification_status"])),
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                minimum_purchase_amount=(
                    None
                    if row["minimum_purchase_amount"] is None
                    else float(row["minimum_purchase_amount"])
                ),
                maximum_purchase_amount=(
                    None
                    if row["maximum_purchase_amount"] is None
                    else float(row["maximum_purchase_amount"])
                ),
                eligible_terms=terms,
                eligible_merchant_codes=merchants,
                agreement_active=bool(row["agreement_active"]),
                source_reference=row["source_reference"],
            )
        )

    if not campaigns:
        return [], [], institution_ids

    codes = [c.campaign_code for c in campaigns]
    rate_rows = await conn.fetch(
        """
        SELECT
          rs.id, rs.monthly_rate, rs.annual_cost_rate, rs.profit_rate,
          rs.rate_type, rs.freshness_status, rs.verification_status,
          rs.minimum_amount, rs.maximum_amount, rs.minimum_term, rs.maximum_term,
          rs.valid_from, rs.valid_until, rs.source_reference,
          fp.product_code, c.campaign_code,
          COALESCE(
            (SELECT jsonb_object_agg(rt.term_months::text, rt.monthly_rate)
             FROM finance_rate_tiers rt WHERE rt.rate_snapshot_id = rs.id),
            '{}'::jsonb
          ) AS term_rates
        FROM finance_rate_snapshots rs
        JOIN financial_products fp ON fp.id = rs.financial_product_id
        LEFT JOIN finance_campaigns c ON c.id = rs.campaign_id
        WHERE rs.freshness_status = 'FRESH'
          AND (c.campaign_code = ANY($1::text[]) OR rs.campaign_id IS NULL)
        ORDER BY rs.id
        """,
        codes,
    )

    rates: list[RateSnapshotRecord] = []
    for row in rate_rows:
        raw_tiers = row["term_rates"] or {}
        if not isinstance(raw_tiers, dict):
            raw_tiers = dict(raw_tiers)
        term_rates = {
            int(k): float(v)
            for k, v in raw_tiers.items()
            if v is not None
        }
        rates.append(
            RateSnapshotRecord(
                financial_product_code=str(row["product_code"]),
                rate_type=RateType(str(row["rate_type"])),
                monthly_rate=(
                    None if row["monthly_rate"] is None else float(row["monthly_rate"])
                ),
                annual_cost_rate=(
                    None
                    if row["annual_cost_rate"] is None
                    else float(row["annual_cost_rate"])
                ),
                profit_rate=(
                    None if row["profit_rate"] is None else float(row["profit_rate"])
                ),
                minimum_amount=(
                    None
                    if row["minimum_amount"] is None
                    else float(row["minimum_amount"])
                ),
                maximum_amount=(
                    None
                    if row["maximum_amount"] is None
                    else float(row["maximum_amount"])
                ),
                minimum_term=(
                    None if row["minimum_term"] is None else int(row["minimum_term"])
                ),
                maximum_term=(
                    None if row["maximum_term"] is None else int(row["maximum_term"])
                ),
                term_rates=term_rates,
                freshness_status=str(row["freshness_status"]),
                verification_status=VerificationStatus(str(row["verification_status"])),
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                source_reference=row["source_reference"],
                campaign_code=(
                    None if row["campaign_code"] is None else str(row["campaign_code"])
                ),
            )
        )

    _ = datetime  # reserved for future valid_from filtering
    return campaigns, rates, institution_ids


__all__ = [
    "CampaignPersistStats",
    "ensure_financial_product",
    "ensure_institution",
    "ensure_merchant",
    "load_term_option_inputs_for_merchant",
    "persist_campaign_feed",
    "upsert_merchant_agreement",
]
