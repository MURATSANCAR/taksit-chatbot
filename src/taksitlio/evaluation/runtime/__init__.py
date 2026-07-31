"""Runtime evaluation helpers (ADR-009) — separate from test-double baseline."""

from taksitlio.evaluation.runtime.fast_quality import (
    FastExtractionMetrics,
    score_fast_extraction,
)
from taksitlio.evaluation.runtime.comparison import (
    RuntimeQualityComparison,
    compare_to_baseline,
)
from taksitlio.evaluation.runtime.fast_ab import (
    build_isolated_extractor,
    candidate_specs_from_env,
    scoring_row,
)

__all__ = [
    "FastExtractionMetrics",
    "RuntimeQualityComparison",
    "build_isolated_extractor",
    "candidate_specs_from_env",
    "compare_to_baseline",
    "score_fast_extraction",
    "scoring_row",
]
