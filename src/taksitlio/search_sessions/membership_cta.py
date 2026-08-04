"""Guest membership CTA for search-sessions (label/url only — no rates)."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional


DEFAULT_LABEL = "Taksitlio'ya üye ol"
DEFAULT_BODY = (
    "Bu seçeneklerden yararlanmak ve başvuruyu tamamlamak için üye olun."
)


def build_guest_membership_cta(
    *,
    product_count: int,
    membership_url: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return a public CTA dict when recommendations were shown.

    Never includes price, rate, term, or bank claims — conversion copy only.
    """

    if product_count <= 0:
        return None
    url = (membership_url if membership_url is not None else _env_membership_url())
    url = (url or "").strip() or None
    return {
        "enabled": True,
        "label": DEFAULT_LABEL,
        "url": url,
        "reason": "guest_product_recommendations",
        "body": DEFAULT_BODY,
    }


def attach_guest_membership_cta(
    payload: dict[str, Any],
    *,
    membership_url: Optional[str] = None,
) -> dict[str, Any]:
    """Mutate payload with ``cta`` when results contain products."""

    snap = payload.get("results") or payload.get("partial_results") or {}
    products: Any
    if isinstance(snap, Mapping):
        products = snap.get("products") or []
    else:
        products = []
    cta = build_guest_membership_cta(
        product_count=len(products) if isinstance(products, list) else 0,
        membership_url=membership_url,
    )
    if cta is not None:
        payload["cta"] = cta
    return payload


def _env_membership_url() -> Optional[str]:
    return os.environ.get("TAKSITLIO_MEMBERSHIP_CTA_URL") or None


__all__ = [
    "DEFAULT_BODY",
    "DEFAULT_LABEL",
    "attach_guest_membership_cta",
    "build_guest_membership_cta",
]
