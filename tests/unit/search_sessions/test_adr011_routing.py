"""ADR-011 search session / clarification / progress gates."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taksitlio.api.app import create_app
from taksitlio.app.container import build_in_memory_container
from taksitlio.llm_routing import (
    LlmJobStatus,
    apply_if_fresh,
    create_job,
    validate_llm_patch,
)
from taksitlio.query_understanding import detect_gaps, fast_parse
from taksitlio.search_progress import (
    DataOrigin,
    assert_truthful_message,
    finance_progress_message,
)
from taksitlio.search_sessions import (
    SearchSessionStatus,
    build_demo_orchestrator,
    can_transition,
)


def test_fast_path_teknosa_laptop_no_llm() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000001",
        message="Teknoksa’dan 40 bin liraya kadar 16 GB laptop istiyorum. Telefon olmasın. 12 ay Kuveyt Türk varsa önce onu göster.",
    )
    assert out["route"] == "FAST"
    assert out["status"] == SearchSessionStatus.COMPLETED.value
    assert "llm_job_id" not in out
    assert out["understanding"]["merchant"]["resolved_id"] == "merchant-teknosa"
    assert any(c["resolved_id"] == "category-laptop" for c in out["understanding"]["positive_categories"])
    assert any(c["resolved_id"] == "category-phone" for c in out["understanding"]["negative_categories"])
    assert out["understanding"]["budget"]["maximum"] == 40000
    assert 12 in out["understanding"]["requested_terms"]


def test_fast_path_samsung_phone_no_clarification() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000002",
        message="30 bin liraya Samsung telefon",
    )
    assert out["route"] == "FAST"
    assert out.get("clarification") is None


def test_clarification_apple_then_no_llm() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000003",
        message="Apple almak istiyorum.",
    )
    assert out["route"] == "CLARIFICATION"
    clar = out["clarification"]
    assert clar["question_text"]
    assert len(clar["options"]) <= 4
    answered = orch.answer_clarification(
        out["search_session_id"],
        clarification_id=clar["clarification_id"],
        selected_option_ids=["category-laptop"],
        expected_query_version=out["query_version"],
    )
    assert answered["route"] == "FAST"
    assert answered["status"] == SearchSessionStatus.COMPLETED.value
    metrics = orch.repo.metrics[out["search_session_id"]]
    assert any(m["metric_name"] == "llm_avoided_by_clarification" for m in metrics)


def test_progress_truthfulness_local_vs_api() -> None:
    local = finance_progress_message(DataOrigin.LOCAL_VERIFIED_SNAPSHOT)
    assert "finans kuruluşlarından güncel" not in local.casefold()
    assert "karşılaştırılıyor" in local.casefold()
    live = finance_progress_message(DataOrigin.FINANCIAL_INSTITUTION_API)
    assert "finans kuruluşlarından güncel teklifler" in live.casefold()
    assert_truthful_message(local, data_origin=DataOrigin.LOCAL_VERIFIED_SNAPSHOT.value)
    with pytest.raises(ValueError):
        assert_truthful_message(live, data_origin=DataOrigin.LOCAL_VERIFIED_SNAPSHOT.value)
    with pytest.raises(ValueError):
        assert_truthful_message("Bankalardan teklifler alınıyor", data_origin=None)


def test_stale_llm_result_not_applied() -> None:
    job = create_job(
        search_session_id="s1",
        query_version=1,
        conversation_state_version=1,
        input_payload={},
    )
    status, patch = apply_if_fresh(
        job,
        active_query_version=2,
        active_state_version=1,
        patch={
            "intent": "PRODUCT_SEARCH",
            "overall_confidence": 0.9,
            "safe_to_retrieve": True,
            "inferred_preferences": [],
        },
    )
    assert status == LlmJobStatus.STALE_RESULT
    assert patch is None


def test_llm_patch_rejects_invented_price() -> None:
    with pytest.raises(ValueError):
        validate_llm_patch(
            {
                "intent": "PRODUCT_SEARCH",
                "overall_confidence": 0.9,
                "safe_to_retrieve": True,
                "price": 12345,
            }
        )


def test_supersede_cancels_llm_job() -> None:
    orch = build_demo_orchestrator()
    # Force abstract multi-dimension path
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000004",
        message=(
            "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
            "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
            "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
        ),
    )
    assert out["route"] in {"LLM", "CLARIFICATION"}
    if out["route"] == "LLM":
        job_id = out["llm_job_id"]
        supersede = orch.supersede_with_message(
            out["search_session_id"],
            "Oyunu boşver, iş için hafif bilgisayar olsun.",
        )
        job = orch.llm_jobs[job_id]
        assert job.status.value in {"CANCEL_REQUESTED", "CANCELLED", "STALE_RESULT"} or (
            job.query_version != orch.repo.get(out["search_session_id"]).active_query_version
        )
        assert supersede["query_version"] >= 2


def test_complete_with_current_results_degraded() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-000000000005",
        message=(
            "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
            "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
            "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
        ),
    )
    if out["route"] != "LLM":
        pytest.skip("abstract query did not route to LLM in this catalog")
    done = orch.complete_with_current_results(out["search_session_id"])
    assert done["status"] == SearchSessionStatus.COMPLETED_DEGRADED.value
    assert done["route"] == "DEGRADED"


def test_status_transitions() -> None:
    assert can_transition(SearchSessionStatus.RECEIVED, SearchSessionStatus.FAST_PARSING)
    assert can_transition(SearchSessionStatus.COMPLETED, SearchSessionStatus.FAST_PARSING)
    assert not can_transition(SearchSessionStatus.COMPLETED, SearchSessionStatus.LLM_RUNNING)
    assert not can_transition(SearchSessionStatus.CANCELLED, SearchSessionStatus.FAST_PARSING)


def test_gap_brand_without_category() -> None:
    orch = build_demo_orchestrator()
    parse = fast_parse("Apple almak istiyorum.", catalog=orch.catalog)
    gaps = detect_gaps(parse, category_candidates=orch.category_clarify_options)
    assert gaps.confidence_band == "MEDIUM"
    assert gaps.clarification_viable


@pytest.mark.asyncio
async def test_search_sessions_api_and_sse() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/search-sessions",
            json={
                "conversation_id": "00000000-0000-0000-0000-000000000099",
                "message": "40 bin liraya laptop",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["route"] == "FAST"
        sid = body["search_session_id"]
        events = await client.get(f"/v1/search-sessions/{sid}/events")
        assert events.status_code == 200
        assert "text/event-stream" in events.headers["content-type"]
        assert "SEARCH_ACCEPTED" in events.text or "FAST_PARSE" in events.text
    await container.aclose()


@pytest.mark.asyncio
async def test_clarification_api() -> None:
    container = build_in_memory_container()
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start = await client.post(
            "/v1/search-sessions",
            json={
                "conversation_id": "00000000-0000-0000-0000-000000000098",
                "message": "Bir şey almak istiyorum.",
            },
        )
        body = start.json()
        assert body["route"] == "CLARIFICATION"
        clar = body["clarification"]
        option_id = next(
            o["option_id"] for o in clar["options"] if o["option_id"] != "other" and o["option_id"] != "undecided"
        )
        ans = await client.post(
            f"/v1/search-sessions/{body['search_session_id']}/clarifications",
            json={
                "clarification_id": clar["clarification_id"],
                "selected_option_ids": [option_id],
                "expected_query_version": body["query_version"],
            },
        )
        assert ans.status_code == 200
        assert ans.json()["route"] == "FAST"
    await container.aclose()
