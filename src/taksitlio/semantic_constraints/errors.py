"""Errors raised by the semantic constraint validator (ADR-007 §4)."""

from __future__ import annotations

from typing import Optional


class SemanticConstraintError(Exception):
    """Base class for semantic constraint pipeline errors."""

    def __init__(self, message: str, *, reason_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class InvalidConstraintPayload(SemanticConstraintError):
    """Structural validation failed (missing keys, wrong types)."""


class ConstraintRejected(SemanticConstraintError):
    """A structurally valid constraint was rejected by domain rules.

    Examples: category ID leaked into a concept, fixture.* key, empty
    concept, positive == negative, correction where previous==replacement.
    """


__all__ = [
    "ConstraintRejected",
    "InvalidConstraintPayload",
    "SemanticConstraintError",
]
