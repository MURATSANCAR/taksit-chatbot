"""Runtime / provisional / campaign gate evaluation (ADR-009)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from taksitlio.runtime_verification.dependencies import RuntimeDependencyReport
from taksitlio.runtime_verification.evidence import RuntimeEvidence


class RuntimeGateStatus(str, Enum):
    RUNTIME_READY = "RUNTIME_READY"
    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
    RUNTIME_QUALITY_REJECT = "RUNTIME_QUALITY_REJECT"


class CampaignGateStatus(str, Enum):
    CLOSED = "CLOSED"
    READY_TO_OPEN = "READY_TO_OPEN"


@dataclass(frozen=True)
class ProvisionalGateResult:
    status: str
    runtime_gate: RuntimeGateStatus
    campaign_gate: CampaignGateStatus
    quality_ready: bool
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "runtime_gate": self.runtime_gate.value,
            "campaign_gate": self.campaign_gate.value,
            "quality_ready": self.quality_ready,
            "violations": list(self.violations),
            "notes": list(self.notes),
        }


# Quality floors — identical to provisional_quality_gate_thresholds; never lower.
_ORACLE_TOP_1 = 0.65
_ORACLE_TOP_2 = 0.90
_ORACLE_REQUIRED = 0.88
_E2E_STATUS = 0.78
_E2E_TOP_1 = 0.65
_E2E_TOP_2 = 0.90
_E2E_REQUIRED = 0.88
_FAST_NEG_RECALL = 0.95
_FAST_CORR_RECALL = 0.90
_MIN_HUMAN_REVIEWED = 100


def evaluate_runtime_gate(
    deps: RuntimeDependencyReport,
    *,
    quality_ok: Optional[bool] = None,
) -> RuntimeGateStatus:
    """Dependency-first runtime gate.

    Missing dependency → BLOCKED_DEPENDENCY (never counted as test success).
    Dependencies green but measured quality fails → RUNTIME_QUALITY_REJECT.
    """

    if not deps.all_available or not deps.all_measured:
        return RuntimeGateStatus.BLOCKED_DEPENDENCY
    if quality_ok is False:
        return RuntimeGateStatus.RUNTIME_QUALITY_REJECT
    return RuntimeGateStatus.RUNTIME_READY


def _metric_floor(
    name: str,
    value: Optional[float],
    floor: float,
    violations: list[str],
) -> None:
    if value is None:
        violations.append(f"{name} not measured")
    elif value + 1e-12 < floor:
        violations.append(f"{name}={value:.4f} < {floor}")


def evaluate_provisional_gate(
    evidence: RuntimeEvidence,
    *,
    deps: Optional[RuntimeDependencyReport] = None,
) -> ProvisionalGateResult:
    """Emit PROVISIONAL_ACCEPT only when every ADR-009 condition holds."""

    violations: list[str] = []
    notes: list[str] = []

    if evidence.human_reviewed_count < _MIN_HUMAN_REVIEWED:
        violations.append(
            f"HUMAN_REVIEWED={evidence.human_reviewed_count} < {_MIN_HUMAN_REVIEWED}"
        )

    if not evidence.real_redis_measured:
        violations.append("real_redis_measured=false")
    if not evidence.real_pgvector_measured:
        violations.append("real_pgvector_measured=false")
    if not evidence.real_fast_measured:
        violations.append("real_fast_measured=false")
    if not evidence.real_embedding_measured:
        violations.append("real_embedding_measured=false")

    if evidence.redis_integration_skipped != 0:
        violations.append(
            f"redis_integration_skipped={evidence.redis_integration_skipped}"
        )
    if evidence.pgvector_integration_skipped != 0:
        violations.append(
            f"pgvector_integration_skipped={evidence.pgvector_integration_skipped}"
        )

    _metric_floor("oracle_top_1", evidence.oracle_top_1, _ORACLE_TOP_1, violations)
    _metric_floor("oracle_top_2", evidence.oracle_top_2, _ORACLE_TOP_2, violations)
    _metric_floor(
        "oracle_required", evidence.oracle_required, _ORACLE_REQUIRED, violations
    )
    if evidence.oracle_forbidden != 0:
        violations.append(f"oracle_forbidden={evidence.oracle_forbidden}")
    if evidence.oracle_unsafe != 0:
        violations.append(f"oracle_unsafe={evidence.oracle_unsafe}")

    _metric_floor("e2e_status", evidence.e2e_status, _E2E_STATUS, violations)
    _metric_floor("e2e_top_1", evidence.e2e_top_1, _E2E_TOP_1, violations)
    _metric_floor("e2e_top_2", evidence.e2e_top_2, _E2E_TOP_2, violations)
    _metric_floor("e2e_required", evidence.e2e_required, _E2E_REQUIRED, violations)
    if evidence.e2e_forbidden != 0:
        violations.append(f"e2e_forbidden={evidence.e2e_forbidden}")
    if evidence.e2e_unsafe != 0:
        violations.append(f"e2e_unsafe={evidence.e2e_unsafe}")

    if evidence.fast_invalid_schema_count is None:
        violations.append("fast_invalid_schema_count not measured")
    elif evidence.fast_invalid_schema_count != 0:
        violations.append(
            f"fast_invalid_schema_count={evidence.fast_invalid_schema_count}"
        )

    if evidence.fast_forbidden_identifier_count is None:
        violations.append("fast_forbidden_identifier_count not measured")
    elif evidence.fast_forbidden_identifier_count != 0:
        violations.append(
            "fast_forbidden_identifier_count="
            f"{evidence.fast_forbidden_identifier_count}"
        )

    _metric_floor(
        "fast_negative_constraint_recall",
        evidence.fast_negative_constraint_recall,
        _FAST_NEG_RECALL,
        violations,
    )
    _metric_floor(
        "fast_correction_recall",
        evidence.fast_correction_recall,
        _FAST_CORR_RECALL,
        violations,
    )

    quality_ready = not any(
        v.startswith(("oracle_", "e2e_")) or "forbidden" in v or "unsafe" in v
        for v in violations
        if "not measured" not in v
    ) and evidence.oracle_forbidden == 0 and evidence.e2e_forbidden == 0

    # Recompute quality_ready more carefully from floors alone.
    quality_ready = (
        evidence.oracle_top_1 is not None
        and evidence.oracle_top_1 >= _ORACLE_TOP_1
        and evidence.oracle_top_2 is not None
        and evidence.oracle_top_2 >= _ORACLE_TOP_2
        and evidence.oracle_required is not None
        and evidence.oracle_required >= _ORACLE_REQUIRED
        and evidence.oracle_forbidden == 0
        and evidence.oracle_unsafe == 0
        and evidence.e2e_status is not None
        and evidence.e2e_status >= _E2E_STATUS
        and evidence.e2e_top_1 is not None
        and evidence.e2e_top_1 >= _E2E_TOP_1
        and evidence.e2e_top_2 is not None
        and evidence.e2e_top_2 >= _E2E_TOP_2
        and evidence.e2e_required is not None
        and evidence.e2e_required >= _E2E_REQUIRED
        and evidence.e2e_forbidden == 0
        and evidence.e2e_unsafe == 0
    )

    runtime_quality_ok = (
        quality_ready
        and evidence.fast_invalid_schema_count == 0
        and evidence.fast_forbidden_identifier_count == 0
        and evidence.fast_negative_constraint_recall is not None
        and evidence.fast_negative_constraint_recall >= _FAST_NEG_RECALL
        and evidence.fast_correction_recall is not None
        and evidence.fast_correction_recall >= _FAST_CORR_RECALL
    )

    if deps is None:
        # Infer dependency readiness from evidence flags alone.
        if not evidence.all_runtime_measured():
            runtime_gate = RuntimeGateStatus.BLOCKED_DEPENDENCY
            notes.append("runtime evidence incomplete — BLOCKED_DEPENDENCY")
        elif not runtime_quality_ok:
            runtime_gate = RuntimeGateStatus.RUNTIME_QUALITY_REJECT
        else:
            runtime_gate = RuntimeGateStatus.RUNTIME_READY
    else:
        runtime_gate = evaluate_runtime_gate(deps, quality_ok=runtime_quality_ok)

    if violations:
        status = (
            "BLOCKED_DEPENDENCY"
            if runtime_gate == RuntimeGateStatus.BLOCKED_DEPENDENCY
            else (
                "RUNTIME_QUALITY_REJECT"
                if runtime_gate == RuntimeGateStatus.RUNTIME_QUALITY_REJECT
                else "PROVISIONAL_REJECT"
            )
        )
        # Prefer the specific runtime gate label when blocked.
        if runtime_gate == RuntimeGateStatus.BLOCKED_DEPENDENCY:
            status = "BLOCKED_DEPENDENCY"
        elif runtime_gate == RuntimeGateStatus.RUNTIME_QUALITY_REJECT:
            status = "RUNTIME_QUALITY_REJECT"
        else:
            status = "PROVISIONAL_REJECT"
        campaign = CampaignGateStatus.CLOSED
        notes.append("PROVISIONAL_ACCEPT deferred — see violations")
    else:
        status = "PROVISIONAL_ACCEPT"
        campaign = CampaignGateStatus.READY_TO_OPEN
        notes.append("All ADR-009 provisional conditions met")
        notes.append("Final ACCEPT requires holdout + broader human review")

    return ProvisionalGateResult(
        status=status,
        runtime_gate=runtime_gate,
        campaign_gate=campaign,
        quality_ready=quality_ready,
        violations=violations,
        notes=notes,
    )


def evaluate_campaign_gate(
    provisional: ProvisionalGateResult,
) -> CampaignGateStatus:
    """Campaign opens only after PROVISIONAL_ACCEPT — never sooner."""

    if provisional.status == "PROVISIONAL_ACCEPT":
        return CampaignGateStatus.READY_TO_OPEN
    return CampaignGateStatus.CLOSED
