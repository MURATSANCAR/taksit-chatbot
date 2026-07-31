"""Clarification lane metrics on Query Golden (ADR-013 L2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.query_golden.loader import QueryGoldenCase
from taksitlio.evaluation.query_golden.metrics import predict_case
from taksitlio.query_understanding import CatalogHints


@dataclass
class ClarificationLaneMetrics:
    case_count: int = 0
    clarification_bucket_cases: int = 0
    clarification_accuracy: Optional[float] = None
    llm_avoided_by_clarification_rate: Optional[float] = None
    unnecessary_llm_on_clarification_count: int = 0
    multi_question_violations: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_clarification_lane(
    cases: Sequence[QueryGoldenCase],
    *,
    catalog: CatalogHints,
) -> tuple[ClarificationLaneMetrics, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    clar_ok = clar_tot = 0
    avoid_ok = avoid_tot = 0
    unnec_llm = multi_q = 0

    for case in cases:
        exp = case.expected
        should_ask = bool((exp.get("clarification") or {}).get("should_ask"))
        if case.bucket != "clarification" and not should_ask:
            continue
        if case.bucket == "adversarial" and exp.get("route") == "OUT_OF_SCOPE":
            continue

        pred = predict_case(case, catalog=catalog)
        pred_ask = bool(pred.get("clarification_should_ask"))
        pred_llm = bool(pred.get("llm_required"))
        pred_route = pred.get("route")

        clar_tot += 1
        ok = should_ask == pred_ask or (
            should_ask and pred_route == "CLARIFICATION"
        )
        # Also accept exact route match
        if exp.get("route") == "CLARIFICATION":
            ok = pred_route == "CLARIFICATION"
        clar_ok += int(ok)

        if should_ask or exp.get("route") == "CLARIFICATION":
            avoid_tot += 1
            avoided = not pred_llm and pred_route != "LLM"
            avoid_ok += int(avoided)
            if pred_llm or pred_route == "LLM":
                unnec_llm += 1

        max_q = int((exp.get("clarification") or {}).get("max_questions") or 1)
        if max_q > 1:
            multi_q += 1

        details.append(
            {
                "case_id": case.case_id,
                "ok": ok,
                "expected_ask": should_ask,
                "pred_route": pred_route,
            }
        )

    metrics = ClarificationLaneMetrics(
        case_count=len(cases),
        clarification_bucket_cases=clar_tot,
        clarification_accuracy=(clar_ok / clar_tot) if clar_tot else None,
        llm_avoided_by_clarification_rate=(avoid_ok / avoid_tot) if avoid_tot else None,
        unnecessary_llm_on_clarification_count=unnec_llm,
        multi_question_violations=multi_q,
        support={"clarification": clar_tot, "avoidance": avoid_tot},
    )
    return metrics, details


def evaluate_clarification_gate(
    metrics: ClarificationLaneMetrics,
    gates: Mapping[str, Any],
    *,
    draft_heavy: bool = True,
) -> dict[str, Any]:
    thresholds = gates.get("clarification_gate_thresholds") or {}
    violations: list[str] = []

    rate = metrics.llm_avoided_by_clarification_rate
    min_rate = (thresholds.get("llm_avoided_by_clarification_rate") or {}).get("min")
    if min_rate is not None and rate is not None and float(rate) < float(min_rate):
        violations.append(
            f"llm_avoided_by_clarification_rate: {rate:.4f} < {min_rate}"
        )

    if metrics.unnecessary_llm_on_clarification_count > 0:
        violations.append(
            f"unnecessary_llm_on_clarification_count: "
            f"{metrics.unnecessary_llm_on_clarification_count} > 0"
        )

    # Zero-tolerance LLM leak always fails; rate miss is BOOTSTRAP while DRAFT-heavy
    hard = [v for v in violations if v.startswith("unnecessary_llm")]
    soft = [v for v in violations if not v.startswith("unnecessary_llm")]
    if hard:
        status = "FAIL"
        notes = hard + [f"warn:{s}" for s in soft]
    elif draft_heavy and soft:
        status = "BOOTSTRAP"
        notes = [
            "clarification promotion deferred while DRAFT-heavy",
            *[f"warn:{s}" for s in soft],
        ]
    elif violations:
        status = "FAIL"
        notes = list(violations)
    else:
        status = "PASS"
        notes = []

    return {"status": status, "violations": violations, "notes": notes}
