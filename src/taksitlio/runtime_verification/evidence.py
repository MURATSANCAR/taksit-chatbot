"""Runtime measurement evidence required for PROVISIONAL_ACCEPT (ADR-009)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RuntimeEvidence:
    """Flags proving real runtime paths were exercised — not test doubles.

    LexicalFallback / DeterministicFastExtractor / in-memory repositories must
    never set these flags to True.
    """

    real_redis_measured: bool = False
    real_pgvector_measured: bool = False
    real_fast_measured: bool = False
    real_embedding_measured: bool = False

    redis_integration_skipped: int = 0
    pgvector_integration_skipped: int = 0

    human_reviewed_count: int = 0

    # Quality — populated from real-runtime evaluation reports.
    oracle_top_1: Optional[float] = None
    oracle_top_2: Optional[float] = None
    oracle_required: Optional[float] = None
    oracle_forbidden: int = 0
    oracle_unsafe: int = 0

    e2e_status: Optional[float] = None
    e2e_top_1: Optional[float] = None
    e2e_top_2: Optional[float] = None
    e2e_required: Optional[float] = None
    e2e_forbidden: int = 0
    e2e_unsafe: int = 0

    fast_invalid_schema_count: Optional[int] = None
    fast_forbidden_identifier_count: Optional[int] = None
    fast_negative_constraint_recall: Optional[float] = None
    fast_correction_recall: Optional[float] = None

    # Optional latency / hardware metadata (no raw utterances).
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def all_runtime_measured(self) -> bool:
        return (
            self.real_redis_measured
            and self.real_pgvector_measured
            and self.real_fast_measured
            and self.real_embedding_measured
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "real_redis_measured": self.real_redis_measured,
            "real_pgvector_measured": self.real_pgvector_measured,
            "real_fast_measured": self.real_fast_measured,
            "real_embedding_measured": self.real_embedding_measured,
            "redis_integration_skipped": self.redis_integration_skipped,
            "pgvector_integration_skipped": self.pgvector_integration_skipped,
            "human_reviewed_count": self.human_reviewed_count,
            "oracle_top_1": self.oracle_top_1,
            "oracle_top_2": self.oracle_top_2,
            "oracle_required": self.oracle_required,
            "oracle_forbidden": self.oracle_forbidden,
            "oracle_unsafe": self.oracle_unsafe,
            "e2e_status": self.e2e_status,
            "e2e_top_1": self.e2e_top_1,
            "e2e_top_2": self.e2e_top_2,
            "e2e_required": self.e2e_required,
            "e2e_forbidden": self.e2e_forbidden,
            "e2e_unsafe": self.e2e_unsafe,
            "fast_invalid_schema_count": self.fast_invalid_schema_count,
            "fast_forbidden_identifier_count": self.fast_forbidden_identifier_count,
            "fast_negative_constraint_recall": self.fast_negative_constraint_recall,
            "fast_correction_recall": self.fast_correction_recall,
            "metadata": dict(self.metadata),
        }
