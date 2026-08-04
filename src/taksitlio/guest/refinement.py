"""Guest multi-turn refinement + complex-utterance signals + strong fallback.

Production helpers used by GuestEntryHandler after the first COMPLETED turn
and when FAST/match confidence is low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RefinementIntent(str, Enum):
    CHEAPER = "CHEAPER"                 # daha ucuz / düşük taksit / düşük faiz
    LONGER_TENURE = "LONGER_TENURE"     # daha uzun vade / 12 ay / 18 ay
    SHORTER_TENURE = "SHORTER_TENURE"   # daha kısa vade
    OTHER_BANK = "OTHER_BANK"           # başka banka / diğer banka
    MORE_OPTIONS = "MORE_OPTIONS"       # başka seçenek / daha fazla göster
    HIGHER_BUDGET = "HIGHER_BUDGET"     # bütçeyi artır
    LOWER_BUDGET = "LOWER_BUDGET"       # bütçeyi düşür
    UNKNOWN = "UNKNOWN"


@dataclass
class RefinementSignal:
    intent: RefinementIntent
    confidence: float = 0.0
    tenure_hint: Optional[int] = None          # e.g. 12
    budget_delta: Optional[float] = None       # absolute new budget if parsed
    bank_exclude: list[str] = field(default_factory=list)
    raw: str = ""


# ---------------------------------------------------------------------------
# Pattern tables (Turkish, morphology-light)
# ---------------------------------------------------------------------------

_CHEAPER = re.compile(
    r"\b(daha\s+ucuz|daha\s+uygun|düşük\s+(?:faiz|oran|taksit|kar)|"
    r"en\s+ucuz|ucuzlat|indirim|daha\s+az\s+öde)",
    re.IGNORECASE,
)
_LONGER = re.compile(
    r"\b(daha\s+uzun\s+vade|vade\s+uzat|uzun\s+vade|"
    r"(?:12|18|24)\s*ay|bir\s+yıl|12['’]ye|18['’]e)",
    re.IGNORECASE,
)
_SHORTER = re.compile(
    r"\b(daha\s+kısa\s+vade|kısa\s+vade|peşin|3\s*ay|az\s+taksit)",
    re.IGNORECASE,
)
_OTHER_BANK = re.compile(
    r"\b(başka\s+banka|diğer\s+banka|farklı\s+banka|banka\s+değiştir|"
    r"albaraka\s+olmasın|kuveyt\s+olmasın)",
    re.IGNORECASE,
)
_MORE = re.compile(
    r"\b(başka\s+(?:seçenek|kampanya|öneri)|daha\s+fazla|hepsini\s+göster|"
    r"alternatif|başka\s+var\s+mı)",
    re.IGNORECASE,
)
_HIGHER_BUDGET = re.compile(
    r"\b(bütçe(?:yi)?\s+(?:artır|yükselt|çıkar)|daha\s+yüksek\s+bütçe|"
    r"(\d+(?:\.\d{3})*)\s*(?:bin\s*)?(?:tl|lira)?\s*(?:yap|olsun|çıkar))",
    re.IGNORECASE,
)
_LOWER_BUDGET = re.compile(
    r"\b(bütçe(?:yi)?\s+(?:düşür|azalt)|daha\s+düşük\s+bütçe|daha\s+az\s+para)",
    re.IGNORECASE,
)
_TENURE_NUM = re.compile(r"\b(\d{1,2})\s*ay\b", re.IGNORECASE)
_BUDGET_NUM = re.compile(
    r"(\d+(?:[.,]\d{3})*|\d+)\s*(?:bin)?\s*(?:tl|lira|₺)?",
    re.IGNORECASE,
)

# Operational OOS only — peşinat/vade belong to COMPLEX_NEED (universal router).
_COMPLEX_OR_OOS = re.compile(
    r"\b(karşılaştır|vs\.?|versus|hangisi\s+daha|stok|kargo|iade|"
    r"şikayet|iptal|hesabım|limitim|başvuru\s+durumu|ödeme\s+planı\s+hesapla|"
    r"kredi\s+notu|ssk|maaş)\b",
    re.IGNORECASE,
)


def detect_refinement(utterance: str) -> RefinementSignal:
    """Detect follow-up intent from free text after a COMPLETED recommendation."""
    text = (utterance or "").strip()
    if not text:
        return RefinementSignal(intent=RefinementIntent.UNKNOWN, raw=text)

    # Order matters: more specific first
    if _OTHER_BANK.search(text):
        exclude = []
        if re.search(r"albaraka", text, re.I):
            exclude.append("Albaraka")
        if re.search(r"kuveyt", text, re.I):
            exclude.append("Kuveyt")
        return RefinementSignal(
            intent=RefinementIntent.OTHER_BANK,
            confidence=0.85,
            bank_exclude=exclude,
            raw=text,
        )

    if _LONGER.search(text):
        m = _TENURE_NUM.search(text)
        return RefinementSignal(
            intent=RefinementIntent.LONGER_TENURE,
            confidence=0.9,
            tenure_hint=int(m.group(1)) if m else 12,
            raw=text,
        )

    if _SHORTER.search(text):
        m = _TENURE_NUM.search(text)
        return RefinementSignal(
            intent=RefinementIntent.SHORTER_TENURE,
            confidence=0.85,
            tenure_hint=int(m.group(1)) if m else 3,
            raw=text,
        )

    if _CHEAPER.search(text):
        return RefinementSignal(intent=RefinementIntent.CHEAPER, confidence=0.9, raw=text)

    if _MORE.search(text):
        return RefinementSignal(intent=RefinementIntent.MORE_OPTIONS, confidence=0.8, raw=text)

    if _HIGHER_BUDGET.search(text):
        budget = _parse_budget(text)
        return RefinementSignal(
            intent=RefinementIntent.HIGHER_BUDGET,
            confidence=0.8,
            budget_delta=budget,
            raw=text,
        )

    if _LOWER_BUDGET.search(text):
        budget = _parse_budget(text)
        return RefinementSignal(
            intent=RefinementIntent.LOWER_BUDGET,
            confidence=0.8,
            budget_delta=budget,
            raw=text,
        )

    return RefinementSignal(intent=RefinementIntent.UNKNOWN, confidence=0.0, raw=text)


def is_complex_or_oos(utterance: str) -> bool:
    """True when the utterance is clearly beyond guest needs-analysis scope."""
    return bool(_COMPLEX_OR_OOS.search(utterance or ""))


def _parse_budget(text: str) -> Optional[float]:
    m = _BUDGET_NUM.search(text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    if re.search(r"bin", text, re.I) and val < 1000:
        val *= 1000
    return val


def apply_refinement_to_profile(
    need_profile: dict[str, Any],
    signal: RefinementSignal,
    last_recommendation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Return a new need_profile with ranking preferences adjusted for the
    refinement intent. Does not mutate the original.
    """
    profile = {
        "budget": dict((need_profile or {}).get("budget") or {}),
        "intent": (need_profile or {}).get("intent"),
        "category_id": (need_profile or {}).get("category_id"),
        "category_code": (need_profile or {}).get("category_code"),
        "preferences": dict((need_profile or {}).get("preferences") or {}),
    }

    prefs = profile["preferences"]

    if signal.intent == RefinementIntent.CHEAPER:
        prefs["prefer_lower_rate"] = True
        prefs["prefer_lower_monthly"] = True
        prefs["importance"] = {"rate": 0.5, "monthly": 0.3, "tenure": 0.2}

    elif signal.intent == RefinementIntent.LONGER_TENURE:
        prefs["prefer_longer_tenure"] = True
        if signal.tenure_hint:
            prefs["min_tenure"] = signal.tenure_hint
        prefs["importance"] = {"tenure": 0.5, "rate": 0.3, "monthly": 0.2}

    elif signal.intent == RefinementIntent.SHORTER_TENURE:
        prefs["prefer_shorter_tenure"] = True
        if signal.tenure_hint:
            prefs["max_tenure"] = signal.tenure_hint
        prefs["importance"] = {"tenure": 0.45, "rate": 0.35, "monthly": 0.2}

    elif signal.intent == RefinementIntent.OTHER_BANK:
        prefs["exclude_banks"] = list(signal.bank_exclude)
        # Soft signal: diversify
        prefs["prefer_other_bank"] = True

    elif signal.intent == RefinementIntent.MORE_OPTIONS:
        prefs["request_more"] = True

    elif signal.intent in (RefinementIntent.HIGHER_BUDGET, RefinementIntent.LOWER_BUDGET):
        if signal.budget_delta:
            profile["budget"]["value"] = signal.budget_delta
            profile["budget"]["type"] = "APPROXIMATE"
        elif last_recommendation and last_recommendation.get("budget_value"):
            base = float(last_recommendation["budget_value"])
            if signal.intent == RefinementIntent.HIGHER_BUDGET:
                profile["budget"]["value"] = base * 1.25
            else:
                profile["budget"]["value"] = max(5000.0, base * 0.75)
            profile["budget"]["type"] = "APPROXIMATE"

    profile["preferences"] = prefs
    return profile


