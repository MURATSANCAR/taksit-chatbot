"""ADR-013 Query Golden Set loader, catalog fixture, and metrics."""

from __future__ import annotations

from .catalog import build_query_golden_test_catalog
from .finance import evaluate_finance_gate, evaluate_finance_lane
from .loader import QueryGoldenCase, load_query_golden_cases, summarize_buckets
from .metrics import ParserLaneMetrics, evaluate_parser_gate, evaluate_parser_lane
from .retrieval import evaluate_retrieval_gate, evaluate_retrieval_lane, load_test_products

__all__ = [
    "QueryGoldenCase",
    "load_query_golden_cases",
    "summarize_buckets",
    "build_query_golden_test_catalog",
    "ParserLaneMetrics",
    "evaluate_parser_lane",
    "evaluate_parser_gate",
    "evaluate_retrieval_lane",
    "evaluate_retrieval_gate",
    "load_test_products",
    "evaluate_finance_lane",
    "evaluate_finance_gate",
]
