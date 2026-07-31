"""Apply ADR-012 quality gates from ingestion runs onto runtime stores."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from taksitlio.ingestion.runner import IngestionRunResult
from taksitlio.recommendation_safety.circuit_breaker import BreakerAction


async def persist_breaker_from_ingestion_result(
    result: IngestionRunResult,
    store: Any,
    *,
    reason_prefix: str = "ingestion_dry_run",
) -> dict[str, Any]:
    """Read runner diagnostics and open circuit breakers for the merchant/source.

    Safe no-op when store is missing or diagnostics lack breaker payload.
    """

    if store is None:
        return {"persisted": False, "reason": "no_store"}
    diag = dict(result.diagnostics or {})
    cb_diag = dict(diag.get("circuit_breaker") or {})
    actions_raw = list(cb_diag.get("actions") or [])
    actions: list[BreakerAction] = []
    for raw in actions_raw:
        try:
            action = BreakerAction(str(raw))
        except ValueError:
            continue
        if action is BreakerAction.NONE:
            continue
        actions.append(action)
    if not actions:
        return {"persisted": False, "reason": "no_actions", "actions": []}

    source_key = str(cb_diag.get("merchant_id") or result.merchant_id)
    reason = f"{reason_prefix}:{result.source_code}"
    drift = dict(diag.get("schema_drift") or {})
    if drift.get("action") and drift.get("action") != "OK":
        reason = f"{reason}:drift={drift.get('action')}"

    # Sync cache always
    record = getattr(store, "record_actions", None)
    if callable(record):
        record(source_key, actions, reason=reason)

    # Durable write when available
    record_async = getattr(store, "record_actions_async", None)
    if callable(record_async):
        await record_async(source_key, actions, reason=reason)

    # Ensure in-memory QualityCircuitBreaker metrics reflect rates
    cb = store.get(source_key) if hasattr(store, "get") else None
    if cb is not None:
        rate = cb_diag.get("broken_price_rate")
        if rate is not None:
            try:
                cb.broken_price_rate = float(rate)
            except (TypeError, ValueError):
                pass
        cb.disabled.update(actions)

    return {
        "persisted": True,
        "source_key": source_key,
        "actions": [a.value for a in actions],
        "price_disabled": BreakerAction.DISABLE_PRICE_RESULTS in actions,
        "reason": reason,
    }


__all__ = ["persist_breaker_from_ingestion_result"]
