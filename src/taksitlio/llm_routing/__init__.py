"""LLM routing policy, job state, and patch validation (ADR-011)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from taksitlio.query_understanding.gap_detector import GapAnalysis
from taksitlio.query_understanding.fast_parser import FastParseResult


class LlmJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    STALE_RESULT = "STALE_RESULT"


PLATFORM_ROLE = "UNDERSTANDING_SERVICE"

# Conditions that must NOT trigger LLM (§12)
_FAST_SIGNAL_KEYS = (
    "positive_categories",
    "brands",
    "merchant",
    "budget",
    "requested_terms",
)


def should_route_to_llm(
    parse: FastParseResult,
    gaps: GapAnalysis,
    *,
    clarification_count: int,
    max_clarifications: int = 2,
    circuit_open: bool = False,
) -> bool:
    if circuit_open:
        return False
    if gaps.confidence_band == "HIGH":
        return False
    if gaps.clarification_viable and clarification_count < max_clarifications:
        return False
    if clarification_count >= max_clarifications and gaps.confidence_band != "HIGH":
        return True
    if parse.requires_llm or gaps.requires_llm:
        return True
    if gaps.confidence_band == "LOW":
        return True
    return False


def build_llm_input(
    *,
    user_message: str,
    parse: FastParseResult,
    conversation_state: Optional[Mapping[str, Any]] = None,
    catalog_candidates: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "task": "UNDERSTAND_PRODUCT_NEED",
        "user_message": user_message,
        "conversation_state": dict(conversation_state or {}),
        "deterministic_parse": {
            "resolved_entities": [
                c.to_dict() if hasattr(c, "to_dict") else c
                for c in []
            ],
            "unresolved_spans": list(parse.unresolved_spans),
            "budget": parse.budget,
            "term": parse.requested_terms[0] if parse.requested_terms else None,
            "parse": parse.to_dict(),
        },
        "catalog_candidates": dict(catalog_candidates or {"categories": [], "attributes": [], "use_cases": []}),
        "output_schema_version": "v1",
        "platform_role": PLATFORM_ROLE,
    }


@dataclass
class LlmUnderstandingJob:
    id: str
    search_session_id: str
    query_version: int
    conversation_state_version: int
    status: LlmJobStatus = LlmJobStatus.QUEUED
    input_payload: dict[str, Any] = field(default_factory=dict)
    output_payload: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    queued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


def create_job(
    *,
    search_session_id: str,
    query_version: int,
    conversation_state_version: int,
    input_payload: Mapping[str, Any],
) -> LlmUnderstandingJob:
    return LlmUnderstandingJob(
        id=str(uuid.uuid4()),
        search_session_id=search_session_id,
        query_version=query_version,
        conversation_state_version=conversation_state_version,
        input_payload=dict(input_payload),
    )


class LlmPatchValidationError(ValueError):
    pass


_FORBIDDEN_OUTPUT_KEYS = {
    "product_id",
    "merchant_id",
    "institution_id",
    "price",
    "monthly_payment",
    "total_repayment",
    "rate",
    "sql",
    "campaign_id",
}


def validate_llm_patch(
    payload: Mapping[str, Any],
    *,
    allowed_entity_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LlmPatchValidationError("payload must be object")
    for key in _FORBIDDEN_OUTPUT_KEYS:
        if key in payload:
            raise LlmPatchValidationError(f"LLM must not emit {key}")
    # Nested scan for invented IDs shaped like product prices
    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, Mapping):
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in _FORBIDDEN_OUTPUT_KEYS and path:
                    raise LlmPatchValidationError(f"Forbidden field at {path}.{k}")
                if lk.endswith("_id") and lk not in {"attribute_id"} and allowed_entity_ids is not None:
                    if v is not None and str(v) not in allowed_entity_ids:
                        raise LlmPatchValidationError(f"Unknown catalog entity: {v}")
                _walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(payload)
    required = ("intent", "safe_to_retrieve")
    for r in required:
        if r not in payload:
            raise LlmPatchValidationError(f"missing {r}")
    # ADR-012 §2: prefer field-level confidence; overall is telemetry only.
    field_conf = payload.get("confidence")
    if isinstance(field_conf, Mapping):
        for k, v in field_conf.items():
            if k == "overall":
                continue
            try:
                fv = float(v)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise LlmPatchValidationError(f"confidence.{k} invalid") from exc
            if fv < 0 or fv > 1:
                raise LlmPatchValidationError(f"confidence.{k} out of range")
    if "overall_confidence" in payload:
        conf = float(payload["overall_confidence"])
        if conf < 0 or conf > 1:
            raise LlmPatchValidationError("overall_confidence out of range")
    elif not isinstance(field_conf, Mapping) or not field_conf:
        raise LlmPatchValidationError(
            "missing confidence (field-level) or overall_confidence"
        )
    return dict(payload)


def apply_if_fresh(
    job: LlmUnderstandingJob,
    *,
    active_query_version: int,
    active_state_version: int,
    patch: Mapping[str, Any],
) -> tuple[LlmJobStatus, Optional[dict[str, Any]]]:
    """Return STALE_RESULT and None if versions do not match."""

    if job.status in {LlmJobStatus.CANCEL_REQUESTED, LlmJobStatus.CANCELLED}:
        job.status = LlmJobStatus.CANCELLED
        return LlmJobStatus.CANCELLED, None
    if job.query_version != active_query_version:
        job.status = LlmJobStatus.STALE_RESULT
        return LlmJobStatus.STALE_RESULT, None
    if job.conversation_state_version != active_state_version:
        job.status = LlmJobStatus.STALE_RESULT
        return LlmJobStatus.STALE_RESULT, None
    validated = validate_llm_patch(patch)
    job.status = LlmJobStatus.COMPLETED
    job.output_payload = validated
    job.completed_at = datetime.now(timezone.utc)
    return LlmJobStatus.COMPLETED, validated
