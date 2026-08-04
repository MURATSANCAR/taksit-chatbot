"""Off-domain / general-chat refuse gate (product assist only)."""

from __future__ import annotations

import pytest

from taksitlio.semantic_matching.query_intent import (
    GREETING_ASSIST_MESSAGE,
    QueryIntentKind,
    assist_message_for_utterance,
    classify_query_intent,
    is_greeting_utterance,
    is_off_domain_for_assist,
)


@pytest.mark.parametrize(
    "utterance",
    (
        "merhaba",
        "nasılsın",
        "tanışalım",
        "sen kimsin",
        "hava durumu nasıl",
        "bana bir fıkra anlat",
        "ödevimi yap",
        "siyaset hakkında ne düşünüyorsun",
        "sohbet edelim",
    ),
)
def test_general_chat_is_off_domain(utterance: str) -> None:
    assert is_off_domain_for_assist(utterance) is True
    assert classify_query_intent(utterance) is QueryIntentKind.OUT_OF_SCOPE


def test_greeting_gets_warm_intro() -> None:
    assert is_greeting_utterance("sen kimsin") is True
    assert is_greeting_utterance("tanışalım") is True
    assert assist_message_for_utterance("merhaba") == GREETING_ASSIST_MESSAGE
    assert "ihtiyaç analizi" in assist_message_for_utterance("kimsin")
    assert "Tanıştığımıza memnun oldum" not in assist_message_for_utterance("kimsin")


def test_offtopic_gets_redirect_not_greeting() -> None:
    msg = assist_message_for_utterance("hava durumu nasıl")
    assert is_greeting_utterance("hava durumu nasıl") is False
    assert "Tanıştığımıza memnun oldum" not in msg
    assert "Bu konuda yardımcı olamam" in msg


@pytest.mark.parametrize(
    "utterance",
    (
        "cep telefonu arıyorum bütçem 40 bin",
        "buzdolabı bakıyorum",
        "iphone 15",
        "merhaba telefon almak istiyorum",
    ),
)
def test_product_queries_stay_in_domain(utterance: str) -> None:
    assert is_off_domain_for_assist(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    (
        "en ucuzlarını getir bana",
        "en ucuzularını getir bana",
        "en ucularını getir bana babama alacağım",
        "en ucuz olsun",
        "en düşük fiyat",
        "taksit sayısın en az olanları getir bana",
        "en düşük aylık ödeme",
        "en uzun vade",
    ),
)
def test_ranking_followups_stay_in_domain(utterance: str) -> None:
    """Sort refinements must not refuse even without product nouns."""

    assert is_off_domain_for_assist(utterance) is False
    assert classify_query_intent(utterance) is QueryIntentKind.PRODUCT_PURCHASE


def test_chitchat_still_refused() -> None:
    assert is_off_domain_for_assist("sadece konuşalım") is True
    assert classify_query_intent("sadece konuşalım") is QueryIntentKind.OUT_OF_SCOPE


def test_travel_out_of_scope_refused() -> None:
    assert is_off_domain_for_assist("kapadokya otel rezervasyonu") is True
