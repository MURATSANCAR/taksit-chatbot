"""Follow-up session reopen + short-term ranking refinements."""

from __future__ import annotations

import pytest

from taksitlio.product_query.chat_bridge import infer_ranking_mode
from taksitlio.product_query.ranking import RankableProduct, RankingMode, rank_products
from taksitlio.progressive_results import build_partial_snapshot, score_partial_candidate
from taksitlio.query_state import QueryNeedState, hydrate_parse_from_state, merge_parse_into_state
from taksitlio.query_understanding import detect_ranking_mode, fast_parse
from taksitlio.search_sessions import (
    InvalidTransitionError,
    SearchSessionStatus,
    build_demo_orchestrator,
    can_transition,
)
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.repository import InMemorySearchSessionRepository
from taksitlio.search_sessions.status import is_hard_terminal


def _phone_pool() -> list[dict]:
    return [
        {
            "product_id": "p-phone-9",
            "display_name": "Telefon 9 ay",
            "category": "telefon",
            "merchant_display_name": "Teknosa",
            "price": 41929,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.9,
            "best_monthly_payment": 4659,
            "best_total_repayment": 41931,
            "best_term_months": 9,
            "best_finance": {
                "term_months": 9,
                "monthly_payment": 4659,
                "total_repayment": 41931,
                "institution_display_name": "İş Bankası",
            },
        },
        {
            "product_id": "p-phone-12",
            "display_name": "Telefon 12 ay",
            "category": "telefon",
            "merchant_display_name": "Teknosa",
            "price": 34999,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.88,
            "best_monthly_payment": 3727,
            "best_total_repayment": 44724,
            "best_term_months": 12,
            "best_finance": {
                "term_months": 12,
                "monthly_payment": 3727,
                "total_repayment": 44724,
                "institution_display_name": "Fibabanka",
            },
        },
        {
            "product_id": "p-phone-6",
            "display_name": "Telefon 6 ay",
            "category": "telefon",
            "merchant_display_name": "MediaMarkt",
            "price": 40999,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.86,
            "best_monthly_payment": 6833,
            "best_total_repayment": 40998,
            "best_term_months": 6,
            "best_finance": {
                "term_months": 6,
                "monthly_payment": 6833,
                "total_repayment": 40998,
                "institution_display_name": "İş Bankası",
            },
        },
    ]


def _phone_orchestrator() -> SearchOrchestrator:
    orch = build_demo_orchestrator()
    orch.product_pool = _phone_pool()
    return orch


def test_completed_can_reopen_to_fast_parsing() -> None:
    assert can_transition(SearchSessionStatus.COMPLETED, SearchSessionStatus.FAST_PARSING)
    assert can_transition(
        SearchSessionStatus.COMPLETED_DEGRADED, SearchSessionStatus.FAST_PARSING
    )
    assert can_transition(
        SearchSessionStatus.PARTIAL_RESULTS_READY, SearchSessionStatus.FAST_PARSING
    )
    assert not can_transition(SearchSessionStatus.CANCELLED, SearchSessionStatus.FAST_PARSING)
    assert is_hard_terminal(SearchSessionStatus.CANCELLED)
    assert not is_hard_terminal(SearchSessionStatus.COMPLETED)


def test_supersede_after_completed_keeps_session_and_ranks_shortest() -> None:
    orch = _phone_orchestrator()
    first = orch.start(
        conversation_id="00000000-0000-0000-0000-00000000f001",
        message="telefon alacağım 40.000 liralık",
    )
    assert first["route"] == "FAST"
    assert first["status"] == SearchSessionStatus.COMPLETED.value
    sid = first["search_session_id"]

    follow = orch.supersede_with_message(sid, "taksit sayısın en az olanları getir bana")
    assert follow["search_session_id"] == sid
    assert follow["query_version"] >= 2
    assert follow["status"] == SearchSessionStatus.COMPLETED.value
    assert follow["understanding"].get("ranking_mode") == "SHORTEST_TERM"
    assert follow["understanding"].get("budget")
    cats = follow["understanding"].get("positive_categories") or []
    assert cats, "prior telefon category must be hydrated"
    assert not any(
        "olan" in str(c.get("display_name") or "").casefold()
        or "getir" in str(c.get("display_name") or "").casefold()
        for c in cats
    )
    products = (follow.get("results") or {}).get("products") or []
    assert products
    assert follow["results"]["label"] == "En kısa vade"
    assert products[0]["product_id"] == "p-phone-6"
    terms = [
        (p.get("best_finance_summary") or {}).get("term_months") for p in products
    ]
    assert terms == sorted(t for t in terms if t is not None)
    assert "reply" in follow


