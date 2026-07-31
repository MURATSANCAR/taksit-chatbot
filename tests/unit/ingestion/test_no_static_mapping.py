"""Guard: production code must not hardcode merchant/bank typo maps (ADR-010 §32)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "taksitlio"

# Typo-style tokens that must never appear as static production mappings.
FORBIDDEN_TYPO_TOKENS = (
    "teknoksa",
    "teknossa",
    "fibabnka",
    "medya markt",
    "samsng",
    "laptob",
)

# Patterns that look like hardcoded query→entity maps.
_STATIC_MAP_PATTERNS = (
    re.compile(
        r"""(?i)if\s+.*(?:query|utterance|token|text)\s*==\s*['"]teknoksa['"]"""
    ),
    re.compile(
        r"""(?i)['"]teknoksa['"]\s*:\s*['"]Teknosa['"]"""
    ),
    re.compile(
        r"""(?i)['"]fibabnka['"]\s*:\s*['"]Fibabanka['"]"""
    ),
    re.compile(
        r"""(?i)['"]kuveyt\s*turk['"]\s*:\s*['"]Kuveyt"""
    ),
    re.compile(
        r"""(?i)STATIC_(?:MERCHANT|BANK|TYPO)_MAP"""
    ),
)


def _iter_python_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if p.is_file())


def test_no_forbidden_typo_tokens_in_production_src() -> None:
    violations: list[str] = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for token in FORBIDDEN_TYPO_TOKENS:
            if token in lower:
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}: contains forbidden typo token {token!r}")
    assert not violations, "Static typo tokens found:\n" + "\n".join(violations)


def test_no_static_entity_map_patterns_in_production_src() -> None:
    violations: list[str] = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for pattern in _STATIC_MAP_PATTERNS:
            if pattern.search(text):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}: matches {pattern.pattern}")
    assert not violations, "Static mapping patterns found:\n" + "\n".join(violations)
