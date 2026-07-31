#!/usr/bin/env python3
"""ADR-009 — FAST A/B + deterministic hybrid HR-100 quality + warm latency.

Never uses 35B / ModelRouter. Campaign stays CLOSED on performance miss.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from taksitlio.evaluation import (
    EvaluationMode,
    build_fixture_catalog,
    dispose_fixture_catalog,
    evaluate,
    load_evaluation_config,
    load_jsonl,
    run_matcher_on_dataset,
)
from taksitlio.evaluation.benchmarks import summarize_latencies
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    EvaluationInputMode,
    EvaluationCase,
)
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.evaluation.runtime.fast_ab import (
    PROMPT_VERSION,
    REGRESSION_CASES,
    SCHEMA_VERSION,
    build_isolated_extractor,
    candidate_specs_from_env,
    draft_correction_cases,
    extract_one,
    human_reviewed_cases,
    scoring_row,
)
from taksitlio.evaluation.runtime.fast_quality import score_fast_extraction
from taksitlio.runtime_verification.report import report_envelope, write_runtime_report
from taksitlio.semantic_constraints import SemanticConstraintValidator
from taksitlio.semantic_matching import SemanticMatchPolicy
from taksitlio.understanding.fast.deterministic import DeterministicFastExtractor
from taksitlio.understanding.fast.hybrid import hybrid_final_constraints


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "datasets" / "validation" / "tr-category-validation.v4.jsonl"
FIXTURE = ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v3.json"
REPORTS = ROOT / "evaluation" / "reports"

DEFAULT_URLS = {
    "A": "http://127.0.0.1:8021",
    "B": "http://127.0.0.1:8022",
    "C": "http://127.0.0.1:8023",
}
DEFAULT_ALIASES = {
    "A": "poc-fast-understanding",
    "B": "poc-fast-challenger",
    "C": "poc-fast-nine-b",
}


def _sh(cmd: str, **kwargs: Any) -> str:
    proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, check=False, **kwargs)
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _isolate_candidate(key: str) -> None:
    cands = candidate_specs_from_env()
    spec = cands[key]
    for other_key, other in cands.items():
        if other_key != key:
            _sh(f"sudo systemctl stop {other.service_name}.service")
    _sh(f"sudo systemctl start {spec.service_name}.service")
    url = spec.base_url or DEFAULT_URLS[key]
    for _ in range(40):
        if _sh(f"curl -fsS -m 2 {url}/health"):
            return
        time.sleep(1)
    raise RuntimeError(f"FAST {key} health failed at {url}")


def _restore_all() -> None:
    _sh(
        "sudo systemctl start "
        "taksitlio-fast-a.service taksitlio-fast-b.service taksitlio-fast-c.service",
        check=False,
    )


def _metric(payload: dict, key: str) -> Optional[float]:
    v = payload.get(key)
    if isinstance(v, dict) and "value" in v:
        return float(v["value"]) if v["value"] is not None else None
    if isinstance(v, (int, float)):
        return float(v)
    return None


async def _hybrid_extract(
    remote,
    deterministic: DeterministicFastExtractor,
    utterance: str,
    *,
    locale: str = "tr-TR",
) -> dict[str, Any]:
    model = await extract_one(remote, utterance, locale=locale)
    det = await deterministic.extract(utterance, locale=locale)
    det_bag = det.constraints.to_matcher_dict() if det.constraints else {}
    model_bag = model.predicted_constraints or {}
    hybrid = hybrid_final_constraints(
        model_constraints=model_bag,
        deterministic_constraints=det_bag,
    )
    return {
        "model": model,
        "deterministic_bag": det_bag,
        "hybrid": hybrid,
        "need_profile": model.need_profile,
    }


async def run_hr100(
    *,
    candidate_key: str,
    timeout_ms: int,
    max_tokens: int,
) -> dict[str, Any]:
    cands = candidate_specs_from_env()
    spec = cands[candidate_key]
    if not spec.base_url:
        spec = replace(spec, base_url=DEFAULT_URLS[candidate_key])

    remote = build_isolated_extractor(spec, timeout_ms=timeout_ms, max_output_tokens=max_tokens)
    deterministic = DeterministicFastExtractor()
    dataset = load_jsonl(DATASET)
    hr = human_reviewed_cases(dataset.cases)
    assert len(hr) >= 100, f"expected >=100 HR, got {len(hr)}"
    hr = hr[:100]

    # Warmup (not scored)
    for _ in range(5):
        await extract_one(remote, "tablet bakıyorum")

    rows_model = []
    rows_hybrid = []
    latencies = []
    prompt_tokens = []
    completion_tokens = []
    detail = []
    hybrid_by_case: dict[str, dict] = {}
    forbidden = 0

    for idx, case in enumerate(hr, start=1):
        packed = await _hybrid_extract(remote, deterministic, case.utterance, locale=case.locale)
        model = packed["model"]
        hybrid = packed["hybrid"]
        hybrid_by_case[case.case_id] = hybrid
        latencies.append(model.latency_ms)
        if model.prompt_tokens is not None:
            prompt_tokens.append(model.prompt_tokens)
        if model.completion_tokens is not None:
            completion_tokens.append(model.completion_tokens)

        rows_model.append(
            scoring_row(
                expected_constraints=case.semantic_constraints or {},
                result=model,
            )
        )
        # Hybrid scoring row: treat as ok unless model failed schema/timeout.
        if model.status == "ok":
            rows_hybrid.append(
                {
                    "error": None,
                    "expected_constraints": case.semantic_constraints or {},
                    "predicted_constraints": hybrid,
                    "expected_need_profile": {},
                    "predicted_need_profile": model.need_profile or {},
                }
            )
        else:
            rows_hybrid.append(
                scoring_row(
                    expected_constraints=case.semantic_constraints or {},
                    result=model,
                )
            )
        if model.status == "FORBIDDEN_IDENTIFIER":
            forbidden += 1
        detail.append(
            {
                "case_id": case.case_id,
                "status": model.status,
                "latency_ms": model.latency_ms,
                "prompt_tokens": model.prompt_tokens,
                "completion_tokens": model.completion_tokens,
                "has_negative": bool((case.semantic_constraints or {}).get("negative")),
                "has_positive": bool((case.semantic_constraints or {}).get("positive")),
                "has_corrections": bool(
                    (case.semantic_constraints or {}).get("corrections")
                ),
            }
        )
        if idx % 10 == 0 or idx == len(hr):
            print(
                f"[HR] {idx}/{len(hr)} last_status={model.status} "
                f"last_lat_ms={round(model.latency_ms)}",
                flush=True,
            )

    model_metrics = score_fast_extraction(rows_model).to_dict()
    hybrid_metrics = score_fast_extraction(rows_hybrid).to_dict()

    # Correction pool: DRAFT annotations + regression correction items (hybrid).
    draft = draft_correction_cases(dataset.cases)
    corr_rows = []
    for case in draft:
        packed = await _hybrid_extract(remote, deterministic, case.utterance, locale=case.locale)
        model = packed["model"]
        if model.status == "ok":
            corr_rows.append(
                {
                    "error": None,
                    "expected_constraints": case.semantic_constraints or {},
                    "predicted_constraints": packed["hybrid"],
                    "expected_need_profile": {},
                    "predicted_need_profile": model.need_profile or {},
                }
            )
        else:
            corr_rows.append(
                scoring_row(
                    expected_constraints=case.semantic_constraints or {},
                    result=model,
                )
            )
    for item in REGRESSION_CASES:
        if not (item["expected_constraints"].get("corrections") or []):
            continue
        packed = await _hybrid_extract(remote, deterministic, item["utterance"])
        model = packed["model"]
        if model.status == "ok":
            corr_rows.append(
                {
                    "error": None,
                    "expected_constraints": item["expected_constraints"],
                    "predicted_constraints": packed["hybrid"],
                    "expected_need_profile": {},
                    "predicted_need_profile": model.need_profile or {},
                }
            )
        else:
            corr_rows.append(
                scoring_row(
                    expected_constraints=item["expected_constraints"],
                    result=model,
                )
            )
    corr_metrics = score_fast_extraction(corr_rows).to_dict() if corr_rows else {}

    wall_s = sum(latencies) / 1000.0 if latencies else 0.0
    latency = summarize_latencies(latencies).to_dict()
    latency_report = {
        "concurrency": 1,
        "phase": "WARM",
        "request_count": len(latencies),
        "timeout_count": model_metrics.get("timeout_count"),
        "timeout_rate": (model_metrics.get("timeout_count") or 0) / max(len(latencies), 1),
        "schema_failure_count": model_metrics.get("invalid_schema_count"),
        "truncated_count": model_metrics.get("truncated_count"),
        "latency": latency,
        "prompt_tokens_per_sec": (
            (sum(prompt_tokens) / wall_s) if wall_s and prompt_tokens else None
        ),
        "generation_tokens_per_sec": (
            (sum(completion_tokens) / wall_s) if wall_s and completion_tokens else None
        ),
        "targets": {"warm_p50_ms": 2000, "warm_p95_ms": 3000},
    }

    await remote.aclose()
    return {
        "hr_count": len(hr),
        "model_metrics": model_metrics,
        "hybrid_metrics": hybrid_metrics,
        "correction_pool_hybrid": {
            "case_count": len(corr_rows),
            "metrics": corr_metrics,
            "note": (
                "HR set has 0 correction gold labels; hybrid correction recall "
                "measured on DRAFT corrections + regression correction utterances."
            ),
        },
        "forbidden_identifier_count": forbidden,
        "latency": latency_report,
        "detail_safe": detail,
        "hybrid_by_case": hybrid_by_case,
        "deployment": {
            "code": spec.code,
            "runtime_alias": spec.runtime_alias,
            "base_url": spec.base_url,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "candidate_key": candidate_key,
        },
    }


class _CachedHybridFast:
    """Replay hybrid constraints for E2E without second remote FAST calls."""

    name = "cached_hybrid_fast"

    def __init__(self, hybrid_by_case: dict[str, dict], cases: list[EvaluationCase]):
        self._by_utt = {
            c.utterance: hybrid_by_case[c.case_id]
            for c in cases
            if c.case_id in hybrid_by_case
        }
        self._validator = SemanticConstraintValidator()

    async def aclose(self) -> None:
        return None

    async def extract(self, utterance: str, *, locale: str = "tr-TR", session_summary=None):
        from taksitlio.understanding.fast.protocol import FastExtractionOutcome

        bag = self._by_utt.get(utterance) or {}
        constraints = self._validator.validate(bag)
        return FastExtractionOutcome(
            utterance=utterance,
            need_profile={
                "intent": {"type": "PRODUCT_PURCHASE", "confidence": 0.8},
                "need_description": utterance[:200] or "…",
                "budget": {
                    "type": "UNKNOWN",
                    "value": None,
                    "minimum": None,
                    "maximum": None,
                    "monthly_payment": None,
                    "currency": "TRY",
                },
                "preferences": [],
                "usage_context": [],
                "entities": [],
                "ambiguities": [],
                "clarification": {"required": False, "question_intent": None},
                "confidence": 0.8,
                "semantic_constraints": bag,
            },
            constraints=constraints,
            extractor=self.name,
            latency_ms=0.0,
            diagnostics={"cached_hybrid": True},
        )


async def run_e2e_cached(hybrid_by_case: dict[str, dict]) -> dict[str, Any]:
    dataset = load_jsonl(DATASET)
    hr = human_reviewed_cases(dataset.cases)[:100]
    limited = replace(dataset, cases=tuple(hr))
    cached = _CachedHybridFast(hybrid_by_case, hr)
    handle = await build_fixture_catalog(fixture_path=FIXTURE)
    try:
        outcome = await run_matcher_on_dataset(
            limited,
            handle,
            policy=SemanticMatchPolicy(),
            config=RunnerConfig(
                mode=EvaluationMode.FULL,
                workers=4,
                embedding_dim=64,
                input_mode=EvaluationInputMode.END_TO_END_RUNTIME_INPUT,
            ),
            fast_extractor=cached,
        )
        report = evaluate(
            limited,
            outcome.predictions,
            mode=EvaluationMode.FULL,
            policy={"policy_code": SemanticMatchPolicy().policy_code},
            config=load_evaluation_config(),
            latency_values=outcome.latencies_ms,
            concurrency=outcome.concurrency.to_dict(),
            gate_profile="provisional",
        )
        metrics = dict(report.metrics)
    finally:
        await dispose_fixture_catalog(handle)
        await cached.aclose()

    return {
        "case_count": len(hr),
        "status_accuracy": _metric(metrics, "status_accuracy"),
        "top_1": _metric(metrics, "top_1_accepted_accuracy"),
        "top_2": _metric(metrics, "top_2_accepted_recall"),
        "required": _metric(metrics, "required_candidate_recall"),
        "forbidden": int(metrics.get("forbidden_candidate_violation_count") or 0),
        "unsafe": int(metrics.get("unsafe_auto_select_count") or 0),
        "quality_gate": report.quality_gate,
        "note": "E2E uses cached hybrid constraints (no second remote FAST call)",
    }


def quality_pass(hybrid: dict, corr: dict, e2e: dict, model: dict) -> tuple[bool, list[str]]:
    violations = []
    schema_ok = (model.get("invalid_schema_count") or 0) == 0 and (
        model.get("truncated_count") or 0
    ) == 0 and (model.get("timeout_count") or 0) == 0
    if not schema_ok:
        violations.append(
            f"schema_validity not 100%: invalid={model.get('invalid_schema_count')} "
            f"trunc={model.get('truncated_count')} timeout={model.get('timeout_count')}"
        )
    pos = hybrid.get("positive_constraint_recall")
    neg = hybrid.get("negative_constraint_recall")
    if pos is None or pos < 0.90:
        violations.append(f"positive_recall={pos} < 0.90")
    if neg is None or neg < 0.95:
        violations.append(f"negative_recall={neg} < 0.95")
    corr_r = (corr.get("metrics") or {}).get("correction_recall")
    if corr_r is None or corr_r < 0.90:
        violations.append(f"hybrid_correction_recall={corr_r} < 0.90")
    if (model.get("forbidden_identifier_generation_count") or 0) != 0:
        violations.append("forbidden_identifier != 0")
    if (e2e.get("unsafe") or 0) != 0:
        violations.append(f"unsafe_auto_select={e2e.get('unsafe')}")
    if (e2e.get("forbidden") or 0) != 0:
        violations.append(f"e2e_forbidden={e2e.get('forbidden')}")
    return (len(violations) == 0), violations


def lock_candidate_as_provisional_primary(key: str) -> dict[str, str]:
    """Point runtime env + DB FAST primary at the chosen candidate."""

    alias = DEFAULT_ALIASES[key]
    url = DEFAULT_URLS[key]
    env_path = ROOT / ".env.runtime"
    if not env_path.exists():
        return {"status": "skipped", "reason": ".env.runtime missing"}
    text = env_path.read_text().splitlines()
    repl = {
        "FAST_PROVIDER_BASE_URL": url,
        "FAST_MODEL_REFERENCE": alias,
        "FAST_RUNTIME_ALIAS": alias,
        "FAST_A_BASE_URL": DEFAULT_URLS["A"],
        "FAST_B_BASE_URL": DEFAULT_URLS["B"],
        "FAST_PROVISIONAL_PRIMARY": key,
    }
    out = []
    seen = set()
    for line in text:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0]
            if k in repl:
                out.append(f"{k}={repl[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in repl.items():
        if k not in seen:
            out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n")

    db = os.environ.get("DATABASE_URL")
    if db:
        sql = f"""
