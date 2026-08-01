"""Lightweight named search-path spans for P3.3 full-path verification.

Not a full OpenTelemetry SDK; records structured span timings that can be
exported alongside HTTP traces until OTel is wired.
"""

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
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecorder:
    trace_id: str
    search_session_id: Optional[str] = None
    query_version: Optional[int] = None
    cohort_id: Optional[int] = None
    catalog_revision: Optional[str] = None
    ranking_policy_version: Optional[str] = None
    spans: list[SpanRecord] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        bag: dict[str, Any] = dict(attrs)
        status = "OK"
        try:
            yield bag
        except Exception:
            status = "ERROR"
            raise
        finally:
            dur = (time.perf_counter() - started) * 1000.0
            self.spans.append(
                SpanRecord(
                    name=name,
                    duration_ms=round(dur, 3),
                    status=status,
                    attrs={
                        "trace_id": self.trace_id,
                        "search_session_id": self.search_session_id,
                        "query_version": self.query_version,
                        "cohort_id": self.cohort_id,
                        "catalog_revision": self.catalog_revision,
                        "ranking_policy_version": self.ranking_policy_version,
                        "candidate_count": bag.get("candidate_count"),
                        "result_count": bag.get("result_count"),
                        **{k: v for k, v in bag.items() if k not in {"candidate_count", "result_count"}},
                    },
                )
            )

    def ranking_span_ms(self) -> float:
        return sum(
            s.duration_ms
            for s in self.spans
            if s.name in {"ranking.score", "ranking.select_topk"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "search_session_id": self.search_session_id,
            "query_version": self.query_version,
            "cohort_id": self.cohort_id,
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attrs": s.attrs,
                }
                for s in self.spans
            ],
            "ranking_span_ms": self.ranking_span_ms(),
        }


REQUIRED_SPAN_NAMES = (
    "search.http",
    "search.session",
    "query.parse",
    "entity.resolve",
    "product.retrieve",
    "constraint.filter",
    "finance.lookup",
    "feature.materialize",
    "ranking.score",
    "ranking.select_topk",
    "ranking.reason_codes",
    "response.compose",
    "response.serialize",
)


__all__ = ["REQUIRED_SPAN_NAMES", "SpanRecord", "TraceRecorder"]
