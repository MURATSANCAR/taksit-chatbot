"""Deterministic fast parser for product+finance queries (ADR-011 §6).

Uses catalog candidates for entity resolution — no static typo maps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from taksitlio.entity_resolution import (
    EntityCandidate,
    ResolutionAction,
    ResolutionPolicy,
    resolve_entity,
)
from taksitlio.semantic_matching.turkish_normalize import normalize_turkish, turkish_lower


@dataclass(frozen=True)
class ResolvedEntityRef:
    resolved_id: Optional[str]
    display_name: str
    match_type: Optional[str] = None
    confidence: float = 0.0
    required: bool = False


@dataclass
class FastParseResult:
    intent: str
    merchant: Optional[ResolvedEntityRef] = None
    positive_categories: list[ResolvedEntityRef] = field(default_factory=list)
    negative_categories: list[ResolvedEntityRef] = field(default_factory=list)
    brands: list[ResolvedEntityRef] = field(default_factory=list)
    institutions: list[ResolvedEntityRef] = field(default_factory=list)
    budget: Optional[dict[str, Any]] = None
    attributes: list[dict[str, Any]] = field(default_factory=list)
    requested_terms: list[int] = field(default_factory=list)
    preferred_institutions: list[dict[str, Any]] = field(default_factory=list)
    usage_contexts: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    confidence: float = 0.0
    field_confidence: dict[str, float] = field(default_factory=dict)
    route: str = "FAST_PATH"  # FAST_PATH | CLARIFICATION_REQUIRED | LLM_REQUIRED | UNSUPPORTED
    requires_llm: bool = False
    unresolved_spans: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def _ent(e: ResolvedEntityRef) -> dict[str, Any]:
            return {
                "resolved_id": e.resolved_id,
                "display_name": e.display_name,
                "match_type": e.match_type,
                "confidence": e.confidence,
                "required": e.required,
            }

        budget = self.budget
        if isinstance(budget, dict) and "maximum" in budget and "type" not in budget:
            budget = {**budget, "type": "TOTAL_MAXIMUM", "value": budget.get("maximum")}
        elif isinstance(budget, dict) and budget.get("type") == "RANGE" and "maximum" in budget:
            budget = {
                **budget,
                "type": "TOTAL_MAXIMUM",
                "value": budget.get("maximum"),
            }

        return {
            "intent": self.intent,
            "entities": {
                "merchants": [_ent(self.merchant)] if self.merchant else [],
                "institutions": list(self.preferred_institutions),
                "brands": [_ent(b) for b in self.brands],
                "categories": [_ent(c) for c in self.positive_categories],
                "products": [],
            },
            "merchant": _ent(self.merchant) if self.merchant else None,
            "positive_categories": [_ent(c) for c in self.positive_categories],
            "negative_categories": [_ent(c) for c in self.negative_categories],
            "brands": [_ent(b) for b in self.brands],
            "budget": budget if budget is not None else self.budget,
            "terms": {
                "type": "EXACT",
                "values": list(self.requested_terms),
            }
            if self.requested_terms
            else None,
            "attributes": list(self.attributes),
            "negative_constraints": [_ent(c) for c in self.negative_categories],
            "corrections": [],
            "requested_terms": list(self.requested_terms),
            "preferred_institutions": list(self.preferred_institutions),
            "usage_contexts": list(self.usage_contexts),
            "preferences": list(self.preferences),
            "confidence": self.confidence,
            "overall_confidence": self.confidence,
            "field_confidence": dict(self.field_confidence),
            "route": self.route,
            "requires_llm": self.requires_llm,
            "unresolved_spans": list(self.unresolved_spans),
        }


# Require ≥1 thousand-separator group in the first alt so "5000" falls through to \d+
# (previously \d{1,3}(?:...) * matched "500" and left "0", dropping unformatted budgets).
_BUDGET_RE = re.compile(
    r"(?P<num>\d{1,3}(?:[.\s]\d{3})+|\d+)\s*(?:bin)?\s*(?:tl|lira|₺)?",
    re.IGNORECASE,
)
_TERM_RE = re.compile(r"(\d{1,2})\s*ay", re.IGNORECASE)
_RAM_RE = re.compile(r"(\d+)\s*gb", re.IGNORECASE)

_NEGATION_MARKERS = ("olmasın", "istemiyorum", "istemem", "değil", "hariç", "boşver")
_MAX_BUDGET_CUES = (
    "kadar",
    "altında",
    "altinda",
    "en fazla",
    "aşmasın",
    "asmasin",
    "geçmesin",
    "gecmesin",
    "üstünü aşma",
    "ustunu asma",
    "fazla olmasın",
    "fazla olmasin",
)
_USAGE_CUES = {
    "okul": "education",
    "ödev": "education",
    "ders": "education",
    "oyun": "gaming",
    "gaming": "gaming",
    "iş": "business",
    "ofis": "business",
    "film": "media",
    "hafif": "lightweight",
    "taşınabilir": "portable",
    "uzun süre": "longevity",
    "uzun yıllar": "longevity",
}
# Function/grammar words stripped when recovering a free-text product noun.
_QUERY_STOPWORDS = frozenset(
    {
        "bana",
        "bir",
        "bu",
        "şu",
        "su",
        "ve",
        "ile",
        "icin",
        "için",
        "lazim",
        "lazım",
        "istiyorum",
        "isterim",
        "istiyoz",
        "ariyorum",
        "arıyorum",
        "bakiyorum",
        "bakıyorum",
        "almak",
        "alacagim",
        "alacağım",
        "alabilir",
        "goster",
        "göster",
        "varsa",
        "olsun",
        "olmasin",
        "olmasın",
        "ama",
        "de",
        "da",
        "mi",
        "mı",
        "mu",
        "mü",
        "lira",
        "tl",
        "bin",
        "kadar",
        "asmasin",
        "aşmasın",
        "gecmesin",
        "geçmesin",
        "altinda",
        "altında",
        "en",
        "fazla",
        "yaklasik",
        "yaklaşık",
        "civari",
        "civarı",
        "ay",
        "taksit",
        "kredi",
        "fiyat",
        "ucuz",
        "iyi",
        "guzel",
        "güzel",
        "lutfen",
        "lütfen",
        "merhaba",
        "selam",
        "sey",
        "şey",
        "urun",
        "ürün",
        "urunler",
        "ürünler",
        "model",
        "modeller",
    }
)


def _has_max_budget_cue(lower: str) -> bool:
    return any(cue in lower for cue in _MAX_BUDGET_CUES)


def _parse_budget(text: str) -> Optional[dict[str, Any]]:
    lower = turkish_lower(text)
    # Prefer "X bin" patterns
    bin_m = re.search(r"(\d+)\s*bin", lower)
    if bin_m:
        value = int(bin_m.group(1)) * 1000
        approx = "civarı" in lower or "yaklaşık" in lower
        if _has_max_budget_cue(lower):
            return {"maximum": value, "currency": "TRY", "type": "RANGE"}
        if approx:
            return {"value": value, "currency": "TRY", "type": "APPROXIMATE"}
        return {"maximum": value, "currency": "TRY", "type": "RANGE"}
    for m in _BUDGET_RE.finditer(lower):
        raw = m.group("num").replace(".", "").replace(" ", "")
        if not raw.isdigit():
            continue
        value = int(raw)
        if value < 1000:
            continue
        if _has_max_budget_cue(lower):
            return {"maximum": value, "currency": "TRY", "type": "RANGE"}
        return {"value": value, "currency": "TRY", "type": "APPROXIMATE"}
    return None


def _free_text_product_nouns(text: str) -> list[str]:
    """Recover concrete product nouns when catalog category resolution misses."""

    normalized = normalize_turkish(text).value or ""
    nouns: list[str] = []
    for tok in normalized.split():
        t = tok.strip(".,!?;:\"'()[]{}").casefold()
        if len(t) < 3 or t.isdigit() or t in _QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"\d+[a-zğüşıöç]*", t):  # 16gb, 12ay
            continue
        nouns.append(tok.strip(".,!?;:\"'()[]{}"))
    # Prefer longer / more specific tokens first, keep order stable after dedupe
    nouns = list(dict.fromkeys(nouns))
    return nouns[:3]


def _resolve_one(
    text: str,
    candidates: Sequence[EntityCandidate],
    *,
    policy: Optional[ResolutionPolicy] = None,
) -> Optional[ResolvedEntityRef]:
    if not candidates or not text.strip():
        return None
    result = resolve_entity(text, candidates, policy=policy or ResolutionPolicy())
    if result.action == ResolutionAction.AUTO_SELECT and result.resolved_entity_id:
        return ResolvedEntityRef(
            resolved_id=result.resolved_entity_id,
            display_name=result.resolved_display_name or text,
            match_type=result.match_type.value if result.match_type else None,
            confidence=float(result.confidence or 0.0),
            required=True,
        )
    if result.action == ResolutionAction.CLARIFY and result.candidates:
        top = result.candidates[0]
        return ResolvedEntityRef(
            resolved_id=None,
            display_name=top.display_name,
            match_type=top.match_type.value,
            confidence=top.confidence,
            required=False,
        )
    return None


def _split_negation_clauses(text: str) -> tuple[str, list[str]]:
    lower = turkish_lower(text)
    negative_spans: list[str] = []
    positive = text
    for marker in _NEGATION_MARKERS:
        if marker not in lower:
            continue
        # Take a short window before the marker as negated span
        idx = lower.find(marker)
        window = lower[max(0, idx - 40) : idx].strip()
        tokens = window.split()
        if tokens:
            negative_spans.append(tokens[-1])
    return positive, negative_spans


@dataclass(frozen=True)
class CatalogHints:
    merchants: tuple[EntityCandidate, ...] = ()
    categories: tuple[EntityCandidate, ...] = ()
    brands: tuple[EntityCandidate, ...] = ()
    institutions: tuple[EntityCandidate, ...] = ()


def fast_parse(text: str, *, catalog: Optional[CatalogHints] = None) -> FastParseResult:
    catalog = catalog or CatalogHints()
    normalized = normalize_turkish(text).value
    lower = turkish_lower(text)
    positive_text, neg_spans = _split_negation_clauses(text)

    def _nv(s: str) -> str:
        return normalize_turkish(s).value

    merchant = None
    hit = _resolve_one(text, catalog.merchants) if catalog.merchants else None
    if hit and hit.resolved_id:
        merchant = hit
    else:
        for cand in catalog.merchants:
            for alias in (cand.display_name, cand.canonical_name, *cand.aliases):
                alias_n = _nv(alias)
                if alias_n and alias_n in normalized:
                    merchant = _resolve_one(alias, catalog.merchants)
                    if merchant:
                        break
            if merchant and merchant.resolved_id:
                break

    positive_categories: list[ResolvedEntityRef] = []
    negative_categories: list[ResolvedEntityRef] = []
    for cand in catalog.categories:
        names = (cand.display_name, cand.canonical_name, *cand.aliases)
        for name in names:
            n = _nv(name)
            if not n:
                continue
            if any(n == _nv(ns) or n in _nv(ns) or _nv(ns) in n for ns in neg_spans):
                negative_categories.append(
                    ResolvedEntityRef(
                        resolved_id=cand.entity_id,
                        display_name=cand.display_name,
                        match_type="NORMALIZED_EXACT",
                        confidence=0.95,
                    )
                )
                break
            if n in normalized or name.casefold() in lower:
                positive_categories.append(
                    ResolvedEntityRef(
                        resolved_id=cand.entity_id,
                        display_name=cand.display_name,
                        match_type="NORMALIZED_EXACT",
                        confidence=0.96,
                        required=True,
                    )
                )
                break

    # Deduplicate by id
    def _dedupe(items: list[ResolvedEntityRef]) -> list[ResolvedEntityRef]:
        seen: set[str] = set()
        out: list[ResolvedEntityRef] = []
        for it in items:
            key = it.resolved_id or it.display_name
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    positive_categories = _dedupe(positive_categories)
    negative_categories = _dedupe(negative_categories)

    brands: list[ResolvedEntityRef] = []
    for cand in catalog.brands:
        for name in (cand.display_name, cand.canonical_name, *cand.aliases):
            n = _nv(name)
            if n and n in normalized:
                brands.append(
                    ResolvedEntityRef(
                        resolved_id=cand.entity_id,
                        display_name=cand.display_name,
                        match_type="NORMALIZED_EXACT",
                        confidence=0.97,
                        required=True,
                    )
                )
                break
    brands = _dedupe(brands)

    abstract_cues = ("herkes", "yer kapla", "mantıklı", "zorlamasın")
    abstract_hits = sum(1 for c in abstract_cues if c in lower)

    # Catalog miss (e.g. "ayakkabı" before FOOTWEAR is seeded): keep the noun so we
    # search products instead of asking electronics product-type clarification.
    # Skip on abstract multi-need utterances that still need LLM / clarification.
    if not positive_categories and not brands and abstract_hits < 2:
        for noun in _free_text_product_nouns(text):
            slug = normalize_turkish(noun).ascii_fold or noun.casefold()
            positive_categories.append(
                ResolvedEntityRef(
                    resolved_id=f"free_text:{slug}",
                    display_name=noun,
                    match_type="FREE_TEXT_PRODUCT",
                    confidence=0.88,
                    required=True,
                )
            )
        positive_categories = _dedupe(positive_categories)

    preferred_institutions: list[dict[str, Any]] = []
    for cand in catalog.institutions:
        for name in (cand.display_name, cand.canonical_name, *cand.aliases):
            n = _nv(name)
            if n and n in normalized:
                preferred_institutions.append(
                    {"institution_id": cand.entity_id, "required": False, "display_name": cand.display_name}
                )
                break

    budget = _parse_budget(text)
    terms = [int(m.group(1)) for m in _TERM_RE.finditer(lower)]
    attributes: list[dict[str, Any]] = []
    ram = _RAM_RE.search(lower)
    if ram and ("ram" in lower or "gb" in lower):
        attributes.append(
            {
                "attribute_id": "ram_gb",
                "operator": "GTE",
                "value": int(ram.group(1)),
                "unit": "GB",
                "required": True,
            }
        )

    usage: list[str] = []
    prefs: list[str] = []
    for cue, code in _USAGE_CUES.items():
        if cue in lower:
            if code in {"lightweight", "portable", "longevity"}:
                prefs.append(code)
            else:
                usage.append(code)
    usage = list(dict.fromkeys(usage))
    prefs = list(dict.fromkeys(prefs))

    has_product_signal = bool(positive_categories or brands or attributes)
    has_finance = bool(terms or preferred_institutions)
    intent = "PRODUCT_WITH_FINANCE" if (has_product_signal and has_finance) else "PRODUCT_SEARCH"

    # Confidence: high when category/brand/budget clear and no multi-candidate brand-only ambiguity
    conf = 0.55
    if positive_categories:
        conf += 0.20
    if brands:
        conf += 0.10
    if budget:
        conf += 0.08
    if attributes:
        conf += 0.05
    if merchant and merchant.resolved_id:
        conf += 0.05
    if terms:
        conf += 0.03
    # Brand without category → often needs clarification (Apple / cihaz)
    if brands and not positive_categories:
        conf = min(conf, 0.72)
    if not positive_categories and not brands and usage:
        conf = min(conf, 0.55)
    if not positive_categories and not brands and not budget:
        conf = min(conf, 0.45)
    # Abstract multi-dimension household use → likely LLM
    requires_llm = False
    if abstract_hits >= 2 and not positive_categories:
        requires_llm = True
        conf = min(conf, 0.48)
    conf = max(0.0, min(0.99, conf))

    field_confidence = {
        "intent": 0.99 if intent else 0.5,
        "merchant": float(merchant.confidence)
        if merchant and merchant.resolved_id
        else (float(merchant.confidence) if merchant else 0.0),
        "category": max((c.confidence for c in positive_categories), default=0.0),
        "brand": max((b.confidence for b in brands), default=0.0),
        "institution": 0.95 if preferred_institutions else 0.0,
        "budget": 1.0 if budget else 0.0,
        "term": 1.0 if terms else 0.0,
        "attributes": 1.0 if attributes else 0.0,
    }
    # Field confidences are independent — high merchant must not validate low institution.
    route = "FAST_PATH"
    if requires_llm:
        route = "LLM_REQUIRED"
    elif brands and not positive_categories:
        route = "CLARIFICATION_REQUIRED"
    elif not positive_categories and not brands and (usage or prefs):
        route = "CLARIFICATION_REQUIRED"
    elif merchant and not merchant.resolved_id and merchant.confidence >= 0.78:
        route = "CLARIFICATION_REQUIRED"

    return FastParseResult(
        intent=intent,
        merchant=merchant if merchant and merchant.resolved_id else merchant,
        positive_categories=positive_categories,
        negative_categories=negative_categories,
        brands=brands,
        budget=budget,
        attributes=attributes,
        requested_terms=terms,
        preferred_institutions=preferred_institutions,
        usage_contexts=usage,
        preferences=prefs,
        confidence=conf,
        field_confidence=field_confidence,
        route=route,
        requires_llm=requires_llm,
        unresolved_spans=[] if positive_categories or brands else [text[:80]],
        evidence={"normalized": normalized, "neg_spans": neg_spans, "positive_text": positive_text[:120]},
    )
