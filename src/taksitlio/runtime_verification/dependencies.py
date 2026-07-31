"""Typed runtime dependency codes and probe result records (ADR-009)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class DependencyCode(str, Enum):
    REDIS_UNAVAILABLE = "REDIS_UNAVAILABLE"
    POSTGRES_UNAVAILABLE = "POSTGRES_UNAVAILABLE"
    PGVECTOR_EXTENSION_UNAVAILABLE = "PGVECTOR_EXTENSION_UNAVAILABLE"
    FAST_DEPLOYMENT_UNAVAILABLE = "FAST_DEPLOYMENT_UNAVAILABLE"
    EMBEDDING_DEPLOYMENT_UNAVAILABLE = "EMBEDDING_DEPLOYMENT_UNAVAILABLE"
    FAST_RUNTIME_UNHEALTHY = "FAST_RUNTIME_UNHEALTHY"
    EMBEDDING_RUNTIME_UNHEALTHY = "EMBEDDING_RUNTIME_UNHEALTHY"


@dataclass(frozen=True)
class DependencyProbeResult:
    """Single dependency probe outcome — never a silent success."""

    code: Optional[DependencyCode]
    available: bool
    measured: bool
    detail: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": None if self.code is None else self.code.value,
            "available": self.available,
            "measured": self.measured,
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }


@dataclass
class RuntimeDependencyReport:
    redis: DependencyProbeResult
    postgres: DependencyProbeResult
    pgvector: DependencyProbeResult
    fast: DependencyProbeResult
    embedding: DependencyProbeResult

    @property
    def blockers(self) -> list[DependencyCode]:
        out: list[DependencyCode] = []
        seen: set[DependencyCode] = set()
        for probe in (
            self.redis,
            self.postgres,
            self.pgvector,
            self.fast,
            self.embedding,
        ):
            if probe.code is not None and not probe.available and probe.code not in seen:
                out.append(probe.code)
                seen.add(probe.code)
        return out

    @property
    def all_available(self) -> bool:
        return all(
            p.available
            for p in (
                self.redis,
                self.postgres,
                self.pgvector,
                self.fast,
                self.embedding,
            )
        )

    @property
    def all_measured(self) -> bool:
        return all(
            p.measured
            for p in (
                self.redis,
                self.postgres,
                self.pgvector,
                self.fast,
                self.embedding,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "redis": self.redis.to_dict(),
            "postgres": self.postgres.to_dict(),
            "pgvector": self.pgvector.to_dict(),
            "fast": self.fast.to_dict(),
            "embedding": self.embedding.to_dict(),
            "blockers": [c.value for c in self.blockers],
            "all_available": self.all_available,
            "all_measured": self.all_measured,
        }
