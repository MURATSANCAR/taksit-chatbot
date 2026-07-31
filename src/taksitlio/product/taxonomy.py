"""Dynamic brand/category codes from merchant feed labels (ADR-010).

No static merchant/brand typo maps — codes and matches are derived from
normalized source text and the live categories/brands tables.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from taksitlio.product.normalize import normalize_display_name

_CODE_RE = re.compile(r"[^a-z0-9]+")


def taxonomy_code(label: str, *, max_len: int = 64, prefix: str = "") -> str:
    """Stable DB code from a display label (brand_code / category_code)."""

    n = normalize_display_name(label)
    slug = _CODE_RE.sub("_", n).strip("_").upper()
    if not slug:
        slug = "UNKNOWN"
    if prefix:
        slug = f"{prefix}{slug}"
    return slug[:max_len]


def enrich_product_attributes(
    attributes: Optional[Mapping[str, Any]],
    *,
    brand_name: Optional[str] = None,
    model_number: Optional[str] = None,
    category_name: Optional[str] = None,
) -> dict[str, Any]:
    """Copy feed brand/model/category into attributes for search + pool hints."""

    out: dict[str, Any] = dict(attributes or {})
    if brand_name and not out.get("brand"):
        out["brand"] = brand_name
    if model_number and not out.get("model"):
        out["model"] = model_number
    if category_name and not out.get("category"):
        out["category"] = category_name
    return out


def product_search_haystack(
    *,
    display_name: str,
    model_number: Optional[str] = None,
    attributes: Optional[Mapping[str, Any]] = None,
) -> str:
    attrs = attributes or {}
    parts = [
        display_name,
        model_number,
        attrs.get("brand"),
        attrs.get("model"),
        attrs.get("category"),
        attrs.get("model_number"),
    ]
    return " ".join(str(p) for p in parts if p).casefold()


def haystack_matches_terms(haystack: str, terms: Sequence[str]) -> bool:
    if not terms:
        return True
    return any(t.casefold() in haystack for t in terms if t and str(t).strip())


def merge_synonym(
    synonyms: Sequence[str],
    *candidates: Optional[str],
) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in (*synonyms, *candidates):
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def pick_existing_category(
    label: str,
    *,
    categories: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Prefer an existing catalog row when the feed label overlaps synonyms."""

    norm = normalize_display_name(label)
    if not norm:
        return None
    tokens = set(norm.split())
    for row in categories:
        code = normalize_display_name(str(row.get("category_code") or ""))
        display = normalize_display_name(str(row.get("display_name") or ""))
        syns = [
            normalize_display_name(str(s))
            for s in (row.get("synonyms") or ())
            if str(s).strip()
        ]
        if norm == code or norm == display or norm in syns:
            return row
        for syn in syns:
            if not syn or len(syn) < 3:
                continue
            if syn in tokens or syn in norm:
                return row
        if display and len(display) >= 3 and (display in tokens or display in norm):
            return row
    return None


__all__ = [
    "enrich_product_attributes",
    "haystack_matches_terms",
    "merge_synonym",
    "pick_existing_category",
    "product_search_haystack",
    "taxonomy_code",
]
