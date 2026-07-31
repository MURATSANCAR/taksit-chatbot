"""Merchant production activation gate (ADR-010 §74)."""

from __future__ import annotations

from dataclasses import dataclass

from taksitlio.merchant.models import MerchantActivationGate


@dataclass(frozen=True)
class MerchantReadinessSignals:
    merchant_verified: bool = False
    source_healthy: bool = False
    product_coverage_ok: bool = False
    image_coverage_ok: bool = False
    price_freshness_ok: bool = False
    bank_agreements_verified: bool = False
    campaign_mapping_verified: bool = False
    payment_calculations_tested: bool = False


@dataclass(frozen=True)
class MerchantGateDecision:
    gate: MerchantActivationGate
    reasons: tuple[str, ...]
    allowed_data_kinds: tuple[str, ...]


_FULL_KINDS = (
    "products",
    "prices",
    "stock",
    "images",
    "finance_options",
    "campaigns",
    "payment_plans",
)


def evaluate_merchant_activation(signals: MerchantReadinessSignals) -> MerchantGateDecision:
    """READY / PARTIAL / BLOCKED — PARTIAL only exposes verified data kinds."""

    missing: list[str] = []
    if not signals.merchant_verified:
        missing.append("merchant_unverified")
    if not signals.source_healthy:
        missing.append("source_unhealthy")

    if missing:
        return MerchantGateDecision(
            gate=MerchantActivationGate.BLOCKED,
            reasons=tuple(missing),
            allowed_data_kinds=(),
        )

    allowed: list[str] = ["products"]
    if signals.price_freshness_ok:
        allowed.extend(["prices", "stock"])
    if signals.image_coverage_ok:
        allowed.append("images")
    if signals.bank_agreements_verified and signals.payment_calculations_tested:
        allowed.extend(["finance_options", "payment_plans"])
    if signals.campaign_mapping_verified:
        allowed.append("campaigns")

    ready = (
        signals.product_coverage_ok
        and signals.image_coverage_ok
        and signals.price_freshness_ok
        and signals.bank_agreements_verified
        and signals.campaign_mapping_verified
        and signals.payment_calculations_tested
    )
    if ready and set(allowed) >= set(_FULL_KINDS):
        return MerchantGateDecision(
            gate=MerchantActivationGate.READY,
            reasons=(),
            allowed_data_kinds=_FULL_KINDS,
        )

    partial_missing = []
    if not signals.product_coverage_ok:
        partial_missing.append("product_coverage")
    if not signals.image_coverage_ok:
        partial_missing.append("image_coverage")
    if not signals.price_freshness_ok:
        partial_missing.append("price_freshness")
    if not signals.bank_agreements_verified:
        partial_missing.append("bank_agreements")
    if not signals.campaign_mapping_verified:
        partial_missing.append("campaign_mapping")
    if not signals.payment_calculations_tested:
        partial_missing.append("payment_calculations")

    return MerchantGateDecision(
        gate=MerchantActivationGate.PARTIAL,
        reasons=tuple(partial_missing),
        allowed_data_kinds=tuple(allowed),
    )


__all__ = [
    "MerchantGateDecision",
    "MerchantReadinessSignals",
    "evaluate_merchant_activation",
]
