"""In-memory index used by the matcher for fast lookups.

The index is rebuilt from the current published snapshot on each match call
(cheap for MVP scale). A future optimization can memoize it per revision.
"""

from __future__ import annotations

from dataclasses import dataclass

from taksitlio.category_catalog.domain import CategorySnapshot, CategorySnapshotNode


@dataclass(frozen=True)
class SnapshotIndex:
    snapshot: CategorySnapshot
    by_id: dict[str, CategorySnapshotNode]

    @classmethod
    def build(cls, snapshot: CategorySnapshot) -> "SnapshotIndex":
        return cls(
            snapshot=snapshot,
            by_id={node.id: node for node in snapshot.nodes},
        )


__all__ = ["SnapshotIndex"]
