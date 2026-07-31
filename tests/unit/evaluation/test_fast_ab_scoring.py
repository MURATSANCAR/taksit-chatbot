"""Unit tests for FAST A/B scoring helpers (ADR-009 P1.1)."""

from __future__ import annotations

from taksitlio.evaluation.runtime.fast_quality import score_fast_extraction


def test_score_fast_extraction_accepts_concept_key_with_turkish_fold() -> None:
    metrics = score_fast_extraction(
        [
            {
                "expected_constraints": {
                    "positive": [{"concept": "Telefon"}],
                    "negative": [{"concept": "tablet"}],
                    "corrections": [
                        {
                            "previous_concept": "tablet",
                            "replacement_concept": "telefon",
                        }
                    ],
                },
                "predicted_constraints": {
                    "positive": [{"concept": "telefon"}],
                    "negative": [{"concept": "Tablet"}],
                    "corrections": [
                        {
                            "previous_concept": "tablet",
                            "replacement_concept": "telefon",
                        }
                    ],
                },
                "expected_need_profile": {},
                "predicted_need_profile": {
                    "clarification": {"required": False},
                },
            }
        ]
    )
    assert metrics.positive_tp == 1
    assert metrics.negative_tp == 1
    assert metrics.correction_tp == 1
    assert metrics.negative_constraint_recall == 1.0
    assert metrics.correction_recall == 1.0


def test_score_fast_truncation_is_not_schema_failure() -> None:
    metrics = score_fast_extraction(
        [
            {
                "error": "TRUNCATED",
                "expected_constraints": {},
                "predicted_constraints": {},
                "expected_need_profile": {},
                "predicted_need_profile": {},
            }
        ]
    )
    assert metrics.truncated_count == 1
    assert metrics.invalid_schema_count == 0
    assert metrics.timeout_count == 0


def test_score_fast_timeout_does_not_count_as_schema_failure() -> None:
    metrics = score_fast_extraction(
        [
            {
                "error": "TIMEOUT",
                "expected_constraints": {},
                "predicted_constraints": {},
                "expected_need_profile": {},
                "predicted_need_profile": {},
            }
        ]
    )
    assert metrics.timeout_count == 1
    assert metrics.invalid_schema_count == 0
    assert metrics.truncated_count == 0
