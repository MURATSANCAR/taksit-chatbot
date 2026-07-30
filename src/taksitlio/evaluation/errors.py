"""Errors for the category-match evaluation package.

These errors keep the evaluator boundary explicit — dataset problems,
fixture wiring, quality gate rejections and privacy violations must
never surface as generic exceptions from downstream callers.
"""

from __future__ import annotations


class EvaluationError(Exception):
    """Base error for the evaluation package."""


class DatasetValidationError(EvaluationError):
    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues: list[str] = list(issues or [])


class FixtureCatalogError(EvaluationError):
    """Raised when the fixture catalog cannot be built or resolved."""


class UnknownFixtureKeyError(EvaluationError):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown fixture key: {key}")
        self.key = key


class PrivacyViolationError(EvaluationError):
    """Raised when the redactor detects raw utterances in a standard report."""


class HoldoutTuningRefused(EvaluationError):
    """Raised when tune-policy is asked to run on the holdout split."""


class BaselineComparisonError(EvaluationError):
    pass


__all__ = [
    "BaselineComparisonError",
    "DatasetValidationError",
    "EvaluationError",
    "FixtureCatalogError",
    "HoldoutTuningRefused",
    "PrivacyViolationError",
    "UnknownFixtureKeyError",
]
