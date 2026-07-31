"""Negative constraint lock + provenance priority (ADR-012 §15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence


class ConstraintSource(str, Enum):
    USER_CORRECTION = "USER_CORRECTION"
    USER_EXPLICIT = "USER_EXPLICIT"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    DETERMINISTIC_PARSE = "DETERMINISTIC_PARSE"
    LLM_INFERENCE = "LLM_INFERENCE"
    EXPLICIT_NEGATION = "EXPLICIT_NEGATION"
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    SESSION_CONTEXT = "SESSION_CONTEXT"


# Higher index = higher priority (wins conflicts)
_PRIORITY: tuple[ConstraintSource, ...] = (
    ConstraintSource.LLM_INFERENCE,
    ConstraintSource.INFERRED,
    ConstraintSource.SESSION_CONTEXT,
    ConstraintSource.DETERMINISTIC_PARSE,
    ConstraintSource.CLARIFICATION_ANSWER,
    ConstraintSource.EXPLICIT,
    ConstraintSource.EXPLICIT_NEGATION,
    ConstraintSource.USER_EXPLICIT,
    ConstraintSource.USER_CORRECTION,
)


def source_priority(source: ConstraintSource | str) -> int:
    try:
        src = source if isinstance(source, ConstraintSource) else ConstraintSource(str(source))
    except ValueError:
        return -1
    try:
        return _PRIORITY.index(src)
    except ValueError:
        return -1


@dataclass(frozen=True)
class LockedConstraint:
    concept: str
    polarity: str  # positive | negative
    source: ConstraintSource


@dataclass
class NegativeConstraintLock:
    """Locked negatives that LLM inference cannot override."""

    negatives: list[LockedConstraint] = field(default_factory=list)

    def lock(
        self,
        concept: str,
        *,
        source: ConstraintSource | str,
        polarity: str = "negative",
    ) -> None:
        src = source if isinstance(source, ConstraintSource) else ConstraintSource(str(source))
        concept_n = concept.strip().casefold()
        existing = next((c for c in self.negatives if c.concept == concept_n), None)
        if existing is None:
            self.negatives.append(
                LockedConstraint(concept=concept_n, polarity=polarity, source=src)
            )
            return
        if source_priority(src) >= source_priority(existing.source):
            self.negatives = [
                c
                if c.concept != concept_n
                else LockedConstraint(concept=concept_n, polarity=polarity, source=src)
                for c in self.negatives
            ]

    def is_locked_negative(self, concept: str) -> bool:
        return any(
            c.concept == concept.strip().casefold() and c.polarity == "negative"
            for c in self.negatives
        )

    def reject_llm_reintroduction(
        self,
        *,
        proposed_positive: Sequence[str],
        proposed_source: ConstraintSource = ConstraintSource.LLM_INFERENCE,
    ) -> tuple[str, ...]:
        """Return concepts that LLM tried to reintroduce against locked negatives."""

        if source_priority(proposed_source) >= source_priority(ConstraintSource.USER_EXPLICIT):
            # Only equal-or-higher user sources may clear — LLM never can.
            pass
        blocked: list[str] = []
        for concept in proposed_positive:
            if self.is_locked_negative(concept):
                if source_priority(proposed_source) < source_priority(
                    ConstraintSource.USER_EXPLICIT
                ):
                    blocked.append(concept.strip().casefold())
        return tuple(blocked)


def merge_constraints_with_priority(
    existing: Iterable[LockedConstraint],
    incoming: Iterable[LockedConstraint],
) -> tuple[LockedConstraint, ...]:
    by_key: dict[tuple[str, str], LockedConstraint] = {
        (c.concept, c.polarity): c for c in existing
    }
    for c in incoming:
        key = (c.concept, c.polarity)
        prev = by_key.get(key)
        if prev is None or source_priority(c.source) >= source_priority(prev.source):
            by_key[key] = c
    return tuple(by_key.values())


__all__ = [
    "ConstraintSource",
    "LockedConstraint",
    "NegativeConstraintLock",
    "merge_constraints_with_priority",
    "source_priority",
]
