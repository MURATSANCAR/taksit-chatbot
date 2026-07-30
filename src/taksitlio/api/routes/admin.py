"""Admin AI management endpoints (MVP API surface for panel screens)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from
from taksitlio.model_gateway.gateway import CompletionRequest, ModelGatewayError

router = APIRouter(tags=["admin-ai"])


class ProfileUpdateIn(BaseModel):
    endpoint_url: Optional[str] = None
    timeout_ms: Optional[int] = Field(default=None, ge=100, le=120000)
    parallel_slots: Optional[int] = Field(default=None, ge=1, le=64)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    status: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None


class CompareIn(BaseModel):
    message: str = Field(..., min_length=1)
    profile_a: str
    profile_b: str
    system_prompt: Optional[str] = None


class ActivatePromptIn(BaseModel):
    version: int = Field(..., ge=1)


@router.get("/models")
async def list_models(request: Request) -> Dict[str, Any]:
    container = container_from(request)
    profile_repo = container.extras.get("profile_repo")
    if profile_repo is not None:
        profiles = await profile_repo.list_profiles()
    else:
        codes = ["FAST_UNDERSTANDING", "DEEP_UNDERSTANDING", "RESPONSE_GENERATION"]
        profiles = []
        for code in codes:
            try:
                profiles.append(container.profiles.get_by_code(code))
            except KeyError:
                continue
    return {
        "profiles": [
            {
                "profile_code": p.profile_code,
                "display_name": p.display_name,
                "provider_type": p.provider_type,
                "endpoint_url": p.endpoint_url,
                "model_reference": p.model_reference,
                "timeout_ms": p.timeout_ms,
                "parallel_slots": p.parallel_slots,
                "temperature": p.temperature,
                "status": p.status,
                "configuration": dict(p.configuration),
            }
            for p in profiles
        ]
    }


@router.patch("/models/{profile_code}")
async def update_model(
    profile_code: str,
    payload: ProfileUpdateIn,
    request: Request,
) -> Dict[str, Any]:
    container = container_from(request)
    profile_repo = container.extras.get("profile_repo")
    if profile_repo is None:
        raise HTTPException(
            status_code=501,
            detail="Profile updates require Postgres-backed deployment",
        )
    try:
        profile = await profile_repo.update_profile(
            profile_code,
            endpoint_url=payload.endpoint_url,
            timeout_ms=payload.timeout_ms,
            parallel_slots=payload.parallel_slots,
            temperature=payload.temperature,
            status=payload.status,
            configuration=payload.configuration,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    adapter = container.extras.get("adapter")
    if adapter is not None:
        await adapter.refresh()
    return {"profile_code": profile.profile_code, "status": profile.status}


@router.post("/models/compare")
async def compare_models(payload: CompareIn, request: Request) -> Dict[str, Any]:
    container = container_from(request)
    gateway = container.gateway
    system = payload.system_prompt or "JSON ihtiyaç profili üret."
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": payload.message},
    ]

    async def run(code: str) -> Dict[str, Any]:
        try:
            parsed, result = await gateway.complete_json(
                code,
                CompletionRequest(
                    messages=messages, response_format={"type": "json_object"}
                ),
            )
            return {
                "profile_code": code,
                "ok": True,
                "latency_ms": result.latency_ms,
                "payload": parsed,
            }
        except (ModelGatewayError, KeyError, Exception) as exc:  # noqa: BLE001
            return {"profile_code": code, "ok": False, "error": str(exc)}

    a = await run(payload.profile_a)
    b = await run(payload.profile_b)
    return {"a": a, "b": b}


@router.get("/prompts/{prompt_code}")
async def list_prompts(prompt_code: str, request: Request) -> Dict[str, Any]:
    container = container_from(request)
    prompt_repo = container.extras.get("prompt_repo")
    if prompt_repo is None:
        return {"prompt_code": prompt_code, "versions": [], "mode": "in_memory"}
    versions = await prompt_repo.list_versions(prompt_code)
    return {"prompt_code": prompt_code, "versions": versions}


@router.post("/prompts/{prompt_code}/activate")
async def activate_prompt(
    prompt_code: str,
    payload: ActivatePromptIn,
    request: Request,
) -> Dict[str, Any]:
    container = container_from(request)
    prompt_repo = container.extras.get("prompt_repo")
    if prompt_repo is None:
        raise HTTPException(status_code=501, detail="Requires Postgres")
    try:
        await prompt_repo.activate(prompt_code, payload.version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"prompt_code": prompt_code, "active_version": payload.version}
