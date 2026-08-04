"""Guest need_extract card from understanding."""

from __future__ import annotations

from taksitlio.search_sessions.need_extract import attach_need_extract, build_need_extract


def test_build_need_extract_category_and_budget() -> None:
    extract = build_need_extract(
        {
            "positive_categories": [{"display_name": "Cep Telefonu"}],
            "budget": {"type": "TOTAL_MAXIMUM", "value": 40000},
        }
    )
    assert extract is not None
    assert extract["title"] == "Anladıklarım"
    assert extract["rows"] == [
        {"k": "Kategori", "v": "Cep Telefonu"},
        {"k": "Bütçe", "v": "≈ 40.000 TL"},
    ]


def test_build_need_extract_empty_without_facts() -> None:
    assert build_need_extract({"intent": "PRODUCT_SEARCH"}) is None
    assert build_need_extract(None) is None


def test_attach_need_extract_on_payload() -> None:
    payload = {
        "understanding": {
            "positive_categories": [{"display_name": "Tablet"}],
            "brands": [{"display_name": "Apple"}],
            "budget": {"maximum": 15000},
        }
    }
    attach_need_extract(payload)
    assert payload["need_extract"]["rows"][0]["v"] == "Tablet"
    assert payload["need_extract"]["rows"][1]["v"] == "Apple"
    assert payload["need_extract"]["rows"][2]["v"] == "≈ 15.000 TL"