def test_supersede_cheapest_followup_not_refused() -> None:
    """Bare 'en ucuzlarını getir' must re-rank, not OUT_OF_SCOPE refuse."""

    orch = _phone_orchestrator()
    first = orch.start(
        conversation_id="00000000-0000-0000-0000-00000000f011",
        message="telefon alacağım",
    )
    assert first["route"] != "OUT_OF_SCOPE"
    sid = first["search_session_id"]

    follow = orch.supersede_with_message(sid, "en ucuzlarını getir bana")
    assert follow["route"] != "OUT_OF_SCOPE"
    assert follow["search_session_id"] == sid
    assert follow["understanding"].get("ranking_mode") == "CHEAPEST_PRODUCT_PRICE"
    products = (follow.get("results") or {}).get("products") or []
    assert products
    assert products[0]["product_id"] == "p-phone-12"
    assert follow["results"]["label"] == "En düşük ürün fiyatı"


def test_hard_terminal_supersede_rejected() -> None:
    orch = build_demo_orchestrator()
    out = orch.start(
        conversation_id="00000000-0000-0000-0000-00000000f002",
        message="30 bin liraya Samsung telefon",
    )
    session = orch.repo.get(out["search_session_id"])
    assert session is not None
    session.status = SearchSessionStatus.CANCELLED
    with pytest.raises(InvalidTransitionError):
        orch.supersede_with_message(out["search_session_id"], "en az taksit getir")


def test_fast_parser_ranking_cues_no_bogus_categories() -> None:
    assert detect_ranking_mode("taksit sayısın en az olanları getir bana") == "SHORTEST_TERM"
    assert detect_ranking_mode("en ucuz olsun") == "CHEAPEST_PRODUCT_PRICE"
    assert detect_ranking_mode("en uzun vade") == "LONGEST_TERM"
    assert detect_ranking_mode("en düşük aylık ödeme") == "LOWEST_MONTHLY_PAYMENT"

    parse = fast_parse("taksit sayısın en az olanları getir bana")
    assert parse.ranking_mode == "SHORTEST_TERM"
    assert parse.positive_categories == []


def test_hydrate_parse_from_prior_state() -> None:
    state = QueryNeedState()
    first = fast_parse("telefon alacağım bütçem 40 bin")
    merge_parse_into_state(state, first.to_dict())
    assert state.active_categories
    assert state.budget

    refine = fast_parse("taksit sayısın en az olanları getir")
    assert refine.ranking_mode == "SHORTEST_TERM"
    assert not refine.positive_categories
    merge_parse_into_state(state, refine.to_dict())
    hydrated = hydrate_parse_from_state(refine, state)
    assert hydrated.positive_categories
    assert hydrated.budget
    assert hydrated.ranking_mode == "SHORTEST_TERM"
    assert state.payment_preferences.get("ranking_mode") == "SHORTEST_TERM"


def test_shortest_and_longest_term_rank_products() -> None:
    items = (
        RankableProduct(
            product_id="long",
            price=30000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=2500,
            best_total_repayment=30000,
            best_term_months=12,
            finance_active=True,
            rate_fresh=True,
        ),
        RankableProduct(
            product_id="short",
            price=30000,
            stock_status="AVAILABLE",
            price_freshness="FRESH",
            has_primary_image=True,
            best_monthly_payment=5000,
            best_total_repayment=30000,
            best_term_months=6,
            finance_active=True,
            rate_fresh=True,
        ),
    )
    short = rank_products(items, mode=RankingMode.SHORTEST_TERM)
    assert short[0].product_id == "short"
    assert short[0].label == "En kısa vade"
    long = rank_products(items, mode=RankingMode.LONGEST_TERM)
    assert long[0].product_id == "long"


def test_progressive_shortest_term_sort() -> None:
    snap = build_partial_snapshot(
        query_version=1,
        products=_phone_pool(),
        constraints={
            "positive_categories": [
                {"resolved_id": "free_text:telefon", "display_name": "telefon"}
            ],
            "ranking_mode": "SHORTEST_TERM",
        },
    )
    assert snap.label == "En kısa vade"
    assert snap.products[0].product_id == "p-phone-6"
    assert score_partial_candidate(
        _phone_pool()[2], {"ranking_mode": "SHORTEST_TERM", "positive_categories": []}
    ) > score_partial_candidate(
        _phone_pool()[1], {"ranking_mode": "SHORTEST_TERM", "positive_categories": []}
    )


def test_infer_ranking_mode_honors_explicit_term_prefs() -> None:
    assert (
        infer_ranking_mode({"ranking_mode": "SHORTEST_TERM"})
        is RankingMode.SHORTEST_TERM
    )
    assert (
        infer_ranking_mode({"payment_preferences": {"ranking_mode": "LONGEST_TERM"}})
        is RankingMode.LONGEST_TERM
    )
    assert (
        infer_ranking_mode({"preferences": ["ranking:CHEAPEST_PRODUCT_PRICE"]})
        is RankingMode.CHEAPEST_PRODUCT_PRICE
    )
