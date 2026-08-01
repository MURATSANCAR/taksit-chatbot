"""Query understanding package (ADR-011)."""

from taksitlio.query_understanding.fast_parser import (
    CatalogHints,
    FastParseResult,
    ResolvedEntityRef,
    detect_ranking_mode,
    fast_parse,
)
from taksitlio.query_understanding.gap_detector import GapAnalysis, Uncertainty, detect_gaps

__all__ = [
    "CatalogHints",
    "FastParseResult",
    "GapAnalysis",
    "ResolvedEntityRef",
    "Uncertainty",
    "detect_gaps",
    "detect_ranking_mode",
    "fast_parse",
]
