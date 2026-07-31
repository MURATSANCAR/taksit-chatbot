"""Guest UI constraint chips are fully suppressed."""

from taksitlio.query_state import QueryNeedState, chips_from_state


def test_chips_from_state_always_empty() -> None:
    state = QueryNeedState(
        budget={"maximum": 30000, "currency": "TRY", "type": "RANGE"},
        active_categories=[{"resolved_id": "phone", "display_name": "Cep Telefonu"}],
        usage_contexts=["gaming"],
        preferences=["lightweight"],
        required_attributes=[{"attribute_id": "ram_gb", "value": 16}],
        state_version=1,
    )
    assert chips_from_state(state) == []
