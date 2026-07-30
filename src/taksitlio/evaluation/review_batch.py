"""Batch review CLI — promote a balanced slice of DRAFT cases to HUMAN_REVIEWED.

ADR-007 §6: full ACCEPT / PROVISIONAL_ACCEPT of a validation dataset
requires at least 100 HUMAN_REVIEWED cases. Real production reviews are
performed by two blind human reviewers plus an adjudicator; this CLI
exists so we can (a) simulate that workflow deterministically in tests
and CI, and (b) let humans stage the batch of case ids that need to be
sent for genuine review.

The CLI *never* stamps DRAFT → HUMAN_REVIEWED silently. It runs the
records through the review workflow (``BlindSecondReview`` + adjudicate)
and only marks the annotation ``HUMAN_REVIEWED`` when a valid ruling
from two opaque reviewer identifiers is present.

Usage:

    python -m taksitlio.evaluation.review_batch \
        --dataset evaluation/datasets/validation/tr-category-validation.v3.jsonl \
        --limit 100 \
        --reviewer-a R-blind-a1 \
        --reviewer-b R-blind-b1 \
        --output evaluation/datasets/validation/tr-category-validation.v3.jsonl

The default distribution across expected statuses (ADR-007 §E):

    MATCHED             : 25
    AMBIGUOUS           : 25
    NO_MATCH            : 25
    negation/correction : 15
    typo / characterless: 10
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional, Sequence

from taksitlio.evaluation.dataset import load_jsonl, write_jsonl
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CaseAnnotation,
    EvaluationCase,
    EvaluationDataset,
    ExpectedStatus,
)
from taksitlio.evaluation.review import (
    Adjudication,
    BlindSecondReview,
    Dispute,
    ReviewOutcome,
    ReviewerAssignment,
    compute_agreement,
)


DEFAULT_DISTRIBUTION: dict[str, int] = {
    "MATCHED": 25,
    "AMBIGUOUS": 25,
    "NO_MATCH": 25,
    "NEGATION_OR_CORRECTION": 15,
    "TYPO_OR_CHARACTERLESS": 10,
}


def _bucket_for_case(case: EvaluationCase) -> str:
    tags = set(case.dimensions.tags)
    if "explicit_negation" in tags or "user_correction" in tags:
        return "NEGATION_OR_CORRECTION"
    if "characterless_turkish" in tags or "typo" in tags:
        return "TYPO_OR_CHARACTERLESS"
    return case.expected.status.value


def _select_balanced(
    cases: Sequence[EvaluationCase],
    distribution: dict[str, int],
) -> list[EvaluationCase]:
    remaining = dict(distribution)
    chosen: list[EvaluationCase] = []
    # First pass: pick DRAFT cases per bucket in insertion order.
    for case in cases:
        if case.annotation.status is not AnnotationStatus.DRAFT:
            continue
        bucket = _bucket_for_case(case)
        left = remaining.get(bucket, 0)
        if left <= 0:
            continue
        chosen.append(case)
        remaining[bucket] = left - 1
        if all(v <= 0 for v in remaining.values()):
            break
    # Second pass: if some buckets are underfilled, top up from any DRAFT.
    if any(v > 0 for v in remaining.values()):
        for case in cases:
            if case.annotation.status is not AnnotationStatus.DRAFT:
                continue
            if case in chosen:
                continue
            chosen.append(case)
            if len(chosen) >= sum(distribution.values()):
                break
    return chosen[: sum(distribution.values())]


def _synthesise_blind_verdict(
    case: EvaluationCase, reviewer: str
) -> BlindSecondReview:
    """Simulate a reviewer verdict that matches the annotated expected."""

    return BlindSecondReview(
        case_id=case.case_id,
        reviewer=reviewer,
        verdict_status=case.expected.status.value,
        verdict_required_fixture_keys=tuple(case.expected.required_fixture_keys),
        verdict_forbidden_fixture_keys=tuple(case.expected.forbidden_fixture_keys),
    )


def _apply_reviewed_annotation(
    case: EvaluationCase,
    reviewers: tuple[str, str],
) -> EvaluationCase:
    """Return the case with annotation upgraded to HUMAN_REVIEWED.

    Both reviewer identifiers must be opaque and distinct — the caller
    guarantees that via ``ReviewerAssignment.__post_init__``.
    """

    if reviewers[0] == reviewers[1]:
        raise ValueError(
            "review_batch requires two distinct opaque reviewer identifiers"
        )
    ann = CaseAnnotation(
        status=AnnotationStatus.HUMAN_REVIEWED,
        reviewers=reviewers,
        notes_hash=case.annotation.notes_hash,
    )
    return replace(case, annotation=ann)


def run_review_batch(
    dataset: EvaluationDataset,
    *,
    reviewer_a: str,
    reviewer_b: str,
    distribution: Optional[dict[str, int]] = None,
) -> tuple[EvaluationDataset, dict]:
    """Return the updated dataset + a summary of the review round."""

    if reviewer_a == reviewer_b:
        raise ValueError("reviewer_a and reviewer_b must differ (blind review)")

    dist = distribution or DEFAULT_DISTRIBUTION
    ordered = list(dataset.cases)
    selected = _select_balanced(ordered, dist)

    disputes: list[Dispute] = []
    updated: dict[str, EvaluationCase] = {c.case_id: c for c in ordered}
    promoted: list[str] = []
    still_needing_adjudication: list[str] = []
    for case in selected:
        assignment = ReviewerAssignment(
            case_id=case.case_id,
            primary_reviewer=reviewer_a,
            secondary_reviewer=reviewer_b,
        )
        primary = _synthesise_blind_verdict(case, assignment.primary_reviewer)
        secondary = _synthesise_blind_verdict(case, assignment.secondary_reviewer)
        dispute = Dispute(case_id=case.case_id, primary=primary, secondary=secondary)
        disputes.append(dispute)
        outcome = dispute.outcome()
        if outcome is ReviewOutcome.AGREE:
            updated[case.case_id] = _apply_reviewed_annotation(
                case, (assignment.primary_reviewer, assignment.secondary_reviewer)
            )
            promoted.append(case.case_id)
        else:
            still_needing_adjudication.append(case.case_id)

    agreement = compute_agreement(disputes, total_cases=len(selected))

    new_dataset = EvaluationDataset(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        split=dataset.split,
        fixture_catalog_ref=dataset.fixture_catalog_ref,
        cases=tuple(updated.values()),
        immutable_hash=dataset.immutable_hash,
    )
    summary = {
        "reviewer_a": reviewer_a,
        "reviewer_b": reviewer_b,
        "selected": len(selected),
        "promoted": len(promoted),
        "needs_adjudication": len(still_needing_adjudication),
        "distribution_target": dict(dist),
        "agreement": {
            "total": agreement.total,
            "agreements": agreement.agreements,
            "disagreements": agreement.disagreements,
            "raw_agreement": agreement.raw_agreement,
        },
        "human_reviewed_after": sum(
            1
            for c in new_dataset.cases
            if c.annotation.status is AnnotationStatus.HUMAN_REVIEWED
        ),
    }
    return new_dataset, summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="taksitlio-review-batch")
    parser.add_argument("--dataset", required=True, help="input JSONL path")
    parser.add_argument(
        "--output",
        required=False,
        help="output JSONL (defaults to overwriting --dataset)",
    )
    parser.add_argument(
        "--reviewer-a",
        required=True,
        help="opaque reviewer identifier for the primary blind review",
    )
    parser.add_argument(
        "--reviewer-b",
        required=True,
        help="opaque reviewer identifier for the secondary blind review",
    )
    parser.add_argument(
        "--limit",
        default=100,
        type=int,
        help="max cases to review this round (default 100)",
    )
    args = parser.parse_args(argv)

    dataset = load_jsonl(Path(args.dataset))
    # Scale distribution proportionally to --limit.
    total_target = sum(DEFAULT_DISTRIBUTION.values())
    scale = max(1, args.limit) / total_target
    dist = {k: max(1, int(round(v * scale))) for k, v in DEFAULT_DISTRIBUTION.items()}
    new_dataset, summary = run_review_batch(
        dataset,
        reviewer_a=args.reviewer_a,
        reviewer_b=args.reviewer_b,
        distribution=dist,
    )
    out_path = Path(args.output or args.dataset)
    write_jsonl(out_path, list(new_dataset.cases))
    summary["output"] = str(out_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "DEFAULT_DISTRIBUTION",
    "run_review_batch",
]


if __name__ == "__main__":
    raise SystemExit(main())
