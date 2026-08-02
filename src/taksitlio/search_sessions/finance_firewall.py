"""Finance capability firewall — strip finance claims when display is BLOCKED."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional, Sequence


FINANCE_CLAIM_KEYS = frozenset(
    {
        "best_finance",
        "best_finance_summary",
        "best_monthly_payment",
        "best_total_repayment",
        "best_term_months",
        "institution_display_name",
        "institution_logo_cdn_url",
        "institution_code",
        "campaign_name",
        "campaign_display_name",
        "monthly_payment",
        "total_repayment",
        "term_months",
        "interest_rate",
        "zero_rate",
        "finance_ready",
        "finance_eligible",
    }
)


def finance_display_allowed(flags: Optional[Mapping[str, Any]]) -> bool:
    """True only when an explicit READY/ENABLED flag is present."""

    if not flags:
        return False
    status = str(
        flags.get("finance_display")
        or flags.get("FINANCE_DISPLAY")
        or flags.get("finance_capability")
        or ""
    ).upper()
    if status in {"BLOCKED", "NOT_APPLICABLE", "DISABLED", "OFF", ""}:
        return False
    return status in {"READY", "ENABLED", "ACTIVE"}


def strip_finance_fields(product: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(product)
    for k in FINANCE_CLAIM_KEYS:
        if k in out:
            out[k] = None
    # Nested summaries
    out["best_finance"] = None
    out["best_finance_summary"] = None
    return out


def apply_finance_firewall(
    products: Sequence[Mapping[str, Any]],
    *,
    flags: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    if finance_display_allowed(flags):
        return [dict(p) for p in products]
    return [strip_finance_fields(p) for p in products]


def assert_no_finance_claims(product: Mapping[str, Any]) -> list[str]:
    """Return list of forbidden finance claim keys that are populated."""

    hits: list[str] = []
    for k in (
        "best_finance",
        "best_finance_summary",
        "best_monthly_payment",
        "best_total_repayment",
    ):
        v = product.get(k)
        if v not in (None, {}, [], ""):
            hits.append(k)
    nested = product.get("best_finance") or product.get("best_finance_summary") or {}
    if isinstance(nested, Mapping):
        for k in (
            "monthly_payment",
            "total_repayment",
            "term_months",
            "institution_display_name",
            "campaign_name",
        ):
            if nested.get(k) not in (None, "", []):
                hits.append(f"nested.{k}")
    return hits


__all__ = [
    "FINANCE_CLAIM_KEYS",
    "apply_finance_firewall",
    "assert_no_finance_claims",
    "finance_display_allowed",
    "strip_finance_fields",
]
