"""Domain models for the category-match evaluation package.

Everything here is fixture-key-based; nothing references production
category UUIDs, slugs or display names. The runtime side maps fixture
keys to UUIDs through the FixtureCatalog once the isolated fixture
catalog is published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DatasetSplit(str, Enum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


class ExpectedStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"


class AnnotationStatus(str, Enum):
    DRAFT = "DRAFT"
    SINGLE_REVIEWED = "SINGLE_REVIEWED"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"


class EvaluationMode(str, Enum):
    FULL = "FULL"
    LEXICAL_ONLY = "LEXICAL_ONLY"
    VECTOR_ONLY = "VECTOR_ONLY"
    ALIAS_ONLY = "ALIAS_ONLY"
    DEGRADED = "DEGRADED"


class EvaluationInputMode(str, Enum):
    """How semantic constraints reach the matcher (ADR-007 §2).

    * ``MATCHER_ORACLE_INPUT`` — pass the annotated
      ``case.semantic_constraints`` directly to the matcher (the classic
      matcher-quality regression path; independent of any FAST model).
    * ``END_TO_END_RUNTIME_INPUT`` — call the FAST extractor on the raw
      utterance, run the SemanticConstraintValidator, then feed the
      resulting constraints to the matcher; annotation constraints are
      ignored on this path.
    * ``FAST_EXTRACTION_ONLY`` — run the FAST extractor + validator but
      do not invoke the matcher; used to score FAST extraction quality
      in isolation.
    * ``MATCHER_ONLY`` — alias of MATCHER_ORACLE_INPUT for callers that
      never had annotation constraints (defaults to empty).
    """

    MATCHER_ORACLE_INPUT = "MATCHER_ORACLE_INPUT"
    END_TO_END_RUNTIME_INPUT = "END_TO_END_RUNTIME_INPUT"
    FAST_EXTRACTION_ONLY = "FAST_EXTRACTION_ONLY"
    MATCHER_ONLY = "MATCHER_ONLY"


class QualityGateStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    PROVISIONAL_ACCEPT = "PROVISIONAL_ACCEPT"
    INSUFFICIENT_REVIEWED_DATA = "INSUFFICIENT_REVIEWED_DATA"
    # ADR-008 P0 — quality bar met but real FAST/embedding/pgvector/Redis
    # not measured yet; never treat LexicalFallback as production runtime.
    QUALITY_READY_RUNTIME_BLOCKED = "QUALITY_READY_RUNTIME_BLOCKED"
    QUALITY_REJECT = "QUALITY_REJECT"


@dataclass(frozen=True)
class CaseExpected:
    status: ExpectedStatus
    acceptable_fixture_keys: tuple[str, ...] = ()
    required_fixture_keys: tuple[str, ...] = ()
    forbidden_fixture_keys: tuple[str, ...] = ()
    expected_confidence_bucket: Optional[str] = None


@dataclass(frozen=True)
class CaseDimensions:
    tags: tuple[str, ...] = ()
    difficulty: Optional[str] = None


@dataclass(frozen=True)
class CasePrivacy:
    synthetic: bool = True
    contains_pii: bool = False
    source: Optional[str] = None


@dataclass(frozen=True)
class CaseAnnotation:
    status: AnnotationStatus
    reviewers: tuple[str, ...] = ()
    notes_hash: Optional[str] = None


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    utterance: str
    locale: str
    expected: CaseExpected
    dimensions: CaseDimensions
    privacy: CasePrivacy
    annotation: CaseAnnotation
    semantic_group_id: Optional[str] = None
    hints: tuple[str, ...] = ()
    # Annotated extraction for matcher eval (concepts only — never category IDs).
    semantic_constraints: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationDataset:
    dataset_id: str
    version: str
    split: DatasetSplit
    fixture_catalog_ref: dict
    cases: tuple[EvaluationCase, ...]
    immutable_hash: Optional[str] = None


@dataclass(frozen=True)
class CandidatePrediction:
    fixture_key: str
    score: float
    rank: int
    alias_mode: Optional[str] = None


@dataclass(frozen=True)
class CasePrediction:
    case_id: str
    predicted_status: str
    selected_fixture_key: Optional[str]
    top_k: tuple[CandidatePrediction, ...]
    latency_ms: float
    degraded: bool = False
    diagnostics: dict = field(default_factory=dict)
    # ADR-006: retrieval / ranking / decision diagnostics.
    pool_fixture_keys: tuple[str, ...] = ()
    retrieved_by: dict = field(default_factory=dict)
    failure_stage: Optional[str] = None
    decision_reason_code: Optional[str] = None
    signals_summary: dict = field(default_factory=dict)


__all__ = [
    "AnnotationStatus",
    "CandidatePrediction",
    "CaseAnnotation",
    "CaseDimensions",
    "CaseExpected",
    "CasePrediction",
    "CasePrivacy",
    "DatasetSplit",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationInputMode",
    "EvaluationMode",
    "ExpectedStatus",
    "QualityGateStatus",
]
