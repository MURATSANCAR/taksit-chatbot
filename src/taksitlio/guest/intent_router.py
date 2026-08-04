"""Guest intent router — FAQ | NEEDS | REFINEMENT | COMPLEX | OOS | SMALLTALK.

Production-grade, deterministic first; no LLM required for routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class GuestIntent(str, Enum):
    SMALLTALK = "SMALLTALK"           # merhaba, teşekkür, tamam
    FAQ = "FAQ"                       # üyelik, taksit nasıl, koşullar
    NEEDS_ANALYSIS = "NEEDS_ANALYSIS" # ürün + bütçe / kategori
    REFINEMENT = "REFINEMENT"         # daha ucuz, uzun vade (after COMPLETED)
    COMPLEX_NEED = "COMPLEX_NEED"     # çok kısıtlı ihtiyaç cümlesi
    OOS = "OOS"                       # stok, şikayet, limit, karşılaştırma detay
    UNKNOWN = "UNKNOWN"


@dataclass
class RouteDecision:
    intent: GuestIntent
    confidence: float
    faq_key: Optional[str] = None
    signals: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


# ---------------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------------

_SMALLTALK = re.compile(
    r"^\s*(merhaba|selam|sa|hi|hello|hey|günaydın|iyi\s*akşamlar|"
    r"teşekkür(?:ler| ederim)?|sağol|tamam|ok|peki|anladım|süper|harika|"
    r"görüşürüz|bye|hoşça\s*kal)\s*[!.]*\s*$",
    re.IGNORECASE,
)

_FAQ_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("membership_how", re.compile(
        r"\b(nasıl\s+üye\s+ol|üye\s+ol(?:mak|ma|urum)|kayıt\s+ol|register|"
        r"üyelik\s+(?:nasıl|ücretsiz|zorunlu)|hesap\s+aç)\b", re.I)),
    ("membership_required", re.compile(
        r"\b(üye\s+olmadan|üyelik\s+şart|üyelik\s+gerekli|login\s+şart|"
        r"üyelik\s+zorunlu\s+mu)\b", re.I)),
    ("installment_how", re.compile(
        r"\b(taksit\s+nasıl|taksitlendirme|taksit\s+işler|aylık\s+ödeme\s+nasıl|"
        r"vade\s+nasıl|kar\s+oranı\s+ne|faiz\s+nasıl|bsmv)\b", re.I)),
    ("campaign_conditions", re.compile(
        r"\b(kampanya\s+koşul|şartlar\s+ne|kimler\s+yararlan(?:abilir)?|yeni\s+müşteri\s+mi|"
        r"koşulları\s+ne|kampanya\s+detay)\b", re.I)),
    ("bank_diff", re.compile(
        r"\b(banka(?:lar)?\s+(?:fark|aras[ıi])|albaraka\s+mı\s+kuveyt|"
        r"hangi\s+banka\s+daha|banka\s+karşılaştır)\b", re.I)),
    ("what_is_taksitlio", re.compile(
        r"\b(taksitlio\s+ne|bu\s+uygulama\s+ne|ne\s+işe\s+yarar|"
        r"nasıl\s+çalışır)\b", re.I)),
    ("fees", re.compile(
        r"\b(ücret|masraf|dosya\s+masrafı|tahsis\s+ücret(?:i)?|bsmv|"
        r"gizli\s+maliyet|ek\s+ücret|gizli\s+masraf)\b", re.I)),
]

_OOS = re.compile(
    r"\b(stok|kargo|iade|şikayet(?:im)?|iptal|hesabım|limitim|"
    r"başvuru\s+durum(?:u|um)|kredi\s+notu(?:m)?|ssk|maaş\s+bordro|"
    r"fatura\s+yükle|belge\s+yükle(?:mek)?|kurye|teslimat|"
    r"garanti\s+süresi|servis)\b",
    re.IGNORECASE,
)

_COMPARE_DETAIL = re.compile(
    r"\b(karşılaştır|vs\.?|versus|hangisi\s+daha\s+(?:iyi|ucuz|hızlı)|"
    r"iphone\s+.*\s+samsung|samsung\s+.*\s+iphone)\b",
    re.IGNORECASE,
)

_NEED_PRODUCT = re.compile(
    r"\b(telefon|cep|iphone|ipad|samsung|xiaomi|bilgisayar|laptop|tablet|"
    r"televizyon|tv|buzdolab[ıi]|çamaş[ıi]r|bulaşık|klima|mobilya|"
    r"yatak|saat|kulaklık|konsol|playstation|xbox|beyaz\s+eşya|"
    r"almak|alıcaz|alacağım|bakıyorum|arıyorum|istiyorum)\b",
    re.IGNORECASE,
)

_BUDGET = re.compile(
    r"(\d+(?:[.,]\d{3})*|\d+)\s*bin(?:\s*(?:tl|lira|₺))?|"
    r"(\d+(?:[.,]\d{3})*|\d+)\s*(?:tl|lira|₺)|bütçe",
    re.IGNORECASE,
)

_COMPLEX_MARKERS = re.compile(
    r"\b(peşinat|peşin\s+düşük|uzun\s+vade|kısa\s+vade|düşük\s+faiz|"
    r"düşük\s+oran|marka|model|karşılaştır|hem\s+.*\s+hem|"
    r"olsun\s+ama|tercihen|mutlaka|şartıyla|en\s+az|en\s+fazla|"
    r"\d+\s*ay\b|taksit\s+sayısı)\b",
    re.IGNORECASE,
)

_REFINEMENT = re.compile(
    r"\b(daha\s+ucuz|daha\s+uygun|daha\s+uzun\s+vade|daha\s+kısa|"
    r"başka\s+banka|diğer\s+banka|daha\s+fazla\s+seçenek|"
    r"bütçe(?:yi)?\s+(?:artır|düşür)|alternatif|"
    r"\d{1,2}\s*ay\s+olsun|albaraka\s+olmasın|kuveyt\s+olmasın)\b",
    re.IGNORECASE,
)


def route_intent(
    utterance: str,
    *,
    phase: str | None = None,
) -> RouteDecision:
    text = (utterance or "").strip()
    if not text:
        return RouteDecision(GuestIntent.SMALLTALK, 0.5, reason="empty")

    if _SMALLTALK.match(text):
        return RouteDecision(GuestIntent.SMALLTALK, 0.95, reason="greeting_or_ack")

    # FAQ before OOS so "üyelik ücretsiz mi" doesn't fall through
    for key, pat in _FAQ_PATTERNS:
        if pat.search(text):
            return RouteDecision(GuestIntent.FAQ, 0.9, faq_key=key, reason=f"faq:{key}")

    if _OOS.search(text):
        return RouteDecision(GuestIntent.OOS, 0.85, reason="oos_operational")

    # Detailed product compare without budget → OOS (needs membership for deep compare)
    if _COMPARE_DETAIL.search(text) and not _BUDGET.search(text):
        return RouteDecision(GuestIntent.OOS, 0.8, reason="compare_without_budget")

    # Refinement only meaningful after COMPLETED/REFINING
    if phase in ("COMPLETED", "REFINING") and _REFINEMENT.search(text):
        return RouteDecision(GuestIntent.REFINEMENT, 0.9, reason="post_rec_refinement")

    has_product = bool(_NEED_PRODUCT.search(text))
    has_budget = bool(_BUDGET.search(text))
    has_complex = bool(_COMPLEX_MARKERS.search(text))

    if has_product and has_budget and has_complex:
        return RouteDecision(
            GuestIntent.COMPLEX_NEED,
            0.85,
            signals={"has_product": True, "has_budget": True, "has_complex": True},
            reason="multi_constraint_need",
        )

    if has_product or has_budget:
        return RouteDecision(
            GuestIntent.NEEDS_ANALYSIS,
            0.8 if (has_product and has_budget) else 0.65,
            signals={"has_product": has_product, "has_budget": has_budget},
            reason="simple_need",
        )

    if has_complex:
        return RouteDecision(
            GuestIntent.COMPLEX_NEED,
            0.6,
            signals={"has_complex": True},
            reason="complex_markers_only",
        )

    return RouteDecision(GuestIntent.UNKNOWN, 0.3, reason="no_pattern")
