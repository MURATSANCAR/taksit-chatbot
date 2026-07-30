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
    build_from_match_result,
)
from taksitlio.evaluation.concurrency import ConcurrencySummary, bounded_gather
from taksitlio.evaluation.domain import (
    CandidatePrediction,
    CasePrediction,
    EvaluationCase,
    EvaluationDataset,
    EvaluationInputMode,
    EvaluationMode,
)
from taksitlio.evaluation.fixture_catalog import FixtureCatalog
from taksitlio.semantic_constraints import SemanticConstraintValidator
from taksitlio.understanding.fast import (
    DeterministicFastExtractor,
    FastDeploymentUnavailable,
    FastExtractionError,
    FastNeedUnderstanding,
)


@dataclass(frozen=True)
class RunnerConfig:
    mode: EvaluationMode = EvaluationMode.FULL
    workers: int = 4
    embedding_dim: int = 64
    top_k_limit: int = 3
    input_mode: EvaluationInputMode = EvaluationInputMode.MATCHER_ORACLE_INPUT


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

    diagnostics = dict(result.diagnostics or {})
    pool_ids = diagnostics.get("candidate_pool_ids") or ()
    retrieved_raw = diagnostics.get("retrieved_by") or {}

    pool_fixture_keys: list[str] = []
    for cat_id in pool_ids:
        key = handle.reverse(cat_id)
        if key:
            pool_fixture_keys.append(key)
    retrieved_by: dict[str, str] = {}
    for cat_id, channel in retrieved_raw.items():
        key = handle.reverse(cat_id)
        if key:
            retrieved_by[key] = str(channel)

    signals_summary: dict = {}
    if result.candidates:
        top_signals = result.candidates[0].signals
        signals_summary = {
            "top_alias": top_signals.alias,
            "top_vector": top_signals.vector,
            "top_direct_alias_match": top_signals.direct_alias_match,
            "top_hierarchy_collapsed": top_signals.hierarchy_collapsed,
            # ADR-008: per-channel breakdown of the top candidate.
            "top_surface_exact_alias": top_signals.surface_exact_alias,
            "top_normalized_exact_alias": top_signals.normalized_exact_alias,
            "top_token_set_alias": top_signals.token_set_alias,
            "top_prefix_safe_alias": top_signals.prefix_safe_alias,
            "top_character_ngram": top_signals.character_ngram,
            "top_morphological_variant": top_signals.morphological_variant,
            "top_negative_penalty": top_signals.negative_penalty,
        }
    diagnostics["decision_reason_code"] = result.decision.reason_code
    # ADR-008 P0: enrich prediction diagnostics with the typed retrieval
    # diagnostic (surface / normalized / variants / channels / reason
    # codes). We only add ``CORRECT_CANDIDATE_RANKED_LOW`` when the case
    # declares a single acceptable fixture key that resolves to a known
    # category id.
    expected_cat_id: Optional[str] = None
    accepted = getattr(case.expected, "acceptable_fixture_keys", ())
    if isinstance(accepted, (tuple, list)) and len(accepted) == 1:
        key = accepted[0]
        if isinstance(key, str) and key:
            try:
                expected_cat_id = handle.resolve(key)
            except Exception:
                expected_cat_id = None
    retrieval_diag = build_from_match_result(
        result, expected_category_id=expected_cat_id
    )
    diagnostics["retrieval_diagnostic"] = retrieval_diag.to_dict()
    return CasePrediction(
        case_id=case.case_id,
        predicted_status=result.status.value,
        selected_fixture_key=selected,
        top_k=tuple(top_k),
        latency_ms=latency_ms,
        degraded=result.degraded,
        diagnostics=diagnostics,
        pool_fixture_keys=tuple(pool_fixture_keys),
        retrieved_by=retrieved_by,
        decision_reason_code=result.decision.reason_code,
        signals_summary=signals_summary,
    )


@dataclass(frozen=True)
class RunOutcome:
    predictions: dict[str, CasePrediction]
    latencies_ms: list[float]
    concurrency: ConcurrencySummary
    dependency_failures: int
    degraded_count: int
    fast_extraction_failures: int = 0
    input_mode: str = EvaluationInputMode.MATCHER_ORACLE_INPUT.value


