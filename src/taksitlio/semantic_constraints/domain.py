"""Domain models for validated semantic constraints (ADR-007 §4).

These models are the *validated* runtime shape that the matcher can trust.
They live between the FAST extractor output (natural language concepts) and
the ``MatchQuery.semantic_constraints`` dict consumed by the matcher.

Nothing here references category IDs, catalog UUIDs, fixture keys, or
business word lists. Concepts are natural-language phrases only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Optional


class ConstraintProvenance(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    EXPLICIT_NEGATION = "EXPLICIT_NEGATION"
    USER_CORRECTION = "USER_CORRECTION"
    SESSION_CONTEXT = "SESSION_CONTEXT"


@dataclass(frozen=True)
class ConstraintItem:
    """One positive / negative constraint concept."""

    concept: str
    provenance: ConstraintProvenance
    confidence: float = 0.9

    def to_matcher_dict(self) -> dict:
        return {
            "concept": self.concept,
            "provenance": self.provenance.value,
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class CorrectionItem:
    """A user-correction pair (previous_concept → replacement_concept)."""

    previous_concept: str
    replacement_concept: str
    confidence: float = 0.95

    def to_matcher_dict(self) -> dict:
        return {
            "previous_concept": self.previous_concept,
            "replacement_concept": self.replacement_concept,
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class ValidatedSemanticConstraints:
    """Validated, matcher-ready semantic constraints (ADR-007)."""

    positive: tuple[ConstraintItem, ...] = ()
    negative: tuple[ConstraintItem, ...] = ()
    corrections: tuple[CorrectionItem, ...] = ()
    rejected_reasons: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.positive or self.negative or self.corrections)

    def to_matcher_dict(self) -> dict:
        """Shape the ``MatchQuery.semantic_constraints`` dict expects."""

        out: dict = {}
        if self.positive:
            out["positive"] = [c.to_matcher_dict() for c in self.positive]
        if self.negative:
            out["negative"] = [c.to_matcher_dict() for c in self.negative]
        if self.corrections:
            out["corrections"] = [c.to_matcher_dict() for c in self.corrections]
        return out


__all__ = [
    "ConstraintItem",
    "ConstraintProvenance",
    "CorrectionItem",
    "ValidatedSemanticConstraints",
]
