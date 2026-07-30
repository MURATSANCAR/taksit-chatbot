"""Postgres campaign repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import asyncpg

from taksitlio.campaign.models import Campaign


def _campaign_from_row(row: asyncpg.Record) -> Campaign:
    attributes = row["attributes"] or {}
    if not isinstance(attributes, dict):
        attributes = dict(attributes)
    return Campaign(
        id=int(row["id"]),
        campaign_code=row["campaign_code"],
        title=row["title"],
        summary=row["summary"],
        category_code=row["category_code"],
        category_id=int(row["category_id"]),
        brand=row["brand"],
        product_name=row["product_name"],
        list_price=float(row["list_price"]) if row["list_price"] is not None else None,
        currency=row["currency"] or "TRY",
        installment_count=int(row["installment_count"])
        if row["installment_count"] is not None
        else None,
        monthly_payment=float(row["monthly_payment"])
        if row["monthly_payment"] is not None
        else None,
        cash_price=float(row["cash_price"]) if row["cash_price"] is not None else None,
        min_budget=float(row["min_budget"]) if row["min_budget"] is not None else None,
        max_budget=float(row["max_budget"]) if row["max_budget"] is not None else None,
        membership_required=bool(row["membership_required"]),
        membership_cta_url=row["membership_cta_url"],
        membership_cta_label=row["membership_cta_label"],
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        status=row["status"],
        attributes=attributes,
        search_text=row["search_text"] or "",
    )


class PostgresCampaignRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_by_category_codes(
        self,
        category_codes: Sequence[str],
        *,
        limit: int = 50,
    ) -> list[Campaign]:
        if not category_codes:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    ca.*,
                    cat.category_code
                FROM campaigns ca
                JOIN categories cat ON cat.id = ca.category_id
                WHERE cat.category_code = ANY($1::text[])
                  AND ca.status = 'ACTIVE'
                ORDER BY ca.updated_at DESC
                LIMIT $2
                """,
                list(category_codes),
                limit,
            )
        return [_campaign_from_row(r) for r in rows]

    async def get_eligibility_rules(self, rule_set_code: str = "DEFAULT") -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rules FROM eligibility_rule_sets WHERE rule_set_code = $1",
                rule_set_code,
            )
        if row is None:
            return []
        rules = row["rules"] or []
        if isinstance(rules, list):
            return [dict(r) if not isinstance(r, dict) else r for r in rules]
        return []

    async def get_ranking_policy(self, policy_code: str = "DEFAULT") -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT weights, max_results FROM ranking_policies WHERE policy_code = $1",
                policy_code,
            )
        if row is None:
            return {
                "weights": {
                    "budget_fit": 0.35,
                    "preference_fit": 0.25,
                    "semantic_relevance": 0.20,
                    "installment_fit": 0.15,
                    "freshness": 0.05,
                },
                "max_results": 5,
            }
        weights = row["weights"] or {}
        if not isinstance(weights, dict):
            weights = dict(weights)
        return {
            "weights": {str(k): float(v) for k, v in weights.items()},
            "max_results": int(row["max_results"] or 5),
        }
