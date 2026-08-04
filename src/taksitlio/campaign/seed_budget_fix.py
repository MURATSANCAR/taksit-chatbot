"""Kalıcı max_budget / min_budget çıkarım düzeltmesi (seed_from_excel).

Sorun:
  Kuveyt metnindeki "10.000 TL’lik başvuru için örnek ödeme planı"
  ilk TL olarak yakalanıp max_budget=10000 oluyordu; 40k bütçede eleniyordu.

Çözüm:
  1. "kadar / üst limit / maksimum / max / finansman üst" yakınındaki tutarı tercih et
  2. Örnek plan / başvuru bağlamındaki tutarları yok say
  3. Sadece gerçek limit tutarları kaldıysa en büyüğünü al
  4. Hiç gerçek limit yoksa max_budget=None (eligibility unrestricted kabul eder)
  5. min_budget için "1.000 TL -" / "en az" alt sınırını yakala
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_AMOUNT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:\.\d{3})+|\d+)\s*(?:TL|tl|₺)",
    re.IGNORECASE,
)

_LIMIT_WINDOW = re.compile(
    r"(?:üst\s*limit|maksimum|max\.?|kadar|finansman\s*üst|"
    r"azami|en\s*fazla|up\s*to)[^\d]{0,24}"
    r"(?P<num>\d{1,3}(?:\.\d{3})+|\d+)\s*(?:TL|tl|₺)",
    re.IGNORECASE,
)

_EXAMPLE_CTX = re.compile(
    r"(?:örnek|ornek|başvuru\s*için|odeme\s*planı|ödeme\s*planı|"
    r"örnek\s*ödeme|sample|örnek\s*plan)",
    re.IGNORECASE,
)

_MIN_RE = re.compile(
    r"(?P<num>\d{1,3}(?:\.\d{3})+|\d+)\s*(?:TL|tl|₺)\s*[–\-ila]"
    r"|(?:en\s*az|minimum|min\.?|itibaren)[^\d]{0,16}"
    r"(?P<num2>\d{1,3}(?:\.\d{3})+|\d+)\s*(?:TL|tl|₺)",
    re.IGNORECASE,
)


def _to_float(num: str) -> float:
    return float(num.replace(".", "").replace(",", ""))


def infer_budget_bounds(text: str, subtitle: str = "") -> Tuple[Optional[float], Optional[float]]:
    """Return (min_budget, max_budget)."""
    combined = f"{subtitle or ''} {text or ''}".strip()
    if not combined:
        return None, None

    # 1) Explicit limit keywords
    limit_hits: list[float] = []
    for m in _LIMIT_WINDOW.finditer(combined):
        try:
            limit_hits.append(_to_float(m.group("num")))
        except ValueError:
            continue
    if limit_hits:
        max_budget: Optional[float] = max(limit_hits)
    else:
        # 2) All amounts, split real vs example-plan context
        real_hits: list[float] = []
        for m in _AMOUNT_RE.finditer(combined):
            try:
                amount = _to_float(m.group("num"))
            except ValueError:
                continue
            start = max(0, m.start() - 48)
            end = min(len(combined), m.end() + 24)
            window = combined[start:end]
            if _EXAMPLE_CTX.search(window):
                continue  # drop sample payment-plan figures
            real_hits.append(amount)
        max_budget = max(real_hits) if real_hits else None

    # 3) min
    min_budget: Optional[float] = None
    m = _MIN_RE.search(combined)
    if m:
        raw = m.group("num") or m.group("num2")
        if raw:
            try:
                min_budget = _to_float(raw)
            except ValueError:
                pass

    if min_budget is not None and max_budget is not None and min_budget > max_budget:
        min_budget, max_budget = max_budget, min_budget

    return min_budget, max_budget


# ---------------------------------------------------------------------------
# seed_from_excel.py entegrasyonu
# ---------------------------------------------------------------------------
#
# Eski:
#   max_budget = _infer_max_budget(text, subtitle or "")
#   min_budget = 1000.0
#
# Yeni:
#   from taksitlio.campaign.seed_budget_fix import infer_budget_bounds
#   min_b, max_b = infer_budget_bounds(text, subtitle or "")
#   min_budget = min_b if min_b is not None else 1000.0
#   max_budget = max_b   # None = tavan yok → eligibility geçirir
#


if __name__ == "__main__":
    albaraka = (
        "Albaraka Türk %1,99 Kar Oranı Finansman Kampanyası’ndan ilk defa "
        "Albaraka Türk müşterisi olacak kişiler yararlanabilir. Finansman üst limiti "
        "maksimum 150.000 TL, maksimum vade 6 aydır. 1.000 TL - 150.000 TL arası "
        "6 ay vade %1,99 kar oranı uygulanacaktır."
    )
    kuveyt = (
        "*İlgili kampanyadan ilk defa Kuveyt Türk müşterisi olacak kişiler ile "
        "tahsis süreçleri olumlu olan müşterilerimiz yararlanabilecektir. "
        "3 ay vadeli 10.000 TL’lik başvuru için örnek ödeme planı: Aylık kar oranı %2,99"
    )
    a_min, a_max = infer_budget_bounds(
        albaraka, "1.000 TL - 150.000 TL arası 6 ay vade %1,99 kar oranı!"
    )
    k_min, k_max = infer_budget_bounds(
        kuveyt, "Yeni müşterilere özel 6 aya kadar %2,99 kar payı oranı!"
    )
    print(f"Albaraka  min={a_min} max={a_max}")
    print(f"Kuveyt    min={k_min} max={k_max}")
    assert a_max == 150000.0, a_max
    assert k_max is None, k_max
    print("OK")
