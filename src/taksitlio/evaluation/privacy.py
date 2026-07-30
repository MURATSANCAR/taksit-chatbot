"""Privacy helpers for evaluation output.

Standard reports never contain raw utterances (ADR-005 §9). Debug logs
are opt-in and always emitted under ``evaluation/private/`` — the
directory is gitignored.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from taksitlio.evaluation.errors import PrivacyViolationError


PRIVATE_DIR = (
    Path(__file__).resolve().parents[3] / "evaluation" / "private"
)

REPORTS_DIR = (
    Path(__file__).resolve().parents[3] / "evaluation" / "reports"
)


_UTTERANCE_KEYS = {"utterance", "raw_text", "message"}


def utterance_hash(text: str) -> str:
    """Deterministic hash of a normalized utterance for correlation only."""
    normalized = " ".join((text or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def redact_report(payload: Mapping[str, Any]) -> dict:
    """Return a copy of ``payload`` guaranteed to be free of raw utterances."""
    return _scrub(payload, path="")


def _scrub(obj: Any, *, path: str) -> Any:
    if isinstance(obj, Mapping):
        redacted: dict = {}
        for key, value in obj.items():
            if key in _UTTERANCE_KEYS:
                continue
            redacted[key] = _scrub(value, path=f"{path}/{key}")
        return redacted
    if isinstance(obj, (list, tuple)):
        return [_scrub(x, path=path) for x in obj]
    return obj


def assert_report_is_safe(payload: Mapping[str, Any]) -> None:
    """Raise if the report contains any raw utterance field."""
    violations: list[str] = []
    _walk(payload, "", violations)
    if violations:
        raise PrivacyViolationError(
            f"raw utterance detected in report: {violations[:3]}"
        )


def _walk(obj: Any, path: str, violations: list[str]) -> None:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            child_path = f"{path}/{k}"
            if k in _UTTERANCE_KEYS:
                violations.append(child_path)
            _walk(v, child_path, violations)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", violations)


__all__ = [
    "PRIVATE_DIR",
    "REPORTS_DIR",
    "assert_report_is_safe",
    "redact_report",
    "utterance_hash",
]
