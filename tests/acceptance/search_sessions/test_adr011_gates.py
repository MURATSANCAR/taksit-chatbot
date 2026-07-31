"""ADR-011 acceptance gates (skeleton scoring)."""

from __future__ import annotations

from taksitlio.search_progress import DataOrigin, assert_truthful_message, finance_progress_message
from taksitlio.search_sessions import SearchSessionStatus, build_demo_orchestrator


def _metric_count(orch, session_id: str, name: str) -> int:
    return sum(1 for m in orch.repo.metrics.get(session_id, []) if m["metric_name"] == name)


def test_clarification_routing_gate() -> None:
    orch = build_demo_orchestrator()
    cases = [
        "Apple almak istiyorum.",
        "Çocuğum için uzun yıllar kullanabileceği bir cihaz arıyorum.",
    ]
    llm_on_clarifyable = 0
    for msg in cases:
        out = orch.start(conversation_id="c1", message=msg)
        if out["route"] == "CLARIFICATION":
            clar = out["clarification"]
            answered = orch.answer_clarification(
                out["search_session_id"],
                clarification_id=clar["clarification_id"],
                selected_option_ids=[clar["options"][0]["option_id"]],
                expected_query_version=out["query_version"],
            )
            if answered.get("route") == "LLM":
                llm_on_clarifyable += 1
        elif out["route"] == "LLM":
            # First case must not be LLM
            if "Apple" in msg:
                llm_on_clarifyable += 1
    assert llm_on_clarifyable == 0


def test_progress_truthfulness_gate() -> None:
    # Fast path uses local snapshot messages only
    orch = build_demo_orchestrator()
    out = orch.start(conversation_id="c2", message="Teknoksa’dan laptop")
    events = orch.list_event_payloads(out["search_session_id"])
    false_progress = 0
    for e in events:
        msg = (e.get("display") or {}).get("message") or ""
        try:
            assert_truthful_message(msg, data_origin=e.get("data_origin"))
        except ValueError:
            false_progress += 1
        if "finans kuruluşlarından güncel" in msg.casefold():
            if e.get("data_origin") != DataOrigin.FINANCIAL_INSTITUTION_API.value:
                false_progress += 1
    assert false_progress == 0


def test_stale_llm_protection_gate() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="c3",
        message=(
            "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
            "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
            "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
        ),
    )
    if out["route"] != "LLM":
        return
    job_id = out["llm_job_id"]
    orch.supersede_with_message(out["search_session_id"], "İş için hafif laptop olsun.")
    result = orch.complete_llm_job(
        job_id,
        {
            "intent": "PRODUCT_SEARCH",
            "overall_confidence": 0.9,
            "safe_to_retrieve": True,
            "inferred_preferences": [{"concept": "gaming", "confidence": 0.9}],
        },
    )
    assert result.get("applied") is False
    assert result.get("status") in {"STALE_RESULT", "CANCELLED"}
    assert _metric_count(orch, out["search_session_id"], "stale_llm_result") >= 1 or result[
        "status"
    ] == "CANCELLED"


def test_logo_correctness_gate() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="c4",
        message="Teknoksa’dan 40 bin liraya laptop, 12 ay Kuveyt Türk",
    )
    logos = out.get("logos") or {}
    # Only resolved merchant / institution ids from parse — no random banks
    for m in logos.get("merchant") or []:
        assert m["entity_id"] == "merchant-teknosa"
    for i in logos.get("institution") or []:
        assert i["entity_id"] == "institution-kuveyt"


def test_llm_timeout_fallback_gate() -> None:
    orch = build_demo_orchestrator()
    orch.repo.policy.hard_timeout_ms = 1
    out = orch.start(
        conversation_id="c5",
        message=(
            "Evde herkes kullanacak, bazen film izlenecek, bazen çocuklar "
            "ödev yapacak ama cihazın odada çok yer kaplamasını istemiyorum. "
            "Aylık ödeme de zorlamasın, uzun vadede mantıklı bir şey olsun."
        ),
    )
    if out["route"] != "LLM":
        return
    # Simulate elapsed by rewinding start mono
    orch.started_mono[out["search_session_id"]] = 0.0
    timed = orch.timeout_if_needed(out["search_session_id"])
    assert timed is not None
    assert timed["status"] == SearchSessionStatus.COMPLETED_DEGRADED.value
