"""Deterministic campaign eligibility engine — no LLM interpretation of rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from taksitlio.campaign.models import Campaign, utcnow


@dataclass(frozen=True)
class EligibilityResult:
    campaign: Campaign
    eligible: bool
    reasons: tuple[str, ...]


class EligibilityEngine:
    """
    Applies DB-defined rule sets deterministically.

    Supported rule types:
      STATUS_ACTIVE, WITHIN_DATE_WINDOW, BUDGET_COMPATIBLE,
      CATEGORY_MATCH, MONTHLY_PAYMENT_COMPATIBLE
    """

    def evaluate(
        self,
        campaigns: Sequence[Campaign],
        need_profile: Mapping[str, Any],
        *,
        category_codes: Sequence[str],
        rules: Sequence[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> list[EligibilityResult]:
        clock = now or utcnow()
        results: list[EligibilityResult] = []
        for campaign in campaigns:
            ok, reasons = self._check(campaign, need_profile, category_codes, rules, clock)
            results.append(
                EligibilityResult(campaign=campaign, eligible=ok, reasons=tuple(reasons))
            )
        return results

    def filter_eligible(
        self,
        campaigns: Sequence[Campaign],
        need_profile: Mapping[str, Any],
        *,
        category_codes: Sequence[str],
        rules: Sequence[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> list[Campaign]:
        return [
            r.campaign
            for r in self.evaluate(
                campaigns,
                need_profile,
                category_codes=category_codes,
                rules=rules,
                now=now,
            )
            if r.eligible
        ]

    def _check(
        self,
        campaign: Campaign,
        need_profile: Mapping[str, Any],
        category_codes: Sequence[str],
        rules: Sequence[Mapping[str, Any]],
        now: datetime,
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        for rule in rules:
            rule_type = str(rule.get("type") or "")
            if rule_type == "STATUS_ACTIVE":
                if campaign.status != "ACTIVE":
                    failures.append("status_not_active")
            elif rule_type == "WITHIN_DATE_WINDOW":
                if not _within_window(campaign, now):
                    failures.append("outside_date_window")
            elif rule_type == "CATEGORY_MATCH":
                if campaign.category_code not in set(category_codes):
                    failures.append("category_mismatch")
            elif rule_type == "BUDGET_COMPATIBLE":
                if not _budget_compatible(campaign, need_profile):
                    failures.append("budget_incompatible")
            elif rule_type == "MONTHLY_PAYMENT_COMPATIBLE":
                if not _monthly_compatible(campaign, need_profile):
                    failures.append("monthly_payment_incompatible")
            # Unknown rule types are ignored (forward compatible)
        return (len(failures) == 0, failures)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _within_window(campaign: Campaign, now: datetime) -> bool:
    starts = _aware(campaign.starts_at)
    ends = _aware(campaign.ends_at)
    if starts and now < starts:
        return False
    if ends and now > ends:
        return False
    return True


def _budget_value(need_profile: Mapping[str, Any]) -> float | None:
    budget = need_profile.get("budget") or {}
    btype = budget.get("type")
    if btype == "RANGE":
        maximum = budget.get("maximum")
        if maximum is not None:
            return float(maximum)
        minimum = budget.get("minimum")
        return float(minimum) if minimum is not None else None
    if btype in {"EXACT", "APPROXIMATE"}:
        value = budget.get("value")
        return float(value) if value is not None else None
    if btype == "MONTHLY_PAYMENT":
        return None
    value = budget.get("value")
    if value is not None:
        return float(value)
    maximum = budget.get("maximum")
    return float(maximum) if maximum is not None else None


def _budget_compatible(campaign: Campaign, need_profile: Mapping[str, Any]) -> bool:
    budget = need_profile.get("budget") or {}
    if budget.get("type") == "UNKNOWN":
        return True
    if budget.get("type") == "MONTHLY_PAYMENT":
        return True

    user_budget = _budget_value(need_profile)
    if user_budget is None:
        return True

    price = campaign.list_price or campaign.cash_price
    if price is None:
        # Fall back to campaign budget band
        if campaign.max_budget is not None and user_budget > float(campaign.max_budget) * 1.15:
            return False
        if campaign.min_budget is not None and user_budget < float(campaign.min_budget) * 0.5:
            return False
        return True

    # Approximate budgets get a small tolerance band
    btype = budget.get("type")
    if btype == "APPROXIMATE":
        return float(price) <= user_budget * 1.15
    if btype == "RANGE":
        minimum = budget.get("minimum")
        maximum = budget.get("maximum")
        if maximum is not None and float(price) > float(maximum) * 1.05:
            return False
        if minimum is not None and float(price) < float(minimum) * 0.5:
            return False
        return True
    return float(price) <= user_budget * 1.05


def _monthly_compatible(campaign: Campaign, need_profile: Mapping[str, Any]) -> bool:
    budget = need_profile.get("budget") or {}
    monthly = budget.get("monthly_payment")
    if monthly is None and budget.get("type") != "MONTHLY_PAYMENT":
        return True
    if monthly is None:
        monthly = budget.get("value")
    if monthly is None:
        return True
    if campaign.monthly_payment is None:
        return True
    return float(campaign.monthly_payment) <= float(monthly) * 1.1
