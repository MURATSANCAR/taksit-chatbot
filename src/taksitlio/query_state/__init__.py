"""Active need / constraint chip state (ADR-011 §20, §32)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


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


def merge_parse_into_state(state: QueryNeedState, parse_dict: dict[str, Any]) -> QueryNeedState:
    for c in parse_dict.get("positive_categories") or []:
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
    state.bump()
    return state


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
    chips: list[dict[str, Any]] = []
    budget = state.budget
    if budget.get("maximum"):
        chips.append({"id": "budget_max", "label": f"{int(budget['maximum']):,} TL’ye kadar".replace(",", "."), "kind": "budget"})
    elif budget.get("value"):
        chips.append({"id": "budget_approx", "label": f"{int(budget['value']):,} TL civarı".replace(",", "."), "kind": "budget"})
    for c in state.active_categories:
        chips.append(
            {
                "id": f"cat:{c.get('resolved_id') or c.get('display_name')}",
                "label": c.get("display_name") or "Kategori",
                "kind": "category",
            }
        )
    if not state.active_categories:
        chips.append({"id": "cat:uncertain", "label": "Ürün türü belirsiz", "kind": "uncertainty"})
    for u in state.usage_contexts:
        label = {"education": "Okul", "gaming": "Oyun", "business": "İş", "media": "Film"}.get(u, u)
        chips.append({"id": f"usage:{u}", "label": label, "kind": "usage"})
    for p in state.preferences:
        label = {"lightweight": "Hafif", "portable": "Taşınabilir", "longevity": "Uzun süreli kullanım"}.get(p, p)
        chips.append({"id": f"pref:{p}", "label": label, "kind": "preference"})
    for a in state.required_attributes:
        if a.get("attribute_id") == "ram_gb":
            chips.append({"id": "attr:ram", "label": f"{a.get('value')} GB RAM", "kind": "attribute"})
    return chips
