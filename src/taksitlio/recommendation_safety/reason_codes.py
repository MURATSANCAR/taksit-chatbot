"""Recommendation reason codes and explanations (ADR-012 §14)."""

from __future__ import annotations

from typing import Mapping, Sequence

REASON_CODE_TEMPLATES: Mapping[str, str] = {
    "REQUIRED_ATTRIBUTES_MATCHED": "zorunlu özellik koşullarını karşılıyor",
    "WITHIN_BUDGET": "bütçenizin altında",
    "LOWEST_TOTAL_REPAYMENT": "12 ay seçeneğinde toplam geri ödemesi diğer doğrulanmış adaylardan daha düşük",
    "LOWEST_MONTHLY_PAYMENT": "aylık ödemesi diğer doğrulanmış adaylardan daha düşük",
    "LOWEST_PRODUCT_PRICE": "satış fiyatı diğer doğrulanmış adaylardan daha düşük",
    "STOCK_VERIFIED": "stok bilgisi güncel ve doğrulanmış",
    "FRESH_PRICE": "fiyat bilgisi güncel",
    "FINANCE_MAPPING_VERIFIED": "banka / kampanya eşlemesi doğrulanmış",
    "CAMPAIGN_ACTIVE": "kampanya aktif",
    "VARIANT_COMPARABLE": "ürün varyantları karşılaştırılabilir",
}


def explain_reason_codes(reason_codes: Sequence[str]) -> str:
    parts = [
        REASON_CODE_TEMPLATES[c]
        for c in reason_codes
        if c in REASON_CODE_TEMPLATES
    ]
    if not parts:
        return "Bu ürünü doğrulanmış kriterlere göre sıraladım."
    if len(parts) == 1:
        body = parts[0]
    else:
        body = ", ".join(parts[:-1]) + " ve " + parts[-1]
    return f"Bu ürünü ilk sıraya aldım çünkü {body}."


__all__ = ["REASON_CODE_TEMPLATES", "explain_reason_codes"]
