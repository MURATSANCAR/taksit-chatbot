"""Privacy contract: standard reports never contain raw utterances."""

from __future__ import annotations

import pytest

from taksitlio.evaluation.errors import PrivacyViolationError
from taksitlio.evaluation.privacy import (
    assert_report_is_safe,
    redact_report,
    utterance_hash,
)


def test_redact_report_strips_raw_utterance_keys():
    payload = {
        "run_id": "abc",
        "cases": [
            {"case_id": "c1", "utterance": "gizli metin", "score": 0.5},
            {"case_id": "c2", "raw_text": "diğer metin"},
        ],
    }
    scrubbed = redact_report(payload)
    for case in scrubbed["cases"]:
        assert "utterance" not in case
        assert "raw_text" not in case


def test_assert_report_is_safe_rejects_raw_utterance():
    payload = {"nested": {"case": {"utterance": "boom"}}}
    with pytest.raises(PrivacyViolationError):
        assert_report_is_safe(payload)


def test_utterance_hash_is_deterministic_and_short():
    assert utterance_hash("Kamera kalitesi iyi bir telefon") == utterance_hash(
        "  kamera kalitesi iyi bir telefon  "
    )
    assert len(utterance_hash("x")) == 16
