"""Active need / constraint chip state (ADR-011 §20, §32)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from taksitlio.query_understanding.fast_parser import FastParseResult, ResolvedEntityRef


@dataclass
class QueryNeedState:
    active_categories: list[dict[str, Any]] = field(default_factory=list)
    excluded_categories: list[dict[str, Any]] = field(default_factory=list)
    merchant_preferences: list[dict[str, Any]] = field(default_factory=list)
    brand_preferences: list[dict[str, Any]] = field(default_factory=list)
    institution_preferences: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    payment_preferences: dict[str, Any] = field(default_factory=dict)
    required_attributes: list[dict[str, Any]] = field(default_factory=list)
    preferred_attributes: list[dict[str, Any]] = field(default_factory=list)
    usage_contexts: list[str] = field(default_factory=list)
    cancelled_constraints: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    state_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_categories": list(self.active_categories),
            "excluded_categories": list(self.excluded_categories),
            "merchant_preferences": list(self.merchant_preferences),
            "brand_preferences": list(self.brand_preferences),
            "institution_preferences": list(self.institution_preferences),
            "budget": dict(self.budget),
            "payment_preferences": dict(self.payment_preferences),
            "required_attributes": list(self.required_attributes),
            "preferred_attributes": list(self.preferred_attributes),
            "usage_contexts": list(self.usage_contexts),
            "cancelled_constraints": list(self.cancelled_constraints),
            "preferences": list(self.preferences),
            "state_version": self.state_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "QueryNeedState":
        data = dict(payload or {})
        return cls(
            active_categories=list(data.get("active_categories") or []),
            excluded_categories=list(data.get("excluded_categories") or []),
            merchant_preferences=list(data.get("merchant_preferences") or []),
            brand_preferences=list(data.get("brand_preferences") or []),
            institution_preferences=list(data.get("institution_preferences") or []),
            budget=dict(data.get("budget") or {}),
            payment_preferences=dict(data.get("payment_preferences") or {}),
            required_attributes=list(data.get("required_attributes") or []),
            preferred_attributes=list(data.get("preferred_attributes") or []),
            usage_contexts=list(data.get("usage_contexts") or []),
            cancelled_constraints=list(data.get("cancelled_constraints") or []),
            preferences=list(data.get("preferences") or []),
            state_version=int(data.get("state_version") or 0),
        )

    def bump(self) -> None:
        self.state_version += 1


def _is_catalog_category(cat: dict[str, Any]) -> bool:
    rid = str(cat.get("resolved_id") or "")
    return bool(rid) and not rid.startswith("free_text:")


def _category_ids(cats: list[dict[str, Any]]) -> set[str]:
    return {str(c.get("resolved_id")) for c in cats if c.get("resolved_id")}


def _dict_to_ref(payload: dict[str, Any]) -> ResolvedEntityRef:
    return ResolvedEntityRef(
        resolved_id=payload.get("resolved_id"),
        display_name=str(payload.get("display_name") or payload.get("resolved_id") or ""),
        match_type=payload.get("match_type"),
        confidence=float(payload.get("confidence") or 0.0),
        required=bool(payload.get("required")),
    )


def merge_parse_into_state(state: QueryNeedState, parse_dict: dict[str, Any]) -> QueryNeedState:
    new_cats = list(parse_dict.get("positive_categories") or [])
    catalog_new = [c for c in new_cats if isinstance(c, dict) and _is_catalog_category(c)]
    if catalog_new:
        new_ids = _category_ids(catalog_new)
        old_catalog = [c for c in state.active_categories if _is_catalog_category(c)]
        old_ids = _category_ids(old_catalog)
        # Distinct catalog product-type → replace prior categories.
        if old_ids and new_ids.isdisjoint(old_ids):
            state.active_categories = list(catalog_new)
        else:
            for c in new_cats:
                if c not in state.active_categories:
                    state.active_categories.append(c)
    else:
        # Free-text invent is cold-start recovery only. On refinements with an
        # active catalog category, drop free_text noise (ADR-010: no static
        # typo→category maps; bogus tokens must not AND-filter the pool).
        has_catalog = any(_is_catalog_category(c) for c in state.active_categories)
        for c in new_cats:
            if not isinstance(c, dict):
                continue
            if has_catalog and not _is_catalog_category(c):
                continue
            if c not in state.active_categories:
                state.active_categories.append(c)

    for c in parse_dict.get("negative_categories") or []:
        state.excluded_categories.append(c)
        state.cancelled_constraints.append({"type": "category", "value": c, "reason": "negation"})
    if parse_dict.get("merchant"):
        state.merchant_preferences = [parse_dict["merchant"]]
    for b in parse_dict.get("brands") or []:
        if b not in state.brand_preferences:
            state.brand_preferences.append(b)
    if parse_dict.get("budget"):
        state.budget = dict(parse_dict["budget"])
    for a in parse_dict.get("attributes") or []:
        if a.get("required"):
            state.required_attributes.append(a)
        else:
            state.preferred_attributes.append(a)
    for u in parse_dict.get("usage_contexts") or []:
        if u not in state.usage_contexts:
            state.usage_contexts.append(u)
    for p in parse_dict.get("preferences") or []:
        if p not in state.preferences:
            state.preferences.append(p)
    for inst in parse_dict.get("preferred_institutions") or []:
        if inst not in state.institution_preferences:
            state.institution_preferences.append(inst)

    ranking_mode = parse_dict.get("ranking_mode")
    if ranking_mode:
        state.payment_preferences = {
            **dict(state.payment_preferences or {}),
            "ranking_mode": str(ranking_mode),
        }

    state.bump()
    return state


def hydrate_parse_from_state(parse: FastParseResult, state: QueryNeedState) -> FastParseResult:
    """Carry prior need into a refinement-only parse before retrieve/gap analysis."""

    catalog_in_parse = [
        c
        for c in parse.positive_categories
        if c.resolved_id and not str(c.resolved_id).startswith("free_text:")
    ]
    catalog_in_state = [
        c for c in state.active_categories if isinstance(c, dict) and _is_catalog_category(c)
    ]
    # Free-text-only invent must not block prior catalog need on follow-ups.
    if not catalog_in_parse and catalog_in_state:
        parse.positive_categories = [_dict_to_ref(c) for c in catalog_in_state]
    elif not parse.positive_categories and state.active_categories:
        parse.positive_categories = [
            _dict_to_ref(c) for c in state.active_categories if isinstance(c, dict)
        ]
    if not parse.budget and state.budget:
        parse.budget = dict(state.budget)
    if not parse.brands and state.brand_preferences:
        parse.brands = [
            _dict_to_ref(b) for b in state.brand_preferences if isinstance(b, dict)
        ]
    if not parse.merchant and state.merchant_preferences:
        first = state.merchant_preferences[0]
        if isinstance(first, dict):
            parse.merchant = _dict_to_ref(first)
    if not parse.preferred_institutions and state.institution_preferences:
        parse.preferred_institutions = list(state.institution_preferences)
    if not parse.attributes and (state.required_attributes or state.preferred_attributes):
        parse.attributes = list(state.required_attributes) + list(state.preferred_attributes)

    ranking = parse.ranking_mode or (state.payment_preferences or {}).get("ranking_mode")
    if ranking:
        parse.ranking_mode = str(ranking)
        pref = f"ranking:{ranking}"
        if pref not in parse.preferences:
            parse.preferences.append(pref)

    if parse.positive_categories and parse.confidence < 0.78:
        parse.confidence = max(parse.confidence, 0.82)
        parse.field_confidence = {
            **dict(parse.field_confidence or {}),
            "category": max(
                float((parse.field_confidence or {}).get("category") or 0.0),
                max((c.confidence for c in parse.positive_categories), default=0.8),
            ),
        }
        parse.unresolved_spans = []
        if parse.route == "CLARIFICATION_REQUIRED" and parse.positive_categories:
            parse.route = "FAST_PATH"
    return parse


def cancel_constraint(state: QueryNeedState, *, constraint_key: str, value: Any) -> QueryNeedState:
    """Explicit user correction — LLM must not resurrect cancelled items."""

    if constraint_key == "category":
        state.active_categories = [
            c for c in state.active_categories if c.get("resolved_id") != value and c.get("display_name") != value
        ]
        state.cancelled_constraints.append({"type": "category", "value": value, "reason": "user_correction"})
    state.bump()
    return state


def chips_from_state(state: QueryNeedState) -> list[dict[str, Any]]:
    """Guest UI no longer shows constraint chips (budget/category/etc.)."""
    del state  # state still drives search; chips are intentionally unused
    return []


__all__ = [
    "QueryNeedState",
    "cancel_constraint",
    "chips_from_state",
    "hydrate_parse_from_state",
    "merge_parse_into_state",
]
