"""Admin ingestion + data-quality endpoints (ADR-010 §67–68).

No merchant display names or inline secrets — adapter_code + credential_ref only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-ingestion"])


class QualitySignalsIn(BaseModel):
    external_product_id: Optional[str] = None
    display_name: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    stock_status: Optional[str] = None
    has_primary_image: bool = False
    image_cdn_ready: bool = False
    source_reference: Optional[str] = None
    price_fresh: bool = False
    parse_failed: bool = False
    schema_invalid: bool = False
    duplicate_suspected: bool = False
    forbidden_hotlink_image: bool = False


class QualityScoreOut(BaseModel):
    status: str
    score: float
    reasons: List[str]
    chatbot_visible: bool


class SourceBindingIn(BaseModel):
    source_code: str = Field(..., min_length=1, max_length=128)
    adapter_code: str = Field(..., min_length=1, max_length=128)
    merchant_id: str = Field(..., min_length=1, max_length=64)
    credential_ref: Optional[str] = Field(default=None, max_length=256)
    config: Dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=200)


@router.get("/ingestion/adapters")
async def list_adapters(request: Request) -> Dict[str, Any]:
    from taksitlio.ingestion.binding import build_default_registry

    container = container_from(request)
    registry = container.extras.get("adapter_registry") or build_default_registry()
    return {
        "adapters": sorted(registry.known_codes()),
        "note": "Bind sources via opaque adapter_code; no merchant names in code",
    }


@router.post("/data-quality/score", response_model=QualityScoreOut)
async def score_quality(payload: QualitySignalsIn) -> QualityScoreOut:
    from taksitlio.data_quality import score_product_quality, signals_from_normalized

    verdict = score_product_quality(
        signals_from_normalized(**payload.model_dump())
    )
    return QualityScoreOut(
        status=verdict.status.value,
        score=verdict.score,
        reasons=list(verdict.reasons),
        chatbot_visible=verdict.chatbot_visible,
    )


@router.post("/ingestion/dry-run")
async def ingestion_dry_run(
    payload: SourceBindingIn, request: Request
) -> Dict[str, Any]:
    """Operator dry-run against a bound feed — does not write production rows."""

    from taksitlio.ingestion.binding import SourceBinding, build_default_registry
    from taksitlio.ingestion.runner import run_ingestion_dry, source_health_snapshot

    if payload.config.get("authorization") or payload.config.get("api_key"):
        raise HTTPException(
            status_code=400,
            detail="Inline secrets forbidden; use credential_ref",
        )

    container = container_from(request)
    registry = container.extras.get("adapter_registry") or build_default_registry()
    binding = SourceBinding(
        source_code=payload.source_code,
        adapter_code=payload.adapter_code,
        merchant_id=payload.merchant_id,
        credential_ref=payload.credential_ref,
        config=payload.config,
    )
    try:
        result = await run_ingestion_dry(
            binding, registry=registry, limit=payload.limit
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    health = source_health_snapshot(
        source_code=result.source_code,
        adapter_code=result.adapter_code,
        last_run=result,
    )
    return {
        "source_code": result.source_code,
        "adapter_code": result.adapter_code,
        "merchant_id": result.merchant_id,
        "discovered": result.discovered,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "quarantined": result.quarantined,
        "chatbot_visible": result.chatbot_visible,
        "health": health,
        "items": [
            {
                "external_product_id": i.external_product_id,
                "quality_status": i.quality.status.value,
                "quality_score": i.quality.score,
                "chatbot_visible": i.quality.chatbot_visible,
                "reasons": list(i.quality.reasons),
                "error": i.error,
                "display_name": None if i.product is None else i.product.display_name,
                "offer_count": len(i.offers),
                "media_count": len(i.media),
            }
            for i in result.items
        ],
        "diagnostics": dict(result.diagnostics),
    }


class UpsertSourceIn(BaseModel):
    merchant_id: int = Field(..., ge=1)
    source_code: str = Field(..., min_length=1, max_length=64)
    source_type: str = Field(default="FEED_JSON")
    adapter_code: str = Field(..., min_length=1, max_length=128)
    credential_ref: Optional[str] = None
    base_url: Optional[str] = None
    status: str = "DRAFT"
    priority: int = 100
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PersistDryRunIn(SourceBindingIn):
    merchant_id_int: int = Field(..., ge=1, description="DB merchants.id")
    source_type: str = "FEED_JSON"
    persist: bool = True
    enqueue_discovery: bool = False
    upsert_products: bool = False


class EnqueueJobIn(BaseModel):
    queue_name: str
    priority: int = 100
    source_id: Optional[int] = None
    product_id: Optional[int] = None
    external_item_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkerTickIn(BaseModel):
    worker_id: str = Field(default="admin-worker", min_length=1, max_length=128)
    queue_name: Optional[str] = None
    lease_seconds: int = Field(default=60, ge=5, le=600)


@router.post("/ingestion/sources")
async def upsert_source(payload: UpsertSourceIn, request: Request) -> Dict[str, Any]:
    container = container_from(request)
    repo = container.extras.get("ingestion_repo")
    if repo is None:
        raise HTTPException(status_code=501, detail="ingestion_repo not configured")
    from taksitlio.ingestion.store import CreateSourceInput

    if payload.metadata.get("authorization") or payload.metadata.get("api_key"):
        raise HTTPException(status_code=400, detail="Inline secrets forbidden")
    source = await repo.upsert_source(
        CreateSourceInput(
            merchant_id=payload.merchant_id,
            source_code=payload.source_code,
            source_type=payload.source_type,
            adapter_code=payload.adapter_code,
            credential_ref=payload.credential_ref,
            base_url=payload.base_url,
            status=payload.status,
            priority=payload.priority,
            metadata=payload.metadata,
        )
    )
    return {
        "id": source.id,
        "source_code": source.source_code,
        "adapter_code": source.adapter_code,
        "merchant_id": source.merchant_id,
        "status": source.status,
    }


@router.post("/ingestion/dry-run/persist")
async def dry_run_and_persist(
    payload: PersistDryRunIn, request: Request
) -> Dict[str, Any]:
    """Dry-run feed then optionally persist run + health (still no product seed)."""

    from taksitlio.ingestion.binding import SourceBinding, build_default_registry
    from taksitlio.ingestion.persist import health_from_run, run_result_to_persist
    from taksitlio.ingestion.runner import run_ingestion_dry
    from taksitlio.ingestion.store import CreateSourceInput
    from taksitlio.ingestion_scheduler.domain import (
        PRIORITY_DEFAULT,
        SchedulerJobSpec,
        SchedulerQueue,
    )

    if payload.config.get("authorization") or payload.config.get("api_key"):
        raise HTTPException(
            status_code=400,
            detail="Inline secrets forbidden; use credential_ref",
        )

    container = container_from(request)
    registry = container.extras.get("adapter_registry") or build_default_registry()
    ingestion_repo = container.extras.get("ingestion_repo")
    scheduler_repo = container.extras.get("scheduler_repo")

    binding = SourceBinding(
        source_code=payload.source_code,
        adapter_code=payload.adapter_code,
        merchant_id=str(payload.merchant_id),
        credential_ref=payload.credential_ref,
        config=payload.config,
    )
    try:
        result = await run_ingestion_dry(
            binding, registry=registry, limit=payload.limit
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out: Dict[str, Any] = {
        "discovered": result.discovered,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "quarantined": result.quarantined,
        "chatbot_visible": result.chatbot_visible,
        "persisted_run_id": None,
        "enqueued_job_id": None,
        "catalog": None,
    }

    if payload.persist:
        if ingestion_repo is None:
            raise HTTPException(status_code=501, detail="ingestion_repo not configured")
        source = await ingestion_repo.upsert_source(
            CreateSourceInput(
                merchant_id=payload.merchant_id_int,
                source_code=payload.source_code,
                source_type=payload.source_type,
                adapter_code=payload.adapter_code,
                credential_ref=payload.credential_ref,
                base_url=payload.config.get("feed_url"),
                status="ACTIVE",
                metadata={"binding_merchant_key": payload.merchant_id},
            )
        )
        run = await ingestion_repo.persist_run(
            run_result_to_persist(result, source_id=source.id)
        )
        health = health_from_run(
            source_id=source.id,
            result=result,
            consecutive_failures=source.consecutive_failures,
        )
        await ingestion_repo.upsert_health(health)
        out["persisted_run_id"] = run.id
        out["source_id"] = source.id
        out["health"] = health.health

        if payload.enqueue_discovery and scheduler_repo is not None:
            job = await scheduler_repo.enqueue(
                SchedulerJobSpec(
                    queue_name=SchedulerQueue.PRODUCT_DISCOVERY,
                    priority=PRIORITY_DEFAULT,
                    source_id=str(source.id),
                    payload={"reason": "post_dry_run"},
                )
            )
            out["enqueued_job_id"] = job.id

        if payload.upsert_products:
            catalog = container.extras.get("product_catalog")
            if catalog is None:
                raise HTTPException(
                    status_code=501, detail="product_catalog not configured"
                )
            from taksitlio.product.catalog import apply_ingestion_to_catalog

            applied = await apply_ingestion_to_catalog(
                result,
                merchant_id=payload.merchant_id_int,
                catalog=catalog,
                only_chatbot_visible=True,
            )
            out["catalog"] = {
                "upserted_products": applied.upserted_products,
                "upserted_offers": applied.upserted_offers,
                "skipped_unchanged": applied.skipped_unchanged,
                "skipped_quarantined": applied.skipped_quarantined,
                "items": [
                    {
                        "external_product_id": i.external_product_id,
                        "product_action": i.product_action,
                        "offer_action": i.offer_action,
                        "product_id": i.product_id,
                        "quality_status": i.quality_status,
                    }
                    for i in applied.items
                ],
            }

    return out


@router.get("/products")
async def list_products(
    request: Request, merchant_id: Optional[int] = None, limit: int = 50
) -> Dict[str, Any]:
    container = container_from(request)
    catalog = container.extras.get("product_catalog")
    if catalog is None:
        raise HTTPException(status_code=501, detail="product_catalog not configured")
    rows = await catalog.list_products(merchant_id=merchant_id, limit=min(limit, 200))
    return {
        "products": [
            {
                "id": p.id,
                "merchant_id": p.merchant_id,
                "external_product_id": p.external_product_id,
                "display_name": p.display_name,
                "data_quality_status": p.data_quality_status,
                "status": p.status,
            }
            for p in rows
        ]
    }


@router.post("/ingestion/scheduler/enqueue")
async def enqueue_job(payload: EnqueueJobIn, request: Request) -> Dict[str, Any]:
    from taksitlio.ingestion_scheduler.domain import SchedulerJobSpec, SchedulerQueue

    container = container_from(request)
    repo = container.extras.get("scheduler_repo")
    if repo is None:
        raise HTTPException(status_code=501, detail="scheduler_repo not configured")
    try:
        queue = SchedulerQueue(payload.queue_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid queue_name") from exc
    job = await repo.enqueue(
        SchedulerJobSpec(
            queue_name=queue,
            priority=payload.priority,
            source_id=None if payload.source_id is None else str(payload.source_id),
            product_id=None if payload.product_id is None else str(payload.product_id),
            external_item_id=payload.external_item_id,
            payload=payload.payload,
        )
    )
    return {
        "id": job.id,
        "queue_name": job.queue_name,
        "status": job.status,
        "priority": job.priority,
    }


@router.post("/ingestion/scheduler/tick")
async def scheduler_tick(payload: WorkerTickIn, request: Request) -> Dict[str, Any]:
    from taksitlio.ingestion_scheduler.worker import LeaseLoopWorker, noop_handler

    container = container_from(request)
    repo = container.extras.get("scheduler_repo")
    if repo is None:
        raise HTTPException(status_code=501, detail="scheduler_repo not configured")
    worker = LeaseLoopWorker(
        repo=repo,
        worker_id=payload.worker_id,
        handler=noop_handler,
        queue_name=payload.queue_name,
        lease_seconds=payload.lease_seconds,
    )
    job = await worker.tick()
    if job is None:
        return {"leased": False}
    return {
        "leased": True,
        "id": job.id,
        "queue_name": job.queue_name,
        "status_after": "SUCCEEDED",
        "attempts": job.attempts,
    }


@router.get("/ingestion/runs")
async def list_runs(
    request: Request, source_id: Optional[int] = None, limit: int = 50
) -> Dict[str, Any]:
    container = container_from(request)
    repo = container.extras.get("ingestion_repo")
    if repo is None:
        raise HTTPException(status_code=501, detail="ingestion_repo not configured")
    rows = await repo.list_runs(source_id=source_id, limit=min(limit, 200))
    return {
        "runs": [
            {
                "id": r.id,
                "source_id": r.source_id,
                "run_type": r.run_type,
                "status": r.status,
                "items_discovered": r.items_discovered,
                "items_failed": r.items_failed,
            }
            for r in rows
        ]
    }


@router.get("/ingestion/sources/health")
async def sources_health(request: Request) -> Dict[str, Any]:
    container = container_from(request)
    repo = container.extras.get("ingestion_repo")
    if repo is not None:
        rows = await repo.list_health()
        return {
            "sources": [
                {
                    "source_id": h.source_id,
                    "health": h.health,
                    "consecutive_failures": h.consecutive_failures,
                    "detail": h.detail,
                }
                for h in rows
            ],
            "mode": "repository",
        }
    cached = container.extras.get("ingestion_source_health")
    if cached is not None:
        return {"sources": list(cached), "mode": "cached"}
    return {"sources": [], "mode": "empty"}
