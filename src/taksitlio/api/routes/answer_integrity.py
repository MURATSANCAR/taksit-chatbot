"""Answer integrity public APIs — feedback / shadow / error-class (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.answer_integrity.policy_store import InMemoryFeedbackStore
from taksitlio.recommendation_safety.feedback import (
    ErrorClass,
    FeedbackResultSnapshot,
    compare_shadow,
)

router = APIRouter(tags=["answer-integrity"])


def _feedback_store(request: Request) -> InMemoryFeedbackStore:
    container = container_from(request)
    store = container.extras.get("feedback_store")
    if store is None:
        store = InMemoryFeedbackStore()
        container.extras["feedback_store"] = store
    return store  # type: ignore[no-any-return]


class FeedbackIn(BaseModel):
    query_version: int = Field(..., ge=0)
    parsed_constraints: Dict[str, Any] = Field(default_factory=dict)
    catalog_revision: Optional[str] = None
    price_snapshot: Optional[str] = None
    campaign_snapshot: Optional[str] = None
    selected_product: Optional[str] = None
    selected_bank: Optional[str] = None
    response_fact_ids: List[str] = Field(default_factory=list)
    error_class: Optional[str] = None
    user_note: Optional[str] = None
    feedback_id: Optional[str] = None


class ShadowIn(BaseModel):
    comparison_key: str = Field(default="default", min_length=1, max_length=128)
    live_payload: Dict[str, Any] = Field(default_factory=dict)
    shadow_payload: Dict[str, Any] = Field(default_factory=dict)
    live: Optional[Dict[str, Any]] = None
    shadow: Optional[Dict[str, Any]] = None


class ErrorClassIn(BaseModel):
    error_class: str = Field(..., min_length=1, max_length=64)
    source_component: Optional[str] = None
    owner: Optional[str] = None
    metric_key: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    detail: str = ""


@router.post("/feedback")
async def post_feedback(payload: FeedbackIn, request: Request) -> Dict[str, Any]:
    store = _feedback_store(request)
    error = None
    if payload.error_class:
        if payload.error_class == "WRONG_ANSWER":
            raise HTTPException(status_code=422, detail="WRONG_ANSWER bucket forbidden")
        try:
            error = ErrorClass(payload.error_class)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown error_class: {payload.error_class}"
            ) from exc
    snap = FeedbackResultSnapshot(
        query_version=payload.query_version,
        parsed_constraints=payload.parsed_constraints,
        catalog_revision=payload.catalog_revision,
        price_snapshot=payload.price_snapshot,
        campaign_snapshot=payload.campaign_snapshot,
        selected_product=payload.selected_product,
        selected_bank=payload.selected_bank,
        response_fact_ids=tuple(payload.response_fact_ids),
        error_class=error,
        user_note=payload.user_note,
    )
    row = snap.to_dict()
    if payload.feedback_id:
        row["feedback_id"] = payload.feedback_id
    if hasattr(store, "save_feedback_async"):
        await store.save_feedback_async(row)
    else:
        store.save_feedback(row)
    return {"ok": True, "feedback": row}


@router.post("/shadow-comparisons")
async def post_shadow(payload: ShadowIn, request: Request) -> Dict[str, Any]:
    store = _feedback_store(request)
    live = payload.live or payload.live_payload
    shadow = payload.shadow or payload.shadow_payload
    comparison = compare_shadow(live, shadow)
    row = {
        "comparison_key": payload.comparison_key,
        "diffs": list(comparison.diffs),
        "shown_to_user": comparison.shown_to_user,
        "live": dict(comparison.live_payload),
        "shadow": dict(comparison.shadow_payload),
    }
    if hasattr(store, "save_shadow_async"):
        await store.save_shadow_async(row)
    else:
        store.save_shadow(row)
    return {"ok": True, "comparison": row, "diffs": list(comparison.diffs)}


@router.post("/error-class-events")
async def post_error_class(payload: ErrorClassIn, request: Request) -> Dict[str, Any]:
    store = _feedback_store(request)
    if payload.error_class == "WRONG_ANSWER":
        raise HTTPException(status_code=422, detail="WRONG_ANSWER bucket forbidden")
    try:
        ErrorClass(payload.error_class)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"unknown error_class: {payload.error_class}"
        ) from exc
    row = {
        "error_class": payload.error_class,
        "source_component": payload.source_component or payload.owner,
        "owner": payload.owner or payload.source_component,
        "metric_key": payload.metric_key,
        "detail": payload.detail,
        "payload": payload.payload,
    }
    try:
        if hasattr(store, "save_error_class_async"):
            await store.save_error_class_async(row)
        else:
            store.save_error_class(row)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "event": row}


@router.get("/error-class-events/summary")
async def error_class_summary(request: Request) -> Dict[str, Any]:
    store = _feedback_store(request)
    if hasattr(store, "metrics_by_error_class_async"):
        counts = await store.metrics_by_error_class_async()
    else:
        counts = store.metrics_by_error_class()
    return {"counts": counts, "total": sum(counts.values())}
