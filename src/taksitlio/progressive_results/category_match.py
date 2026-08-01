"""Category family match for progressive retrieval (phone ≠ laptop).

Known family token lists remain as optional enrichment for V003 codes that
map onto electronics families. Live include tokens come from the category
catalog (synonyms / display_name) via constraints.include_tokens.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

# Letters used for Turkish-aware token boundaries (avoid "kamp" ⊂ "kampanyalı").
_TR_WORD_CHARS = "0-9a-zçğıöşü"
# Split on non-word chars — compiled once (fast_parse can call this 10k+ times/request).
_NON_WORD_RE = re.compile(rf"[^{_TR_WORD_CHARS}]+", re.IGNORECASE)


def _token_pad(text: str) -> str:
    collapsed = " ".join(_NON_WORD_RE.sub(" ", (text or "").casefold()).split())
    return f" {collapsed} " if collapsed else " "


def alias_mentioned_in_text(alias: str, text: str) -> bool:
    """True when alias appears as a whole token/phrase, not a substring of a longer word."""

    a = (alias or "").casefold().strip()
    if not a or len(a) < 3:
        return False
    needle = _token_pad(a)
    if needle == " ":
        return False
    return needle in _token_pad(text)


def _token_in_haystack(tok: str, hay: str) -> bool:
    """Include/exclude match; short tokens use word boundaries (tel ≠ Intel)."""

    t = (tok or "").casefold().strip()
    if not t:
        return False
    # ≤3-char tokens as bare substrings false-positive (tel⊂intel, pc⊂laptop…).
    if len(t) <= 3:
        needle = _token_pad(t)
        return bool(needle != " " and needle in _token_pad(hay))
    return t in hay

# Optional enrichment for well-known V003 / legacy ids (not the sole source of truth).
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

# V003 category_code → optional legacy family id for include enrichment.
_CODE_TO_FAMILY = {
    "MOBILE_PHONE": "category-phone",
    "LAPTOP": "category-laptop",
    "TABLET": "category-tablet",
    "TELEVISION": "category-tv",
    "TV": "category-tv",
}

_NAME_TO_FAMILY = {
    "cep telefonu": "category-phone",
    "telefon": "category-phone",
    "tel": "category-phone",
    "phone": "category-phone",
    "akıllı telefon": "category-phone",
    "dizüstü bilgisayar": "category-laptop",
    "laptop": "category-laptop",
    "notebook": "category-laptop",
    "tablet": "category-tablet",
    "televizyon": "category-tv",
    "tv": "category-tv",
    "beyaz eşya": "HOME_APPLIANCE",
    "buzdolabı": "HOME_APPLIANCE",
    "buzdolabi": "HOME_APPLIANCE",
}


def legacy_family_includes(category_code: str) -> tuple[str, ...]:
    """Include tokens from optional legacy family maps for a V003 code."""

    fid = _CODE_TO_FAMILY.get(str(category_code or "").strip())
    if not fid:
        return ()
    family = CATEGORY_FAMILIES.get(fid) or {}
    return tuple(family.get("include") or ())


def family_id_for_category(cat: Mapping[str, Any] | str) -> Optional[str]:
    if isinstance(cat, Mapping):
        rid = str(cat.get("resolved_id") or cat.get("entity_id") or "").strip()
        if rid in CATEGORY_FAMILIES:
            return rid
        if rid in _CODE_TO_FAMILY:
            return _CODE_TO_FAMILY[rid]
        if rid:
            # Dynamic catalog id (e.g. HOME_APPLIANCE) is its own family key.
            return rid
        name = str(cat.get("display_name") or cat.get("canonical_name") or "").casefold().strip()
    else:
        name = str(cat).casefold().strip()
        rid = name
        if rid in CATEGORY_FAMILIES:
            return rid
        if rid.upper() in _CODE_TO_FAMILY:
            return _CODE_TO_FAMILY[rid.upper()]
    if not name:
        return None
    if name in CATEGORY_FAMILIES:
        return name
    mapped = _NAME_TO_FAMILY.get(name)
    if mapped:
        return mapped
    return name or None


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


def _include_tokens_for(cat: Mapping[str, Any] | str) -> tuple[str, ...]:
    if isinstance(cat, Mapping):
        raw = cat.get("include_tokens") or ()
        tokens = tuple(str(t).casefold().strip() for t in raw if str(t).strip())
        fid = family_id_for_category(cat)
        name = str(cat.get("display_name") or "").casefold().strip()
    else:
        fid = family_id_for_category(cat)
        name = str(cat).casefold().strip()
        tokens = ()
    family_inc = (
        tuple(CATEGORY_FAMILIES[fid]["include"])
        if fid and fid in CATEGORY_FAMILIES
        else ()
    )
    # Colloquial free-text like include_tokens=["tel"] must not be the sole
    # haystack needle (tel⊂intel). Promote to the mapped family includes.
    if family_inc and (not tokens or all(len(t) <= 3 for t in tokens)):
        return family_inc
    if tokens:
        return tokens
    if name:
        return (name,)
    return ()


def _exclude_tokens_for(cat: Mapping[str, Any] | str) -> tuple[str, ...]:
    fid = family_id_for_category(cat)
    if fid and fid in CATEGORY_FAMILIES:
        return tuple(CATEGORY_FAMILIES[fid].get("exclude") or ())
    return ()


def matches_category_family(product: Mapping[str, Any], family_id: str) -> bool:
    family = CATEGORY_FAMILIES.get(family_id)
    if not family:
        # Dynamic id with no legacy family → allow (token filter applied elsewhere).
        return True
    hay = product_haystack(product)
    if any(_token_in_haystack(tok, hay) for tok in family["exclude"]):
        return False
    return any(_token_in_haystack(tok, hay) for tok in family["include"])


def matches_category_tokens(product: Mapping[str, Any], cat: Mapping[str, Any] | str) -> bool:
    hay = product_haystack(product)
    for tok in _exclude_tokens_for(cat):
        if tok and _token_in_haystack(tok, hay):
            return False
    includes = _include_tokens_for(cat)
    if not includes:
        return True
    return any(_token_in_haystack(tok, hay) for tok in includes if tok)


def required_category_families(constraints: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for cat in constraints.get("positive_categories") or ():
        fid = family_id_for_category(cat)
        if fid and fid not in out:
            out.append(fid)
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
    cats = list(constraints.get("positive_categories") or ())
    ptype = constraints.get("product_type")
    if isinstance(ptype, str) and ptype.strip():
        cats.append(ptype.strip())
    elif isinstance(ptype, Mapping):
        cats.append(ptype)
    if not cats:
        return True
    # OR across positives: compound cues like "spor ayakkabı" resolve FOOTWEAR+SPORTS,
    # but catalog titles often carry only the noun. AND emptied recall incorrectly.
    return any(matches_category_tokens(product, cat) for cat in cats)


def utterance_name_terms(
    utterance: str,
    *,
    category_candidates: Sequence[Any] = (),
) -> tuple[str, ...]:
    """DB search terms from live category aliases matching the utterance."""

    u = (utterance or "").casefold()
    if not u:
        return ()

    # Fast path: inverted alias index over category candidates.
    if category_candidates:
        from taksitlio.entity_resolution import EntityCandidate
        from taksitlio.query_understanding.alias_index import (
            build_alias_index,
            matched_alias_labels,
        )

        entities: list[EntityCandidate] = []
        for cand in category_candidates:
            if isinstance(cand, EntityCandidate):
                entities.append(cand)
                continue
            if isinstance(cand, Mapping):
                entities.append(
                    EntityCandidate(
                        entity_id=str(
                            cand.get("entity_id") or cand.get("resolved_id") or ""
                        ),
                        display_name=str(cand.get("display_name") or ""),
                        canonical_name=str(
                            cand.get("canonical_name") or cand.get("display_name") or ""
                        ),
                        aliases=tuple(str(a) for a in (cand.get("aliases") or ())),
                        entity_type="category",
                    )
                )
                continue
            entities.append(
                EntityCandidate(
                    entity_id=str(getattr(cand, "entity_id", "") or ""),
                    display_name=str(getattr(cand, "display_name", "") or ""),
                    canonical_name=str(
                        getattr(cand, "canonical_name", None)
                        or getattr(cand, "display_name", "")
                        or ""
                    ),
                    aliases=tuple(
                        str(a) for a in (getattr(cand, "aliases", ()) or ())
                    ),
                    entity_type="category",
                )
            )
        index = build_alias_index(categories=entities)
        matched: list[str] = []
        for cand in index.lookup_categories(utterance):
            matched.extend(matched_alias_labels(cand, utterance))
        if matched:
            return tuple(dict.fromkeys(str(m).strip() for m in matched if str(m).strip()))[
                :12
            ]

    matched: list[str] = []
    for cand in category_candidates or ():
        names: list[str] = []
        if isinstance(cand, Mapping):
            names = [
                str(cand.get("display_name") or ""),
                str(cand.get("canonical_name") or ""),
                *[str(a) for a in (cand.get("aliases") or ())],
            ]
        else:
            names = [
                str(getattr(cand, "display_name", "") or ""),
                str(getattr(cand, "canonical_name", "") or ""),
                *[str(a) for a in (getattr(cand, "aliases", ()) or ())],
            ]
        hit_names = [
            n for n in names if n and alias_mentioned_in_text(str(n).strip(), u)
        ]
        if not hit_names:
            continue
        display = str(names[0]).strip() if names else ""
        chosen: list[str] = []
        if display:
            chosen.append(display)
        for n in hit_names:
            ns = str(n).strip()
            if ns and ns not in chosen:
                chosen.append(ns)
        matched.extend(chosen)

    if matched:
        return tuple(dict.fromkeys(str(m).strip() for m in matched if str(m).strip()))[:12]

    # Legacy fallback for unit tests that still use demo category ids without a repo.
    if any(t in u for t in ("cep telefon", "telefon", "iphone", "akıllı telefon", "akilli telefon")):
        return ("Akıllı Telefon", "iPhone", "Galaxy", "Redmi", "Xiaomi", "Oppo", "Realme", "Vivo")
    if any(t in u for t in ("laptop", "notebook", "macbook", "dizüstü", "dizustu", "bilgisayar")):
        return ("Laptop", "Notebook", "MacBook", "Ideapad")
    if "tablet" in u or "ipad" in u:
        return ("Tablet", "iPad")
    if "televizyon" in u or " tv" in f" {u}" or u.startswith("tv"):
        return ("Televizyon", "TV")
    if any(t in u for t in ("buzdolab", "beyaz eşya", "beyaz esya", "çamaşır", "camasir")):
        return ("Buzdolabı", "Beyaz Eşya", "Çamaşır")
    if any(
        alias_mentioned_in_text(t, u)
        for t in ("ayakkabı", "ayakkabi", "sneaker", "terlik", "sandalet")
    ) or alias_mentioned_in_text("bot", u):
        return ("Ayakkabı", "Spor Ayakkabı", "Sneaker")
    if any(
        alias_mentioned_in_text(t, u)
        for t in ("kulaklık", "kulaklik", "earbud", "airpods", "kulaklı")
    ):
        return ("Kulaklık", "Earbud", "AirPods", "Kulaklık")

    # Last resort: content tokens from the utterance so catalog search still runs
    # when the DB category list has not caught up (ADR-010 dynamic resolution).
    from taksitlio.query_understanding.fast_parser import _free_text_product_nouns

    nouns = _free_text_product_nouns(utterance)
    if nouns:
        return tuple(nouns)[:8]
    return ()


__all__ = [
    "CATEGORY_FAMILIES",
    "alias_mentioned_in_text",
    "family_id_for_category",
    "legacy_family_includes",
    "matches_category_family",
    "matches_category_tokens",
    "matches_required_categories",
    "product_haystack",
    "required_category_families",
    "utterance_name_terms",
]
