"""Merchant domain stubs for ADR-010 (resolution-ready, no static maps)."""

from taksitlio.merchant.activation import (
    MerchantGateDecision,
    MerchantReadinessSignals,
    evaluate_merchant_activation,
)
from taksitlio.merchant.models import MerchantActivationGate, MerchantRecord

__all__ = [
    "MerchantActivationGate",
    "MerchantGateDecision",
    "MerchantReadinessSignals",
    "MerchantRecord",
    "evaluate_merchant_activation",
]

