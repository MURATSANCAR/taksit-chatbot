"""Answer integrity package (ADR-012)."""

from __future__ import annotations

from taksitlio.answer_integrity.claim_validator import (
    CLAIM_VALIDATION_FAILED,
    ClaimValidationResult,
    ClaimValidator,
    ClaimViolation,
    validate_claims,
)
from taksitlio.answer_integrity.confidence import (
    ConfidenceField,
    FieldConfidence,
    FieldConfidencePolicy,
    FieldDecision,
    FieldDecisionResult,
    decide_all,
    decide_field,
    reject_overall_confidence,
)
from taksitlio.answer_integrity.conflict import (
    ConflictResolution,
    DataKind,
    SourceObservation,
    SourcePrecedencePolicy,
    resolve_conflict,
)
from taksitlio.answer_integrity.errors import (
    AnswerIntegrityError,
    ClaimValidationFailed,
    NoEvidenceError,
    PaymentReconciliationFailed,
)
from taksitlio.answer_integrity.facts import (
    CRITICAL_FACT_TYPES,
    EVIDENCE_FIELD_BY_TYPE,
    EvidenceRef,
    Fact,
    FactEnvelope,
    FactType,
    GroundedFact,
    ProvenanceError,
    assert_claimable,
    build_envelope,
    build_fact,
    build_fact_envelope,
    validate_provenance,
)
from taksitlio.answer_integrity.pipeline import (
    GroundedAnswer,
    IntegrityPipelineResult,
    compose_grounded_answer,
    run_answer_integrity_pipeline,
)
from taksitlio.answer_integrity.prompt_injection import (
    UntrustedContent,
    wrap_untrusted,
)
from taksitlio.answer_integrity.response_composer import (
    ComposedResponse,
    compose_deterministic,
    compose_from_facts,
    compose_reason_explanation,
    filter_llm_decoration,
    merge_optional_llm_fields,
)
from taksitlio.answer_integrity.truth_status import (
    CLAIMABLE_STATUSES,
    CostKind,
    ErrorClass,
    FieldTruthStatus,
    FinanceAvailability,
    ResponseOutcome,
    UNSAFE_FOR_BEST_OFFER,
    is_claimable,
)

ADR_SCOPE = "ADR-012"
PACKAGE_STATUS = "P0_PROD"

QUALITY_GATES = (
    "SOURCE_PROVENANCE_GATE",
    "CLAIM_GROUNDING_GATE",
    "PAYMENT_CALCULATION_GATE",
    "PRODUCT_IDENTITY_GATE",
    "FINANCE_MAPPING_GATE",
    "RECOMMENDATION_INTEGRITY_GATE",
    "NEGATIVE_CONSTRAINT_GATE",
    "SOURCE_CONFLICT_GATE",
    "SCHEMA_DRIFT_GATE",
    "PROMPT_INJECTION_GATE",
)

__all__ = [
    "ADR_SCOPE",
    "CLAIMABLE_STATUSES",
    "CLAIM_VALIDATION_FAILED",
    "CRITICAL_FACT_TYPES",
    "CostKind",
    "ConfidenceField",
    "ComposedResponse",
    "ConflictResolution",
    "DataKind",
    "EVIDENCE_FIELD_BY_TYPE",
    "ErrorClass",
    "EvidenceRef",
    "Fact",
    "FactEnvelope",
    "FactType",
    "FieldConfidence",
    "FieldConfidencePolicy",
    "FieldDecision",
    "FieldDecisionResult",
    "FieldTruthStatus",
    "FinanceAvailability",
    "GroundedAnswer",
    "GroundedFact",
    "IntegrityPipelineResult",
    "NoEvidenceError",
    "PACKAGE_STATUS",
    "ProvenanceError",
    "QUALITY_GATES",
    "ResponseOutcome",
    "SourceObservation",
    "SourcePrecedencePolicy",
    "UNSAFE_FOR_BEST_OFFER",
    "UntrustedContent",
    "AnswerIntegrityError",
    "ClaimValidationFailed",
    "ClaimValidationResult",
    "ClaimValidator",
    "ClaimViolation",
    "PaymentReconciliationFailed",
    "assert_claimable",
    "build_envelope",
    "build_fact",
    "build_fact_envelope",
    "compose_deterministic",
    "compose_from_facts",
    "compose_grounded_answer",
    "compose_reason_explanation",
    "decide_all",
    "decide_field",
    "filter_llm_decoration",
    "is_claimable",
    "merge_optional_llm_fields",
    "reject_overall_confidence",
    "resolve_conflict",
    "run_answer_integrity_pipeline",
    "validate_claims",
    "validate_provenance",
    "wrap_untrusted",
]
