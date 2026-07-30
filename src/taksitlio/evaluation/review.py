"""Human-review workflow stubs for the evaluation dataset (ADR-006 §H).

The classes here model the *contract* for the reviewer / adjudication
workflow that upgrades DRAFT synthetic cases into HUMAN_REVIEWED cases
which the quality gate can trust. This file intentionally does *not*
persist data — production storage lives in an admin service; these
dataclasses exist so the CLI, admin tools and unit tests share a single
typed vocabulary.

Nothing here mutates production tables or evaluation datasets. All
functions are pure and privacy-safe (no raw utterance content leaves
this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

from taksitlio.evaluation.domain import (
    AnnotationStatus,
    EvaluationCase,
    EvaluationDataset,
)


class ReviewOutcome(str, Enum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    NEEDS_ADJUDICATION = "NEEDS_ADJUDICATION"


@dataclass(frozen=True)
class ReviewerAssignment:
    """Assign a case to two blind reviewers.

    Reviewer identifiers are opaque short codes (e.g. ``R-a1b2c3``);
    they are *not* linked to user PII in this module.
    """

    case_id: str
    primary_reviewer: str
    secondary_reviewer: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.primary_reviewer == self.secondary_reviewer:
            raise ValueError("blind second review requires two different reviewers")


@dataclass(frozen=True)
class BlindSecondReview:
    """One reviewer's blind verdict on a case."""

    case_id: str
    reviewer: str
    verdict_status: str  # "MATCHED" | "AMBIGUOUS" | "NO_MATCH"
    verdict_required_fixture_keys: tuple[str, ...] = ()
    verdict_forbidden_fixture_keys: tuple[str, ...] = ()
    notes_hash: Optional[str] = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Dispute:
    """A dispute is raised whenever two blind reviewers disagree."""

    case_id: str
    primary: BlindSecondReview
    secondary: BlindSecondReview

    def outcome(self) -> ReviewOutcome:
        agree = (
            self.primary.verdict_status == self.secondary.verdict_status
            and set(self.primary.verdict_required_fixture_keys)
            == set(self.secondary.verdict_required_fixture_keys)
            and set(self.primary.verdict_forbidden_fixture_keys)
            == set(self.secondary.verdict_forbidden_fixture_keys)
        )
        return ReviewOutcome.AGREE if agree else ReviewOutcome.NEEDS_ADJUDICATION


@dataclass(frozen=True)
class Adjudication:
    """Final ruling by a senior reviewer on a disputed case."""

    case_id: str
    adjudicator: str
    resolved_status: str
    resolved_required_fixture_keys: tuple[str, ...] = ()
    resolved_forbidden_fixture_keys: tuple[str, ...] = ()
    notes_hash: Optional[str] = None
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AgreementStats:
    """Inter-rater agreement summary for a slice of cases."""

    total: int
    agreements: int
    disagreements: int
    pending: int

    @property
    def raw_agreement(self) -> Optional[float]:
        judged = self.agreements + self.disagreements
        if judged == 0:
            return None
        return self.agreements / judged


def compute_agreement(disputes: Iterable[Dispute], *, total_cases: int) -> AgreementStats:
    """Compute simple raw agreement across finished blind pairs."""

    finished = list(disputes)
    agreements = sum(1 for d in finished if d.outcome() is ReviewOutcome.AGREE)
    disagreements = len(finished) - agreements
    pending = max(total_cases - len(finished), 0)
    return AgreementStats(
        total=total_cases,
        agreements=agreements,
        disagreements=disagreements,
        pending=pending,
    )


# ---------------------------------------------------------------------------
# Progress reporting utilities (used by the ``review-status`` CLI).
# ---------------------------------------------------------------------------


HUMAN_REVIEWED_TARGET = 100  # ADR-006 milestone target for validation split.


@dataclass(frozen=True)
class ReviewProgress:
    dataset_id: str
    total_cases: int
    draft: int
    single_reviewed: int
    human_reviewed: int
    target: int = HUMAN_REVIEWED_TARGET

    @property
    def remaining_to_target(self) -> int:
        return max(self.target - self.human_reviewed, 0)

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "total_cases": self.total_cases,
            "draft": self.draft,
            "single_reviewed": self.single_reviewed,
            "human_reviewed": self.human_reviewed,
            "target": self.target,
            "remaining_to_target": self.remaining_to_target,
        }


def summarise_progress(dataset: EvaluationDataset) -> ReviewProgress:
    counts = {status: 0 for status in AnnotationStatus}
    for case in dataset.cases:
        counts[case.annotation.status] += 1
    return ReviewProgress(
        dataset_id=dataset.dataset_id,
        total_cases=len(dataset.cases),
        draft=counts[AnnotationStatus.DRAFT],
        single_reviewed=counts[AnnotationStatus.SINGLE_REVIEWED],
        human_reviewed=counts[AnnotationStatus.HUMAN_REVIEWED],
    )


def next_cases_for_review(
    dataset: EvaluationDataset, *, limit: int = 25
) -> tuple[EvaluationCase, ...]:
    """Return DRAFT-first, then SINGLE_REVIEWED case IDs to schedule reviews on."""

    priority = {
        AnnotationStatus.DRAFT: 0,
        AnnotationStatus.SINGLE_REVIEWED: 1,
        AnnotationStatus.HUMAN_REVIEWED: 2,
    }
    ordered = sorted(dataset.cases, key=lambda c: (priority[c.annotation.status], c.case_id))
    return tuple(c for c in ordered if c.annotation.status is not AnnotationStatus.HUMAN_REVIEWED)[
        :limit
    ]


__all__ = [
    "Adjudication",
    "AgreementStats",
    "BlindSecondReview",
    "Dispute",
    "HUMAN_REVIEWED_TARGET",
    "ReviewOutcome",
    "ReviewProgress",
    "ReviewerAssignment",
    "compute_agreement",
    "next_cases_for_review",
    "summarise_progress",
]
