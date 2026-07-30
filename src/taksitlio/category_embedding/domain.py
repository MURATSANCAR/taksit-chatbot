"""Domain models for category embedding projection + async jobs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

CATEGORY_PROJECTION_KIND = "category-projection.v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class EmbeddingJobStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"


@dataclass(frozen=True)
class SemanticDocument:
    """Text projection sent to the embedding model."""

    kind: str
    category_id: str
    catalog_revision: int
    locale: str
    projection_text: str
    content_hash: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        category_id: str,
        catalog_revision: int,
        locale: str,
        projection_text: str,
        metadata: Optional[dict] = None,
    ) -> "SemanticDocument":
        return cls(
            kind=CATEGORY_PROJECTION_KIND,
            category_id=category_id,
            catalog_revision=catalog_revision,
            locale=locale,
            projection_text=projection_text,
            content_hash=hashlib.sha256(projection_text.encode("utf-8")).hexdigest(),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class CategoryEmbeddingRecord:
    id: str
    category_id: str
    catalog_revision: int
    locale: str
    embedding_profile_id: str
    content_hash: str
    embedding: tuple[float, ...]
    embedding_dimension: int
    projection_text: str
    status: EmbeddingJobStatus = EmbeddingJobStatus.READY
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class CategoryEmbeddingJob:
    id: str
    category_id: str
    catalog_revision: int
    locale: str
    embedding_profile_id: str
    content_hash: str
    projection_text: str
    status: EmbeddingJobStatus = EmbeddingJobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 5
    last_error: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @property
    def dedupe_key(self) -> tuple[str, int, str, str, str]:
        return (
            self.category_id,
            self.catalog_revision,
            self.locale,
            self.embedding_profile_id,
            self.content_hash,
        )


__all__ = [
    "CATEGORY_PROJECTION_KIND",
    "CategoryEmbeddingJob",
    "CategoryEmbeddingRecord",
    "EmbeddingJobStatus",
    "SemanticDocument",
    "new_id",
]
