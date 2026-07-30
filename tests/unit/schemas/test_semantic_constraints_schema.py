"""Schema-level checks for the semantic_constraints extension (ADR-006 §C)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SCHEMA_DIR = Path(__file__).resolve().parents[3] / "src" / "taksitlio" / "schemas"


def _validator(schema_name: str) -> Draft202012Validator:
    with (SCHEMA_DIR / schema_name).open(encoding="utf-8") as fh:
        schema = json.load(fh)
    return Draft202012Validator(schema)


def _profile_shell(**extra) -> dict:
    """Minimum valid need_profile payload; extra overrides fields."""

    base = {
        "intent": {"type": "PRODUCT_PURCHASE", "confidence": 0.9},
        "need_description": "example",
        "budget": {
            "type": "UNKNOWN",
            "value": None,
            "minimum": None,
            "maximum": None,
            "monthly_payment": None,
            "currency": "TRY",
        },
        "preferences": [],
        "usage_context": [],
        "entities": [],
        "ambiguities": [],
        "clarification": {"required": False, "question_intent": None},
        "confidence": 0.9,
    }
    base.update(extra)
    return base


def test_need_profile_accepts_semantic_constraints() -> None:
    v = _validator("need_profile.schema.json")
    payload = _profile_shell(
        semantic_constraints={
            "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}],
            "negative": [
                {"concept": "telefon", "provenance": "EXPLICIT_NEGATION", "weight": 0.9}
            ],
            "corrections": [
                {"concept": "tablet", "provenance": "USER_CORRECTION"}
            ],
        },
    )
    errors = sorted(v.iter_errors(payload), key=lambda e: e.path)
    assert not errors, [e.message for e in errors]


def test_need_seed_accepts_semantic_constraints() -> None:
    v = _validator("need_seed.schema.json")
    payload = {
        "need_description": "telefon",
        "semantic_constraints": {
            "positive": [{"concept": "laptop", "provenance": "EXPLICIT"}]
        },
    }
    errors = list(v.iter_errors(payload))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("schema", ("need_seed.schema.json",))
def test_semantic_constraints_rejects_unknown_provenance(schema: str) -> None:
    v = _validator(schema)
    payload = {
        "need_description": "example",
        "semantic_constraints": {
            "positive": [{"concept": "x", "provenance": "MADE_UP"}],
        },
    }
    errors = list(v.iter_errors(payload))
    assert errors


@pytest.mark.parametrize("schema", ("need_seed.schema.json",))
def test_semantic_constraint_forbids_category_id(schema: str) -> None:
    """Constraints reference *concepts*, never a hard category_id."""

    v = _validator(schema)
    payload = {
        "need_description": "example",
        "semantic_constraints": {
            "positive": [
                {
                    "concept": "laptop",
                    "provenance": "EXPLICIT",
                    "category_id": "should-not-be-allowed",
                }
            ]
        },
    }
    errors = list(v.iter_errors(payload))
    assert errors
