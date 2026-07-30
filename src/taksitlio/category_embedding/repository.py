"""Repository protocol for category embedding jobs and records."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

from taksitlio.category_embedding.domain import (
    CategoryEmbeddingJob,
    CategoryEmbeddingRecord,
    EmbeddingJobStatus,
)


class CategoryEmbeddingRepository(Protocol):
    async def enqueue_job(self, job: CategoryEmbeddingJob) -> CategoryEmbeddingJob: ...

    async def get_job(self, job_id: str) -> Optional[CategoryEmbeddingJob]: ...

    async def list_pending_jobs(self, limit: int = 32) -> list[CategoryEmbeddingJob]: ...

    async def update_job_status(
        self,
        job_id: str,
        *,
        status: EmbeddingJobStatus,
        last_error: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None: ...

    async def store_embedding(
        self,
        record: CategoryEmbeddingRecord,
    ) -> CategoryEmbeddingRecord: ...

    async def mark_previous_stale(
        self,
        *,
        category_id: str,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
        exclude_content_hash: str,
    ) -> None: ...

    async def get_embedding(
        self,
        *,
        category_id: str,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
    ) -> Optional[CategoryEmbeddingRecord]: ...

    async def list_ready(
        self,
        *,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
    ) -> Sequence[CategoryEmbeddingRecord]: ...


__all__ = ["CategoryEmbeddingRepository"]
