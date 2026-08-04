"""Guest «Anladıklarım» extract — category/brand/budget only, no finance claims."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


def _names(entities: Any) -> list[str]:
    if not isinstance(entities, list):
        return []
    out: list[str] = []
    for item in entities:
        if isinstance(item, Mapping):
            name = str(item.get("display_name") or "").strip()
            if name:
                out.append(name)
    return out


def _budget_label(budget: Any) -> Optional[str]:
    if not isinstance(budget, Mapping):
        return None
    amount = budget.get("value")
    if amount is None:
        amount = budget.get("maximum")
    if amount is None:
        amount = budget.get("min")
    try:
        value = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Turkish grouping without inventing rates/terms.
    formatted = f"{int(round(value)):,}".replace(",", ".")
    return f"≈ {formatted} TL"


def build_need_extract(
    understanding: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """Build public extract card from FastParseResult.to_dict()-shaped payload."""

    if not understanding:
        return None
    rows: list[dict[str, str]] = []

    cats = understanding.get("positive_categories")
    if not cats and isinstance(understanding.get("entities"), Mapping):
        cats = understanding["entities"].get("categories")
    cat_names = _names(cats)
    if cat_names:
        rows.append({"k": "Kategori", "v": ", ".join(cat_names)})

    brand_names = _names(understanding.get("brands"))
    if brand_names:
        rows.append({"k": "Marka", "v": ", ".join(brand_names)})

    budget_v = _budget_label(understanding.get("budget"))
    if budget_v:
        rows.append({"k": "Bütçe", "v": budget_v})

    if not rows:
        return None
    return {"title": "Anladıklarım", "rows": rows}


def attach_need_extract(payload: dict[str, Any]) -> dict[str, Any]:
    extract = build_need_extract(
        payload.get("understanding")
        if isinstance(payload.get("understanding"), Mapping)
        else None
    )
    if extract is not None:
        payload["need_extract"] = extract
    return payload


__all__ = ["attach_need_extract", "build_need_extract"]
