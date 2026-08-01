"""Consume PENDING catalog_domain_events idempotently (at-least-once)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_pending_events(
    conn: Any,
    *,
    limit: int = 500,
    catalog_revision: Optional[str] = None,
) -> dict[str, Any]:
    """Drain PENDING/FAILED events; mark DONE or FAILED. Idempotent per event_id."""

    rows = await conn.fetch(
        """
        SELECT event_id, event_type, merchant_id, product_id, offer_id,
               payload, catalog_revision, attempt
        FROM catalog_domain_events
        WHERE processing_status IN ('PENDING', 'FAILED')
        ORDER BY received_at
        LIMIT $1
        FOR UPDATE SKIP LOCKED
        """,
        limit,
    )
    completed = 0
    failed = 0
    retried = 0
    readiness_merchants: set[int] = set()
    traces: list[dict[str, Any]] = []

    for row in rows:
        eid = row["event_id"]
        attempt = int(row["attempt"] or 0) + 1
        retried += 1 if attempt > 1 else 0
        try:
            await conn.execute(
                """
                UPDATE catalog_domain_events
                   SET processing_status='PROCESSING', attempt=$2
                 WHERE event_id=$1
                """,
                eid,
                attempt,
            )
            mid = int(row["merchant_id"]) if row["merchant_id"] is not None else None
            if mid is not None:
                readiness_merchants.add(mid)
            # Selective work: ledger auto_ops job for readiness when requested
            et = str(row["event_type"])
            job_id = None
            if et == "MERCHANT_READINESS_RECALCULATION_REQUESTED" and mid is not None:
                job_id = uuid4()
                try:
                    await conn.execute(
                        """
                        INSERT INTO auto_ops_jobs
                          (job_id, job_type, catalog_revision, attempt, status, started_at, details)
                        VALUES ($1, 'MERCHANT_READINESS', $2, 1, 'COMPLETED', NOW(), $3::jsonb)
                        """,
                        job_id,
                        catalog_revision or row["catalog_revision"] or _now(),
                        json.dumps(
                            {
                                "trigger_event_id": str(eid),
                                "merchant_id": mid,
                                "product_id": row["product_id"],
                            }
                        ),
                    )
                except Exception:
                    job_id = None
            await conn.execute(
                """
                UPDATE catalog_domain_events
                   SET processing_status='DONE', processed_at=NOW(), error_code=NULL
                 WHERE event_id=$1
                """,
                eid,
            )
            completed += 1
            traces.append(
                {
                    "event_id": str(eid),
                    "event_type": et,
                    "job_id": str(job_id) if job_id else None,
                    "affected_product_id": row["product_id"],
                    "affected_merchant_id": mid,
                    "catalog_revision": row["catalog_revision"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            await conn.execute(
                """
                UPDATE catalog_domain_events
                   SET processing_status='FAILED', error_code=$2, attempt=$3
                 WHERE event_id=$1
                """,
                eid,
                str(exc)[:500],
                attempt,
            )

    return {
        "fetched": len(rows),
        "completed": completed,
        "failed": failed,
        "retried": retried,
        "readiness_merchant_ids": sorted(readiness_merchants),
        "traces": traces[:200],
        "captured_at": _now(),
    }


__all__ = ["process_pending_events"]
