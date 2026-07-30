"""In-memory repository for category embeddings + jobs."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from typing import Optional, Sequence

from taksitlio.category_embedding.domain import (
    CategoryEmbeddingJob,
    CategoryEmbeddingRecord,
    EmbeddingJobStatus,
)


class InMemoryCategoryEmbeddingRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, CategoryEmbeddingJob] = {}
        self._jobs_by_key: dict[tuple, str] = {}
        self._embeddings: dict[str, CategoryEmbeddingRecord] = {}
        self._embeddings_by_key: dict[tuple, str] = {}
        self._lock = asyncio.Lock()

    async def enqueue_job(self, job: CategoryEmbeddingJob) -> CategoryEmbeddingJob:
        async with self._lock:
            existing_id = self._jobs_by_key.get(job.dedupe_key)
            if existing_id:
                return self._jobs[existing_id]
            self._jobs[job.id] = job
            self._jobs_by_key[job.dedupe_key] = job.id
            return job

    async def get_job(self, job_id: str) -> Optional[CategoryEmbeddingJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_pending_jobs(self, limit: int = 32) -> list[CategoryEmbeddingJob]:
        async with self._lock:
            pending = [
                j for j in self._jobs.values() if j.status == EmbeddingJobStatus.PENDING
            ]
            pending.sort(key=lambda j: j.created_at)
            return pending[:limit]

    async def update_job_status(
        self,
        job_id: str,
        *,
        status: EmbeddingJobStatus,
        last_error: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None:
        async with self._lock:
            existing = self._jobs.get(job_id)
            if existing is None:
                return
            self._jobs[job_id] = replace(
                existing,
                status=status,
                last_error=last_error if last_error is not None else existing.last_error,
                attempts=attempts if attempts is not None else existing.attempts,
            )

    async def store_embedding(
        self,
        record: CategoryEmbeddingRecord,
    ) -> CategoryEmbeddingRecord:
        async with self._lock:
            key = (
                record.category_id,
                record.catalog_revision,
                record.locale,
                record.embedding_profile_id,
                record.content_hash,
            )
            self._embeddings[record.id] = record
            self._embeddings_by_key[key] = record.id
            return record

    async def mark_previous_stale(
        self,
        *,
        category_id: str,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
        exclude_content_hash: str,
    ) -> None:
        async with self._lock:
            for rec_id, rec in list(self._embeddings.items()):
                if (
                    rec.category_id == category_id
                    and rec.catalog_revision == catalog_revision
                    and rec.locale == locale
                    and rec.embedding_profile_id == embedding_profile_id
                    and rec.content_hash != exclude_content_hash
                    and rec.status == EmbeddingJobStatus.READY
                ):
                    self._embeddings[rec_id] = replace(
                        rec, status=EmbeddingJobStatus.STALE
                    )

    async def get_embedding(
        self,
        *,
        category_id: str,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
    ) -> Optional[CategoryEmbeddingRecord]:
        async with self._lock:
            latest: Optional[CategoryEmbeddingRecord] = None
            for rec in self._embeddings.values():
                if (
                    rec.category_id == category_id
                    and rec.catalog_revision == catalog_revision
                    and rec.locale == locale
                    and rec.embedding_profile_id == embedding_profile_id
                    and rec.status == EmbeddingJobStatus.READY
                ):
                    if latest is None or rec.updated_at > latest.updated_at:
                        latest = rec
            return latest

    async def list_ready(
        self,
        *,
        catalog_revision: int,
        locale: str,
        embedding_profile_id: str,
    ) -> Sequence[CategoryEmbeddingRecord]:
        async with self._lock:
            return [
                rec
                for rec in self._embeddings.values()
                if rec.catalog_revision == catalog_revision
                and rec.locale == locale
                and rec.embedding_profile_id == embedding_profile_id
                and rec.status == EmbeddingJobStatus.READY
            ]

    async def dump_state(self) -> dict:
        async with self._lock:
            return {
                "jobs": deepcopy(self._jobs),
                "embeddings": deepcopy(self._embeddings),
            }


__all__ = ["InMemoryCategoryEmbeddingRepository"]