UPDATE ai_model_profiles
SET model_reference = '{alias}', updated_at = NOW()
WHERE profile_code = 'FAST_UNDERSTANDING';
UPDATE ai_provider_connections
SET base_url = '{url}', updated_at = NOW()
WHERE connection_code = 'POC_FAST_RUNTIME';
UPDATE ai_model_deployments
SET runtime_alias = '{alias}', updated_at = NOW()
WHERE deployment_code = 'POC_FAST_RUNTIME_PRIMARY';
"""
        subprocess.run(["psql", db, "-v", "ON_ERROR_STOP=1", "-c", sql], check=False)
    return {"status": "locked", "primary": key, "alias": alias}


async def main_async(args: argparse.Namespace) -> int:
    key = args.candidate.upper()
    if key not in {"A", "B", "C"}:
        raise SystemExit("candidate must be A, B, or C")
    slug = key.lower()
    deployment_id = f"FAST_{key}_REAL"

    timeout_ms = int(os.environ.get("FAST_TIMEOUT_MS") or args.timeout_ms)
    max_tokens = int(os.environ.get("FAST_MAX_OUTPUT_TOKENS") or args.max_tokens)
    _isolate_candidate(key)
    try:
        print(f"=== HR100 hybrid quality + warm latency ({key}) ===", flush=True)
        result = await run_hr100(
            candidate_key=key,
            timeout_ms=timeout_ms,
            max_tokens=max_tokens,
        )
        print("=== E2E cached hybrid ===", flush=True)
        e2e = await run_e2e_cached(result["hybrid_by_case"])
    except Exception:
        _restore_all()
        raise

    hybrid = result["hybrid_metrics"]
    model = result["model_metrics"]
    corr = result["correction_pool_hybrid"]
    ok, violations = quality_pass(hybrid, corr, e2e, model)
    lat = result["latency"]["latency"]
    perf_ok = (lat.get("p95_ms") or 1e9) < 3000

    if ok and args.lock_on_pass:
        lock = lock_candidate_as_provisional_primary(key)
    else:
        lock = {
            "status": "not_locked",
            "reason": "quality_gate_failed" if not ok else "lock_on_pass_disabled",
        }

    hardware = {
        "platform": _sh("uname -srm"),
        "note": f"{key} isolated; sibling FAST stopped during measurement",
    }

    quality_payload = report_envelope(
        report_id=f"adr009-fast-{slug}-hr100-hybrid-quality",
        environment="nanobase-runtime",
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        deployment_id=deployment_id,
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "deployment": result["deployment"],
            "model_metrics": model,
            "hybrid_metrics": hybrid,
            "correction_pool_hybrid": {
                k: corr[k] for k in corr if k != "metrics"
            }
            | {"metrics": corr.get("metrics")},
            "forbidden_identifier_count": result["forbidden_identifier_count"],
            "detail_safe": result["detail_safe"],
            "quality_ok": ok,
            "violations": violations,
        },
    )
    write_runtime_report(f"adr009-fast-{slug}-hr100-hybrid-quality.json", quality_payload)

    latency_payload = report_envelope(
        report_id=f"adr009-fast-{slug}-hr100-warm-latency",
        environment="nanobase-runtime",
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        deployment_id=deployment_id,
        dataset_version="validation.v4",
        extra={
            "prompt_version": PROMPT_VERSION,
            "latency": result["latency"],
            "performance_ok": perf_ok,
            "note": "Concurrency 4 not run: P95 expected >> 3s on CPU",
        },
    )
    write_runtime_report(f"adr009-fast-{slug}-hr100-warm-latency.json", latency_payload)

    e2e_payload = report_envelope(
        report_id=f"adr009-fast-{slug}-hr100-hybrid-e2e",
        environment="nanobase-runtime",
        hardware=hardware,
        deployment_id=deployment_id,
        dataset_version="validation.v4",
        extra={"e2e": e2e},
    )
    write_runtime_report(f"adr009-fast-{slug}-hr100-hybrid-e2e.json", e2e_payload)

    runtime_gate = (
        "RUNTIME_READY"
        if ok and perf_ok
        else ("RUNTIME_PERFORMANCE_REJECT" if ok else "RUNTIME_QUALITY_REJECT")
    )
    gate = report_envelope(
        report_id=f"adr009-fast-{slug}-hr100-gate",
        environment="nanobase-runtime",
        hardware=hardware,
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "quality_ok": ok,
            "performance_ok": perf_ok,
            "violations": violations,
            "runtime_gate": runtime_gate,
            "provisional_primary": lock,
            "campaign_gate": "CLOSED",
            "campaign_note": "CLOSED until warm P95 < 3000ms (GPU or smaller model)",
            "summary": {
                "schema_invalid": model.get("invalid_schema_count"),
                "hybrid_pos_recall": hybrid.get("positive_constraint_recall"),
                "hybrid_neg_recall": hybrid.get("negative_constraint_recall"),
                "hybrid_corr_recall": (corr.get("metrics") or {}).get("correction_recall"),
                "forbidden": result["forbidden_identifier_count"],
                "e2e_unsafe": e2e.get("unsafe"),
                "warm_p50_ms": lat.get("p50_ms"),
                "warm_p95_ms": lat.get("p95_ms"),
                "warm_p99_ms": lat.get("p99_ms"),
            },
        },
    )
    write_runtime_report(f"adr009-fast-{slug}-hr100-gate.json", gate)

    print(
        json.dumps(
            {
                "candidate": key,
                "quality_ok": ok,
                "performance_ok": perf_ok,
                "runtime_gate": runtime_gate,
                "campaign_gate": "CLOSED",
                "lock": lock,
                "violations": violations,
                "summary": gate["summary"],
            },
            indent=2,
        )
    )
    _restore_all()
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", choices=["A", "B", "C", "a", "b", "c"], default="B")
    p.add_argument("--timeout-ms", type=int, default=60000)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument(
        "--lock-on-pass",
        action="store_true",
        help="Only lock provisional primary if quality gate passes",
    )
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
