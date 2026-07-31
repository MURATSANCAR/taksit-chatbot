"""Canonical product key resolution (ADR-010 §36).

Low-confidence merges are refused — callers must not force a join.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from taksitlio.product.normalize import normalize_display_name


@dataclass(frozen=True)
class CanonicalKey:
    code: str
    method: str  # GTIN | EAN | MPN | BRAND_MODEL | SIGNATURE
    confidence: float


def resolve_canonical_key(
    *,
    gtin: Optional[str] = None,
    ean: Optional[str] = None,
    mpn: Optional[str] = None,
    brand_name: Optional[str] = None,
    model_number: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[CanonicalKey]:
    """Return a canonical key only when identity is strong enough to merge."""

    g = (gtin or "").strip()
    if len(g) >= 8:
        return CanonicalKey(code=f"gtin:{g}", method="GTIN", confidence=0.99)

    e = (ean or "").strip()
    if len(e) >= 8:
        return CanonicalKey(code=f"ean:{e}", method="EAN", confidence=0.98)

    m = (mpn or "").strip()
    b = normalize_display_name(brand_name or "")
    if m and b:
        return CanonicalKey(code=f"mpn:{b}:{normalize_display_name(m)}", method="MPN", confidence=0.92)

    model = normalize_display_name(model_number or "")
    if b and model:
        return CanonicalKey(code=f"bm:{b}:{model}", method="BRAND_MODEL", confidence=0.88)

    # Display-name-only is too weak for auto-merge.
    _ = display_name
    return None


__all__ = ["CanonicalKey", "resolve_canonical_key"]
