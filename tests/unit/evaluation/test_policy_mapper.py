"""SemanticMatchPolicyMapper V008 legacy compatibility."""

from __future__ import annotations

import pytest

from taksitlio.semantic_matching.policy import (
    SemanticMatchPolicy,
    SemanticMatchPolicyMapper,
)


def test_legacy_storage_row_maps_to_canonical_fields():
    row = {
        "policy_code": "CATEGORY_MATCH_DEFAULT",
        "minimum_score": 0.5,
        "clarify_score_gap": 0.12,
        "minimum_auto_select_score": 0.7,
        "maximum_candidates": 3,
        "alias_weight": 0.4,
        "policy_version": 4,
    }
    policy = SemanticMatchPolicyMapper.from_storage(row)
    assert policy.minimum_candidate_score == 0.5
    assert policy.minimum_auto_select_gap == 0.12
    assert policy.minimum_auto_select_score == 0.7
    assert policy.alias_weight == 0.4
    assert policy.policy_version == 4


def test_canonical_row_maps_to_canonical_fields():
    row = {
        "policy_code": "CATEGORY_MATCH_DEFAULT",
        "minimum_candidate_score": 0.5,
        "minimum_auto_select_score": 0.7,
        "minimum_auto_select_gap": 0.09,
    }
    policy = SemanticMatchPolicyMapper.from_storage(row)
    assert policy.minimum_candidate_score == 0.5
    assert policy.minimum_auto_select_gap == 0.09


def test_to_storage_uses_v008_column_names():
    policy = SemanticMatchPolicy(
        minimum_candidate_score=0.6,
        minimum_auto_select_score=0.75,
        minimum_auto_select_gap=0.1,
    )
    stored = SemanticMatchPolicyMapper.to_storage(policy)
    assert stored["minimum_score"] == 0.6
    assert stored["clarify_score_gap"] == 0.1
    assert stored["minimum_auto_select_score"] == 0.75
