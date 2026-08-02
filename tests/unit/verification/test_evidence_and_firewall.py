"""Evidence provenance + finance firewall unit gates."""

from __future__ import annotations

import pytest

from taksitlio.search_sessions.finance_firewall import (
    apply_finance_firewall,
    assert_no_finance_claims,
    finance_display_allowed,
)
from taksitlio.verification.evidence import (
    evidence_metric,
    evaluate_provenance_gate,
    query_hash,
)


def test_forbidden_source_rejected() -> None:
    with pytest.raises(ValueError):
        evidence_metric(
            metric_name="candidates",
            metric_value=250,
            source_type="HARDCODED_VALUE",
        )


def test_provenance_gate_detects_mismatch() -> None:
    metrics = [
        evidence_metric(
            metric_name="approved",
            metric_value=0,
            source_type="DATABASE_QUERY",
            source_table_or_endpoint="continuous_golden_cases",
            source_query_hash=query_hash("SELECT count(*) FROM continuous_golden_cases WHERE lifecycle_status='APPROVED'"),
        )
    ]
    gate = evaluate_provenance_gate(
        metrics,
        db_counts={"approved": 0},
        artifact_counts={"approved": 0},
        report_counts={"approved": 44},  # dishonest report
    )
    assert gate["pass"] is False
    assert gate["untraceable_report_metric"] == 0
    assert any("artifact_report_mismatch" in f for f in gate["failures"])


def test_finance_firewall_strips_when_blocked() -> None:
    products = [
        {
            "product_id": "p1",
            "best_finance": {
                "monthly_payment": 999,
                "term_months": 12,
                "institution_display_name": "FakeBank",
            },
            "best_monthly_payment": 999,
        }
    ]
    out = apply_finance_firewall(products, flags={"finance_display": "BLOCKED"})
    assert out[0]["best_finance"] is None
    assert assert_no_finance_claims(out[0]) == []
    assert finance_display_allowed({"finance_display": "BLOCKED"}) is False
