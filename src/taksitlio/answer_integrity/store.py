"""Postgres writers for feedback / shadow / error-class events (ADR-012)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional, Sequence

from taksitlio.recommendation_safety.feedback import (
    FORBIDDEN_ERROR_BUCKET,
    FeedbackResultSnapshot,
    ShadowComparison,
    compare_shadow,
)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


class AnswerIntegrityStore:
    """Persists feedback snapshots, shadow comparisons, error-class events."""

    def __init__(self, pool: Any | None = None) -> None:
        self._pool = pool
        self.feedback: list[dict[str, Any]] = []
        self.shadows: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []

    async def save_feedback(
        self,
        snapshot: FeedbackResultSnapshot,
        *,
        feedback_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if snapshot.error_class and snapshot.error_class.value == FORBIDDEN_ERROR_BUCKET:
            raise ValueError("WRONG_ANSWER error bucket is forbidden")
        fid = feedback_id or str(uuid.uuid4())
        row = {
            "feedback_id": fid,
            **snapshot.to_dict(),
        }
        self.feedback.append(row)
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO feedback_result_snapshots (
                        feedback_id, query_version, parsed_constraints, catalog_revision,
                        price_snapshot, campaign_snapshot, selected_product, selected_bank,
                        response_fact_ids, error_class, user_note
                    ) VALUES (
                        $1::uuid,$2,$3::jsonb,$4,$5,$6,$7,$8,$9::text[],$10,$11
                    )
                    """,
                    fid,
                    snapshot.query_version,
                    _json(snapshot.parsed_constraints),
                    snapshot.catalog_revision,
                    snapshot.price_snapshot,
                    snapshot.campaign_snapshot,
                    snapshot.selected_product,
                    snapshot.selected_bank,
                    list(snapshot.response_fact_ids),
                    None if snapshot.error_class is None else snapshot.error_class.value,
                    snapshot.user_note,
                )
        return row

    async def save_shadow(
        self,
        comparison: ShadowComparison,
        *,
        comparison_key: str,
    ) -> dict[str, Any]:
        row = {
            "comparison_key": comparison_key,
            "live_payload": dict(comparison.live_payload),
            "shadow_payload": dict(comparison.shadow_payload),
            "diffs": list(comparison.diffs),
            "shown_to_user": bool(comparison.shown_to_user),
        }
        self.shadows.append(row)
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO shadow_mode_comparisons (
                        comparison_key, live_payload, shadow_payload, diffs, shown_to_user
                    ) VALUES ($1,$2::jsonb,$3::jsonb,$4::text[],$5)
                    """,
                    comparison_key,
                    _json(comparison.live_payload),
                    _json(comparison.shadow_payload),
                    list(comparison.diffs),
                    bool(comparison.shown_to_user),
                )
        return row

    async def record_error_class(
        self,
        error_class: str,
        *,
        source_component: Optional[str] = None,
        metric_key: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        if error_class == FORBIDDEN_ERROR_BUCKET:
            raise ValueError("WRONG_ANSWER error bucket is forbidden")
        row = {
            "error_class": error_class,
            "source_component": source_component,
            "metric_key": metric_key,
            "payload": dict(payload or {}),
        }
        self.errors.append(row)
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO error_class_events (
                        error_class, source_component, metric_key, payload
                    ) VALUES ($1,$2,$3,$4::jsonb)
                    """,
                    error_class,
                    source_component,
                    metric_key,
                    _json(payload or {}),
                )
        return row

    async def error_class_summary(self, *, limit: int = 200) -> dict[str, Any]:
        counts: dict[str, int] = {}
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT error_class, COUNT(*) AS n
                    FROM error_class_events
                    GROUP BY error_class
                    ORDER BY n DESC
                    LIMIT $1
                    """,
                    limit,
                )
            for r in rows:
                counts[str(r["error_class"])] = int(r["n"])
        else:
            for e in self.errors:
                key = str(e["error_class"])
                counts[key] = counts.get(key, 0) + 1
        return {"counts": counts, "total": sum(counts.values())}


def build_shadow_comparison(
    live: Mapping[str, Any],
    shadow: Mapping[str, Any],
    *,
    keys: Sequence[str] = ("product_ids", "ranking_labels", "monthly_payments"),
) -> ShadowComparison:
    return compare_shadow(live, shadow, keys=keys)


__all__ = [
    "AnswerIntegrityStore",
    "build_shadow_comparison",
]
