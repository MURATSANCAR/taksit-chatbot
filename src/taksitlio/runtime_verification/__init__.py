"""ADR-009 real runtime verification — dependency probes and provisional gates.

Matcher quality is out of scope. This package only measures whether Redis,
PostgreSQL/pgvector, FAST, and CATEGORY_EMBEDDING are actually reachable and
whether measured quality clears the provisional bar without silent fallbacks.
"""

from taksitlio.runtime_verification.dependencies import (
    DependencyCode,
    DependencyProbeResult,
    RuntimeDependencyReport,
)
from taksitlio.runtime_verification.evidence import RuntimeEvidence
from taksitlio.runtime_verification.gate import (
    CampaignGateStatus,
    ProvisionalGateResult,
    RuntimeGateStatus,
    evaluate_campaign_gate,
    evaluate_provisional_gate,
    evaluate_runtime_gate,
)

__all__ = [
    "CampaignGateStatus",
    "DependencyCode",
    "DependencyProbeResult",
    "ProvisionalGateResult",
    "RuntimeDependencyReport",
    "RuntimeEvidence",
    "RuntimeGateStatus",
    "evaluate_campaign_gate",
    "evaluate_provisional_gate",
    "evaluate_runtime_gate",
]
