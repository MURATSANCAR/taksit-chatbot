"""Top-level query planner: orchestration, complexity detection, plan-to-constraint bridge."""

from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from taksitlio.query_planning.capability_checker import check_capabilities
from taksitlio.query_planning.clarification_planner import build_clarification_questions
from taksitlio.query_planning.conflict_resolver import resolve_conflicts
from taksitlio.query_planning.models import (
    PLAN_VERSION,
    CanonicalSearchPlan,
    ConstraintStrength,
    PlanSource,
    RequestType,
)
from taksitlio.query_planning.normalizer import normalize_fast_parse_to_plan
from taksitlio.query_planning.validator import PlanValidationError, validate_plan

log = logging.getLogger(__name__)

_COMPLEXITY_THRESHOLDS = {
    "multi_category_count": 2,
    "attribute_count": 3,
    "unresolved_span_count": 2,
}


def detect_complex_route(parse_dict: dict[str, Any], message: str) -> bool:
    """Return True when the query warrants the full plan pipeline rather than fast path."""
    pos_cats = parse_dict.get("positive_categories") or []
    neg_cats = parse_dict.get("negative_categories") or []
    attrs = parse_dict.get("attributes") or []
    unresolved = parse_dict.get("unresolved_spans") or []
    terms = parse_dict.get("requested_terms") or []
    budget = parse_dict.get("budget") or {}

    distinct_cat_ids = {
        c.get("resolved_id")
        for c in pos_cats
        if isinstance(c, dict) and c.get("resolved_id")
    }
    if len(distinct_cat_ids) >= _COMPLEXITY_THRESHOLDS["multi_category_count"]:
        return True

    if len(attrs) >= _COMPLEXITY_THRESHOLDS["attribute_count"]:
        return True

    if len(unresolved) >= _COMPLEXITY_THRESHOLDS["unresolved_span_count"]:
        return True

    if neg_cats and pos_cats:
        return True

    if terms and budget:
        return True

    route = str(parse_dict.get("route", "")).upper()
    if route in {"LLM_REQUIRED", "CLARIFICATION_REQUIRED"}:
        return True

    return False


def build_plan_from_fast_parse(
    parse_dict: dict[str, Any],
    message: str = "",
    *,
    catalog_dimensions: set[str] | None = None,
    finance_ready: bool = True,
    max_clarification_questions: int = 2,
) -> CanonicalSearchPlan:
    """Build, validate, and enrich a plan from fast-parse output."""
    plan = normalize_fast_parse_to_plan(parse_dict, message=message)
    plan = resolve_conflicts(plan)
    plan = check_capabilities(plan, catalog_dimensions=catalog_dimensions, finance_ready=finance_ready)
    build_clarification_questions(plan, max_questions=max_clarification_questions)
    return plan


def merge_llm_plan_patch(
    base_plan: CanonicalSearchPlan,
    llm_patch: dict[str, Any],
) -> CanonicalSearchPlan:
    """Merge an LLM-generated patch into the base plan.

    On validation failure the base plan is returned with a clarification note.
    """
    merged_dict = copy.deepcopy(base_plan.to_dict())

    if "items" in llm_patch:
        merged_dict["items"] = llm_patch["items"]
    if "global_constraints" in llm_patch:
        gc = merged_dict.get("global_constraints") or {}
        gc.update(llm_patch["global_constraints"])
        merged_dict["global_constraints"] = gc
    if "campaign_intent" in llm_patch:
        ci = merged_dict.get("campaign_intent") or {}
        ci.update(llm_patch["campaign_intent"])
        merged_dict["campaign_intent"] = ci
    if "ambiguities" in llm_patch:
        existing = merged_dict.get("ambiguities") or []
        merged_dict["ambiguities"] = existing + llm_patch["ambiguities"]
    if "request_type" in llm_patch:
        merged_dict["request_type"] = llm_patch["request_type"]

    try:
        merged = validate_plan(merged_dict)
        merged.source = PlanSource(fast_parser_used=True, llm_patch_used=True)
        merged = resolve_conflicts(merged)
        return merged
    except PlanValidationError as exc:
        log.warning("LLM patch rejected: %s", exc)
        base_plan.ambiguities = list(base_plan.ambiguities) + [
            f"LLM patch rejected: {'; '.join(exc.errors)}"
        ]
        base_plan.clarification_required = True
        return base_plan


def plan_to_constraints_dict(plan: CanonicalSearchPlan) -> dict[str, Any]:
    """Convert a CanonicalSearchPlan into a flat constraints dict for progressive_results."""
    result: dict[str, Any] = {
        "plan_version": plan.plan_version,
        "request_type": plan.request_type.value if isinstance(plan.request_type, RequestType) else plan.request_type,
    }

    pos_categories: list[dict[str, Any]] = []
    neg_categories: list[dict[str, Any]] = []
    brands: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    ranking_priorities: list[str] = []
    conditional_exceptions: list[dict[str, Any]] = []

    for item in plan.items:
        if item.category and item.category.resolved_id:
            pos_categories.append({
                "resolved_id": item.category.resolved_id,
                "display_name": item.category.raw_text,
                "confidence": item.category.confidence,
            })

        for c in item.hard_constraints + item.soft_preferences:
            if c.dimension == "brand":
                brands.append({
                    "resolved_id": c.value,
                    "display_name": c.source_text,
                    "confidence": c.confidence,
                    "required": c.strength == ConstraintStrength.HARD,
                })
            elif c.dimension not in ("category", "budget", "merchant"):
                attributes.append({
                    "dimension": c.dimension,
                    "operator": c.operator.value if hasattr(c.operator, "value") else c.operator,
                    "value": c.value,
                    "required": c.strength == ConstraintStrength.HARD,
                    "unit": c.unit,
                })

        for exc in item.excluded_constraints:
            if exc.dimension == "category":
                neg_categories.append({
                    "resolved_id": exc.value,
                    "display_name": exc.source_text,
                    "confidence": exc.confidence,
                })

        ranking_priorities.extend(item.ranking_priorities)

        for ce in item.conditional_exceptions:
            conditional_exceptions.append(ce.to_dict())

    result["positive_categories"] = pos_categories
    result["negative_categories"] = neg_categories
    result["brands"] = brands
    result["attributes"] = attributes
    result["ranking_priorities"] = ranking_priorities
    result["conditional_exceptions"] = conditional_exceptions

    gc = plan.global_constraints
    if gc:
        if gc.allowed_merchants:
            result["merchant"] = {
                "resolved_id": gc.allowed_merchants[0],
                "display_name": gc.allowed_merchants[0],
            }
        budget = gc.budget
        if budget:
            result["budget"] = {
                "maximum": budget.target_maximum,
                "stretch_maximum": budget.stretch_maximum,
                "minimum": budget.minimum,
                "currency": budget.currency,
            }

    ci = plan.campaign_intent
    if ci:
        result["ranking_mode"] = ci.ranking_mode
        if ci.requested_terms:
            result["requested_terms"] = ci.requested_terms

    result["capability_flags"] = {}
    if plan.unsupported_capabilities:
        for cap in plan.unsupported_capabilities:
            if cap == "FINANCE_NOT_READY":
                result["capability_flags"]["finance_display"] = "BLOCKED"

    result["items"] = [item.to_dict() for item in plan.items]

    return result
