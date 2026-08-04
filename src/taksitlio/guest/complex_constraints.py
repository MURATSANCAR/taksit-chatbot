"""Extract multi-constraints from complex need utterances (deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ComplexConstraints:
    category_hints: list[str] = field(default_factory=list)
    brand_hints: list[str] = field(default_factory=list)
    budget_value: Optional[float] = None
    budget_type: str = "APPROXIMATE"
    min_tenure: Optional[int] = None
    max_tenure: Optional[int] = None
    prefer_lower_rate: bool = False
    prefer_low_downpayment: bool = False
    prefer_longer_tenure: bool = False
    prefer_shorter_tenure: bool = False
    must_have: list[str] = field(default_factory=list)
    clarify_missing: list[str] = field(default_factory=list)
    raw: str = ""

    def to_preferences(self) -> dict[str, Any]:
        prefs: dict[str, Any] = {}
        if self.prefer_lower_rate:
            prefs["prefer_lower_rate"] = True
        if self.prefer_low_downpayment:
            prefs["prefer_low_downpayment"] = True
        if self.prefer_longer_tenure:
            prefs["prefer_longer_tenure"] = True
        if self.prefer_shorter_tenure:
            prefs["prefer_shorter_tenure"] = True
        if self.min_tenure:
            prefs["min_tenure"] = self.min_tenure
        if self.max_tenure:
            prefs["max_tenure"] = self.max_tenure
        if self.brand_hints:
            prefs["brand_hints"] = list(self.brand_hints)
        return prefs


_BUDGET = re.compile(
    r"(?:bütçe(?:m|si)?\s*(?:yaklaşık|civarı|kadar)?\s*)?"
    r"(\d+(?:[.,]\d{3})*|\d+)\s*(bin)?\s*(?:tl|lira|₺)?",
    re.I,
)
_TENURE = re.compile(r"(\d{1,2})\s*ay", re.I)
_LONGER = re.compile(r"daha\s+uzun\s+vade|uzun\s+vade|vade\s+uzun", re.I)
_SHORTER = re.compile(r"daha\s+kısa\s+vade|kısa\s+vade|peşin(?:e)?\s+yakın", re.I)
_LOW_RATE = re.compile(r"düşük\s+(?:faiz|oran|kar)|ucuz\s+kredi|düşük\s+maliyet", re.I)
_LOW_DOWN = re.compile(r"peşinat\s+düşük|düşük\s+peşinat|peşinatsız|az\s+peşinat", re.I)

_CATEGORY_MAP = [
    (re.compile(r"\b(cep\s*telefon\w*|telefon\w*|iphone|samsung\s*galaxy|xiaomi|redmi)\b", re.I), "cep telefonu"),
    (re.compile(r"\b(bilgisayar|laptop|notebook|macbook)\b", re.I), "bilgisayar"),
    (re.compile(r"\b(tablet|ipad)\b", re.I), "tablet"),
    (re.compile(r"\b(televizyon|tv\b|smart\s*tv)\b", re.I), "televizyon"),
    (re.compile(r"\b(buzdolab\w*|çamaşır\w*|bulaşık\w*|beyaz\s*eşya)\b", re.I), "beyaz eşya"),
    (re.compile(r"\b(klima)\b", re.I), "klima"),
    (re.compile(r"\b(oyun\s*konsol\w*|playstation|xbox|nintendo)\b", re.I), "oyun konsolu"),
]

_BRANDS = re.compile(
    r"\b(apple|iphone|samsung|xiaomi|redmi|oppo|huawei|casper|asus|lenovo|"
    r"hp|dell|lg|arçelik|beko|bosch|siemens|vestel|philips|sony)\b",
    re.I,
)


def extract_complex_constraints(utterance: str) -> ComplexConstraints:
    text = utterance or ""
    c = ComplexConstraints(raw=text)

    for pat, name in _CATEGORY_MAP:
        if pat.search(text):
            c.category_hints.append(name)

    for m in _BRANDS.finditer(text):
        b = m.group(1).lower()
        if b not in c.brand_hints:
            c.brand_hints.append(b)

    # budget: prefer explicit "bütçe" nearby, else largest plausible
    budgets: list[float] = []
    for m in _BUDGET.finditer(text):
        raw = m.group(1).replace(".", "").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if m.group(2) or (val < 1000 and "bin" in text.lower()):
            val *= 1000
        if 500 <= val <= 5_000_000:
            budgets.append(val)
    if budgets:
        # If multiple, take the one most likely the ceiling (max)
        c.budget_value = max(budgets)
        c.budget_type = "APPROXIMATE"

    tenures = [int(m.group(1)) for m in _TENURE.finditer(text)]
    if tenures:
        if _LONGER.search(text) or any(t >= 9 for t in tenures):
            c.min_tenure = max(tenures)
            c.prefer_longer_tenure = True
        elif _SHORTER.search(text) or any(t <= 3 for t in tenures):
            c.max_tenure = min(tenures)
            c.prefer_shorter_tenure = True
        else:
            c.min_tenure = min(tenures)
            c.max_tenure = max(tenures)

    if _LONGER.search(text):
        c.prefer_longer_tenure = True
        c.min_tenure = c.min_tenure or 9
    if _SHORTER.search(text):
        c.prefer_shorter_tenure = True
        c.max_tenure = c.max_tenure or 6
    if _LOW_RATE.search(text):
        c.prefer_lower_rate = True
    if _LOW_DOWN.search(text):
        c.prefer_low_downpayment = True

    # Clarify list
    if not c.category_hints:
        c.clarify_missing.append("category")
    if c.budget_value is None:
        c.clarify_missing.append("budget")

    return c


def build_clarify_message(c: ComplexConstraints) -> str:
    missing = c.clarify_missing
    if missing == ["category", "budget"]:
        return (
            "Anladım, birkaç tercihin var. Hem kategoriyi hem yaklaşık bütçeyi "
            "yazarsan en uygun 1-2 kampanyayı getireyim.\n\n"
            "Örnek: \"Samsung telefon, 40 bin TL, uzun vade\""
        )
    if missing == ["budget"]:
        return (
            f"{c.category_hints[0] if c.category_hints else 'Ürün'} için "
            "yaklaşık bütçeni de yazar mısın? (örnek: 40.000 TL)"
        )
    if missing == ["category"]:
        return (
            "Bütçeni aldım. Hangi ürün kategorisine bakıyorsun? "
            "(cep telefonu, bilgisayar, tablet, beyaz eşya…)"
        )
    # Has both but complex — acknowledge constraints
    parts = []
    if c.budget_value:
        parts.append(f"bütçe ≈ {c.budget_value:,.0f} TL".replace(",", "."))
    if c.category_hints:
        parts.append(c.category_hints[0])
    if c.prefer_longer_tenure:
        parts.append("uzun vade tercihi")
    if c.prefer_lower_rate:
        parts.append("düşük oran tercihi")
    if c.prefer_low_downpayment:
        parts.append("düşük peşinat tercihi")
    if c.brand_hints:
        parts.append("marka: " + ", ".join(c.brand_hints[:3]))
    joined = ", ".join(parts) if parts else "tercihlerin"
    return (
        f"Tercihlerini not ettim ({joined}). "
        "Şimdi bütçene uyan aktif kampanyalara bakıyorum…"
    )
