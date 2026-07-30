"""Parent-child ambiguity collapse (ADR-006 F).

When the matcher returns two candidates that share an ancestor/descendant
relationship *and* their scores are within a configured gap, we collapse
the pair to the more specific candidate. Siblings are never collapsed —
that would silently hide legitimately ambiguous cases.

The helper is pure over the SnapshotIndex; it uses
``CategorySnapshotNode.ancestor_ids`` to decide relationships and never
inspects business content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from taksitlio.category_catalog.domain import CategorySnapshotNode
from taksitlio.semantic_matching.domain import CategoryCandidate, SemanticMatchPolicy
from taksitlio.semantic_matching.in_memory_index import SnapshotIndex


@dataclass(frozen=True)
class CollapseOutcome:
    candidates: tuple[CategoryCandidate, ...]
    collapsed_pairs: tuple[tuple[str, str], ...]  # (kept_id, dropped_id)


def _is_ancestor(
    ancestor_node: CategorySnapshotNode,
    descendant_node: CategorySnapshotNode,
) -> bool:
    return ancestor_node.id in descendant_node.ancestor_ids


def _pick_more_specific(
    a: CategoryCandidate,
    b: CategoryCandidate,
    *,
    index: SnapshotIndex,
) -> Optional[tuple[CategoryCandidate, CategoryCandidate]]:
    """Return (kept, dropped) when ``a`` and ``b`` are parent-child.

    ``None`` means the pair is a sibling or unrelated pair — do not
    collapse.
    """

    node_a = index.by_id.get(a.category_id)
    node_b = index.by_id.get(b.category_id)
    if node_a is None or node_b is None:
        return None
    if _is_ancestor(node_a, node_b):
        # b is descendant of a → keep b (more specific).
        return b, a
    if _is_ancestor(node_b, node_a):
        return a, b
    return None


def collapse_parent_child(
    candidates: Sequence[CategoryCandidate],
    *,
    index: SnapshotIndex,
    policy: SemanticMatchPolicy,
) -> CollapseOutcome:
    """Collapse parent-child pairs that fall within the policy gap.

    Iterates until no more collapses happen so a chain
    (grandparent → parent → child) reduces to the leaf when all three
    scores are within the gap.
    """

    if not policy.parent_child_collapse_enabled or len(candidates) < 2:
        return CollapseOutcome(
            candidates=tuple(candidates), collapsed_pairs=()
        )

    working = list(candidates)
    collapsed_pairs: list[tuple[str, str]] = []

    changed = True
    while changed and len(working) >= 2:
        changed = False
        for i in range(len(working)):
            for j in range(i + 1, len(working)):
                cand_i = working[i]
                cand_j = working[j]
                gap = abs(cand_i.score - cand_j.score)
                if gap > policy.parent_child_collapse_gap:
                    continue
                pick = _pick_more_specific(cand_i, cand_j, index=index)
                if pick is None:
                    continue
                kept, dropped = pick
                collapsed_pairs.append((kept.category_id, dropped.category_id))
                # Mark the surviving candidate as collapsed for observability.
                kept = _flag_collapsed(kept)
                # Remove the dropped candidate; keep kept in place.
                if dropped is cand_i:
                    working[i] = kept
                    del working[j]
                else:
                    working[i] = kept
                    del working[j]
                changed = True
                break
            if changed:
                break

    working.sort(key=lambda c: c.score, reverse=True)
    return CollapseOutcome(
        candidates=tuple(
            _renumber(c, rank + 1) for rank, c in enumerate(working)
        ),
        collapsed_pairs=tuple(collapsed_pairs),
    )


def _flag_collapsed(cand: CategoryCandidate) -> CategoryCandidate:
    signals = cand.signals
    from dataclasses import replace

    new_signals = replace(signals, hierarchy_collapsed=True)
    return CategoryCandidate(
        category_id=cand.category_id,
        slug=cand.slug,
        display_name=cand.display_name,
        score=cand.score,
        rank=cand.rank,
        signals=new_signals,
        matchable=cand.matchable,
    )


def _renumber(cand: CategoryCandidate, rank: int) -> CategoryCandidate:
    return CategoryCandidate(
        category_id=cand.category_id,
        slug=cand.slug,
        display_name=cand.display_name,
        score=cand.score,
        rank=rank,
        signals=cand.signals,
        matchable=cand.matchable,
    )


__all__ = ["CollapseOutcome", "collapse_parent_child"]
