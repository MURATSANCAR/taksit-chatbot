"""Field-level confidence policy (ADR-012 §2).

Overall confidence is forbidden for auto-select. Decisions are per field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class ConfidenceField(str, Enum):
    INTENT = "intent"
    MERCHANT = "merchant"
    CATEGORY = "category"
    BRAND = "brand"
    INSTITUTION = "institution"
    BUDGET = "budget"
    TERM = "term"
    PRODUCT = "product"


class FieldDecision(str, Enum):
    USE = "USE"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"


@dataclass(frozen=True)
class FieldConfidencePolicy:
    """Per-field thresholds — never collapse into overall_confidence."""

    use_threshold: float = 0.85
    clarify_threshold: float = 0.55
    # Optional overrides: field → (use, clarify)
    overrides: Mapping[str, tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.overrides is None:
            object.__setattr__(self, "overrides", {})

    def thresholds_for(self, field: ConfidenceField) -> tuple[float, float]:
        override = self.overrides.get(field.value)
        if override:
            return float(override[0]), float(override[1])
        return self.use_threshold, self.clarify_threshold


@dataclass(frozen=True)
class FieldConfidence:
    scores: Mapping[str, float]

    def get(self, field: ConfidenceField | str) -> Optional[float]:
        key = field.value if isinstance(field, ConfidenceField) else field
        if key not in self.scores:
            return None
        return float(self.scores[key])

    def has_overall_confidence(self) -> bool:
        return "overall_confidence" in self.scores or "overall" in self.scores


def reject_overall_confidence(payload: Mapping[str, object]) -> None:
    """Raise ValueError if overall_confidence is used for auto-select."""

    if "overall_confidence" in payload:
        raise ValueError("overall_confidence is forbidden for auto-select (ADR-012)")
    conf = payload.get("confidence")
    if isinstance(conf, Mapping) and (
        "overall_confidence" in conf or "overall" in conf
    ):
        raise ValueError("overall_confidence is forbidden for auto-select (ADR-012)")


def decide_field(
    confidence: FieldConfidence,
    field: ConfidenceField,
    *,
    policy: FieldConfidencePolicy | None = None,
) -> FieldDecision:
    pol = policy or FieldConfidencePolicy()
    if confidence.has_overall_confidence():
        raise ValueError("overall_confidence is forbidden for auto-select (ADR-012)")
    score = confidence.get(field)
    if score is None:
        return FieldDecision.CLARIFY
    use_t, clarify_t = pol.thresholds_for(field)
    if score >= use_t:
        return FieldDecision.USE
    if score >= clarify_t:
        return FieldDecision.CLARIFY
    return FieldDecision.REJECT


def decide_all(
    confidence: FieldConfidence,
    fields: tuple[ConfidenceField, ...] | None = None,
    *,
    policy: FieldConfidencePolicy | None = None,
) -> dict[str, FieldDecision]:
    targets = fields or tuple(ConfidenceField)
    return {
        f.value: decide_field(confidence, f, policy=policy) for f in targets
    }


__all__ = [
    "ConfidenceField",
    "FieldConfidence",
    "FieldConfidencePolicy",
    "FieldDecision",
    "decide_all",
    "decide_field",
    "reject_overall_confidence",
]
