"""Embedding readiness gate for two-stage catalog publish."""

from __future__ import annotations

from typing import Protocol, Sequence

from taksitlio.category_catalog.domain import CategorySnapshot
from taksitlio.category_embedding.domain import EmbeddingJobStatus
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)


class EmbeddingReadinessChecker(Protocol):
    async def missing_category_ids(
        self,
        snapshot: CategorySnapshot,
        *,
        embedding_profile_id: str,
    ) -> list[str]:
        """Return category_ids that lack a READY embedding for the snapshot revision."""
        ...


class AlwaysReadyEmbeddingChecker:
    """Test/dev gate when embeddings are intentionally skipped."""

    async def missing_category_ids(
        self,
        snapshot: CategorySnapshot,
        *,
        embedding_profile_id: str,
    ) -> list[str]:
        return []


class RepositoryEmbeddingReadinessChecker:
    def __init__(self, repository: InMemoryCategoryEmbeddingRepository) -> None:
        self._repo = repository

    async def missing_category_ids(
        self,
        snapshot: CategorySnapshot,
        *,
        embedding_profile_id: str,
    ) -> list[str]:
        ready = await self._repo.list_ready(
            catalog_revision=snapshot.revision,
            locale=snapshot.locale,
            embedding_profile_id=embedding_profile_id,
        )
        ready_ids = {r.category_id for r in ready if r.status == EmbeddingJobStatus.READY}
        return [n.id for n in snapshot.nodes if n.id not in ready_ids]


__all__ = [
    "AlwaysReadyEmbeddingChecker",
    "EmbeddingReadinessChecker",
    "RepositoryEmbeddingReadinessChecker",
]
