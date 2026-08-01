"""P3.4 TraceRecorder — named full-path spans for INTERNAL verification."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class SpanRecord:
    name: str
    duration_ms: float
    status: str = "OK"
    error_code: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecorder:
    trace_id: str
    search_session_id: Optional[str] = None
    query_version: Optional[int] = None
    cohort_id: Optional[int] = None
    cohort_version: Optional[int] = None
    catalog_revision: Optional[str] = None
    entity_index_revision: Optional[str] = None
    taxonomy_revision: Optional[str] = None
    media_policy_version: Optional[str] = None
    finance_revision: Optional[str] = None
    ranking_policy_version: Optional[str] = None
    spans: list[SpanRecord] = field(default_factory=list)

    def _base_attrs(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "search_session_id": self.search_session_id,
            "query_version": self.query_version,
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "catalog_revision": self.catalog_revision,
            "entity_index_revision": self.entity_index_revision,
            "taxonomy_revision": self.taxonomy_revision,
            "media_policy_version": self.media_policy_version,
            "finance_revision": self.finance_revision,
            "ranking_policy_version": self.ranking_policy_version,
        }

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        bag: dict[str, Any] = dict(attrs)
        status = "OK"
        error_code: Optional[str] = None
        try:
            yield bag
        except Exception as exc:  # noqa: BLE001
            status = "ERROR"
            error_code = type(exc).__name__
            raise
        finally:
            dur = (time.perf_counter() - started) * 1000.0
            merged = {**self._base_attrs(), **bag}
            self.spans.append(
                SpanRecord(
                    name=name,
                    duration_ms=round(dur, 3),
                    status=status,
                    error_code=error_code or bag.get("error_code"),
                    attrs=merged,
                )
            )

    def span_ms(self, *names: str) -> float:
        want = set(names)
        return sum(s.duration_ms for s in self.spans if s.name in want)

    def ranking_span_ms(self) -> float:
        return self.span_ms("ranking.score", "ranking.select_topk")

    def missing_required(self, required: tuple[str, ...] | None = None) -> list[str]:
        names = {s.name for s in self.spans}
        req = required or REQUIRED_SPAN_NAMES
        return [n for n in req if n not in names]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._base_attrs(),
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "error_code": s.error_code,
                    "candidate_count": s.attrs.get("candidate_count"),
                    "filtered_candidate_count": s.attrs.get("filtered_candidate_count"),
                    "result_count": s.attrs.get("result_count"),
                }
                for s in self.spans
            ],
            "ranking_span_ms": self.ranking_span_ms(),
            "missing_required_spans": self.missing_required(),
        }


# P3.4 required chain (subset always expected on completed fast-path).
REQUIRED_SPAN_NAMES = (
    "search.http",
    "search.authorization",
    "search.cohort.resolve",
    "search.session",
    "query.parse",
    "entity.resolve",
    "product.retrieve",
    "constraint.filter",
    "ranking.score",
    "ranking.select_topk",
    "response.compose",
    "response.serialize",
)

OPTIONAL_SPAN_NAMES = (
    "query.normalize",
    "query.gap_analyze",
    "clarification.plan",
    "finance.lookup",
    "feature.materialize",
    "ranking.reason_codes",
    "claim.validate",
    "sse.publish",
)


__all__ = [
    "OPTIONAL_SPAN_NAMES",
    "REQUIRED_SPAN_NAMES",
    "SpanRecord",
    "TraceRecorder",
]
