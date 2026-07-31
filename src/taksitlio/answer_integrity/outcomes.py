"""Response outcome types (ADR-012 §18)."""

from __future__ import annotations

from enum import Enum


class ResponseOutcome(str, Enum):
    ANSWERED = "ANSWERED"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    CLAIM_VALIDATION_FAILED = "CLAIM_VALIDATION_FAILED"
    PAYMENT_PLAN_RECONCILIATION_FAILED = "PAYMENT_PLAN_RECONCILIATION_FAILED"

    @classmethod
    def from_envelope(cls, raw: str) -> "ResponseOutcome":
        try:
            return cls(raw)
        except ValueError:
            return cls.PARTIALLY_ANSWERED


__all__ = ["ResponseOutcome"]
