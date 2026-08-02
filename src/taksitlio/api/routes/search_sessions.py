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
from taksitlio.search_sessions import SearchOrchestrator, build_empty_orchestrator
from taksitlio.search_sessions.catalog_pool import refresh_orchestrator_from_catalog
from taksitlio.search_sessions.hydrate import ensure_session_loaded
from taksitlio.search_sessions.metrics import GLOBAL_SEARCH_METRICS
from taksitlio.search_sessions.status import InvalidTransitionError

router = APIRouter(tags=["search-sessions"])


def _orchestrator(request: Request) -> SearchOrchestrator:
    container = container_from(request)
    orch = container.extras.get("search_orchestrator")
    if orch is None:
        orch = build_empty_orchestrator()
        container.extras["search_orchestrator"] = orch
    return orch  # type: ignore[no-any-return]


async def _ensure_session(request: Request, session_id: str) -> SearchOrchestrator:
    orch = _orchestrator(request)
    if orch.repo.get(session_id) is not None:
        return orch
    container = container_from(request)
    loaded = await ensure_session_loaded(
        orch, session_id, pg=container.extras.get("search_session_pg")
    )
    if loaded is None:
        raise HTTPException(status_code=404, detail="search_session_not_found")
    return orch


async def _maybe_refresh_catalog(
    request: Request,
    orch: SearchOrchestrator,
    utterance: str,
    *,
    trace: Any = None,
    prefer_search_ready: bool = False,
) -> None:
    container = container_from(request)
    catalog = container.extras.get("product_catalog")
    if catalog is None:
        return
    logos = container.extras.get("logo_resolver")

    async def _run() -> None:
        await refresh_orchestrator_from_catalog(
            orch,
            catalog=catalog,
            merchants=container.extras.get("merchant_directory"),
            finance_index=container.extras.get("finance_option_index"),
            institutions=container.extras.get("institution_labels"),
            logos=logos,
            categories=container.extras.get("category_repo"),
            utterance=utterance,
            prefer_search_ready=prefer_search_ready,
            pg_pool=container.extras.get("pool"),
        )

    try:
        if trace is not None:
            with trace.span("catalog.refresh", prefer_search_ready=prefer_search_ready):
                await asyncio.wait_for(_run(), timeout=12.0)
        else:
            await asyncio.wait_for(_run(), timeout=12.0)
    except Exception:  # noqa: BLE001
        # Catalog hydrate must not 500 the search UX (DB timeout under ingest load, etc.)
        # Keep prior product_pool / catalog hints on the orchestrator.
        pass


async def _maybe_persist(request: Request, session_id: Optional[str]) -> None:
    if not session_id:
        return
    container = container_from(request)
    persister = container.extras.get("search_session_persister")
    orch = container.extras.get("search_orchestrator")
    if persister is None or orch is None:
        return
    try:
        await persister.persist(orch, session_id)
    except Exception:  # noqa: BLE001
        # Persistence must not break search UX
        pass


def _schedule_persist(request: Request, session_id: Optional[str]) -> None:
    """Persist off the search critical path (bounded fire-and-forget)."""

    if not session_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_maybe_persist(request, session_id))


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


@router.get("/search-sessions/metrics/summary")
async def search_metrics_summary(request: Request) -> Dict[str, Any]:
    """Live P50/P95 for queue / inference / partial-result latencies."""

    container = container_from(request)
    summary = GLOBAL_SEARCH_METRICS.summary()
    pg = container.extras.get("search_session_pg")
    if pg is not None and hasattr(pg, "list_recent_metrics"):
        try:
            rows = await pg.list_recent_metrics(limit=500)
            GLOBAL_SEARCH_METRICS.ingest_session_metrics(rows)
            summary = GLOBAL_SEARCH_METRICS.summary()
        except Exception:  # noqa: BLE001
            pass
    worker = container.extras.get("llm_understanding_worker")
    if worker is not None:
        for name, values in getattr(worker, "metrics", {}).items():
            for v in values:
                if name.endswith("_ms"):
                    GLOBAL_SEARCH_METRICS.observe(name, float(v))
        summary = GLOBAL_SEARCH_METRICS.summary()
        summary["provider_mode"] = getattr(worker, "provider_mode", None)
    return summary


