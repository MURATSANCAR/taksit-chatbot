"""Product identity / variant gate (ADR-012 §11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from taksitlio.product.canonical import resolve_canonical_key


@dataclass(frozen=True)
class VariantIdentity:
    offer_id: str
    gtin: Optional[str] = None
    ean: Optional[str] = None
    mpn: Optional[str] = None
    brand_name: Optional[str] = None
    model_number: Optional[str] = None
    variant_attributes: Mapping[str, str] | None = None


@dataclass(frozen=True)
class IdentityMatchResult:
    ok: bool
    reason: str
    canonical_code: Optional[str] = None


VARIANT_KEYS = ("ram_gb", "storage_gb", "color", "model_year", "capacity")


def variants_compatible(a: VariantIdentity, b: VariantIdentity) -> IdentityMatchResult:
    """Refuse silent merge when critical variant attributes diverge."""

    attrs_a = dict(a.variant_attributes or {})
    attrs_b = dict(b.variant_attributes or {})
    for key in VARIANT_KEYS:
        va = attrs_a.get(key)
        vb = attrs_b.get(key)
        if va is not None and vb is not None and str(va).casefold() != str(vb).casefold():
            return IdentityMatchResult(
                ok=False,
                reason=f"variant_mismatch:{key}",
            )

    key = resolve_canonical_key(
        gtin=a.gtin or b.gtin,
        ean=a.ean or b.ean,
        mpn=a.mpn or b.mpn,
        brand_name=a.brand_name or b.brand_name,
        model_number=a.model_number or b.model_number,
    )
    if key is None:
        # Name-only merge forbidden
        return IdentityMatchResult(ok=False, reason="weak_identity_no_merge")
    return IdentityMatchResult(ok=True, reason="canonical_match", canonical_code=key.code)


def assert_finance_bound_to_offer(*, campaign_bound_to_offer: bool) -> None:
    """Campaign/price must bind to exact offer/variant, not only canonical product."""

    if not campaign_bound_to_offer:
        raise ValueError("PRODUCT_IDENTITY_GATE: finance must bind to exact offer/variant")


__all__ = [
    "IdentityMatchResult",
    "VARIANT_KEYS",
    "VariantIdentity",
    "assert_finance_bound_to_offer",
    "variants_compatible",
]
