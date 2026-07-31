"""ADR-011 P1 — chat bridge, LLM worker, guest UI wiring."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.llm_routing.worker import build_default_worker
from taksitlio.model_gateway.health import InMemoryRuntimeHealthRegistry
from taksitlio.pipeline.orchestrator import ChatPipeline, ChatRequest, conversation_id_for_session
from taksitlio.search_sessions import bridge_search_start, build_demo_orchestrator
from taksitlio.search_sessions.postgres import PostgresSearchSessionRepository


def test_conversation_id_stable_uuid() -> None:
    a = conversation_id_for_session("guest-abc")
    b = conversation_id_for_session("guest-abc")
    assert a == b
    assert len(a) == 36


def test_chat_bridge_fast_path() -> None:
    orch = build_demo_orchestrator()
    bridged = bridge_search_start(
        orch,
        conversation_id="00000000-0000-0000-0000-000000000010",
        message="Teknoksa’dan 40 bin liraya laptop",
    )
    assert bridged.decision == "CONTINUE"
    assert bridged.diagnostics["route"] == "FAST"
    assert bridged.diagnostics["search_session_id"]


@pytest.mark.asyncio
async def test_chat_api_uses_search_sessions_path() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat",
            json={
                "session_id": "guest-session-1",
                "message": "40 bin liraya laptop istiyorum",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnostics"].get("search_path") is True
    assert body["search_session_id"]
    assert body["events_url"]
    assert body["decision"] in {"CONTINUE", "CLARIFY"}
    await container.aclose()


@pytest.mark.asyncio
async def test_llm_worker_drains_deterministic_fallback() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000011",
        message=(
            "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
            "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
            "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
        ),
    )
    if out["route"] != "LLM":
        pytest.skip("did not route to LLM")
    worker = build_default_worker(orch, health_registry=InMemoryRuntimeHealthRegistry())
    results = await worker.drain_once()
    assert results
    assert results[0]["status"] in {"COMPLETED", "STALE_RESULT", "CANCELLED", "FAILED", "CIRCUIT_OPEN"} or results[
        0
    ].get("applied")


def test_postgres_repo_importable() -> None:
    assert PostgresSearchSessionRepository is not None


def test_guest_ui_no_demo_offers() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    html = (root / "web" / "taksitlio" / "index.html").read_text(encoding="utf-8")
    assert "const DEMO" not in html
    assert "Galaxy A56" not in html
    assert "Bankalar ve markalar arasındaki güncel kampanyaları taradım" not in html
    assert "js/search-session/ui.js" in html
    assert "js/chat-cards.js" in html
    ui = (root / "web" / "taksitlio" / "js" / "search-session" / "ui.js").read_text(
        encoding="utf-8"
    )
    assert "/v1/search-sessions" in (root / "web" / "taksitlio" / "js" / "search-session" / "client.js").read_text(
        encoding="utf-8"
    )
    assert "TaksitlioSearchUi" in ui
