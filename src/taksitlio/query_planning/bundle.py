"""Bounded beam-search bundle solver for multi-item plans."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BundleResult:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_price: float = 0.0
    budget_remaining: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    finance_bundle: str = "NOT_SUPPORTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": dict(self.items),
            "total_price": self.total_price,
            "budget_remaining": self.budget_remaining,
            "reason_codes": list(self.reason_codes),
            "finance_bundle": self.finance_bundle,
        }


_DEFAULT_POLICY: dict[str, Any] = {
    "candidate_top_k": 5,
    "beam_width": 10,
    "maximum_combinations": 5000,
    "timeout_ms": 2000,
}


def _product_price(product: dict[str, Any]) -> float:
    raw = product.get("price") or product.get("current_price")
    if raw is None:
        return float("inf")
    return float(raw)


def solve_bundle(
    items_candidates: dict[str, list[dict[str, Any]]],
    *,
    global_budget_max: float,
    policy: dict[str, Any] | None = None,
) -> BundleResult:
    """Find the best combination of one product per item within budget.

    Uses a bounded beam search to avoid combinatorial explosion.
    """
    cfg = {**_DEFAULT_POLICY, **(policy or {})}
    top_k = int(cfg["candidate_top_k"])
    beam_width = int(cfg["beam_width"])
    max_combos = int(cfg["maximum_combinations"])
    timeout_ms = int(cfg["timeout_ms"])

    if not items_candidates:
        return BundleResult(reason_codes=["NO_ITEMS"])

    item_ids = sorted(items_candidates.keys())
    trimmed: dict[str, list[dict[str, Any]]] = {}
    for iid in item_ids:
        candidates = items_candidates[iid]
        sorted_cands = sorted(candidates, key=_product_price)[:top_k]
        if not sorted_cands:
            return BundleResult(reason_codes=[f"NO_CANDIDATES:{iid}"])
        trimmed[iid] = sorted_cands

    total_combos = 1
    for cands in trimmed.values():
        total_combos *= len(cands)

    if total_combos <= max_combos:
        return _exhaustive_search(trimmed, item_ids, global_budget_max)

    return _beam_search(trimmed, item_ids, global_budget_max, beam_width, timeout_ms)


def _exhaustive_search(
    trimmed: dict[str, list[dict[str, Any]]],
    item_ids: list[str],
    budget: float,
) -> BundleResult:
    ordered_lists = [trimmed[iid] for iid in item_ids]
    best: Optional[BundleResult] = None

    for combo in itertools.product(*ordered_lists):
        total = sum(_product_price(p) for p in combo)
        if total > budget:
            continue
        remaining = budget - total
        if best is None or remaining > best.budget_remaining or (
            remaining == best.budget_remaining and total < best.total_price
        ):
            selection = {item_ids[i]: _summarize(p) for i, p in enumerate(combo)}
            best = BundleResult(
                items=selection,
                total_price=round(total, 2),
                budget_remaining=round(remaining, 2),
                reason_codes=["EXHAUSTIVE_OPTIMAL"],
            )

    if best is None:
        cheapest_total = sum(
            _product_price(trimmed[iid][0]) for iid in item_ids
        )
        selection = {iid: _summarize(trimmed[iid][0]) for iid in item_ids}
        return BundleResult(
            items=selection,
            total_price=round(cheapest_total, 2),
            budget_remaining=round(budget - cheapest_total, 2),
            reason_codes=["OVER_BUDGET_CHEAPEST"],
        )

    return best


def _beam_search(
    trimmed: dict[str, list[dict[str, Any]]],
    item_ids: list[str],
    budget: float,
    beam_width: int,
    timeout_ms: int,
) -> BundleResult:
    """Layer-by-layer beam search, one layer per item."""
    deadline = time.monotonic() + timeout_ms / 1000.0

    # Each beam entry: (total_price, {item_id: product_summary})
    beam: list[tuple[float, dict[str, dict[str, Any]]]] = [(0.0, {})]

    for iid in item_ids:
        next_beam: list[tuple[float, dict[str, dict[str, Any]]]] = []
        for total, selection in beam:
            for product in trimmed[iid]:
                price = _product_price(product)
                new_total = total + price
                new_sel = {**selection, iid: _summarize(product)}
                next_beam.append((new_total, new_sel))

            if time.monotonic() > deadline:
                break

        next_beam.sort(key=lambda x: x[0])
        beam = next_beam[:beam_width]

        if time.monotonic() > deadline:
            break

    within_budget = [(t, s) for t, s in beam if t <= budget]
    if within_budget:
        best_total, best_sel = within_budget[0]
        return BundleResult(
            items=best_sel,
            total_price=round(best_total, 2),
            budget_remaining=round(budget - best_total, 2),
            reason_codes=["BEAM_SEARCH_OPTIMAL"],
        )

    if beam:
        cheapest_total, cheapest_sel = beam[0]
        return BundleResult(
            items=cheapest_sel,
            total_price=round(cheapest_total, 2),
            budget_remaining=round(budget - cheapest_total, 2),
            reason_codes=["OVER_BUDGET_BEAM_CHEAPEST"],
        )

    return BundleResult(reason_codes=["BEAM_SEARCH_EMPTY"])


def _summarize(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": product.get("product_id", ""),
        "display_name": product.get("display_name", ""),
        "price": _product_price(product),
    }
