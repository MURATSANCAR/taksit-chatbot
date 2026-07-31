"""Diversity-aware Top-K selection (ADR-008 P0.1).

Runs after negative filtering and hierarchy collapse. Goals:

* Prefer candidates with real alias / token-set / coverage signal over
  pure-vector noise when filling Top-K slots.
* Keep meaningful siblings visible in Top-2 when scores are close.
* Demote a parent that crowds out its children without inventing
  category-specific rules.
* Never re-introduce forbidden / non-matchable / hard-excluded nodes.
* Diversity bonus must not alone drive auto-select (Top-1 preserved
  unless a child outranks a parent via existing scores + mild demotion).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from taksitlio.semantic_matching.domain import (
    CategoryCandidate,
    SemanticMatchPolicy,
    SignalBreakdown,
)
from taksitlio.semantic_matching.in_memory_index import SnapshotIndex


@dataclass(frozen=True)
class DiversificationOutcome:
    candidates: tuple[CategoryCandidate, ...]
    diversity_notes: tuple[str, ...] = ()


def _has_positive_channel(signals: SignalBreakdown) -> bool:
    return (
        signals.alias > 0
        or signals.surface_exact_alias > 0
        or signals.normalized_exact_alias > 0
        or signals.token_set_alias > 0
        or signals.prefix_safe_alias > 0
        or signals.morphological_variant > 0
        or signals.character_ngram > 0
        or signals.use_case > 0
    )


def _parent_id(index: SnapshotIndex, category_id: str) -> Optional[str]:
    node = index.by_id.get(category_id)
    if node is None:
        return None
    return node.parent_id


def _is_ancestor_of(
    index: SnapshotIndex, ancestor_id: str, descendant_id: str
) -> bool:
    node = index.by_id.get(descendant_id)
    if node is None:
        return False
    return ancestor_id in node.ancestor_ids


def diversify_top_k(
    candidates: Sequence[CategoryCandidate],
    *,
    index: SnapshotIndex,
    policy: SemanticMatchPolicy,
    k: Optional[int] = None,
) -> DiversificationOutcome:
    """Select up to ``k`` diversified candidates from a scored pool.

    Input should already be post-negative / post-hierarchy and sorted by
    descending score. Output preserves relative order except for mild
    parent demotion and signal-preferring slot fills.
    """

    limit = k if k is not None else policy.maximum_candidates
    limit = max(1, int(limit))
    if not candidates:
        return DiversificationOutcome(candidates=())

    working = list(candidates)
    notes: list[str] = []

    # 1) Mild parent demotion when a child is also present and parent
    #    primarily won via direct alias while the child has a positive channel.
    if getattr(policy, "diversification_enabled", True):
        working = _demote_crowding_parents(
            working, index=index, policy=policy, notes=notes
        )

    # 2) Slot fill: prefer positive-channel candidates for ranks 2..k when
    #    the next pure-vector candidate would otherwise occupy the slot.
    selected = _select_with_signal_preference(
        working, limit=limit, policy=policy, index=index, notes=notes
    )

    # Re-rank.
    ranked = tuple(
        CategoryCandidate(
            category_id=c.category_id,
            slug=c.slug,
            display_name=c.display_name,
            score=c.score,
            rank=i + 1,
            signals=c.signals,
            matchable=c.matchable,
        )
        for i, c in enumerate(selected)
    )
    return DiversificationOutcome(
        candidates=ranked, diversity_notes=tuple(notes)
    )


def _demote_crowding_parents(
    candidates: list[CategoryCandidate],
    *,
    index: SnapshotIndex,
    policy: SemanticMatchPolicy,
    notes: list[str],
) -> list[CategoryCandidate]:
    """Soft-demote a parent when at least one of its descendants is a
    viable candidate (score >= minimum_candidate_score * 0.5) so the
    child can surface into Top-2 without global threshold changes.

    Never removes the parent — a parent with genuinely stronger score
    survives. Only re-sorts by demoted score.
    """

    penalty = float(getattr(policy, "same_parent_penalty", 0.06) or 0.06)
    viable_floor = float(
        max(0.0, policy.minimum_candidate_score * 0.5)
    )
    adjusted: list[CategoryCandidate] = []
    for cand in candidates:
        viable_child = any(
            _is_ancestor_of(index, cand.category_id, other.category_id)
            and other.score >= viable_floor
            for other in candidates
            if other.category_id != cand.category_id
        )
        # Original heuristic (unchanged): demote a direct-alias parent so
        # a close child can enter Top-2.
        direct_alias_parent = (
            viable_child
            and cand.signals.direct_alias_match
            and cand.rank == 1
        )
        # ADR-008 P0.1: also demote when the parent is at rank 1 without
        # direct_alias_match and any viable child exists — parent nodes
        # like "Bilgisayar" that beat "Taşınabilir Bilgisayar" on soft
        # channels should still lose Top-1 to the more specific child.
        crowds_child = (
            viable_child
            and cand.rank == 1
            and getattr(policy, "diversification_enabled", True)
        )
        if direct_alias_parent or crowds_child:
            new_score = max(0.0, cand.score - penalty)
            adjusted.append(
                CategoryCandidate(
                    category_id=cand.category_id,
                    slug=cand.slug,
                    display_name=cand.display_name,
                    score=new_score,
                    rank=cand.rank,
                    signals=cand.signals,
                    matchable=cand.matchable,
                )
            )
            notes.append(
                f"parent_demoted:{cand.category_id}:penalty={penalty:.3f}"
            )
        else:
            adjusted.append(cand)
    adjusted.sort(key=lambda c: c.score, reverse=True)
    return adjusted


def _select_with_signal_preference(
    candidates: list[CategoryCandidate],
    *,
    limit: int,
    policy: SemanticMatchPolicy,
    index: SnapshotIndex,
    notes: list[str],
) -> list[CategoryCandidate]:
    if len(candidates) <= limit:
        return list(candidates)

    selected: list[CategoryCandidate] = []
    remaining = list(candidates)

    # Always take the current top as Top-1 (safety / auto-select stability).
    selected.append(remaining.pop(0))

    while remaining and len(selected) < limit:
        # Prefer next candidate with a positive channel over pure vector.
        pick_idx = 0
        if getattr(policy, "prefer_positive_channel_in_topk", True):
            for idx, cand in enumerate(remaining):
                if _has_positive_channel(cand.signals):
                    pick_idx = idx
                    break
            else:
                pick_idx = 0
            if pick_idx > 0:
                notes.append(
                    f"signal_prefer:{remaining[pick_idx].category_id}"
                    f":over={remaining[0].category_id}"
                )
        # Sibling diversity: prefer an alternate-parent candidate with a
        # positive channel over stacking two siblings of the already
        # selected top. Uses SnapshotIndex.by_id — no display heuristics.
        if (
            len(selected) >= 1
            and getattr(policy, "sibling_diversity_enabled", True)
        ):
            selected_parents = {
                _parent_id(index, sel.category_id) for sel in selected
            }
            selected_parents.discard(None)
            candidate_parent = _parent_id(index, remaining[pick_idx].category_id)
            if candidate_parent and candidate_parent in selected_parents:
                for alt_idx, alt in enumerate(remaining):
                    if alt_idx == pick_idx:
                        continue
                    if not _has_positive_channel(alt.signals):
                        continue
                    alt_parent = _parent_id(index, alt.category_id)
                    if alt_parent and alt_parent in selected_parents:
                        continue
                    pick_idx = alt_idx
                    notes.append(
                        f"sibling_diverse:{alt.category_id}"
                        f":over={remaining[0].category_id}"
                    )
                    break
        pick = remaining.pop(pick_idx)
        selected.append(pick)

    return selected


__all__ = ["DiversificationOutcome", "diversify_top_k"]
