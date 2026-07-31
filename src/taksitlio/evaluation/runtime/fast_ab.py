"""Isolated FAST A/B benchmark helpers (ADR-009 P1.1).

Calls RemoteFastExtractor directly — never ModelRouter / DEEP fallback.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.benchmarks import (
    ConcurrencyBenchmarkResult,
    run_concurrency_benchmark,
    summarize_latencies,
)
from taksitlio.evaluation.domain import AnnotationStatus, EvaluationCase, ExpectedStatus
from taksitlio.evaluation.runtime.fast_quality import score_fast_extraction
from taksitlio.understanding.fast.errors import (
    FastDeploymentUnavailable,
    FastExtractionError,
    NeedProfileSchemaError,
)
from taksitlio.understanding.fast.remote import RemoteFastExtractor


PROMPT_VERSION = "needprofile-schema-v1-rules-2026-07-31"
SCHEMA_VERSION = "need_profile.schema.json"


REGRESSION_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "reg-neg-pc",
        "utterance": "telefon istemiyorum, bilgisayar arıyorum",
        "expected_constraints": {
            "positive": [{"concept": "bilgisayar"}],
            "negative": [{"concept": "telefon"}],
            "corrections": [],
        },
    },
    {
        "id": "reg-corr-ses",
        "utterance": "yanlış söyledim ses sistemi lazım televizyon değil",
        "expected_constraints": {
            "positive": [{"concept": "ses sistemi"}],
            "negative": [{"concept": "televizyon"}],
            "corrections": [
                {
                    "previous_concept": "televizyon",
                    "replacement_concept": "ses sistemi",
                }
            ],
        },
    },
    {
        "id": "reg-corr-tablet",
        "utterance": "hayır telefon demedim tablet dedim",
        "expected_constraints": {
            "positive": [{"concept": "tablet"}],
            "negative": [{"concept": "telefon"}],
            "corrections": [
                {"previous_concept": "telefon", "replacement_concept": "tablet"}
            ],
        },
    },
    {
        "id": "reg-corr-phone",
        "utterance": "özür dilerim tablet değil telefon",
        "expected_constraints": {
            "positive": [{"concept": "telefon"}],
            "negative": [{"concept": "tablet"}],
            "corrections": [
                {"previous_concept": "tablet", "replacement_concept": "telefon"}
            ],
        },
    },
    {
        "id": "reg-indecision",
        "utterance": "laptop yerine mi masaüstü almalıyım karar veremedim",
        "expected_constraints": {
            "positive": [{"concept": "laptop"}, {"concept": "masaüstü"}],
            "negative": [],
            "corrections": [],
        },
        "expect_clarification": True,
        "expect_multi_need": True,
    },
    {
        "id": "reg-neg-tablet",
        "utterance": "telefon istemiyorum tablet bakıyorum",
        "expected_constraints": {
            "positive": [{"concept": "tablet"}],
            "negative": [{"concept": "telefon"}],
            "corrections": [],
        },
    },
    {
        "id": "reg-corr-laptop",
        "utterance": "tablet değil laptop lazım",
        "expected_constraints": {
            "positive": [{"concept": "laptop"}],
            "negative": [{"concept": "tablet"}],
            "corrections": [
                {"previous_concept": "tablet", "replacement_concept": "laptop"}
            ],
        },
    },
    {
        "id": "reg-neg-pc2",
        "utterance": "yok telefonu boşver bilgisayar alacağız",
        "expected_constraints": {
            "positive": [{"concept": "bilgisayar"}],
            "negative": [{"concept": "telefon"}],
            "corrections": [],
        },
    },
    {
        "id": "reg-neg-have-phone",
        "utterance": "telefonum var yeni telefon almak istemiyorum tablet lazım",
        "expected_constraints": {
            "positive": [{"concept": "tablet"}],
            "negative": [{"concept": "telefon"}],
            "corrections": [],
        },
    },
)


@dataclass(frozen=True)
class CandidateSpec:
    code: str  # FAST_A_REAL | FAST_B_REAL
    role: str
    base_url: str
    model_reference: str
    runtime_alias: str
    service_name: str
    sibling_service: str
    quantization: str = "q4_k_m"


def candidate_specs_from_env() -> dict[str, CandidateSpec]:
    """Build A/B specs from env — never hardcode hosts inside ``src/``."""

    import os

    a_url = (
        os.environ.get("FAST_A_BASE_URL")
        or os.environ.get("FAST_PROVIDER_BASE_URL")
        or ""
    ).rstrip("/")
    b_url = (os.environ.get("FAST_B_BASE_URL") or os.environ.get("FAST_CHALLENGER_BASE_URL") or "").rstrip("/")
    a_model = (
        os.environ.get("FAST_A_MODEL_REFERENCE")
        or os.environ.get("FAST_MODEL_REFERENCE")
        or "poc-fast-understanding"
    )
    b_model = (
        os.environ.get("FAST_B_MODEL_REFERENCE")
        or os.environ.get("FAST_CHALLENGER_MODEL_REFERENCE")
        or "poc-fast-challenger"
    )
    return {
        "A": CandidateSpec(
            code="FAST_A_REAL",
            role="PRIMARY",
            base_url=a_url,
            model_reference=a_model,
            runtime_alias=os.environ.get("FAST_A_RUNTIME_ALIAS")
            or os.environ.get("FAST_RUNTIME_ALIAS")
            or "poc-fast-understanding",
            service_name=os.environ.get("FAST_A_SERVICE") or "taksitlio-fast-a",
            sibling_service=os.environ.get("FAST_B_SERVICE") or "taksitlio-fast-b",
            quantization=os.environ.get("FAST_QUANTIZATION") or "q4_k_m",
        ),
        "B": CandidateSpec(
            code="FAST_B_REAL",
            role="CHALLENGER",
            base_url=b_url,
            model_reference=b_model,
            runtime_alias=os.environ.get("FAST_B_RUNTIME_ALIAS")
            or os.environ.get("FAST_CHALLENGER_RUNTIME_ALIAS")
            or "poc-fast-challenger",
            service_name=os.environ.get("FAST_B_SERVICE") or "taksitlio-fast-b",
            sibling_service=os.environ.get("FAST_A_SERVICE") or "taksitlio-fast-a",
            quantization=os.environ.get("FAST_QUANTIZATION") or "q4_k_m",
        ),
    }


@dataclass
class ExtractResult:
    status: str  # ok | INVALID_SCHEMA | FORBIDDEN_IDENTIFIER | TIMEOUT | PROVIDER_ERROR | EMPTY_OUTPUT | TRUNCATED
    latency_ms: float
    need_profile: Optional[dict[str, Any]] = None
    predicted_constraints: Optional[dict[str, Any]] = None
    usage: Optional[Mapping[str, Any]] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


def build_isolated_extractor(
    spec: CandidateSpec,
    *,
    timeout_ms: int,
    max_output_tokens: int,
    temperature: float = 0.0,
) -> RemoteFastExtractor:
    """Construct extractor bound to one candidate — env ModelRouter unused."""

    return RemoteFastExtractor(
        base_url=spec.base_url,
        model_reference=spec.model_reference,
        timeout_ms=timeout_ms,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        deployment_code=spec.code,
        profile_code="FAST_UNDERSTANDING",
    )


def human_reviewed_cases(cases: Sequence[EvaluationCase]) -> list[EvaluationCase]:
    return [c for c in cases if c.annotation.status is AnnotationStatus.HUMAN_REVIEWED]


def draft_correction_cases(cases: Sequence[EvaluationCase]) -> list[EvaluationCase]:
    return [
        c
        for c in cases
        if (c.semantic_constraints or {}).get("corrections")
        and c.annotation.status is AnnotationStatus.DRAFT
    ]


def _usage_tokens(usage: Optional[Mapping[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(usage, Mapping):
        return None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return (
        int(prompt) if isinstance(prompt, (int, float)) else None,
        int(completion) if isinstance(completion, (int, float)) else None,
    )


async def extract_one(
    extractor: RemoteFastExtractor,
    utterance: str,
    *,
    locale: str = "tr-TR",
) -> ExtractResult:
    started = time.perf_counter()
    try:
        outcome = await extractor.extract(utterance, locale=locale)
    except NeedProfileSchemaError as exc:
        return ExtractResult(
            status="INVALID_SCHEMA",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=str(exc),
        )
    except FastDeploymentUnavailable as exc:
        msg = str(exc).lower()
        status = "TIMEOUT" if "timeout" in msg else "PROVIDER_ERROR"
        return ExtractResult(
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=str(exc),
        )
    except FastExtractionError as exc:
        code = getattr(exc, "reason_code", "") or ""
        if code == "FORBIDDEN_IDENTIFIER_GENERATION":
            return ExtractResult(
                status="FORBIDDEN_IDENTIFIER",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=code,
            )
        return ExtractResult(
            status="PROVIDER_ERROR",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=code or str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(
            status="PROVIDER_ERROR",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=f"{type(exc).__name__}: {exc}",
        )

    diag = outcome.diagnostics or {}
    usage = diag.get("usage") if isinstance(diag, Mapping) else None
    finish = diag.get("finish_reason") if isinstance(diag, Mapping) else None
    prompt_tokens, completion_tokens = _usage_tokens(
        usage if isinstance(usage, Mapping) else None
    )
    status = "ok"
    if finish == "length":
        status = "TRUNCATED"
    pred = outcome.constraints.to_matcher_dict() if outcome.constraints else {}
    return ExtractResult(
        status=status,
        latency_ms=outcome.latency_ms,
        need_profile=dict(outcome.need_profile or {}),
        predicted_constraints=pred,
        usage=usage if isinstance(usage, Mapping) else None,
        finish_reason=str(finish) if finish else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def scoring_row(
    *,
    expected_constraints: Mapping[str, Any],
    result: ExtractResult,
    expect_clarification: Optional[bool] = None,
) -> dict[str, Any]:
    err_map = {
        "INVALID_SCHEMA": "INVALID_SCHEMA",
        "FORBIDDEN_IDENTIFIER": "FORBIDDEN_IDENTIFIER",
        "TIMEOUT": "TIMEOUT",
        "PROVIDER_ERROR": "PROVIDER_ERROR",
        "EMPTY_OUTPUT": "INVALID_SCHEMA",
        "TRUNCATED": "INVALID_SCHEMA",
    }
    if result.status != "ok":
        return {
            "error": err_map.get(result.status, "PROVIDER_ERROR"),
            "expected_constraints": dict(expected_constraints),
            "predicted_constraints": {},
            "expected_need_profile": {},
            "predicted_need_profile": {},
        }
    expected_np: dict[str, Any] = {}
    if expect_clarification is not None:
        expected_np["clarification"] = {"required": expect_clarification}
    return {
        "error": None,
        "expected_constraints": dict(expected_constraints),
        "predicted_constraints": dict(result.predicted_constraints or {}),
        "expected_need_profile": expected_np,
        "predicted_need_profile": dict(result.need_profile or {}),
    }


@dataclass
class WarmupReport:
    cold_latency_ms: Optional[float] = None
    warmups: list[float] = field(default_factory=list)
    fifth_warmup_latency_ms: Optional[float] = None


async def run_warmup(
    extractor: RemoteFastExtractor,
    *,
    count: int = 5,
    utterance: str = "tablet bakıyorum",
) -> WarmupReport:
    report = WarmupReport()
    for i in range(count):
        result = await extract_one(extractor, utterance)
        if i == 0:
            report.cold_latency_ms = result.latency_ms
        report.warmups.append(result.latency_ms)
    if report.warmups:
        report.fifth_warmup_latency_ms = report.warmups[-1]
    return report


async def run_quality_pass(
    extractor: RemoteFastExtractor,
    cases: Sequence[EvaluationCase],
) -> tuple[dict[str, Any], list[ExtractResult], list[dict[str, Any]]]:
    results: list[ExtractResult] = []
    rows: list[dict[str, Any]] = []
    detail_safe: list[dict[str, Any]] = []
    for case in cases:
        result = await extract_one(extractor, case.utterance, locale=case.locale)
        results.append(result)
        rows.append(
            scoring_row(
                expected_constraints=case.semantic_constraints or {},
                result=result,
            )
        )
        detail_safe.append(
            {
                "case_id": case.case_id,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "finish_reason": result.finish_reason,
                "tags": list(case.dimensions.tags),
                "expected_status": case.expected.status.value,
                "has_negative": bool((case.semantic_constraints or {}).get("negative")),
                "has_positive": bool((case.semantic_constraints or {}).get("positive")),
                "has_corrections": bool(
                    (case.semantic_constraints or {}).get("corrections")
                ),
            }
        )
    metrics = score_fast_extraction(rows).to_dict()
    # Extra counters not in score_fast_extraction.
    metrics["provider_error_count"] = sum(1 for r in results if r.status == "PROVIDER_ERROR")
    metrics["empty_output_count"] = sum(1 for r in results if r.status == "EMPTY_OUTPUT")
    metrics["truncated_output_count"] = sum(1 for r in results if r.status == "TRUNCATED")
    metrics["valid_schema_count"] = sum(1 for r in results if r.status == "ok")
    metrics["fixture_key_generation_count"] = metrics.get(
        "forbidden_identifier_generation_count", 0
    )
    metrics["category_uuid_generation_count"] = metrics.get(
        "forbidden_identifier_generation_count", 0
    )
    # Segment accuracies (status-level proxies).
    no_match_cases = [
        (c, r)
        for c, r in zip(cases, results)
        if c.expected.status is ExpectedStatus.NO_MATCH
    ]
    if no_match_cases:
        ok = 0
        for _c, r in no_match_cases:
            intent = ((r.need_profile or {}).get("intent") or {}).get("type")
            if r.status == "ok" and intent in {"OUT_OF_SCOPE", "OTHER"}:
                ok += 1
        metrics["no_match_intent_accuracy"] = ok / len(no_match_cases)
    else:
        metrics["no_match_intent_accuracy"] = None

    multi = [
        (c, r)
        for c, r in zip(cases, results)
        if any("multi_need" in t for t in c.dimensions.tags)
    ]
    if multi:
        ok = 0
        for _c, r in multi:
            signals = (r.need_profile or {}).get("signals") or {}
            prefs = (r.need_profile or {}).get("preferences") or []
            if r.status == "ok" and (signals.get("multiple_needs") or len(prefs) >= 2):
                ok += 1
        metrics["multi_need_accuracy"] = ok / len(multi)
    else:
        metrics["multi_need_accuracy"] = None

    return metrics, results, detail_safe


async def run_regression_pass(
    extractor: RemoteFastExtractor,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for item in REGRESSION_CASES:
        result = await extract_one(extractor, item["utterance"])
        rows.append(
            scoring_row(
                expected_constraints=item["expected_constraints"],
                result=result,
                expect_clarification=item.get("expect_clarification"),
            )
        )
        sc = result.predicted_constraints or {}
        details.append(
            {
                "id": item["id"],
                # utterance omitted from persisted reports by privacy scrub;
                # keep only hashed-safe fields in returned detail for internal use.
                "utterance_len": len(item["utterance"]),
                "status": result.status,
                "schema_valid": result.status == "ok",
                "latency_ms": result.latency_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "positive": [x.get("concept") for x in (sc.get("positive") or [])],
                "negative": [x.get("concept") for x in (sc.get("negative") or [])],
                "corrections": sc.get("corrections") or [],
                "clarification": (result.need_profile or {}).get("clarification"),
                "multi_need": bool(
                    ((result.need_profile or {}).get("signals") or {}).get(
                        "multiple_needs"
                    )
                ),
            }
        )
    metrics = score_fast_extraction(rows).to_dict()
    return metrics, details


async def run_concurrency_levels(
    extractor: RemoteFastExtractor,
    utterances: Sequence[str],
    *,
    timeout_ms: int,
    levels: Sequence[int] = (1, 4, 8),
    requests_per_level: int = 100,
    warmup_per_level: int = 5,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Run concurrency ladder with safety stop before level 8."""

    reports: list[dict[str, Any]] = []
    safety_stop: Optional[str] = None
    if not utterances:
        utterances = ["tablet bakıyorum"]

    for level in levels:
        if safety_stop and level >= 8:
            reports.append(
                {
                    "concurrency": level,
                    "status": "CONCURRENCY_8_NOT_RUN_SAFETY_STOP",
                    "reason": safety_stop,
                }
            )
            continue

        # Warmups for this concurrency (not counted).
        async def _warmup_factory(i: int) -> tuple[str, float, int]:
            utt = utterances[i % len(utterances)]
            r = await extract_one(extractor, utt)
            status = "ok" if r.status == "ok" else (
                "timeout" if r.status == "TIMEOUT" else (
                    "schema_failure"
                    if r.status in {"INVALID_SCHEMA", "TRUNCATED", "EMPTY_OUTPUT"}
                    else "error"
                )
            )
            return status, r.latency_ms, int(r.completion_tokens or 0)

        await run_concurrency_benchmark(
            _warmup_factory,
            concurrency=level,
            requests=warmup_per_level,
            phase="WARMUP",
        )

        async def _factory(i: int) -> tuple[str, float, int]:
            utt = utterances[i % len(utterances)]
            r = await extract_one(extractor, utt)
            if r.status == "ok":
                status = "ok"
            elif r.status == "TIMEOUT":
                status = "timeout"
            elif r.status in {"INVALID_SCHEMA", "TRUNCATED", "EMPTY_OUTPUT"}:
                status = "schema_failure"
            else:
                status = "error"
            return status, r.latency_ms, int(r.completion_tokens or 0)

        result: ConcurrencyBenchmarkResult = await run_concurrency_benchmark(
            _factory,
            concurrency=level,
            requests=requests_per_level,
            phase="WARM",
        )
        payload = result.to_dict()
        timeout_rate = (
            result.timeout / result.requests if result.requests else 0.0
        )
        payload["timeout_rate"] = timeout_rate
        payload["provider_error"] = result.requests - result.success - result.timeout - result.schema_failure
        reports.append(payload)

        if level == 4:
            p95 = result.latency.p95_ms
            if timeout_rate > 0.10:
                safety_stop = f"concurrency4_timeout_rate={timeout_rate:.3f}>0.10"
            elif p95 > timeout_ms * 0.90:
                safety_stop = (
                    f"concurrency4_p95={p95:.0f}ms > 90% of timeout_ms={timeout_ms}"
                )

    return reports, safety_stop


