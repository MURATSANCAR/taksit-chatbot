"""Search session API — clarification-first progressive search (ADR-011)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.llm_routing.worker import schedule_llm_job
from taksitlio.search_sessions import SearchOrchestrator, build_demo_orchestrator

router = APIRouter(tags=["search-sessions"])


def _orchestrator(request: Request) -> SearchOrchestrator:
    container = container_from(request)
    orch = container.extras.get("search_orchestrator")
    if orch is None:
        orch = build_demo_orchestrator()
        container.extras["search_orchestrator"] = orch
    return orch  # type: ignore[no-any-return]


def _schedule_understanding(request: Request, result: Dict[str, Any]) -> None:
    job_id = result.get("llm_job_id")
    if not job_id:
        return
    container = container_from(request)
    worker = container.extras.get("llm_understanding_worker")
    schedule_llm_job(worker, job_id)


class StartSearchIn(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=4000)
    client_query_id: Optional[str] = None
    user_id: Optional[str] = None
    organization_id: Optional[str] = None


class ClarificationIn(BaseModel):
    clarification_id: str
    selected_option_ids: List[str] = Field(default_factory=list)
    free_text: Optional[str] = None
    expected_query_version: int = Field(..., ge=1)


class ConstraintIn(BaseModel):
    action: str = Field(..., pattern="^(UPDATE|DELETE|REQUIRE|PREFER)$")
    constraint_id: str
    value: Any = None
    expected_query_version: int = Field(..., ge=1)


class SupersedeIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class LlmCompleteIn(BaseModel):
    """Test/ops helper — production worker would call internal service."""

    patch: Dict[str, Any]
    active_state_version: Optional[int] = None


@router.post("/search-sessions")
async def start_search(payload: StartSearchIn, request: Request) -> Dict[str, Any]:
    orch = _orchestrator(request)
    result = orch.start(
        conversation_id=payload.conversation_id,
        message=payload.message,
        client_query_id=payload.client_query_id,
        user_id=payload.user_id,
        organization_id=payload.organization_id,
    )
    # Normalize events_url to this API prefix
    sid = result["search_session_id"]
    result["events_url"] = f"/v1/search-sessions/{sid}/events"
    _schedule_understanding(request, result)
    return result


@router.post("/search-sessions/{session_id}/clarifications")
async def post_clarification(
    session_id: str,
    payload: ClarificationIn,
    request: Request,
) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        return orch.answer_clarification(
            session_id,
            clarification_id=payload.clarification_id,
            selected_option_ids=payload.selected_option_ids,
            free_text=payload.free_text,
            expected_query_version=payload.expected_query_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/constraints")
async def post_constraints(
    session_id: str,
    payload: ConstraintIn,
    request: Request,
) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        return orch.update_constraint(
            session_id,
            action=payload.action,
            constraint_id=payload.constraint_id,
            value=payload.value,
            expected_query_version=payload.expected_query_version,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/complete-with-current-results")
async def complete_with_current(session_id: str, request: Request) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        return orch.complete_with_current_results(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/cancel")
async def cancel_search(session_id: str, request: Request) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        return orch.cancel(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/messages")
async def supersede_message(
    session_id: str,
    payload: SupersedeIn,
    request: Request,
) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        result = orch.supersede_with_message(session_id, payload.message)
        _schedule_understanding(request, result)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/llm-jobs/drain")
async def drain_llm_jobs(session_id: str, request: Request) -> Dict[str, Any]:
    """Process queued UNDERSTANDING_SERVICE jobs for this session (ops / tests)."""

    orch = _orchestrator(request)
    if orch.repo.get(session_id) is None:
        raise HTTPException(status_code=404, detail="search_session_not_found")
    container = container_from(request)
    worker = container.extras.get("llm_understanding_worker")
    if worker is None:
        raise HTTPException(status_code=501, detail="llm_understanding_worker unavailable")
    results = []
    for job_id, job in list(orch.llm_jobs.items()):
        if job.search_session_id == session_id and job.status.value in {
            "QUEUED",
            "RUNNING",
        }:
            results.append(await worker.process_job(job_id))
    return {
        "search_session_id": session_id,
        "processed": len(results),
        "results": results,
        "provider_mode": getattr(worker, "provider_mode", None),
    }


@router.post("/search-sessions/{session_id}/llm-jobs/{job_id}/complete")
async def complete_llm_job(
    session_id: str,
    job_id: str,
    payload: LlmCompleteIn,
    request: Request,
) -> Dict[str, Any]:
    orch = _orchestrator(request)
    try:
        result = orch.complete_llm_job(
            job_id,
            payload.patch,
            active_state_version=payload.active_state_version,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/search-sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> Dict[str, Any]:
    orch = _orchestrator(request)
    session = orch.repo.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="search_session_not_found")
    return {
        "search_session_id": session.id,
        "status": session.status.value,
        "query_version": session.active_query_version,
        "clarification_count": session.clarification_count,
        "events_url": f"/v1/search-sessions/{session_id}/events",
    }


@router.get("/search-sessions/{session_id}/events")
async def search_session_events(session_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream for search progress."""

    orch = _orchestrator(request)
    if orch.repo.get(session_id) is None:
        raise HTTPException(status_code=404, detail="search_session_not_found")

    async def event_generator() -> AsyncIterator[str]:
        last_id: Optional[str] = None
        idle_rounds = 0
        while idle_rounds < 100:
            if await request.is_disconnected():
                break
            payloads = orch.list_event_payloads(session_id, after_id=last_id)
            if payloads:
                idle_rounds = 0
                for p in payloads:
                    last_id = p["event_id"]
                    yield f"id: {p['event_id']}\nevent: {p['type']}\ndata: {json.dumps(p, ensure_ascii=False)}\n\n"
                session = orch.repo.get(session_id)
                if session and session.status.value in {
                    "COMPLETED",
                    "COMPLETED_DEGRADED",
                    "FAILED",
                    "CANCELLED",
                    "SUPERSEDED",
                }:
                    break
            else:
                idle_rounds += 1
                yield ": keepalive\n\n"
                await asyncio.sleep(0.15)
        # Final snapshot if any remaining
        payloads = orch.list_event_payloads(session_id, after_id=last_id)
        for p in payloads:
            yield f"id: {p['event_id']}\nevent: {p['type']}\ndata: {json.dumps(p, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
