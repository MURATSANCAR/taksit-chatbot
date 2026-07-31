"""Typed errors for answer integrity gates (ADR-012)."""

from __future__ import annotations


class AnswerIntegrityError(Exception):
    """Base for ADR-012 gate failures."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class NoEvidenceError(AnswerIntegrityError):
    def __init__(self, field: str, message: str = "") -> None:
        self.field = field
        super().__init__("NO_EVIDENCE", message or f"no evidence for {field}")


class ClaimValidationFailed(AnswerIntegrityError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__(
            "CLAIM_VALIDATION_FAILED",
            "; ".join(reasons) if reasons else "CLAIM_VALIDATION_FAILED",
        )


class PaymentReconciliationFailed(AnswerIntegrityError):
    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "PAYMENT_PLAN_RECONCILIATION_FAILED",
            detail or "PAYMENT_PLAN_RECONCILIATION_FAILED",
        )


class SourceConflictUnresolved(AnswerIntegrityError):
    def __init__(self, field: str, detail: str = "") -> None:
        self.field = field
        super().__init__("SOURCE_CONFLICT_UNRESOLVED", detail or field)


class RecommendationIntegrityFailed(AnswerIntegrityError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            "RECOMMENDATION_INTEGRITY_FAILED",
            "; ".join(missing) if missing else "RECOMMENDATION_INTEGRITY_FAILED",
        )


__all__ = [
    "AnswerIntegrityError",
    "ClaimValidationFailed",
    "NoEvidenceError",
    "PaymentReconciliationFailed",
    "RecommendationIntegrityFailed",
    "SourceConflictUnresolved",
]
