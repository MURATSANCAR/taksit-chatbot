"""Admin answer-integrity feedback / shadow / error-class APIs (ADR-012).

Production container wires ``PostgresFeedbackStore`` (V023 tables).
Demo/in-memory container keeps ``InMemoryFeedbackStore``.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.answer_integrity.policy_store import InMemoryFeedbackStore
from taksitlio.recommendation_safety.feedback import (
    ErrorClass,
    FeedbackResultSnapshot,
    compare_shadow,
)

router = APIRouter(tags=["admin-answer-integrity"])


class FeedbackStore(Protocol):
    async def save_feedback_async(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def save_shadow_async(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def save_error_class_async(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def metrics_by_error_class_async(self) -> dict[str, int]: ...


def _feedback_store(request: Request) -> FeedbackStore:
    container = request.app.state.container
    store = container.extras.get("feedback_store")
    if store is None:
        store = InMemoryFeedbackStore()
        container.extras["feedback_store"] = store
    return store


class FeedbackIn(BaseModel):
    query_version: int
    parsed_constraints: dict[str, Any] = Field(default_factory=dict)
    catalog_revision: Optional[str] = None
    price_snapshot: Optional[str] = None
    campaign_snapshot: Optional[str] = None
    selected_product: Optional[str] = None
    selected_bank: Optional[str] = None
    response_fact_ids: list[str] = Field(default_factory=list)
    error_class: Optional[str] = None
    user_note: Optional[str] = None
    feedback_id: Optional[str] = None


class ShadowIn(BaseModel):
    live: dict[str, Any]
    shadow: dict[str, Any]
    comparison_key: Optional[str] = None


class ErrorClassIn(BaseModel):
    error_class: str
    owner: str
    metric_key: str
    detail: str = ""
    source_id: Optional[str] = None


@router.post("/answer-integrity/feedback")
async def post_feedback(
    body: FeedbackIn,
    store: FeedbackStore = Depends(_feedback_store),
) -> dict[str, Any]:
    error_class = None
    if body.error_class:
        try:
            error_class = ErrorClass(body.error_class)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"unknown error_class: {body.error_class}"
            ) from exc
        if body.error_class == "WRONG_ANSWER":
            raise HTTPException(status_code=400, detail="WRONG_ANSWER bucket forbidden")
    snap = FeedbackResultSnapshot(
        query_version=body.query_version,
        parsed_constraints=body.parsed_constraints,
        catalog_revision=body.catalog_revision,
        price_snapshot=body.price_snapshot,
        campaign_snapshot=body.campaign_snapshot,
        selected_product=body.selected_product,
        selected_bank=body.selected_bank,
        response_fact_ids=tuple(body.response_fact_ids),
        error_class=error_class,
        user_note=body.user_note,
    )
    payload = snap.to_dict()
    if body.feedback_id:
        payload["feedback_id"] = body.feedback_id
    saved = await store.save_feedback_async(payload)
    if error_class is not None:
        await store.save_error_class_async(
            {
                "error_class": error_class.value,
                "owner": "feedback",
                "metric_key": f"{error_class.value.lower()}_count",
                "detail": body.user_note or "",
            }
        )
    return {"ok": True, "snapshot": saved, "persisted": True}


@router.post("/answer-integrity/shadow-compare")
async def post_shadow(
    body: ShadowIn,
    store: FeedbackStore = Depends(_feedback_store),
) -> dict[str, Any]:
    comparison = compare_shadow(body.live, body.shadow)
    payload = {
        "comparison_key": body.comparison_key or "default",
        "diffs": list(comparison.diffs),
        "shown_to_user": comparison.shown_to_user,
        "live": dict(comparison.live_payload),
        "shadow": dict(comparison.shadow_payload),
    }
    saved = await store.save_shadow_async(payload)
    return {**saved, "persisted": True}


@router.post("/answer-integrity/error-class")
async def post_error_class(
    body: ErrorClassIn,
    store: FeedbackStore = Depends(_feedback_store),
) -> dict[str, Any]:
    if body.error_class == "WRONG_ANSWER":
        raise HTTPException(status_code=400, detail="WRONG_ANSWER bucket forbidden")
    try:
        ErrorClass(body.error_class)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"unknown error_class: {body.error_class}"
        ) from exc
    payload = body.model_dump()
    saved = await store.save_error_class_async(payload)
    return {"ok": True, "event": saved, "persisted": True}


@router.get("/answer-integrity/error-class/metrics")
async def error_class_metrics(
    store: FeedbackStore = Depends(_feedback_store),
) -> dict[str, Any]:
    return {"counts": await store.metrics_by_error_class_async()}
