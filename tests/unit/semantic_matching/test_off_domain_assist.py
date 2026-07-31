"""Off-domain / general-chat refuse gate (product assist only)."""

from __future__ import annotations

import pytest

from taksitlio.semantic_matching.query_intent import (
    QueryIntentKind,
    classify_query_intent,
    is_off_domain_for_assist,
)


@pytest.mark.parametrize(
    "utterance",
    (
        "merhaba",
        "nasılsın",
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


def test_travel_out_of_scope_refused() -> None:
    assert is_off_domain_for_assist("kapadokya otel rezervasyonu") is True
