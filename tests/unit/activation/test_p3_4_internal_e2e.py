"""Unit tests for P3.4 internal access + trace completeness (no live API)."""

from __future__ import annotations

from taksitlio.api.internal_access import evaluate_internal_access
from taksitlio.applicability_readiness.tracing import REQUIRED_SPAN_NAMES, TraceRecorder
from taksitlio.progressive_results import build_partial_snapshot


def test_external_path_allowed_without_headers() -> None:
    d = evaluate_internal_access(
        {},
        flag_status="INTERNAL",
        flag_config={"cohort_id": 1, "cohort_version": 1},
        configured_token="secret",
    )
    assert d.allowed and not d.is_internal


def test_forged_internal_traffic_rejected() -> None:
    d = evaluate_internal_access(
        {"X-Taksitlio-Traffic": "internal", "X-Taksitlio-Internal-Token": "wrong"},
        flag_status="INTERNAL",
        flag_config={"cohort_id": 1},
        configured_token="secret",
    )
    assert not d.allowed
    assert d.reason == "invalid_internal_token"


def test_cohort_id_manipulation_rejected() -> None:
    d = evaluate_internal_access(
        {
            "X-Taksitlio-Traffic": "internal",
            "X-Taksitlio-Internal-Token": "secret",
            "X-Taksitlio-Cohort-Id": "999",
        },
        flag_status="INTERNAL",
        flag_config={"cohort_id": 1, "cohort_version": 1},
        configured_token="secret",
    )
    assert not d.allowed
    assert d.reason == "cohort_id_manipulation"


def test_build_partial_snapshot_emits_ranking_spans() -> None:
    tr = TraceRecorder(trace_id="t1")
    snap = build_partial_snapshot(
        query_version=1,
        products=[
            {
                "product_id": "1",
                "display_name": "Phone",
                "merchant_display_name": "Vatan",
                "price": 1000,
                "category_tokens": ["telefon"],
            }
        ],
        constraints={"positive_categories": [{"display_name": "telefon", "include_tokens": ["telefon"]}]},
        trace=tr,
    )
    names = {s.name for s in tr.spans}
    assert "constraint.filter" in names
    assert "ranking.score" in names
    assert "ranking.select_topk" in names
    assert tr.ranking_span_ms() >= 0
    assert snap.query_version == 1


def test_required_span_names_include_auth_and_cohort() -> None:
    assert "search.authorization" in REQUIRED_SPAN_NAMES
    assert "search.cohort.resolve" in REQUIRED_SPAN_NAMES