# ---------------------------------------------------------------------------
# Strong fallback copy
# ---------------------------------------------------------------------------

FALLBACK_OOS = (
    "Bu soruyu loginsiz alanda net cevaplayamıyorum.\n\n"
    "Üye olursan kampanyaları detaylı filtreleyebilir, ödeme planı çıkarabilir "
    "ve başvuru sürecini birlikte yönetebiliriz.\n\n"
    "Şimdilik ihtiyaç ve bütçeni yazarsan sana en uygun 1-2 kampanyayı gösterebilirim "
    "(örnek: \"cep telefonu, 40 bin TL\")."
)

FALLBACK_UNKNOWN_REFINEMENT = (
    "Tam olarak neyi değiştirmek istediğini anlayamadım.\n\n"
    "Şunlardan birini yazabilirsin:\n"
    "• \"daha ucuz olsun\"\n"
    "• \"daha uzun vade\"\n"
    "• \"başka banka\"\n"
    "• \"daha fazla seçenek\"\n\n"
    "Ya da üye ol, tüm filtreleri birlikte açalım."
)

FALLBACK_NO_MATCH_AFTER_REFINEMENT = (
    "Bu tercihe uyan başka aktif kampanya bulamadım.\n\n"
    "Üye olursan anlık kampanya havuzunu ve stoklu ürünleri birlikte tarayabiliriz."
)
