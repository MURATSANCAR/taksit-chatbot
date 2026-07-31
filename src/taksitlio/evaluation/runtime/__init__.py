"""Runtime evaluation helpers (ADR-009) — separate from test-double baseline."""

from taksitlio.evaluation.runtime.fast_quality import (
    FastExtractionMetrics,
    score_fast_extraction,
)
from taksitlio.evaluation.runtime.comparison import (
    RuntimeQualityComparison,
    compare_to_baseline,
)

__all__ = [
    "FastExtractionMetrics",
    "RuntimeQualityComparison",
    "compare_to_baseline",
    "score_fast_extraction",
]