@router.post("/search-sessions")
async def start_search(payload: StartSearchIn, request: Request) -> Dict[str, Any]:
    import uuid

    from taksitlio.api.internal_access import enforce_search_access
    from taksitlio.applicability_readiness.tracing import TraceRecorder
    from taksitlio.semantic_matching.query_intent import is_off_domain_for_assist

    access = await enforce_search_access(request)
    orch = _orchestrator(request)
    container = container_from(request)
    cohort_id = access.cohort_id or (container.extras.get("dynamic_readiness_config") or {}).get(
        "cohort_id"
    )
    cohort_version = access.cohort_version or (
        container.extras.get("dynamic_readiness_config") or {}
    ).get("cohort_version")
    catalog_rev = container.extras.get("catalog_revision")
    trace = TraceRecorder(
        trace_id=str(uuid.uuid4()),
        cohort_id=int(cohort_id) if cohort_id is not None else None,
        cohort_version=int(cohort_version) if cohort_version is not None else None,
        catalog_revision=str(catalog_rev) if catalog_rev else None,
    )
    with trace.span("search.http"):
        with trace.span(
            "search.authorization",
            is_internal=access.is_internal,
            access_reason=access.reason,
        ):
            pass
        with trace.span(
            "search.cohort.resolve",
            cohort_id=trace.cohort_id,
            cohort_version=trace.cohort_version,
        ):
            pass
        # Greeting / off-topic: skip catalog hydrate (can take seconds under load).
        if not is_off_domain_for_assist(payload.message):
            await _maybe_refresh_catalog(
                request,
                orch,
                payload.message,
                trace=trace,
                prefer_search_ready=bool(access.is_internal),
            )
        result = orch.start(
            conversation_id=payload.conversation_id,
            message=payload.message,
            client_query_id=payload.client_query_id,
            user_id=payload.user_id,
            organization_id=payload.organization_id,
            trace=trace,
        )
    # Normalize events_url to this API prefix
    sid = result["search_session_id"]
    result["events_url"] = f"/v1/search-sessions/{sid}/events"
    result["trace_id"] = trace.trace_id
    # Diagnostics for INTERNAL harness only (no raw user text).
    include_trace = access.is_internal and (
        request.headers.get("X-Taksitlio-Include-Trace", "1").strip() != "0"
    )
    if include_trace:
        result["trace"] = trace.to_dict()
    if result.get("route") != "OUT_OF_SCOPE":
        _schedule_understanding(request, result)
        _schedule_persist(request, sid)
    return result


@router.post("/search-sessions/{session_id}/clarifications")
async def post_clarification(
    session_id: str,
    payload: ClarificationIn,
    request: Request,
) -> Dict[str, Any]:
    orch = await _ensure_session(request, session_id)
    try:
        result = orch.answer_clarification(
            session_id,
            clarification_id=payload.clarification_id,
            selected_option_ids=payload.selected_option_ids,
            free_text=payload.free_text,
            expected_query_version=payload.expected_query_version,
        )
        _schedule_understanding(request, result)
        await _maybe_persist(request, session_id)
        return result
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
    orch = await _ensure_session(request, session_id)
    try:
        result = orch.update_constraint(
            session_id,
            action=payload.action,
            constraint_id=payload.constraint_id,
            value=payload.value,
            expected_query_version=payload.expected_query_version,
        )
        await _maybe_persist(request, session_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/complete-with-current-results")
async def complete_with_current(session_id: str, request: Request) -> Dict[str, Any]:
    orch = await _ensure_session(request, session_id)
    try:
        result = orch.complete_with_current_results(session_id)
        await _maybe_persist(request, session_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/cancel")
async def cancel_search(session_id: str, request: Request) -> Dict[str, Any]:
    orch = await _ensure_session(request, session_id)
    try:
        result = orch.cancel(session_id)
        await _maybe_persist(request, session_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/messages")
async def supersede_message(
    session_id: str,
    payload: SupersedeIn,
    request: Request,
) -> Dict[str, Any]:
    from taksitlio.semantic_matching.query_intent import is_off_domain_for_assist

    orch = await _ensure_session(request, session_id)
    try:
        if not is_off_domain_for_assist(payload.message):
            await _maybe_refresh_catalog(request, orch, payload.message)
        result = orch.supersede_with_message(session_id, payload.message)
        if result.get("route") != "OUT_OF_SCOPE":
            _schedule_understanding(request, result)
            await _maybe_persist(request, session_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/search-sessions/{session_id}/llm-jobs/drain")
async def drain_llm_jobs(session_id: str, request: Request) -> Dict[str, Any]:
    """Process queued UNDERSTANDING_SERVICE jobs for this session (ops / tests)."""

    orch = await _ensure_session(request, session_id)
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
    orch = await _ensure_session(request, session_id)
    try:
        result = orch.complete_llm_job(
            job_id,
            payload.patch,
            active_state_version=payload.active_state_version,
        )
        await _maybe_persist(request, session_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/search-sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> Dict[str, Any]:
    orch = await _ensure_session(request, session_id)
    session = orch.repo.get(session_id)
    assert session is not None
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

    orch = await _ensure_session(request, session_id)
    resume_after = (request.headers.get("Last-Event-ID") or "").strip() or None

    async def event_generator() -> AsyncIterator[str]:
        last_id: Optional[str] = resume_after
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