async def _resolve_constraints(
    case: EvaluationCase,
    *,
    input_mode: EvaluationInputMode,
    fast: Optional[FastNeedUnderstanding],
    validator: SemanticConstraintValidator,
) -> tuple[dict, Optional[str]]:
    """Return ``(constraints_dict, fast_failure_reason)`` for one case.

    ``fast_failure_reason`` is set only when the FAST extractor raised —
    the caller then records BLOCKED_DEPENDENCY in the diagnostics bucket.
    """

    if input_mode is EvaluationInputMode.MATCHER_ONLY:
        return {}, None
    if input_mode is EvaluationInputMode.MATCHER_ORACLE_INPUT:
        return dict(case.semantic_constraints or {}), None
    if fast is None:
        return {}, "FAST_EXTRACTOR_NOT_CONFIGURED"
    try:
        outcome = await fast.extract(case.utterance, locale=case.locale)
    except FastDeploymentUnavailable as exc:
        return {}, exc.reason_code or "FAST_DEPLOYMENT_UNAVAILABLE"
    except FastExtractionError as exc:
        return {}, exc.reason_code or "FAST_EXTRACTION_ERROR"
    validated = validator.validate(outcome.constraints.to_matcher_dict())
    return validated.to_matcher_dict(), None


async def run_matcher_on_dataset(
    dataset: EvaluationDataset,
    handle: FixtureCatalog,
    *,
    policy: SemanticMatchPolicy,
    config: RunnerConfig,
    fast_extractor: Optional[FastNeedUnderstanding] = None,
    constraint_validator: Optional[SemanticConstraintValidator] = None,
) -> RunOutcome:
    policy = _policy_for_mode(policy, config.mode)
    matcher = _build_matcher(
        handle,
        policy=policy,
        mode=config.mode,
        embedding_dim=config.embedding_dim,
    )
    input_mode = config.input_mode
    validator = constraint_validator or SemanticConstraintValidator()
    fast = fast_extractor
    if input_mode in {
        EvaluationInputMode.END_TO_END_RUNTIME_INPUT,
        EvaluationInputMode.FAST_EXTRACTION_ONLY,
    } and fast is None:
        # Default to the deterministic offline extractor so evaluation
        # runs are reproducible without a real FAST deployment. This is
        # explicit — no silent lexical fallback in the production path.
        fast = DeterministicFastExtractor(validator=validator)

    async def _run_case(case: EvaluationCase) -> tuple[str, Optional[CasePrediction], bool, Optional[str]]:
        constraints_dict, fast_failure = await _resolve_constraints(
            case,
            input_mode=input_mode,
            fast=fast,
            validator=validator,
        )
        if input_mode is EvaluationInputMode.FAST_EXTRACTION_ONLY:
            # No matcher — synthesise a bare prediction whose top_k / status
            # are undefined; downstream metrics that require a matcher
            # result will simply skip these cases.
            elapsed_ms = 0.0
            prediction = CasePrediction(
                case_id=case.case_id,
                predicted_status="FAST_ONLY",
                selected_fixture_key=None,
                top_k=(),
                latency_ms=elapsed_ms,
                diagnostics={
                    "input_mode": input_mode.value,
                    "extracted_constraints": constraints_dict,
                    "fast_failure": fast_failure,
                },
            )
            return case.case_id, prediction, False, fast_failure

        query = MatchQuery(
            need_description=case.utterance,
            catalog_id=handle.catalog_id,
            locale=case.locale,
            embedding_profile_id=handle.embedding_profile_id,
            catalog_revision=handle.revision,
            extra_hints=case.hints,
            semantic_constraints=constraints_dict,
        )
        started = time.perf_counter()
        try:
            result = await matcher.match(query)
        except Exception:  # noqa: BLE001 — dependency failure bucket
            return case.case_id, None, False, fast_failure
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        prediction = _result_to_prediction(
            case, result, handle, latency_ms=elapsed_ms, top_k_limit=config.top_k_limit
        )
        # Enrich diagnostics with input-mode + FAST reason.
        prediction = replace(
            prediction,
            diagnostics={
                **prediction.diagnostics,
                "input_mode": input_mode.value,
                "fast_failure": fast_failure,
            },
        )
        return case.case_id, prediction, result.degraded, fast_failure

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
    fast_failures = 0
    for case_id, prediction, was_degraded, fast_failure in outcomes:
        if prediction is None:
            dep_failures += 1
            if fast_failure is not None:
                fast_failures += 1
            continue
        predictions[case_id] = prediction
        latencies.append(prediction.latency_ms)
        if was_degraded:
            degraded += 1
        if fast_failure is not None:
            fast_failures += 1

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
        fast_extraction_failures=fast_failures,
        input_mode=input_mode.value,
    )


__all__ = [
    "RunOutcome",
    "RunnerConfig",
    "run_matcher_on_dataset",
]
