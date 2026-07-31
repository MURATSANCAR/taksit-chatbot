"""Regression: shoe + unformatted max budget must not ask product-type clarify."""

from __future__ import annotations

from taksitlio.query_understanding.fast_parser import _parse_budget, fast_parse
from taksitlio.query_understanding.gap_detector import detect_gaps
from taksitlio.query_clarification.policy import should_ask_clarification
from taksitlio.search_sessions.orchestrator import build_demo_orchestrator, build_empty_orchestrator


def test_parse_budget_unformatted_max_asmasin() -> None:
    budget = _parse_budget("bana ayakkabı lazım 5000 lira aşmasın")
    assert budget == {"maximum": 5000, "currency": "TRY", "type": "RANGE"}


def test_parse_budget_unformatted_plain() -> None:
    assert _parse_budget("5000 lira") == {
        "value": 5000,
        "currency": "TRY",
        "type": "APPROXIMATE",
    }
    assert _parse_budget("15000") == {
        "value": 15000,
        "currency": "TRY",
        "type": "APPROXIMATE",
    }


def test_fast_parse_ayakkabi_without_catalog() -> None:
    parse = fast_parse("bana ayakkabı lazım 5000 lira aşmasın")
    assert parse.budget and parse.budget.get("maximum") == 5000
    assert any("ayakkab" in c.display_name.casefold() for c in parse.positive_categories)
    gaps = detect_gaps(parse)
    assert gaps.confidence_band == "HIGH"
    assert not should_ask_clarification(gaps=gaps, clarification_count=0)


def test_demo_orch_ayakkabi_skips_product_type_clarify() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="shoe-1",
        message="bana ayakkabı lazım 5000 lira aşmasın",
    )
    assert out["route"] != "CLARIFICATION"
    assert out.get("clarification") is None
    cats = out["understanding"]["positive_categories"]
    assert any(
        c.get("resolved_id") == "FOOTWEAR" or "ayakkab" in str(c.get("display_name", "")).casefold()
        for c in cats
    )
    assert out["understanding"]["budget"]["maximum"] == 5000


def test_empty_orch_ayakkabi_uses_free_text_not_electronics_clarify() -> None:
    orch = build_empty_orchestrator()
    out = orch.start(
        conversation_id="shoe-2",
        message="bana ayakkabı lazım 5000 lira aşmasın",
    )
    assert out["route"] != "CLARIFICATION"
    assert out.get("clarification") is None
    cats = out["understanding"]["positive_categories"]
    assert cats
    assert all(c.get("resolved_id") != "phone" for c in cats)
