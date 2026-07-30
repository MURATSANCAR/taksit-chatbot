"""Evaluation runner: executes cases against a matcher stack.

The runner is agnostic to concrete category names — it uses the
fixture catalog handle to translate matcher-returned category UUIDs
back to fixture keys before passing predictions to the evaluator.

Mode handling:
    FULL             — matcher uses vector + lexical + alias signals
    LEXICAL_ONLY     — zero out vector + use-case weights on a copy of the policy
    VECTOR_ONLY      — zero out alias + lexical + use-case weights
    ALIAS_ONLY       — zero out lexical + vector + use-case weights
    DEGRADED         — force the query gateway to always fail (matcher enters
                       degraded lexical mode; DECISION_POLICY handles auto-select)

Latency is measured per case around the ``matcher.match`` call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from taksitlio.semantic_matching import (
    AlwaysFailingGateway,
    CategoryMatchResult,
    CategoryMatchStatus,
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    MatchQuery,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.evaluation.concurrency import ConcurrencySummary, bounded_gather
from taksitlio.evaluation.domain import (
    CandidatePrediction,
    CasePrediction,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMode,
)
from taksitlio.evaluation.fixture_catalog import FixtureCatalog


@dataclass(frozen=True)
class RunnerConfig:
    mode: EvaluationMode = EvaluationMode.FULL
    workers: int = 4
    embedding_dim: int = 64
    top_k_limit: int = 3


def _policy_for_mode(base: SemanticMatchPolicy, mode: EvaluationMode) -> SemanticMatchPolicy:
    if mode is EvaluationMode.FULL or mode is EvaluationMode.DEGRADED:
        return base
    if mode is EvaluationMode.LEXICAL_ONLY:
        return replace(
            base,
            vector_weight=0.0,
            use_case_weight=0.0,
            alias_weight=base.alias_weight or 0.35,
            lexical_weight=max(base.lexical_weight, 0.5),
        )
    if mode is EvaluationMode.VECTOR_ONLY:
        return replace(
            base,
            alias_weight=0.0,
            lexical_weight=0.0,
            use_case_weight=0.0,
            hierarchy_weight=0.0,
            vector_weight=max(base.vector_weight, 0.9),
        )
    if mode is EvaluationMode.ALIAS_ONLY:
        return replace(
            base,
            lexical_weight=0.0,
            vector_weight=0.0,
            use_case_weight=0.0,
            hierarchy_weight=0.0,
            alias_weight=1.0,
        )
    return base


def _build_matcher(
    handle: FixtureCatalog,
    *,
    policy: SemanticMatchPolicy,
    mode: EvaluationMode,
    embedding_dim: int,
) -> SemanticCategoryMatcher:
    if mode is EvaluationMode.DEGRADED:
        gateway = AlwaysFailingGateway()
    else:
        gateway = LexicalFallbackGateway(dim=embedding_dim)
    return SemanticCategoryMatcher(
        snapshot_provider=handle.service,
        embedding_repository=handle.embedding_repository,
        query_gateway=gateway,
        policy_provider=StaticSemanticMatchPolicyProvider(policy),
        cache=InMemoryCategoryMatchCache(),
    )


def _result_to_prediction(
    case: EvaluationCase,
    result: CategoryMatchResult,
    handle: FixtureCatalog,
    *,
    latency_ms: float,
    top_k_limit: int,
) -> CasePrediction:
    top_k = []
    for cand in result.candidates[:top_k_limit]:
        key = handle.reverse(cand.category_id) or f"fixture.unknown:{cand.slug}"
        top_k.append(
            CandidatePrediction(
                fixture_key=key,
                score=cand.score,
                rank=cand.rank,
                alias_mode=cand.signals.alias_mode,
            )
        )
    selected: Optional[str] = None
    if result.selected_category_id is not None:
        selected = handle.reverse(result.selected_category_id)
    return CasePrediction(
        case_id=case.case_id,
        predicted_status=result.status.value,
        selected_fixture_key=selected,
        top_k=tuple(top_k),
        latency_ms=latency_ms,
        degraded=result.degraded,
        diagnostics={"decision_reason_code": result.decision.reason_code},
    )


@dataclass(frozen=True)
class RunOutcome:
    predictions: dict[str, CasePrediction]
    latencies_ms: list[float]
    concurrency: ConcurrencySummary
    dependency_failures: int
    degraded_count: int


async def run_matcher_on_dataset(
    dataset: EvaluationDataset,
    handle: FixtureCatalog,
    *,
    policy: SemanticMatchPolicy,
    config: RunnerConfig,
) -> RunOutcome:
    policy = _policy_for_mode(policy, config.mode)
    matcher = _build_matcher(
        handle,
        policy=policy,
        mode=config.mode,
        embedding_dim=config.embedding_dim,
    )

    async def _run_case(case: EvaluationCase) -> tuple[str, Optional[CasePrediction], bool]:
        query = MatchQuery(
            need_description=case.utterance,
            catalog_id=handle.catalog_id,
            locale=case.locale,
            embedding_profile_id=handle.embedding_profile_id,
            catalog_revision=handle.revision,
            extra_hints=case.hints,
        )
        started = time.perf_counter()
        try:
            result = await matcher.match(query)
        except Exception:  # noqa: BLE001 — dependency failure bucket
            return case.case_id, None, False
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        prediction = _result_to_prediction(
            case, result, handle, latency_ms=elapsed_ms, top_k_limit=config.top_k_limit
        )
        return case.case_id, prediction, result.degraded

    wall_started = time.perf_counter()
    outcomes = await bounded_gather(
        [lambda c=case: _run_case(c) for case in dataset.cases],
        workers=config.workers,
    )
    wall_ms = (time.perf_counter() - wall_started) * 1000.0

    predictions: dict[str, CasePrediction] = {}
    latencies: list[float] = []
    dep_failures = 0
    degraded = 0
    for case_id, prediction, was_degraded in outcomes:
        if prediction is None:
            dep_failures += 1
            continue
        predictions[case_id] = prediction
        latencies.append(prediction.latency_ms)
        if was_degraded:
            degraded += 1

    throughput = (len(predictions) / (wall_ms / 1000.0)) if wall_ms else 0.0
    queue_wait = 0.0
    if latencies and wall_ms:
        expected_serial = sum(latencies)
        if expected_serial > wall_ms:
            queue_wait = (expected_serial - wall_ms) / max(1, len(latencies))
    concurrency = ConcurrencySummary(
        workers=config.workers,
        queue_wait_p95_ms=queue_wait,
        throughput_qps=throughput,
        total_wall_ms=wall_ms,
    )
    return RunOutcome(
        predictions=predictions,
        latencies_ms=latencies,
        concurrency=concurrency,
        dependency_failures=dep_failures,
        degraded_count=degraded,
    )


__all__ = [
    "RunOutcome",
    "RunnerConfig",
    "run_matcher_on_dataset",
]
