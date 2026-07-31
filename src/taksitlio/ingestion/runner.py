"""In-memory / dry-run ingestion runner (ADR-010).

Does not persist fake production data. Operator binds real feeds via SourceBinding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.data_quality import (
    ProductQualityVerdict,
    score_product_quality,
    signals_from_normalized,
)
from taksitlio.ingestion.binding import SourceBinding, instantiate_adapter
from taksitlio.ingestion.errors import IngestionError
from taksitlio.ingestion.protocol import (
    NormalizedMediaRef,
    NormalizedOffer,
    NormalizedProduct,
    NormalizedStock,
)
from taksitlio.ingestion.registry import AdapterRegistry


@dataclass(frozen=True)
class IngestedItemSnapshot:
    external_product_id: str
    product: Optional[NormalizedProduct]
    offers: tuple[NormalizedOffer, ...]
    stock: tuple[NormalizedStock, ...]
    media: tuple[NormalizedMediaRef, ...]
    quality: ProductQualityVerdict
    error: Optional[str] = None


@dataclass(frozen=True)
class IngestionRunResult:
    source_code: str
    adapter_code: str
    merchant_id: str
    discovered: int
    succeeded: int
    failed: int
    quarantined: int
    chatbot_visible: int
    items: tuple[IngestedItemSnapshot, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


async def run_ingestion_dry(
    binding: SourceBinding,
    *,
    registry: Optional[AdapterRegistry] = None,
    limit: int = 50,
    price_fresh_default: bool = False,
) -> IngestionRunResult:
    """Discover + fetch up to ``limit`` products; score quality; no DB writes."""

    adapter = instantiate_adapter(binding, registry=registry)
    items: list[IngestedItemSnapshot] = []
    discovered = 0
    succeeded = 0
    failed = 0
    quarantined = 0
    visible = 0

    async for ref in adapter.discover_products():
        if discovered >= limit:
            break
        discovered += 1
        ext_id = ref.external_product_id
        try:
            product = await adapter.fetch_product(ext_id)
            offers = tuple(await adapter.fetch_offers(ext_id))
            stock = tuple(await adapter.fetch_stock(ext_id))
            media = tuple(await adapter.fetch_media(ext_id))
            price = offers[0].current_price if offers else None
            currency = offers[0].currency if offers else None
            stock_status = stock[0].stock_status if stock else None
            has_image = any(m.media_role == "PRIMARY" for m in media) or bool(media)
            # Hotlink URLs are source refs only — CDN ready is false until media pipeline.
            verdict = score_product_quality(
                signals_from_normalized(
                    external_product_id=product.external_product_id,
                    display_name=product.display_name,
                    price=price,
                    currency=currency,
                    stock_status=stock_status,
                    has_primary_image=has_image,
                    image_cdn_ready=False,
                    source_reference=product.source_reference or binding.source_code,
                    price_fresh=price_fresh_default,
                )
            )
            if verdict.chatbot_visible:
                visible += 1
            else:
                quarantined += 1
            succeeded += 1
            items.append(
                IngestedItemSnapshot(
                    external_product_id=ext_id,
                    product=product,
                    offers=offers,
                    stock=stock,
                    media=media,
                    quality=verdict,
                )
            )
        except IngestionError as exc:
            failed += 1
            quarantined += 1
            verdict = score_product_quality(
                signals_from_normalized(
                    external_product_id=ext_id,
                    display_name=None,
                    price=None,
                    currency=None,
                    stock_status=None,
                    has_primary_image=False,
                    image_cdn_ready=False,
                    source_reference=binding.source_code,
                    parse_failed=True,
                )
            )
            items.append(
                IngestedItemSnapshot(
                    external_product_id=ext_id,
                    product=None,
                    offers=(),
                    stock=(),
                    media=(),
                    quality=verdict,
                    error=f"{type(exc).__name__}:{exc}",
                )
            )
        except Exception as exc:  # noqa: BLE001 — dry-run isolates adapter faults
            failed += 1
            quarantined += 1
            verdict = score_product_quality(
                signals_from_normalized(
                    external_product_id=ext_id,
                    display_name=None,
                    price=None,
                    currency=None,
                    stock_status=None,
                    has_primary_image=False,
                    image_cdn_ready=False,
                    source_reference=binding.source_code,
                    parse_failed=True,
                )
            )
            items.append(
                IngestedItemSnapshot(
                    external_product_id=ext_id,
                    product=None,
                    offers=(),
                    stock=(),
                    media=(),
                    quality=verdict,
                    error=f"{type(exc).__name__}:{exc}",
                )
            )

    diagnostics: dict[str, Any] = {"limit": limit, "dry_run": True}
    # ADR-012 SCHEMA_DRIFT_GATE + quality circuit breaker (source-scoped)
    try:
        from taksitlio.recommendation_safety import (
            BreakerAction,
            BreakerScope,
            DriftSignals,
            QualityCircuitBreaker,
            decide_breaker,
            evaluate_schema_drift,
        )
    except ImportError:
        # Keep dry-run usable if recommendation_safety has a circular import in some deploys.
        return IngestionRunResult(
            source_code=binding.source_code,
            adapter_code=binding.adapter_code,
            discovered=discovered,
            succeeded=succeeded,
            failed=failed,
            quarantined=quarantined,
            chatbot_visible=visible,
            items=tuple(items),
            diagnostics={**diagnostics, "adr012_breaker": "skipped_import_error"},
        )

    failed_rate = (failed / discovered) if discovered else 0.0
    prices = [
        float(item.offers[0].current_price)
        for item in items
        if item.offers and item.offers[0].current_price is not None
    ]
    # Without prior baseline, only signal structural anomalies from this run.
    all_oos = bool(items) and all(
        (not item.stock)
        or str(getattr(item.stock[0], "stock_status", "")).upper()
        in {"OUT_OF_STOCK", "UNAVAILABLE", "UNKNOWN"}
        for item in items
        if item.error is None
    )
    image_zero = bool(items) and all(not item.media for item in items if item.error is None)
    currencies = {
        str(item.offers[0].currency)
        for item in items
        if item.offers and getattr(item.offers[0], "currency", None)
    }
    drift = evaluate_schema_drift(
        DriftSignals(
            all_out_of_stock=all_oos and succeeded > 0,
            image_count_zero=image_zero and succeeded > 0,
            currency_changed=len(currencies) > 1,
            product_count_drop_ratio=None,
            price_drop_ratio=None,
        )
    )
    breaker_action = decide_breaker(
        scope=BreakerScope.MERCHANT_PRICE,
        broken_rate=failed_rate,
        mismatch_count=1 if drift.action.value != "OK" else 0,
    )
    cb = QualityCircuitBreaker(
        broken_price_rate=failed_rate,
        campaign_mismatch_count=1 if drift.action.value != "OK" else 0,
    )
    actions = cb.evaluate()
    if breaker_action is not BreakerAction.NONE and breaker_action not in actions:
        actions = tuple(dict.fromkeys([*actions, breaker_action]))
    diagnostics["schema_drift"] = {
        "action": drift.action.value,
        "reasons": list(drift.reasons),
    }
    diagnostics["circuit_breaker"] = {
        "merchant_id": binding.merchant_id,
        "source_code": binding.source_code,
        "broken_price_rate": failed_rate,
        "actions": [a.value for a in actions],
        "price_disabled": BreakerAction.DISABLE_PRICE_RESULTS in actions
        or cb.is_price_disabled(),
    }
    if prices:
        diagnostics["price_sample_count"] = len(prices)

    return IngestionRunResult(
        source_code=binding.source_code,
        adapter_code=binding.adapter_code,
        merchant_id=binding.merchant_id,
        discovered=discovered,
        succeeded=succeeded,
        failed=failed,
        quarantined=quarantined,
        chatbot_visible=visible,
        items=tuple(items),
        diagnostics=diagnostics,
    )


def source_health_snapshot(
    *,
    source_code: str,
    adapter_code: str,
    last_run: Optional[IngestionRunResult] = None,
    consecutive_failures: int = 0,
) -> dict[str, Any]:
    if last_run is None:
        status = "UNKNOWN"
    elif last_run.failed and last_run.succeeded == 0:
        status = "UNHEALTHY"
    elif last_run.failed:
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    if consecutive_failures >= 3:
        status = "UNHEALTHY"
    return {
        "source_code": source_code,
        "adapter_code": adapter_code,
        "status": status,
        "consecutive_failures": consecutive_failures,
        "last_discovered": None if last_run is None else last_run.discovered,
        "last_succeeded": None if last_run is None else last_run.succeeded,
        "last_failed": None if last_run is None else last_run.failed,
    }


__all__ = [
    "IngestedItemSnapshot",
    "IngestionRunResult",
    "run_ingestion_dry",
    "source_health_snapshot",
]