def latency_summary_from_results(results: Sequence[ExtractResult]) -> dict[str, Any]:
    ok_latencies = [r.latency_ms for r in results if r.status == "ok"]
    all_latencies = [r.latency_ms for r in results]
    prompt_tokens = [r.prompt_tokens for r in results if r.prompt_tokens is not None]
    completion_tokens = [
        r.completion_tokens for r in results if r.completion_tokens is not None
    ]
    stats = summarize_latencies(ok_latencies or all_latencies)
    wall = sum(all_latencies) / 1000.0 if all_latencies else 0.0
    return {
        "request_count": len(results),
        "success_count": sum(1 for r in results if r.status == "ok"),
        "timeout_count": sum(1 for r in results if r.status == "TIMEOUT"),
        "schema_failure_count": sum(
            1
            for r in results
            if r.status in {"INVALID_SCHEMA", "TRUNCATED", "EMPTY_OUTPUT"}
        ),
        "latency": stats.to_dict(),
        "prompt_tokens": {
            "count": len(prompt_tokens),
            "p50": summarize_latencies(prompt_tokens).p50_ms if prompt_tokens else None,
            "p95": summarize_latencies(prompt_tokens).p95_ms if prompt_tokens else None,
            "max": max(prompt_tokens) if prompt_tokens else None,
            "mean": (sum(prompt_tokens) / len(prompt_tokens)) if prompt_tokens else None,
        },
        "completion_tokens": {
            "count": len(completion_tokens),
            "p50": summarize_latencies(completion_tokens).p50_ms
            if completion_tokens
            else None,
            "p95": summarize_latencies(completion_tokens).p95_ms
            if completion_tokens
            else None,
            "max": max(completion_tokens) if completion_tokens else None,
            "mean": (sum(completion_tokens) / len(completion_tokens))
            if completion_tokens
            else None,
            "share_under_128": (
                sum(1 for t in completion_tokens if t <= 128) / len(completion_tokens)
                if completion_tokens
                else None
            ),
            "share_under_192": (
                sum(1 for t in completion_tokens if t <= 192) / len(completion_tokens)
                if completion_tokens
                else None
            ),
            "share_under_256": (
                sum(1 for t in completion_tokens if t <= 256) / len(completion_tokens)
                if completion_tokens
                else None
            ),
        },
        "throughput_rps_serial": (len(results) / wall) if wall > 0 else None,
        "generation_tokens_per_sec_est": (
            (sum(completion_tokens) / wall) if wall > 0 and completion_tokens else None
        ),
    }


def default_timeout_ms() -> int:
    return int(os.environ.get("FAST_TIMEOUT_MS") or "60000")


def default_max_tokens() -> int:
    return int(os.environ.get("FAST_MAX_OUTPUT_TOKENS") or "384")
