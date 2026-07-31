"""Category family match for progressive retrieval (phone ≠ laptop)."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# Required include / hard exclude token families for known category ids.
# Tokens are matched casefold against product display/category text.
CATEGORY_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "category-phone": {
        "include": (
            "akıllı telefon",
            "akilli telefon",
            "cep telefon",
            "iphone",
            "galaxy a",
            "galaxy s",
            "galaxy z",
            "galaxy m",
            "redmi",
            "poco",
            "xiaomi",
            "oppo",
            "realme",
            "vivo",
            "huawei",
            "honor",
            "tecno",
            "infinix",
            "pixel",
            "gsm",
            "telefon",
        ),
        "exclude": (
            "laptop",
            "notebook",
            "macbook",
            "ideapad",
            "thinkpad",
            "vivobook",
            "zenbook",
            "süpürge",
            "supurge",
            "televizyon",
            " tablet",
            "ipad",
            "monitor",
            "monitör",
        ),
    },
    "category-laptop": {
        "include": (
            "laptop",
            "notebook",
            "macbook",
            "ideapad",
            "thinkpad",
            "vivobook",
            "zenbook",
            "yoga slim",
            "loq",
            "tuf gaming",
            "aspire",
            "pavilion",
            "inspiron",
            "latitude",
            "dizüstü",
            "dizustu",
        ),
        "exclude": (
            "akıllı telefon",
            "akilli telefon",
            "cep telefon",
            "iphone",
            "süpürge",
            "supurge",
            "televizyon",
        ),
    },
    "category-tablet": {
        "include": ("tablet", "ipad", "galaxy tab"),
        "exclude": ("laptop", "notebook", "macbook", "akıllı telefon", "iphone", "süpürge"),
    },
    "category-tv": {
        "include": ("televizyon", " smart tv", "qled", "oled tv", " led tv"),
        "exclude": ("laptop", "telefon", "iphone", "süpürge", "tablet"),
    },
}

_NAME_TO_FAMILY = {
    "cep telefonu": "category-phone",
    "telefon": "category-phone",
    "phone": "category-phone",
    "akıllı telefon": "category-phone",
    "dizüstü bilgisayar": "category-laptop",
    "laptop": "category-laptop",
    "notebook": "category-laptop",
    "tablet": "category-tablet",
    "televizyon": "category-tv",
    "tv": "category-tv",
}


def family_id_for_category(cat: Mapping[str, Any] | str) -> Optional[str]:
    if isinstance(cat, Mapping):
        rid = str(cat.get("resolved_id") or cat.get("entity_id") or "").strip()
        if rid in CATEGORY_FAMILIES:
            return rid
        name = str(cat.get("display_name") or cat.get("canonical_name") or "").casefold().strip()
    else:
        name = str(cat).casefold().strip()
    if not name:
        return None
    if name in CATEGORY_FAMILIES:
        return name
    return _NAME_TO_FAMILY.get(name)


def product_haystack(product: Mapping[str, Any]) -> str:
    return " ".join(
        str(x)
        for x in (
            product.get("display_name"),
            product.get("category"),
            product.get("category_name"),
            product.get("brand_model"),
            " ".join(str(t) for t in (product.get("tags") or ())),
        )
        if x
    ).casefold()


def matches_category_family(product: Mapping[str, Any], family_id: str) -> bool:
    family = CATEGORY_FAMILIES.get(family_id)
    if not family:
        return True
    hay = product_haystack(product)
    if any(tok in hay for tok in family["exclude"]):
        return False
    return any(tok in hay for tok in family["include"])


def required_category_families(constraints: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for cat in constraints.get("positive_categories") or ():
        fid = family_id_for_category(cat)
        if fid and fid not in out:
            out.append(fid)
    # Clarification / chip product_type (phone|laptop|…)
    ptype = constraints.get("product_type")
    if isinstance(ptype, str) and ptype.strip():
        fid = family_id_for_category(ptype.strip())
        if fid and fid not in out:
            out.append(fid)
    elif isinstance(ptype, Mapping):
        fid = family_id_for_category(ptype)
        if fid and fid not in out:
            out.append(fid)
    return tuple(out)


def matches_required_categories(
    product: Mapping[str, Any], constraints: Mapping[str, Any]
) -> bool:
    families = required_category_families(constraints)
    if not families:
        return True
    # All required families must match (usually one).
    return all(matches_category_family(product, fid) for fid in families)


def utterance_name_terms(utterance: str) -> tuple[str, ...]:
    """DB search terms derived from utterance category cues."""

    u = (utterance or "").casefold()
    if any(t in u for t in ("cep telefon", "telefon", "iphone", "akıllı telefon", "akilli telefon")):
        return ("Akıllı Telefon", "iPhone", "Galaxy", "Redmi", "Xiaomi", "Oppo", "Realme", "Vivo")
    if any(t in u for t in ("laptop", "notebook", "macbook", "dizüstü", "dizustu", "bilgisayar")):
        return ("Laptop", "Notebook", "MacBook", "Ideapad")
    if "tablet" in u or "ipad" in u:
        return ("Tablet", "iPad")
    if "televizyon" in u or " tv" in f" {u}" or u.startswith("tv"):
        return ("Televizyon", "TV")
    return ()


__all__ = [
    "CATEGORY_FAMILIES",
    "family_id_for_category",
    "matches_category_family",
    "matches_required_categories",
    "product_haystack",
    "required_category_families",
    "utterance_name_terms",
]
