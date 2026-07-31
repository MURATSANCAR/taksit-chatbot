"""Unit tests for remote+deterministic hybrid constraint merge."""

from __future__ import annotations

from taksitlio.understanding.fast.hybrid import hybrid_final_constraints, merge_constraint_bags


def test_hybrid_merge_fills_model_correction_gap() -> None:
    model = {
        "positive": [{"concept": "tablet", "provenance": "EXPLICIT"}],
        "negative": [{"concept": "telefon", "provenance": "EXPLICIT_NEGATION"}],
        "corrections": [],
    }
    deterministic = {
        "positive": [{"concept": "tablet", "source": "EXPLICIT"}],
        "negative": [{"concept": "telefon", "source": "EXPLICIT_NEGATION"}],
        "corrections": [
            {
                "previous_concept": "telefon",
                "replacement_concept": "tablet",
                "source": "USER_CORRECTION",
            }
        ],
    }
    merged = merge_constraint_bags(model, deterministic)
    assert any(c.concept == "tablet" for c in merged.positive)
    assert any(c.concept == "telefon" for c in merged.negative)
    assert len(merged.corrections) >= 1
    final = hybrid_final_constraints(
        model_constraints=model,
        deterministic_constraints=deterministic,
    )
    assert final.get("corrections")


def test_hybrid_merge_empty_inputs() -> None:
    merged = merge_constraint_bags({}, {})
    assert merged.positive == ()
    assert merged.negative == ()
    assert merged.corrections == ()
