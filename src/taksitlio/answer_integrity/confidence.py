"""Field-level confidence policy (ADR-012 §2).

Overall confidence is forbidden for auto-select. Decisions are per field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Union


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
    overrides: Mapping[str, tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.overrides is None:
            object.__setattr__(self, "overrides", {})

    def thresholds_for(self, field: str) -> tuple[float, float]:
        override = self.overrides.get(field)
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


@dataclass(frozen=True)
class FieldDecisionResult:
    """Per-field decision returned by decide_field (ADR-012 test API)."""

    field_name: str
    score: Optional[float]
    action: str  # USE | CLARIFY | REJECT
    accepted: bool


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
    field_name: Union[str, ConfidenceField, FieldConfidence],
    score: Union[float, None, ConfidenceField] = None,
    *,
    policy: FieldConfidencePolicy | None = None,
) -> Union[FieldDecisionResult, FieldDecision]:
    """Decide a single field.

    Test / ADR API:
        decide_field(field_name: str, score: float|None) → FieldDecisionResult

    Legacy:
        decide_field(confidence: FieldConfidence, field: ConfidenceField) → FieldDecision
    """

    pol = policy or FieldConfidencePolicy()

    # Legacy: decide_field(confidence, field)
    if isinstance(field_name, FieldConfidence) and isinstance(score, ConfidenceField):
        if field_name.has_overall_confidence():
            raise ValueError("overall_confidence is forbidden for auto-select (ADR-012)")
        s = field_name.get(score)
        use_t, clarify_t = pol.thresholds_for(score.value)
        if s is None:
            return FieldDecision.CLARIFY
        if s >= use_t:
            return FieldDecision.USE
        if s >= clarify_t:
            return FieldDecision.CLARIFY
        return FieldDecision.REJECT

    name = (
        field_name.value if isinstance(field_name, ConfidenceField) else str(field_name)
    )
    s = float(score) if score is not None else None
    use_t, clarify_t = pol.thresholds_for(name)
    if s is None:
        action = FieldDecision.CLARIFY.value
    elif s >= use_t:
        action = FieldDecision.USE.value
    elif s >= clarify_t:
        action = FieldDecision.CLARIFY.value
    else:
        action = FieldDecision.REJECT.value
    return FieldDecisionResult(
        field_name=name,
        score=s,
        action=action,
        accepted=action == FieldDecision.USE.value,
    )


def decide_all(
    confidence: FieldConfidence,
    fields: tuple[ConfidenceField, ...] | None = None,
    *,
    policy: FieldConfidencePolicy | None = None,
) -> dict[str, FieldDecision]:
    targets = fields or tuple(ConfidenceField)
    out: dict[str, FieldDecision] = {}
    for f in targets:
        result = decide_field(confidence, f, policy=policy)
        assert isinstance(result, FieldDecision)
        out[f.value] = result
    return out


__all__ = [
    "ConfidenceField",
    "FieldConfidence",
    "FieldConfidencePolicy",
    "FieldDecision",
    "FieldDecisionResult",
    "decide_all",
    "decide_field",
    "reject_overall_confidence",
]
