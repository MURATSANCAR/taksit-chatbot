"""
PATCH NOTES — product-before-budget bug fix
==========================================

Symptom
-------
"bana telefon lazım" → MediaMarkt ürün kartları (Xiaomi/iPhone) dönüyor.
Beklenen (conversion / guest funnel): bütçe clarify VEYA kampanya; ürün vitrini değil.

Root cause (pipeline/orchestrator.py)
-------------------------------------
1) Erken ADR-011 bloğu: ``_looks_like_product_query`` ("telefon" cue) true olunca
   ``bridge_search_start`` hemen product cards döndürüyor — bütçe / kampanya hiç
   çalışmıyor.

2) ``_try_product_path``: need_profile'da bütçe olmasa bile katalog araması
   çalışıyor; kampanya path'ine düşülmüyor.

Fix policy (conversion-first)
-----------------------------
* Bütçe sinyali YOKSA → product search short-circuit YAPMA.
* need_profile'da budget yoksa → product path yerine budget CLARIFY.
* Explicit ``product_phase=FIRST_CARDS`` client'tan gelirse ürün browse serbest
  (bilinçli progressive catalog).
* Guest branch (user_id is None) zaten UniversalGuestHandler kullanmalı;
  pipeline'a düşerse aynı kural geçerli.

Apply
-----
Aşağıdaki helper'ları ChatPipeline sınıfına ekle / mevcut metotları değiştir.
Tam patch satırları için ``apply_budget_gate_to_orchestrator.py`` veya
manuel entegrasyon README'sine bak.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional


_BUDGET_CUE = re.compile(
    r"(\d+(?:[.,]\d{3})*|\d+)\s*(?:bin)?\s*(?:tl|lira|₺)"
    r"|(?:\bbütçe|\bbutce|\bbütçem|\bbutcem\b)",
    re.IGNORECASE,
)


def has_budget_cue(message: str) -> bool:
    """True if utterance contains an explicit budget signal."""
    return bool(_BUDGET_CUE.search(message or ""))


def need_profile_has_budget(need_profile: Mapping[str, Any] | None) -> bool:
    if not need_profile:
        return False
    budget = need_profile.get("budget") or {}
    if not isinstance(budget, Mapping):
        return False
    for key in ("value", "maximum", "monthly_payment"):
        raw = budget.get(key)
        if raw is None:
            continue
        try:
            if float(raw) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def should_run_early_product_search(
    *,
    message: str,
    product_phase: Optional[str],
    prefer_search_sessions: bool,
    looks_like_product: bool,
) -> bool:
    """
    Gate for ADR-011 early bridge_search_start.

    Allow early product search only when:
      - explicit product_phase from client, OR
      - product-like query AND budget cue present
    """
    if not prefer_search_sessions or not looks_like_product:
        return False
    phase = (product_phase or "").upper()
    if phase in ("FIRST_CARDS", "FINANCE_ENRICHED"):
        return True
    # No explicit phase → require budget for product short-circuit
    return has_budget_cue(message)


def should_try_product_path(
    *,
    need_profile: Mapping[str, Any] | None,
    product_phase: Optional[str],
    product_path_enabled: bool,
) -> bool:
    """
    Gate for _try_product_path after understanding.

    Without budget, skip product catalog and let caller clarify or use campaigns.
    Exception: explicit FIRST_CARDS phase (client wants browse).
    """
    if not product_path_enabled:
        return False
    phase = (product_phase or "").upper()
    if phase == "FIRST_CARDS":
        return True
    return need_profile_has_budget(need_profile)


# ---------------------------------------------------------------------------
# Drop-in snippets for ChatPipeline.handle
# ---------------------------------------------------------------------------
#
# REPLACE early block:
#
#   if (
#       request.prefer_search_sessions
#       and self._search_orchestrator is not None
#       and not request.product_phase
#       and self._looks_like_product_query(request.message)
#   ):
#
# WITH:
#
#   if (
#       self._search_orchestrator is not None
#       and should_run_early_product_search(
#           message=request.message,
#           product_phase=request.product_phase,
#           prefer_search_sessions=request.prefer_search_sessions,
#           looks_like_product=self._looks_like_product_query(request.message),
#       )
#   ):
#
# REPLACE:
#
#   product_hit = await self._try_product_path(request, need_profile)
#
# WITH:
#
#   if should_try_product_path(
#       need_profile=need_profile,
#       product_phase=request.product_phase,
#       product_path_enabled=bool(
#           self._product_path and getattr(self._product_path, "enabled", True)
#       ),
#   ):
#       product_hit = await self._try_product_path(request, need_profile)
#   else:
#       product_hit = None
#       # Budget missing → conversion-first clarify (skip product dump)
#       if need_profile and not need_profile_has_budget(need_profile):
#           reply = await self._responder.clarify("budget")
#           # Optional: softer copy via custom template if clarify("budget") exists
#           return self._build(request, turn, reply, match_result.matches, [], started,
#                              phase="CLARIFY",
#                              extra={"reason": "budget_required_before_products"})
#
