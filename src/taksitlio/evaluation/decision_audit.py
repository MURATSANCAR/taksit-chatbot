"""Decision-policy failure audit (ADR-007 §G / §8).

Given a dataset + predictions, classify each *decision failure* — a
case where the expected acceptable top_1 was in the top-2 but the
predicted status disagreed with the expected status — into one of a
small, fixed set of reason codes derived from ADR-007 §8.

The output is a plain dict:

    {
        "totals": {"decisions_evaluated": 42, "failures": 7, ...},
        "by_reason": {"AMBIGUOUS_WHEN_MATCHED_EXPECTED": {"count": 4, ...}},
        "failure_details": [{"case_id": ..., "reason": ..., ...}],
    }

There is an optional CLI (``python -m taksitlio.evaluation.decision_audit
--dataset ... --predictions ...``) that reads a report JSON emitted by
the runner. Fixture keys / concept text NEVER leak into the payload —
we only surface case_id + reason codes + decision_reason_code.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from taksitlio.evaluation.domain import (
    CasePrediction,
    EvaluationCase,
    ExpectedStatus,
)


# Reason codes for decision-policy failures. Each maps to a bucket the
# admin dashboard can render as a proportion of decision failures.
REASON_CODES = (
    "AMBIGUOUS_WHEN_MATCHED_EXPECTED",
    "MATCHED_WHEN_AMBIGUOUS_EXPECTED",
    "MATCHED_WHEN_NO_MATCH_EXPECTED",
    "AMBIGUOUS_WHEN_NO_MATCH_EXPECTED",
    "NO_MATCH_WHEN_MATCHED_EXPECTED",
    "NO_MATCH_WHEN_AMBIGUOUS_EXPECTED",
    "WRONG_TOP_1_WHEN_MATCHED_EXPECTED",
    "WEAK_LEXICAL_TOP_UNCONFIRMED",
    "GAP_TOO_SMALL_MULTI_NEED",
    "OUT_OF_SCOPE_TOP_CANDIDATE_SLIPPED_IN",
    "DEPENDENCY_FAILURE",
    "UNKNOWN",
)


@dataclass(frozen=True)
class DecisionAuditRecord:
    case_id: str
    reason: str
    expected_status: str
    predicted_status: str
    decision_reason_code: Optional[str]
    top_1_in_acceptable: bool
    top_1_score: Optional[float]
    top_2_gap: Optional[float]

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "reason": self.reason,
            "expected_status": self.expected_status,
            "predicted_status": self.predicted_status,
            "decision_reason_code": self.decision_reason_code,
            "top_1_in_acceptable": self.top_1_in_acceptable,
            "top_1_score": self.top_1_score,
            "top_2_gap": self.top_2_gap,
        }


def _acceptable_keys(case: EvaluationCase) -> set[str]:
    keys = set(case.expected.acceptable_fixture_keys)
    keys.update(case.expected.required_fixture_keys)
    return keys


def _classify(case: EvaluationCase, pred: Optional[CasePrediction]) -> str:
    if pred is None:
        return "DEPENDENCY_FAILURE"
    expected = case.expected.status.value
    predicted = pred.predicted_status
    if predicted == expected:
        return "UNKNOWN"  # not a failure — filtered out before we get here
    acceptable = _acceptable_keys(case)
    top_1_key = pred.top_k[0].fixture_key if pred.top_k else None
    top_1_in_accept = bool(top_1_key and top_1_key in acceptable)

    if expected == ExpectedStatus.MATCHED.value:
        if predicted == "AMBIGUOUS":
            # Distinguish the "top-1 is right but ambiguous" case from
            # the "top-1 wrong, ambiguous" case.
            if top_1_in_accept:
                if pred.decision_reason_code == "MULTI_NEED_SIGNAL_AMBIGUOUS":
                    return "GAP_TOO_SMALL_MULTI_NEED"
                if pred.decision_reason_code == "WEAK_LEXICAL_ONLY_TOP":
                    return "WEAK_LEXICAL_TOP_UNCONFIRMED"
                return "AMBIGUOUS_WHEN_MATCHED_EXPECTED"
            return "WRONG_TOP_1_WHEN_MATCHED_EXPECTED"
        if predicted == "NO_MATCH":
            return "NO_MATCH_WHEN_MATCHED_EXPECTED"
        # predicted MATCHED but of the wrong category
        return "WRONG_TOP_1_WHEN_MATCHED_EXPECTED"
    if expected == ExpectedStatus.AMBIGUOUS.value:
        if predicted == "MATCHED":
            return "MATCHED_WHEN_AMBIGUOUS_EXPECTED"
        if predicted == "NO_MATCH":
            return "NO_MATCH_WHEN_AMBIGUOUS_EXPECTED"
    if expected == ExpectedStatus.NO_MATCH.value:
        if predicted == "MATCHED":
            if pred.decision_reason_code == "OUT_OF_SCOPE_TOP_CANDIDATE":
                return "OUT_OF_SCOPE_TOP_CANDIDATE_SLIPPED_IN"
            return "MATCHED_WHEN_NO_MATCH_EXPECTED"
        if predicted == "AMBIGUOUS":
            return "AMBIGUOUS_WHEN_NO_MATCH_EXPECTED"
    return "UNKNOWN"


def audit(
    cases: Sequence[EvaluationCase],
    predictions: Mapping[str, CasePrediction],
) -> dict:
    """Return a decision-audit report over the (case, prediction) pairs."""

    by_reason: dict[str, list[DecisionAuditRecord]] = {
        code: [] for code in REASON_CODES
    }
    failures: list[DecisionAuditRecord] = []
    total = 0
    for case in cases:
        pred = predictions.get(case.case_id)
        total += 1
        if pred is None:
            reason = "DEPENDENCY_FAILURE"
        else:
            expected = case.expected.status.value
            if pred.predicted_status == expected:
                continue
            reason = _classify(case, pred)
        top_1_key = pred.top_k[0].fixture_key if pred and pred.top_k else None
        top_1_score = pred.top_k[0].score if pred and pred.top_k else None
        top_2_gap: Optional[float] = None
        if pred and len(pred.top_k) >= 2:
            top_2_gap = pred.top_k[0].score - pred.top_k[1].score
        record = DecisionAuditRecord(
            case_id=case.case_id,
            reason=reason,
            expected_status=case.expected.status.value,
            predicted_status=pred.predicted_status if pred else "MISSING",
            decision_reason_code=pred.decision_reason_code if pred else None,
            top_1_in_acceptable=bool(
                top_1_key and top_1_key in _acceptable_keys(case)
            ),
            top_1_score=top_1_score,
            top_2_gap=top_2_gap,
        )
        failures.append(record)
        by_reason.setdefault(reason, []).append(record)

    return {
        "totals": {
            "cases": total,
            "failures": len(failures),
            "failure_rate": (len(failures) / total) if total else 0.0,
        },
        "by_reason": {
            code: {
                "count": len(records),
                "example_case_ids": [r.case_id for r in records[:5]],
            }
            for code, records in by_reason.items()
        },
        "failure_details": [r.to_dict() for r in failures],
    }


# ---------------------------------------------------------------------------
# Optional CLI — reads a rehydrated JSON of predictions + cases.
# ---------------------------------------------------------------------------


def _load_predictions_from_report(report_path: Path) -> dict:
    with report_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="taksitlio-decision-audit")
    parser.add_argument("--dataset", required=True, help="dataset JSONL path")
    parser.add_argument(
        "--predictions",
        required=True,
        help="predictions JSON (dict of case_id → CasePrediction dict)",
    )
    args = parser.parse_args(argv)
    from taksitlio.evaluation.dataset import load_jsonl
    from taksitlio.evaluation.domain import CandidatePrediction, CasePrediction

    dataset = load_jsonl(Path(args.dataset))
    raw = _load_predictions_from_report(Path(args.predictions))
    predictions: dict[str, CasePrediction] = {}
    for case_id, payload in raw.items():
        top_k = tuple(
            CandidatePrediction(
                fixture_key=str(c.get("fixture_key", "")),
                score=float(c.get("score", 0.0)),
                rank=int(c.get("rank", 0)),
                alias_mode=c.get("alias_mode"),
            )
            for c in payload.get("top_k") or ()
        )
        predictions[case_id] = CasePrediction(
            case_id=case_id,
            predicted_status=str(payload.get("predicted_status", "MISSING")),
            selected_fixture_key=payload.get("selected_fixture_key"),
            top_k=top_k,
            latency_ms=float(payload.get("latency_ms", 0.0)),
            decision_reason_code=payload.get("decision_reason_code"),
        )
    report = audit(dataset.cases, predictions)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


__all__ = ["DecisionAuditRecord", "REASON_CODES", "audit"]


if __name__ == "__main__":
    raise SystemExit(main())
