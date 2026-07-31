"""Errors raised by the FAST need-understanding layer (ADR-007)."""

from __future__ import annotations

from typing import Optional


class FastExtractionError(Exception):
    """Base class for FAST extraction failures."""

    def __init__(self, message: str, *, reason_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class FastDeploymentUnavailable(FastExtractionError):
    """Real FAST deployment is not reachable — no silent fallback.

    ADR-007 §8: we never silently substitute a lexical or rule-based
    extractor for the production model — the caller must handle this
    error and either surface BLOCKED_DEPENDENCY or run a documented
    development-only extractor.
    """

    def __init__(self, message: str = "FAST deployment unavailable") -> None:
        super().__init__(message, reason_code="FAST_DEPLOYMENT_UNAVAILABLE")


class NeedProfileSchemaError(FastExtractionError):
    """The FAST payload did not validate against the NeedProfile schema."""

    def __init__(self, message: str, *, issues: Optional[list[str]] = None) -> None:
        super().__init__(message, reason_code="NEED_PROFILE_SCHEMA_ERROR")
        self.issues = issues or []


class TruncatedNeedProfileError(FastExtractionError):
    """Model hit max_tokens before emitting a complete NeedProfile JSON object."""

    def __init__(
        self,
        message: str = "FAST output truncated at max_tokens",
        *,
        issues: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message, reason_code="TRUNCATED_OUTPUT")
        self.issues = issues or []


__all__ = [
    "FastDeploymentUnavailable",
    "FastExtractionError",
    "NeedProfileSchemaError",
    "TruncatedNeedProfileError",
]
