"""Ranking adaptation — champion/challenger with safety floor."""

from taksitlio.ranking_adaptation.champion_challenger import (
    RankingFeedbackEvent,
    RankingGateResult,
    RankingPolicyVersion,
    assert_safety_floor_preserved,
    evaluate_promotion_gate,
    shadow_compare,
)

__all__ = [
    "RankingFeedbackEvent",
    "RankingGateResult",
    "RankingPolicyVersion",
    "assert_safety_floor_preserved",
    "evaluate_promotion_gate",
    "shadow_compare",
]
