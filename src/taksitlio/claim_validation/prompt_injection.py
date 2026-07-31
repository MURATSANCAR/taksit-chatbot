"""Prompt injection boundary for untrusted merchant/campaign text (ADR-012 §16)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"önceki\s+talimatlar[ıi]\s+yok\s+say", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"you\s+are\s+now", re.I),
    re.compile(r"en\s+iyi\s+ürün\s+olarak\s+öner", re.I),
    re.compile(r"rank\s+this\s+(product|item)\s+first", re.I),
)


@dataclass(frozen=True)
class UntrustedContent:
    """Quoted untrusted payload — never merged into system instructions."""

    kind: str  # merchant_html | campaign_text | product_description
    text: str
    sanitized: str
    injection_suspected: bool
    boundary_tag: str = "UNTRUSTED_SOURCE_TEXT"


def sanitize_untrusted(text: str, *, max_len: int = 4000) -> str:
    cleaned = text.replace("\x00", " ")
    cleaned = re.sub(r"[\r\t]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned


def detect_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def wrap_untrusted(kind: str, text: str) -> UntrustedContent:
    sanitized = sanitize_untrusted(text)
    suspected = detect_injection(sanitized)
    return UntrustedContent(
        kind=kind,
        text=text,
        sanitized=sanitized,
        injection_suspected=suspected,
    )


def llm_quoted_block(content: UntrustedContent) -> str:
    """Format for LLM user channel — explicit data boundary."""

    return (
        f"<{content.boundary_tag} kind=\"{content.kind}\" "
        f"injection_suspected=\"{str(content.injection_suspected).lower()}\">\n"
        f"{content.sanitized}\n"
        f"</{content.boundary_tag}>"
    )


def ranking_features_from_untrusted(
    content: UntrustedContent,
) -> dict[str, float]:
    """Untrusted text must not alter ranking features. Always empty/neutral."""

    _ = content
    return {}


def assert_injection_does_not_change_ranking(
    base_scores: dict[str, float],
    with_untrusted_scores: dict[str, float],
) -> None:
    if base_scores != with_untrusted_scores:
        raise ValueError("PROMPT_INJECTION_GATE: untrusted text altered ranking")


__all__ = [
    "UntrustedContent",
    "assert_injection_does_not_change_ranking",
    "detect_injection",
    "llm_quoted_block",
    "ranking_features_from_untrusted",
    "sanitize_untrusted",
    "wrap_untrusted",
]
