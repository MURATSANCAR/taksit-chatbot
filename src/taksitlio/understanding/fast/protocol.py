"""FAST need-understanding protocol + outcome types (ADR-007)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

from taksitlio.semantic_constraints import ValidatedSemanticConstraints


@dataclass(frozen=True)
class FastExtractionOutcome:
    """Result of a FAST extraction round.

    ``need_profile`` is the raw dict validated against
    ``need_profile.schema.json``. ``constraints`` is the validated
    matcher-ready object produced by SemanticConstraintValidator.
    """

    utterance: str
    need_profile: dict
    constraints: ValidatedSemanticConstraints
    extractor: str
    latency_ms: float = 0.0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class FastNeedUnderstanding(Protocol):
    """Every FAST implementation must expose the same async surface."""

    name: str

    async def extract(
        self,
        utterance: str,
        *,
        locale: str = "tr-TR",
        session_summary: Optional[Mapping[str, Any]] = None,
    ) -> FastExtractionOutcome: ...


__all__ = ["FastExtractionOutcome", "FastNeedUnderstanding"]
