from __future__ import annotations

from fastapi import APIRouter, Request

from taksitlio.api.deps import container_from

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "taksitlio-chatbot"}


@router.get("/ready")
async def ready(request: Request) -> dict:
    payload: dict = {"status": "ready"}
    try:
        container = container_from(request)
        storage = container.extras.get("media_storage")
        if storage is not None:
            from taksitlio.media.config import describe_object_storage

            st = describe_object_storage(storage)
            payload["media_storage"] = {
                "backend": st.backend,
                "ready": st.ready,
                "placeholder_cdn": st.placeholder_cdn,
            }
            if not st.ready:
                payload["status"] = "degraded"
    except Exception:  # noqa: BLE001
        pass
    return payload
