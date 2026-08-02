"""Map FastParseResult.to_dict() output to a CanonicalSearchPlan."""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from taksitlio.query_planning.models import (
    BudgetConstraint,
    CampaignIntent,
    CanonicalSearchPlan,
    CategoryRef,
    ConditionalException,
    ConstraintOperator,
    ConstraintStrength,
    GlobalConstraints,
    PlanConstraint,
    PlanItem,
    PlanSource,
    RequestType,
)

_STRETCH_PATTERNS = [
    re.compile(r"(?:biraz|bi[rı]?[\s\-]?(?:t[iı]k)?)?\s*(?:a[sş]abilir|ç[ıi]kabilir|ge[cç]ebilir)", re.IGNORECASE),
    re.compile(r"kadar\s+(?:ç[ıi]k|ge[cç]|a[sş])", re.IGNORECASE),
    re.compile(r"ama\b.{0,20}(?:ç[ıi]k(?:abilir|sa)|ge[cç](?:ebilir|se)|olabilir)", re.IGNORECASE),
    re.compile(r"(?:esnek|tolerans|esneklik)", re.IGNORECASE),
    re.compile(r"(?:civar[ıi](?:nda)?|dolay[ıi])", re.IGNORECASE),
]

_STRETCH_RATIO = 1.15


def _has_stretch_signal(message: str) -> bool:
    if not message:
        return False
    return any(p.search(message) for p in _STRETCH_PATTERNS)


def _entity_to_category_ref(ent: dict[str, Any]) -> CategoryRef:
    return CategoryRef(
        resolved_id=ent.get("resolved_id"),
        raw_text=ent.get("display_name", ""),
        confidence=float(ent.get("confidence", 0.0)),
    )


def _make_constraint(
    dimension: str,
    operator: ConstraintOperator,
    value: Any,
    strength: ConstraintStrength,
    *,
    source_text: str = "",
    confidence: float = 0.0,
    unit: Optional[str] = None,
    priority: int = 0,
    source: str = "fast_parser",
) -> PlanConstraint:
    return PlanConstraint(
        dimension=dimension,
        operator=operator,
        value=value,
        strength=strength,
        source_text=source_text,
        confidence=confidence,
        unit=unit,
        priority=priority,
        source=source,
    )


def _detect_request_type(parse_dict: dict[str, Any]) -> RequestType:
    pos_cats = parse_dict.get("positive_categories") or []
    distinct_ids = {
        c.get("resolved_id")
        for c in pos_cats
        if isinstance(c, dict) and c.get("resolved_id")
    }
    if len(distinct_ids) >= 2:
        return RequestType.MULTI_ITEM_BUNDLE

    has_terms = bool(parse_dict.get("requested_terms"))
    has_ranking = bool(parse_dict.get("ranking_mode"))
    if has_terms or has_ranking:
        if pos_cats:
            return RequestType.PRODUCT_AND_CAMPAIGN_SEARCH

    intent = str(parse_dict.get("intent", "")).upper()
    if intent == "OUT_OF_SCOPE":
        return RequestType.OUT_OF_SCOPE
    if intent in {"COMPARE", "COMPARISON"}:
        return RequestType.COMPARISON
    if intent in {"EXPLORE", "EXPLORATORY"}:
        return RequestType.EXPLORATORY_SEARCH

    if pos_cats:
        return RequestType.SINGLE_PRODUCT_SEARCH

    return RequestType.EXPLORATORY_SEARCH


def _build_items(parse_dict: dict[str, Any]) -> list[PlanItem]:
    """Build plan items from parse categories and constraints."""
    pos_cats = parse_dict.get("positive_categories") or []
    neg_cats = parse_dict.get("negative_categories") or []
    brands = parse_dict.get("brands") or []
    attributes = parse_dict.get("attributes") or []

    items_by_cat: dict[str, list[dict[str, Any]]] = {}
    for cat in pos_cats:
        if not isinstance(cat, dict):
            continue
        key = cat.get("resolved_id") or cat.get("display_name") or "unknown"
        items_by_cat.setdefault(key, []).append(cat)

    if not items_by_cat:
        items_by_cat["default"] = [{}]

    plan_items: list[PlanItem] = []
    for cat_key, cat_entries in items_by_cat.items():
        cat_ent = cat_entries[0]
        cat_ref = _entity_to_category_ref(cat_ent) if cat_ent else None

        hard: list[PlanConstraint] = []
        soft: list[PlanConstraint] = []
        excluded: list[PlanConstraint] = []

        if cat_ref and cat_ref.resolved_id:
            is_required = cat_ent.get("required", False)
            strength = ConstraintStrength.HARD if is_required else ConstraintStrength.SOFT
            c = _make_constraint(
                "category", ConstraintOperator.EQ, cat_ref.resolved_id,
                strength,
                source_text=cat_ref.raw_text,
                confidence=cat_ref.confidence,
            )
            (hard if strength == ConstraintStrength.HARD else soft).append(c)

        for neg in neg_cats:
            if not isinstance(neg, dict):
                continue
            # EQ + exclude bucket: product matching value is dropped.
            excluded.append(_make_constraint(
                "category", ConstraintOperator.EQ,
                neg.get("resolved_id") or neg.get("display_name", ""),
                ConstraintStrength.HARD,
                source_text=neg.get("display_name", ""),
                confidence=float(neg.get("confidence", 0.0)),
            ))

        for brand in brands:
            if not isinstance(brand, dict):
                continue
            polarity = str(brand.get("polarity") or "positive").lower()
            display = brand.get("display_name", "")
            value = brand.get("resolved_id") or display
            if polarity in {"negative", "excluded", "exclude"}:
                excluded.append(_make_constraint(
                    "brand", ConstraintOperator.EQ, value,
                    ConstraintStrength.HARD,
                    source_text=display,
                    confidence=float(brand.get("confidence", 0.0)),
                ))
                continue
            is_req = brand.get("required", False)
            strength = ConstraintStrength.HARD if is_req else ConstraintStrength.SOFT
            c = _make_constraint(
                "brand", ConstraintOperator.EQ, value,
                strength,
                source_text=display,
                confidence=float(brand.get("confidence", 0.0)),
            )
            (hard if strength == ConstraintStrength.HARD else soft).append(c)

        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            dim = (
                attr.get("dimension")
                or attr.get("attribute_id")
                or attr.get("name")
                or attr.get("key")
                or ""
            )
            val = attr.get("value")
            is_req = attr.get("required", False)
            strength = ConstraintStrength.HARD if is_req else ConstraintStrength.SOFT
            op_str = str(attr.get("operator", "EQ")).upper()
            try:
                op = ConstraintOperator(op_str)
            except ValueError:
                op = ConstraintOperator.EQ
            c = _make_constraint(
                dim, op, val, strength,
                source_text=attr.get("source_text", ""),
                confidence=float(attr.get("confidence", 0.0)),
                unit=attr.get("unit"),
            )
            (hard if strength == ConstraintStrength.HARD else soft).append(c)

        plan_items.append(PlanItem(
            category=cat_ref,
            hard_constraints=hard,
            soft_preferences=soft,
            excluded_constraints=excluded,
        ))

    return plan_items


