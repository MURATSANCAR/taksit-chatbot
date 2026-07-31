"""FAST extraction quality metrics on HUMAN_REVIEWED cases (ADR-009 §6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence


@dataclass
class FastExtractionMetrics:
    case_count: int = 0
    invalid_schema_count: int = 0
    forbidden_identifier_generation_count: int = 0
    fallback_count: int = 0
    timeout_count: int = 0

    intent_correct: int = 0
    intent_total: int = 0
    budget_type_correct: int = 0
    budget_type_total: int = 0
    budget_value_correct: int = 0
    budget_value_total: int = 0
    clarification_correct: int = 0
    clarification_total: int = 0

    positive_tp: int = 0
    positive_fp: int = 0
    positive_fn: int = 0
    negative_tp: int = 0
    negative_fp: int = 0
    negative_fn: int = 0
    correction_tp: int = 0
    correction_fp: int = 0
    correction_fn: int = 0

    def rate(self, num: int, den: int) -> Optional[float]:
        if den <= 0:
            return None
        return num / den

    @property
    def invalid_schema_rate(self) -> float:
        return 0.0 if self.case_count <= 0 else self.invalid_schema_count / self.case_count

    @property
    def fallback_rate(self) -> float:
        return 0.0 if self.case_count <= 0 else self.fallback_count / self.case_count

    @property
    def negative_constraint_recall(self) -> Optional[float]:
        return self.rate(self.negative_tp, self.negative_tp + self.negative_fn)

    @property
    def correction_recall(self) -> Optional[float]:
        return self.rate(self.correction_tp, self.correction_tp + self.correction_fn)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "invalid_schema_count": self.invalid_schema_count,
            "invalid_schema_rate": self.invalid_schema_rate,
            "forbidden_identifier_generation_count": self.forbidden_identifier_generation_count,
            "fallback_count": self.fallback_count,
            "fallback_rate": self.fallback_rate,
            "timeout_count": self.timeout_count,
            "intent_accuracy": self.rate(self.intent_correct, self.intent_total),
            "budget_type_accuracy": self.rate(
                self.budget_type_correct, self.budget_type_total
            ),
            "budget_value_accuracy": self.rate(
                self.budget_value_correct, self.budget_value_total
            ),
            "clarification_accuracy": self.rate(
                self.clarification_correct, self.clarification_total
            ),
            "positive_constraint_precision": self.rate(
                self.positive_tp, self.positive_tp + self.positive_fp
            ),
            "positive_constraint_recall": self.rate(
                self.positive_tp, self.positive_tp + self.positive_fn
            ),
            "negative_constraint_precision": self.rate(
                self.negative_tp, self.negative_tp + self.negative_fp
            ),
            "negative_constraint_recall": self.negative_constraint_recall,
            "correction_precision": self.rate(
                self.correction_tp, self.correction_tp + self.correction_fp
            ),
            "correction_recall": self.correction_recall,
        }


def _normalize_for_score(text: str) -> str:
    """Reuse Turkish-aware folding used by SemanticConstraintValidator."""

    try:
        from taksitlio.semantic_constraints.validator import _normalize_concept

        return _normalize_concept(text)
    except Exception:  # noqa: BLE001
        return text.strip().lower()


def _concept_texts(items: Sequence[Mapping[str, Any]] | None) -> set[str]:
    """Extract comparable concept strings from annotation or NeedProfile bags.

    Accepts both evaluation annotation keys (``concept``) and morphology-safe
    fields (``surface_form`` / ``normalized``). Values are folded with the
    same Turkish normalization used by SemanticConstraintValidator.
    """

    out: set[str] = set()
    if not items:
        return out
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in (
            "surface_form",
            "normalized",
            "concept",
            "text",
            "value",
            "previous_concept",
            "replacement_concept",
        ):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                out.add(_normalize_for_score(val))
    return out


def _correction_texts(items: Sequence[Mapping[str, Any]] | None) -> set[str]:
    """Score corrections as ``previous->replacement`` pairs when both exist."""

    out: set[str] = set()
    if not items:
        return out
    for item in items:
        if not isinstance(item, Mapping):
            continue
        prev = item.get("previous_concept") or item.get("previous_surface_form")
        repl = item.get("replacement_concept") or item.get("replacement_surface_form")
        if isinstance(prev, str) and isinstance(repl, str) and prev.strip() and repl.strip():
            out.add(
                f"{_normalize_for_score(prev)}->{_normalize_for_score(repl)}"
            )
            continue
        # Single-concept USER_CORRECTION / legacy shape: fall back to concept set.
        for key in ("surface_form", "normalized", "concept", "text", "value"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                out.add(_normalize_for_score(val))
                break
    return out


def _prf_update(
    metrics: FastExtractionMetrics,
    *,
    kind: str,
    predicted: set[str],
    expected: set[str],
) -> None:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    if kind == "positive":
        metrics.positive_tp += tp
        metrics.positive_fp += fp
        metrics.positive_fn += fn
    elif kind == "negative":
        metrics.negative_tp += tp
        metrics.negative_fp += fp
        metrics.negative_fn += fn
    else:
        metrics.correction_tp += tp
        metrics.correction_fp += fp
        metrics.correction_fn += fn


def score_fast_extraction(
    rows: Iterable[Mapping[str, Any]],
) -> FastExtractionMetrics:
    """Score rows of ``{expected_need_profile, predicted_need_profile, error}``.

    Annotation semantic constraints must not be sent to the model; they may
    appear here only as ``expected_*`` fields for offline scoring.
    """

    m = FastExtractionMetrics()
    for row in rows:
        m.case_count += 1
        err = row.get("error")
        if err == "TIMEOUT":
            m.timeout_count += 1
            continue
        if err == "FALLBACK":
            m.fallback_count += 1
            continue
        if err == "INVALID_SCHEMA":
            m.invalid_schema_count += 1
            continue
        if err == "FORBIDDEN_IDENTIFIER":
            m.forbidden_identifier_generation_count += 1
            continue
        if err:
            continue

        expected = row.get("expected_need_profile") or {}
        predicted = row.get("predicted_need_profile") or {}
        if not isinstance(expected, Mapping) or not isinstance(predicted, Mapping):
            m.invalid_schema_count += 1
            continue

        exp_intent = (expected.get("intent") or {}).get("type")
        pred_intent = (predicted.get("intent") or {}).get("type")
        if exp_intent is not None:
            m.intent_total += 1
            if exp_intent == pred_intent:
                m.intent_correct += 1

        exp_budget = expected.get("budget") or {}
        pred_budget = predicted.get("budget") or {}
        if isinstance(exp_budget, Mapping) and exp_budget.get("type") is not None:
            m.budget_type_total += 1
            if exp_budget.get("type") == (pred_budget or {}).get("type"):
                m.budget_type_correct += 1
            if exp_budget.get("value") is not None:
                m.budget_value_total += 1
                if exp_budget.get("value") == (pred_budget or {}).get("value"):
                    m.budget_value_correct += 1

        exp_clar = bool((expected.get("clarification") or {}).get("required"))
        pred_clar = bool((predicted.get("clarification") or {}).get("required"))
        m.clarification_total += 1
        if exp_clar == pred_clar:
            m.clarification_correct += 1

        # Prefer explicit constraint bags when present.
        exp_c = row.get("expected_constraints") or {}
        pred_c = row.get("predicted_constraints") or {}
        _prf_update(
            m,
            kind="positive",
            predicted=_concept_texts((pred_c or {}).get("positive")),
            expected=_concept_texts((exp_c or {}).get("positive")),
        )
        _prf_update(
            m,
            kind="negative",
            predicted=_concept_texts((pred_c or {}).get("negative")),
            expected=_concept_texts((exp_c or {}).get("negative")),
        )
        _prf_update(
            m,
            kind="correction",
            predicted=_correction_texts((pred_c or {}).get("corrections")),
            expected=_correction_texts((exp_c or {}).get("corrections")),
        )
    return m
