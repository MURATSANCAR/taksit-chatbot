"""Constraint chips omit budget / uncertainty noise."""

from taksitlio.query_state import QueryNeedState, chips_from_state


def test_chips_skip_budget_and_uncertain_category() -> None:
    state = QueryNeedState(
        budget={"maximum": 30000, "currency": "TRY", "type": "RANGE"},
        active_categories=[],
        usage_contexts=["gaming"],
        state_version=1,
    )
    chips = chips_from_state(state)
    labels = [c["label"] for c in chips]
    kinds = [c["kind"] for c in chips]
    assert "30.000 TL’ye kadar" not in labels
    assert "Ürün türü belirsiz" not in labels
    assert "budget" not in kinds
    assert "uncertainty" not in kinds
    assert any(c["kind"] == "usage" for c in chips)


def test_chips_include_resolved_category() -> None:
    state = QueryNeedState(
        budget={"value": 25000},
        active_categories=[{"resolved_id": "phone", "display_name": "Cep Telefonu"}],
        state_version=1,
    )
    chips = chips_from_state(state)
    assert chips[0]["label"] == "Cep Telefonu"
    assert chips[0]["kind"] == "category"
