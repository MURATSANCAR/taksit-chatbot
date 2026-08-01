"""High-confidence brand/category/attribute resolution (Recovery-P1).

Uses live taxonomy tables and generic extractors — no merchant-specific
category/brand if/else branches and no invented stock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from taksitlio.product.normalize import normalize_display_name
from taksitlio.product.taxonomy import pick_existing_category, taxonomy_code

Confidence = str  # HIGH | MEDIUM | LOW


@dataclass(frozen=True)
class CategoryResolution:
    product_id: int
    source_category: Optional[str]
    resolved_category_id: Optional[int]
    resolution_method: str
    confidence: Confidence
    evidence: str


@dataclass(frozen=True)
class BrandResolution:
    product_id: int
    brand_id: Optional[int]
    source_method: str
    confidence: Confidence
    evidence_span: str


@dataclass(frozen=True)
class AttributeResolution:
    product_id: int
    attribute_key: str
    normalized_value: str
    unit: Optional[str]
    raw_value: str
    source: str
    confidence: Confidence
    evidence: str


_ATTR_PATTERNS: tuple[tuple[str, re.Pattern[str], str, Optional[str]], ...] = (
    ("ram_gb", re.compile(r"(?i)\b(\d+)\s*gb\s*ram\b"), "GB", "RAM"),
    ("storage_gb", re.compile(r"(?i)\b(\d+)\s*gb\s*(?:ssd|depolama|storage|hdd)?\b"), "GB", "storage"),
    ("screen_inch", re.compile(r"(?i)\b(\d+(?:[.,]\d+)?)\s*(?:inç|inc|inch|\" )\b"), "in", "screen"),
    ("weight_kg", re.compile(r"(?i)\b(\d+(?:[.,]\d+)?)\s*kg\b"), "kg", "weight"),
)


def _token_hit(haystack: str, needle: str) -> bool:
    n = needle.casefold().strip()
    if not n or len(n) < 3:
        return False
    h = haystack.casefold()
    if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", h):
        return True
    # Allow multi-word synonym containment for feed paths.
    if " " in n or ">" in n:
        return n in h
    return False


def resolve_category_for_product(
    *,
    product_id: int,
    title: str,
    description: str = "",
    attributes: Optional[Mapping[str, Any]] = None,
    categories: Sequence[Mapping[str, Any]],
    existing_category_id: Optional[int] = None,
    synonym_index: Optional[Sequence[tuple[int, str]]] = None,
) -> CategoryResolution:
    attrs = dict(attributes or {})
    source_cat = None
    for key in (
        "category",
        "source_category",
        "google_product_category",
        "product_type",
        "breadcrumb",
    ):
        val = attrs.get(key)
        if val is not None and str(val).strip() and str(val).strip().lower() not in {"none", "null"}:
            source_cat = str(val).strip()
            break

    if existing_category_id is not None:
        return CategoryResolution(
            product_id=product_id,
            source_category=source_cat,
            resolved_category_id=int(existing_category_id),
            resolution_method="existing_relation",
            confidence="HIGH",
            evidence=f"products.category_id={existing_category_id}",
        )

    if source_cat:
        hit = pick_existing_category(source_cat, categories=categories)
        if hit is not None:
            return CategoryResolution(
                product_id=product_id,
                source_category=source_cat,
                resolved_category_id=int(hit["id"]),
                resolution_method="source_category_alias",
                confidence="HIGH",
                evidence=f"source_category={source_cat}",
            )

    hay = f"{title} {description} {attrs.get('brand') or ''}".strip().casefold()
    hits: list[tuple[int, str]] = []
    index = synonym_index
    if index is None:
        built: list[tuple[int, str]] = []
        for row in categories:
            cat_id = int(row["id"])
            labels = [
                str(row.get("display_name") or ""),
                *[str(s) for s in (row.get("synonyms") or ())],
            ]
            for label in labels:
                lab = label.casefold().strip()
                if len(lab) >= 3:
                    built.append((cat_id, lab))
        index = built
    for cat_id, label in index:
        if label in hay:
            hits.append((cat_id, label))
    uniq = {h[0] for h in hits}
    if len(uniq) == 1:
        cat_id, label = hits[0]
        return CategoryResolution(
            product_id=product_id,
            source_category=source_cat,
            resolved_category_id=cat_id,
            resolution_method="title_synonym_match",
            confidence="HIGH",
            evidence=f"matched_synonym={label}",
        )
    if len(uniq) > 1:
        return CategoryResolution(
            product_id=product_id,
            source_category=source_cat,
            resolved_category_id=None,
            resolution_method="title_synonym_conflict",
            confidence="LOW",
            evidence="conflict=" + ",".join(str(x) for x in sorted(uniq)),
        )

    return CategoryResolution(
        product_id=product_id,
        source_category=source_cat,
        resolved_category_id=None,
        resolution_method="unresolved",
        confidence="LOW",
        evidence="no_high_confidence_match",
    )


def resolve_brand_for_product(
    *,
    product_id: int,
    title: str,
    attributes: Optional[Mapping[str, Any]] = None,
    existing_brand_id: Optional[int] = None,
    brand_alias_map: Optional[Mapping[str, int]] = None,
    brand_alias_rows: Sequence[Mapping[str, Any]] = (),
) -> BrandResolution:
    attrs = dict(attributes or {})
    if existing_brand_id is not None:
        return BrandResolution(
            product_id=product_id,
            brand_id=int(existing_brand_id),
            source_method="existing_relation",
            confidence="HIGH",
            evidence_span=f"products.brand_id={existing_brand_id}",
        )

    structured = None
    for key in ("brand", "manufacturer", "marka"):
        val = attrs.get(key)
        if val is not None and str(val).strip() and str(val).strip().lower() not in {"none", "null"}:
            structured = str(val).strip()
            break

    alias_map = brand_alias_map
    if alias_map is None:
        alias_map = {}
        for row in brand_alias_rows:
            key = normalize_display_name(str(row.get("normalized_alias") or row.get("alias_text") or ""))
            if key:
                alias_map[key] = int(row["brand_id"])

    if structured:
        norm = normalize_display_name(structured)
        if norm in alias_map:
            return BrandResolution(
                product_id=product_id,
                brand_id=alias_map[norm],
                source_method="structured_source_brand",
                confidence="HIGH",
                evidence_span=structured,
            )
        return BrandResolution(
            product_id=product_id,
            brand_id=None,
            source_method="structured_source_brand_unlinked",
            confidence="HIGH",
            evidence_span=structured,
        )

    # Title alias scan only for short aliases (>=3) exact token — skipped when map huge
    # to avoid O(n*m); prefer structured brand.
    return BrandResolution(
        product_id=product_id,
        brand_id=None,
        source_method="unresolved",
        confidence="LOW",
        evidence_span="",
    )


def extract_attributes_from_text(
    *,
    product_id: int,
    title: str,
    description: str = "",
    attributes: Optional[Mapping[str, Any]] = None,
) -> list[AttributeResolution]:
    out: list[AttributeResolution] = []
    attrs = dict(attributes or {})
    for key, val in attrs.items():
        if val is None or str(val).strip() == "" or str(val).lower() == "null":
            continue
        out.append(
            AttributeResolution(
                product_id=product_id,
                attribute_key=str(key)[:128],
                normalized_value=str(val),
                unit=None,
                raw_value=str(val),
                source="structured_source_specs",
                confidence="HIGH",
                evidence=f"attributes.{key}",
            )
        )

    hay = f"{title}\n{description}"
    seen = {a.attribute_key for a in out}
    for key, pat, unit, _hint in _ATTR_PATTERNS:
        if key in seen:
            continue
        m = pat.search(hay)
        if not m:
            continue
        raw = m.group(0)
        norm = m.group(1).replace(",", ".")
        out.append(
            AttributeResolution(
                product_id=product_id,
                attribute_key=key,
                normalized_value=norm,
                unit=unit,
                raw_value=raw,
                source="title_description_pattern",
                confidence="HIGH",
                evidence=raw,
            )
        )
        seen.add(key)
    return out


def ensure_taxonomy_seed_categories() -> list[dict[str, Any]]:
    """Additional high-level categories for apparel-heavy merchant feeds.

    Returned as seed rows for staging upsert into `categories` — not hardcoded
    product routing rules.
    """

    return [
        {
            "category_code": "APPAREL",
            "display_name": "Giyim",
            "synonyms": [
                "giyim",
                "tişört",
                "tshirt",
                "t-shirt",
                "polo",
                "pantolon",
                "şort",
                "sweatshirt",
                "eşofman",
                "gömlek",
                "ceket",
                "pijama",
                "blazer",
                "elbise",
                "etek",
                "çorap",
                "takım",
                "takımı",
                "kazak",
                "mont",
                "yelek",
                "kapüşonlu",
                "oversize",
                "eşarp",
                "atkı",
                "bere",
                "eldiven",
                "tayt",
                "body",
                "atlet",
                "sweat",
                "hoodie",
                "jean",
                "kot",
                "kaban",
                "trençkot",
                "yağmurluk",
                "mayo",
                "bikini",
                "iç çamaşır",
                "sütyen",
                "külot",
            ],
            "description": "Apparel taxonomy seed",
        },
        {
            "category_code": "BAGS",
            "display_name": "Çanta",
            "synonyms": [
                "çanta",
                "çantası",
                "cüzdan",
                "valiz",
                "sırt çantası",
                "omuz çantası",
                "el çantası",
                "bel çantası",
                "laptop çantası",
            ],
            "description": "Bags taxonomy seed",
        },
        {
            "category_code": "WATCHES",
            "display_name": "Saat",
            "synonyms": ["saat", "kol saati", "wristwatch", "akıllı saat", "smartwatch"],
            "description": "Watches taxonomy seed",
        },
        {
            "category_code": "TV_AUDIO",
            "display_name": "TV & Ses",
            "synonyms": [
                "televizyon",
                "tv",
                "smart tv",
                "soundbar",
                "hoparlör",
                "kulaklık",
                "earbud",
                "airpods",
                "mikrofon",
            ],
            "description": "TV and audio taxonomy seed",
        },
        {
            "category_code": "SMALL_APPLIANCE",
            "display_name": "Küçük Ev Aleti",
            "synonyms": [
                "ütü",
                "süpürge",
                "kahve makinesi",
                "blender",
                "tost makinesi",
                "mikser",
                "airfryer",
                "air fryer",
                "saç kurutma",
                "epilatör",
            ],
            "description": "Small appliances taxonomy seed",
        },
        {
            "category_code": "SPORTS",
            "display_name": "Spor & Outdoor",
            "synonyms": [
                "spor",
                "fitness",
                "dumbbell",
                "halter",
                "yoga",
                "kamp",
                "outdoor",
                "bisiklet",
                "koşu",
                "krampon",
            ],
            "description": "Sports taxonomy seed",
        },
        {
            "category_code": "KIDS",
            "display_name": "Bebek & Çocuk",
            "synonyms": ["bebek", "çocuk", "oyuncak", "mama sandalyesi", "beşik", "puset"],
            "description": "Kids taxonomy seed",
        },
        {
            "category_code": "BEAUTY",
            "display_name": "Kozmetik & Kişisel Bakım",
            "synonyms": [
                "kozmetik",
                "parfüm",
                "krem",
                "şampuan",
                "makyaj",
                "ruj",
                "fondöten",
            ],
            "description": "Beauty taxonomy seed",
        },
        {
            "category_code": "BOOKS_MUSIC",
            "display_name": "Kitap & Müzik",
            "synonyms": ["kitap", "roman", "dergi", "albüm", "plak", "cd", "dvd"],
            "description": "Books and music taxonomy seed",
        },
        {
            "category_code": "ACCESSORIES",
            "display_name": "Aksesuar",
            "synonyms": [
                "aksesuar",
                "kılıf",
                "şarj",
                "kablo",
                "powerbank",
                "mouse",
                "klavye",
                "webcam",
            ],
            "description": "Accessories taxonomy seed",
        },
        {
            "category_code": "MOBILE_PHONE_EXTRA",
            "display_name": None,
            "synonyms": [
                "tel",
            ],
            "merge_into_code": "MOBILE_PHONE",
        },
        {
            "category_code": "FOOTWEAR_EXTRA",
            "display_name": None,
            "synonyms": [
                "çizme",
                "sabo",
                "loafer",
                "sneaker",
                "spor ayakkabı",
                "topuklu",
                "babet",
                "sandalet",
                "ayakkabı",
                "bot",
                "terlik",
                "krampon",
                "hiking",
                "outdoor ayakkabı",
            ],
            "merge_into_code": "FOOTWEAR",
        },
        {
            "category_code": "HOME_EXTRA",
            "display_name": None,
            "synonyms": [
                "buzdolabı",
                "çamaşır makinesi",
                "bulaşık makinesi",
                "fırın",
                "ankastre",
                "klima",
                "derin dondurucu",
            ],
            "merge_into_code": "HOME_APPLIANCE",
        },
    ]


__all__ = [
    "AttributeResolution",
    "BrandResolution",
    "CategoryResolution",
    "ensure_taxonomy_seed_categories",
    "extract_attributes_from_text",
    "resolve_brand_for_product",
    "resolve_category_for_product",
    "taxonomy_code",
]
