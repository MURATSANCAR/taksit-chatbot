"""Router decision / policy typed structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RouteDecision(str, Enum):
    CONTINUE = "CONTINUE"
    CLARIFY = "CLARIFY"
    FALLBACK = "FALLBACK"
    SAFE_FAILURE = "SAFE_FAILURE"


class ReasonCode(str, Enum):
    LOW_SYSTEM_CONFIDENCE = "LOW_SYSTEM_CONFIDENCE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    COMPREHENSION_FAILURE = "COMPREHENSION_FAILURE"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    MISSING_PRODUCT_FORM = "MISSING_PRODUCT_FORM"
    MULTIPLE_INDEPENDENT_NEEDS = "MULTIPLE_INDEPENDENT_NEEDS"
    BUDGET_AMBIGUITY = "BUDGET_AMBIGUITY"
    SESSION_CONFLICT = "SESSION_CONFLICT"
    PRIMARY_UNAVAILABLE = "PRIMARY_UNAVAILABLE"
    DEADLINE_EXHAUSTED = "DEADLINE_EXHAUSTED"
    FALLBACK_FAILED = "FALLBACK_FAILED"
    OK = "OK"


@dataclass(frozen=True)
class ConfidencePolicy:
    policy_code: str
    minimum_system_confidence: float = 0.78
    maximum_category_score_gap_for_clarification: float = 0.08
    fallback_on_invalid_schema: bool = True
    fallback_on_low_confidence: bool = True
    prefer_clarification_when_ambiguous: bool = True
    clarify_on_session_conflict: bool = True
    clarify_on_multiple_needs: bool = True
    # legacy aliases kept for older call sites
    minimum_confidence: float | None = None
    fallback_on_conflict: bool = False
    fallback_on_multiple_needs: bool = False
    fallback_on_budget_confusion: bool = False

    @property
    def min_confidence(self) -> float:
        if self.minimum_confidence is not None:
            return float(self.minimum_confidence)
        return float(self.minimum_system_confidence)


@dataclass(frozen=True)
class TimeoutPolicy:
    policy_code: str
    primary_timeout_ms: int = 3000
    fallback_timeout_ms: int = 8000
    total_budget_ms: int = 10000
    min_fallback_remaining_ms: int = 500
    retry_same_model: bool = False


@dataclass(frozen=True)
class UnderstandingRequest:
    message: str
    session_summary: dict[str, Any] | None = None
    system_prompt: str = ""
    json_schema: dict[str, Any] | None = None
    route_context: dict[str, Any] | None = None
    correlation_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class UnderstandingResult:
    decision: RouteDecision
    reason_code: ReasonCode
    need_profile: dict[str, Any] | None
    used_deployment_code: str | None
    used_profile_code: str | None
    latency_ms: float
    system_confidence: float | None = None
    model_reported_confidence: float | None = None
    clarification_question_intent: str | None = None
    missing_concepts: tuple[str, ...] = ()
    fallback_used: bool = False
    raw_content: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
