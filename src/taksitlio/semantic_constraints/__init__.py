"""Semantic constraint validator (ADR-007 §4).

Ensures that whatever the FAST extractor / oracle annotation produces
becomes a safe, matcher-ready ``ValidatedSemanticConstraints`` structure
before reaching ``SemanticCategoryMatcher``.
"""

from taksitlio.semantic_constraints.domain import (
    ConstraintItem,
    ConstraintProvenance,
    CorrectionItem,
    ValidatedSemanticConstraints,
)
from taksitlio.semantic_constraints.errors import (
    ConstraintRejected,
    InvalidConstraintPayload,
    SemanticConstraintError,
)
from taksitlio.semantic_constraints.validator import (
    SemanticConstraintValidator,
    SemanticConstraintValidatorConfig,
)

__all__ = [
    "ConstraintItem",
    "ConstraintProvenance",
    "ConstraintRejected",
    "CorrectionItem",
    "InvalidConstraintPayload",
    "SemanticConstraintError",
    "SemanticConstraintValidator",
    "SemanticConstraintValidatorConfig",
    "ValidatedSemanticConstraints",
]
