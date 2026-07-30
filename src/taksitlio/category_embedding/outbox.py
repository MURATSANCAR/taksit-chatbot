"""Outbox helper: enqueue embedding jobs from a catalog snapshot.

The outbox does not talk to the embedding server; it only decides which
categories need a fresh embedding for the new revision and hands them to the
repository. Dedupe is enforced by the repository via the content-hash key.
"""

from __future__ import annotations

from typing import Iterable

from taksitlio.category_catalog.domain import CategorySnapshot
from taksitlio.category_embedding.domain import (
    CategoryEmbeddingJob,
    EmbeddingJobStatus,
    SemanticDocument,
    new_id,
)
from taksitlio.category_embedding.in_memory_repository import (
    InMemoryCategoryEmbeddingRepository,
)
from taksitlio.category_embedding.projector import CategorySemanticProjector


class CategoryEmbeddingOutbox:
    def __init__(
        self,
        repository: InMemoryCategoryEmbeddingRepository,
        *,
        projector: CategorySemanticProjector | None = None,
    ) -> None:
        self._repo = repository
        self._projector = projector or CategorySemanticProjector()

    async def enqueue_for_snapshot(
        self,
        snapshot: CategorySnapshot,
        *,
        embedding_profile_id: str,
        max_attempts: int = 5,
    ) -> list[CategoryEmbeddingJob]:
        jobs: list[CategoryEmbeddingJob] = []
        for node in snapshot.nodes:
            document: SemanticDocument = self._projector.project(
                node, catalog_revision=snapshot.revision
            )
            job = CategoryEmbeddingJob(
                id=new_id(),
                category_id=document.category_id,
                catalog_revision=document.catalog_revision,
                locale=document.locale,
                embedding_profile_id=embedding_profile_id,
                content_hash=document.content_hash,
                projection_text=document.projection_text,
                status=EmbeddingJobStatus.PENDING,
                max_attempts=max_attempts,
            )
            stored = await self._repo.enqueue_job(job)
            jobs.append(stored)
        return jobs


__all__ = ["CategoryEmbeddingOutbox"]
