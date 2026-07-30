from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "taksitlio-chatbot"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}
