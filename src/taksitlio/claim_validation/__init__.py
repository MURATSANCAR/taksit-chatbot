"""Claim validation + prompt injection boundary (ADR-012)."""

from __future__ import annotations

from taksitlio.claim_validation.claim_validator import (
    ClaimValidationResult,
    assert_claims,
    normalize_money_token,
    validate_claims,
    validate_institution_mentions,
)
from taksitlio.claim_validation.prompt_injection import (
    UntrustedContent,
    assert_injection_does_not_change_ranking,
    detect_injection,
    llm_quoted_block,
    ranking_features_from_untrusted,
    sanitize_untrusted,
    wrap_untrusted,
)

ADR_SCOPE = "ADR-012"
PACKAGE_STATUS = "P0_PROD"

__all__ = [
    "ADR_SCOPE",
    "PACKAGE_STATUS",
    "ClaimValidationResult",
    "UntrustedContent",
    "assert_claims",
    "assert_injection_does_not_change_ranking",
    "detect_injection",
    "llm_quoted_block",
    "normalize_money_token",
    "ranking_features_from_untrusted",
    "sanitize_untrusted",
    "validate_claims",
    "validate_institution_mentions",
    "wrap_untrusted",
]
