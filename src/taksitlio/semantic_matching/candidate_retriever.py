"""Snapshot retrieval for the matcher.

The retriever asks the CategoryCatalogService for the currently published
snapshot; matcher logic never touches drafts.
"""

from __future__ import annotations

from typing import Optional, Protocol

from taksitlio.category_catalog.domain import CategorySnapshot


class SnapshotProvider(Protocol):
    async def get_published_snapshot(
        self,
        catalog_id: str,
        *,
        locale: Optional[str] = None,
    ) -> Optional[CategorySnapshot]: ...


__all__ = ["SnapshotProvider"]
