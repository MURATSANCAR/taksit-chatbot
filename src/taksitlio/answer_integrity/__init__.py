"""Answer integrity, claim grounding, recommendation safety (ADR-012).

Package init is intentionally tolerant while P0 modules land — import
submodules directly when possible.
"""

from __future__ import annotations

from typing import Any

__all__: list[str] = []


def __getattr__(name: str) -> Any:  # pragma: no cover - lazy re-exports
    if name in {
        "CLAIM_VALIDATION_FAILED",
        "ClaimValidationResult",
        "ClaimValidator",
        "validate_claims",
    }:
        from taksitlio.answer_integrity import claim_validator as mod

        return getattr(mod, name)
    if name in {
        "Fact",
        "FactEnvelope",
        "FactType",
        "GroundedFact",
        "build_fact_envelope",
        "validate_provenance",
    }:
        from taksitlio.answer_integrity import facts as mod

        return getattr(mod, name)
    if name in {"FieldConfidence", "FieldDecision", "decide_all", "decide_field"}:
        from taksitlio.answer_integrity import confidence as mod

        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
