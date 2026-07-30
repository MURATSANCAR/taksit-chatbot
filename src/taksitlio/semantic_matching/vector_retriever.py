"""Vector retriever for the semantic matcher.

Fetches ready embedding records for a snapshot's revision + locale + profile
and computes cosine similarity against a query vector. When the embedding
gateway is unavailable, the caller runs the matcher in degraded mode.
"""

from __future__ import annotations

from typing import Optional

from taksitlio.category_catalog.domain import CategorySnapshot
from taksitlio.category_embedding.domain import (
    CategoryEmbeddingRecord,
    EmbeddingJobStatus,
)
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.embeddings.vectors import cosine_similarity


class VectorRetriever:
    def __init__(self, repository: InMemoryCategoryEmbeddingRepository) -> None:
        self._repo = repository

    async def load_embeddings(
        self,
        *,
        snapshot: CategorySnapshot,
        embedding_profile_id: str,
    ) -> dict[str, CategoryEmbeddingRecord]:
        records = await self._repo.list_ready(
            catalog_revision=snapshot.revision,
            locale=snapshot.locale,
            embedding_profile_id=embedding_profile_id,
        )
        return {
            rec.category_id: rec
            for rec in records
            if rec.status == EmbeddingJobStatus.READY
        }

    def cosine(
        self,
        query_vec: list[float],
        record: Optional[CategoryEmbeddingRecord],
    ) -> float:
        if record is None or not query_vec:
            return 0.0
        return max(0.0, cosine_similarity(query_vec, list(record.embedding)))


__all__ = ["VectorRetriever"]
