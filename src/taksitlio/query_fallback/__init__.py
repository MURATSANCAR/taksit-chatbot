"""Deterministic timeout / degraded fallback (ADR-011 §17–§18)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from taksitlio.search_sessions.repository import SearchTimeoutPolicy


@dataclass(frozen=True)
class FallbackDecision:
    action: str  # CONTINUE | SHOW_PARTIAL_PROMPT | COMPLETE_DEGRADED | HARD_TIMEOUT
    message: str
    complete_status: Optional[str] = None


def evaluate_deadlines(
    *,
    elapsed_ms: float,
    policy: SearchTimeoutPolicy,
    has_partial_results: bool,
    llm_still_running: bool,
) -> FallbackDecision:
    if elapsed_ms >= policy.hard_timeout_ms:
        return FallbackDecision(
            action="HARD_TIMEOUT",
            message=(
                "Elimdeki kesin kriterlere göre ürünleri sıraladım. "
                "Dilerseniz bir tercih daha ekleyerek sonuçları daraltabilirsiniz."
            ),
            complete_status="COMPLETED_DEGRADED",
        )
    if elapsed_ms >= policy.ux_fallback_deadline_ms and llm_still_running:
        return FallbackDecision(
            action="SHOW_PARTIAL_PROMPT",
            message="Mevcut sonuçları gösterebilir veya aramayı netleştirebilirsiniz.",
        )
    if elapsed_ms >= policy.partial_result_deadline_ms and has_partial_results:
        return FallbackDecision(
            action="CONTINUE",
            message="İlk uygun ürünleri buldum. Sonuçları tercihlerinize göre daraltıyorum.",
        )
    if not llm_still_running and has_partial_results:
        return FallbackDecision(
            action="CONTINUE",
            message="Sonuçlar hazırlanıyor...",
        )
    return FallbackDecision(action="CONTINUE", message="İsteğini çözümlüyorum...")


def degrade_with_deterministic(
    *,
    constraints: dict[str, Any],
    partial_products: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "COMPLETED_DEGRADED",
        "message": (
            "Elimdeki kesin kriterlere göre en yakın sonuçları hazırladım. "
            "Bir tercih daha ekleyerek sonuçları daraltabilirsiniz."
        ),
        "constraints": constraints,
        "products": partial_products,
        "degraded": True,
    }
