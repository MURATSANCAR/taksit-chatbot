"""Map dry-run results into persistable run rows."""

from __future__ import annotations

from datetime import datetime, timezone

from taksitlio.ingestion.runner import IngestionRunResult
from taksitlio.ingestion.store import PersistRunInput, SourceHealthRecord


def run_result_to_persist(
    result: IngestionRunResult,
    *,
    source_id: int,
    run_type: str = "FULL",
) -> PersistRunInput:
    if result.failed and result.succeeded == 0:
        status = "FAILED"
    elif result.failed:
        status = "PARTIAL"
    else:
        status = "SUCCEEDED"

    items = []
    for row in result.items:
        if row.error:
            action = "FAILED"
            error_code = "PRODUCT_PARSE_FAILED"
        elif not row.quality.chatbot_visible:
            action = "SKIPPED"
            error_code = row.quality.status.value
        else:
            action = "DISCOVERED"
            error_code = None
        items.append(
            {
                "external_item_id": row.external_product_id,
                "action": action,
                "error_code": error_code,
                "error_detail": row.error or ",".join(row.quality.reasons) or None,
                "source_reference": result.source_code,
            }
        )

    return PersistRunInput(
        source_id=source_id,
        run_type=run_type,
        status=status,
        items_discovered=result.discovered,
        items_changed=result.succeeded,
        items_skipped=max(0, result.quarantined - result.failed),
        items_failed=result.failed,
        metadata={
            "dry_run_persisted": True,
            "chatbot_visible": result.chatbot_visible,
            "adapter_code": result.adapter_code,
        },
        items=tuple(items),
    )


def health_from_run(
    *,
    source_id: int,
    result: IngestionRunResult,
    consecutive_failures: int = 0,
) -> SourceHealthRecord:
    now = datetime.now(timezone.utc)
    if result.failed and result.succeeded == 0:
        health = "UNAVAILABLE"
        failures = consecutive_failures + 1
        return SourceHealthRecord(
            source_id=source_id,
            health=health,
            consecutive_failures=failures,
            last_check_at=now,
            last_failure_at=now,
            detail=f"failed={result.failed}",
        )
    if result.failed:
        health = "DEGRADED"
    else:
        health = "HEALTHY"
    return SourceHealthRecord(
        source_id=source_id,
        health=health,
        consecutive_failures=0 if health == "HEALTHY" else consecutive_failures,
        last_check_at=now,
        last_success_at=now if result.succeeded else None,
        last_failure_at=now if result.failed else None,
        detail=f"discovered={result.discovered};visible={result.chatbot_visible}",
    )


__all__ = ["health_from_run", "run_result_to_persist"]