def _build_budget(parse_dict: dict[str, Any], message: str) -> Optional[BudgetConstraint]:
    budget_raw = parse_dict.get("budget")
    if not budget_raw or not isinstance(budget_raw, dict):
        return None

    maximum = budget_raw.get("maximum") or budget_raw.get("value")
    if maximum is None:
        return None

    maximum = float(maximum)
    minimum = budget_raw.get("minimum")
    if minimum is not None:
        minimum = float(minimum)

    stretch: Optional[float] = None
    if _has_stretch_signal(message):
        stretch = round(maximum * _STRETCH_RATIO, 2)

    return BudgetConstraint(
        minimum=minimum,
        target_maximum=maximum,
        stretch_maximum=stretch,
        currency=budget_raw.get("currency", "TRY"),
    )


def _build_campaign_intent(parse_dict: dict[str, Any]) -> Optional[CampaignIntent]:
    terms = parse_dict.get("requested_terms") or []
    ranking = parse_dict.get("ranking_mode")
    institutions = parse_dict.get("preferred_institutions") or []

    if not terms and not ranking and not institutions:
        return None

    inst_names: list[str] = []
    for inst in institutions:
        if isinstance(inst, dict):
            inst_names.append(inst.get("display_name") or inst.get("resolved_id") or "")
        elif isinstance(inst, str):
            inst_names.append(inst)

    zero_pref = False
    if ranking and "zero" in str(ranking).lower():
        zero_pref = True

    return CampaignIntent(
        requested=True,
        required=False,
        institution_preferences=[n for n in inst_names if n],
        requested_terms=[int(t) for t in terms],
        zero_rate_preferred=zero_pref,
        ranking_mode=ranking,
    )


def _build_conditional_exceptions(
    items: list[PlanItem],
    budget: Optional[BudgetConstraint],
    message: str,
) -> list[PlanItem]:
    """Attach stretch-budget conditional exceptions to items."""
    if not budget or not budget.stretch_maximum:
        return items

    for item in items:
        budget_constraints = [
            c for c in item.hard_constraints + item.soft_preferences
            if c.dimension == "budget"
        ]
        if not budget_constraints:
            continue
        for bc in budget_constraints:
            item.conditional_exceptions.append(ConditionalException(
                target_constraint_id=bc.constraint_id,
                condition_code="significant_value_improvement",
                stretch_value=budget.stretch_maximum,
            ))

    return items


def normalize_fast_parse_to_plan(
    parse_dict: dict[str, Any],
    *,
    message: str = "",
) -> CanonicalSearchPlan:
    """Convert a FastParseResult.to_dict() output into a CanonicalSearchPlan."""
    request_type = _detect_request_type(parse_dict)
    items = _build_items(parse_dict)
    budget = _build_budget(parse_dict, message)
    campaign = _build_campaign_intent(parse_dict)

    merchant_raw = parse_dict.get("merchant")
    allowed: list[str] = []
    excluded_merchants: list[str] = []
    if isinstance(merchant_raw, dict) and merchant_raw.get("resolved_id"):
        allowed.append(merchant_raw["resolved_id"])

    global_constraints: Optional[GlobalConstraints] = None
    if budget or allowed or excluded_merchants:
        global_constraints = GlobalConstraints(
            budget=budget,
            allowed_merchants=allowed,
            excluded_merchants=excluded_merchants,
        )

    items = _build_conditional_exceptions(items, budget, message)

    ranking_mode = parse_dict.get("ranking_mode")
    if ranking_mode:
        for item in items:
            if not item.ranking_priorities:
                item.ranking_priorities = [ranking_mode]

    return CanonicalSearchPlan(
        request_type=request_type,
        items=items,
        global_constraints=global_constraints,
        campaign_intent=campaign,
        source=PlanSource(fast_parser_used=True, llm_patch_used=False),
    )
