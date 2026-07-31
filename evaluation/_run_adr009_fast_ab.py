#!/usr/bin/env python3
"""ADR-009 P1.1 — Isolated FAST A/B quality & performance benchmark.

Never routes through ModelRouter / 35B DEEP fallback. Each candidate is called
via RemoteFastExtractor bound to its own base_url + opaque runtime alias.

Usage (on runtime host)::

    set -a && source .env.runtime && set +a
    python evaluation/_run_adr009_fast_ab.py --candidate both --phases all

Optional::

    --limit 20          # HR case limit (default 100)
    --concurrency-requests 100
    --skip-service-toggle
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
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
from taksitlio.evaluation.domain import EvaluationInputMode
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.evaluation.runtime.fast_ab import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    CandidateSpec,
    build_isolated_extractor,
    candidate_specs_from_env,
    default_max_tokens,
    default_timeout_ms,
    draft_correction_cases,
    extract_one,
    human_reviewed_cases,
    latency_summary_from_results,
    run_concurrency_levels,
    run_quality_pass,
    run_regression_pass,
    run_warmup,
    scoring_row,
)
from taksitlio.evaluation.runtime.fast_quality import score_fast_extraction
from taksitlio.runtime_verification.gate import (
    CampaignGateStatus,
    RuntimeGateStatus,
    evaluate_runtime_gate,
)
from taksitlio.runtime_verification.probes import (
    evidence_metadata_from_env,
    probe_all_dependencies,
)
from taksitlio.runtime_verification.report import report_envelope, write_runtime_report
from taksitlio.semantic_matching import SemanticMatchPolicy


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "datasets" / "validation" / "tr-category-validation.v4.jsonl"
FIXTURE = ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v3.json"
REPORTS = ROOT / "evaluation" / "reports"
BASELINE_ORACLE = REPORTS / "adr008-p01-oracle.json"
BASELINE_E2E = REPORTS / "adr008-p01-e2e.json"


def _sh(cmd: str, *, check: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {cmd}\n{out}")
    return out.strip()


def _preflight() -> dict[str, Any]:
    hardware = evidence_metadata_from_env()
    hardware.update(
        {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "python": platform.python_version(),
            "lscpu": _sh("lscpu | egrep 'Model name|Socket|Core|Thread|NUMA node|CPU\\(s\\):'"),
            "memory": _sh("free -h"),
            "numa": _sh("numactl --hardware 2>/dev/null || echo unavailable"),
            "swap": _sh("swapon --show || true"),
        }
    )
    services = {}
    for name in (
        "taksitlio-fast-a",
        "taksitlio-fast-b",
        "nanobase-qwen36-35b-a3b-mtp",
    ):
        services[name] = {
            "active": _sh(f"systemctl is-active {name}.service || true"),
            "main_pid": _sh(
                f"systemctl show {name}.service -p MainPID --value 2>/dev/null || true"
            ),
            "memory_current": _sh(
                f"systemctl show {name}.service -p MemoryCurrent --value 2>/dev/null || true"
            ),
        }
    ports = _sh("ss -lntp | grep -E ':8010|:8021|:8022' || true")
    health = {
        "a": _sh("curl -fsS http://127.0.0.1:8021/health || echo FAIL"),
        "b": _sh("curl -fsS http://127.0.0.1:8022/health || echo FAIL"),
        "deep": _sh("curl -fsS http://127.0.0.1:8010/health || echo FAIL"),
    }
    runtime_args = {
        "a_env": _sh("sudo cat /etc/nanobaseai/taksitlio-fast-a.env 2>/dev/null || true"),
        "b_env": _sh("sudo cat /etc/nanobaseai/taksitlio-fast-b.env 2>/dev/null || true"),
        "note": "MODEL_FILE paths are internal-only; public reports use opaque aliases",
    }
    # Strip model file paths from public env dumps.
    for key in ("a_env", "b_env"):
        lines = []
        for line in runtime_args[key].splitlines():
            if line.startswith("MODEL_FILE="):
                lines.append("MODEL_FILE=<redacted-internal-path>")
            else:
                lines.append(line)
        runtime_args[key] = "\n".join(lines)
    return {
        "hardware": hardware,
        "services": services,
        "ports": ports,
        "health": health,
        "runtime_args": runtime_args,
        "models_a": _sh("curl -fsS http://127.0.0.1:8021/v1/models || true"),
        "models_b": _sh("curl -fsS http://127.0.0.1:8022/v1/models || true"),
    }


def _toggle_sibling(spec: CandidateSpec, *, stop: bool, enabled: bool) -> None:
    if not enabled:
        return
    action = "stop" if stop else "start"
    _sh(f"sudo systemctl {action} {spec.sibling_service}.service", check=False)
    # Ensure candidate itself is up.
    _sh(f"sudo systemctl start {spec.service_name}.service", check=False)
    time.sleep(2)
    for _ in range(30):
        health = _sh(f"curl -fsS {spec.base_url}/health || echo FAIL")
        if "ok" in health.lower() or '"status"' in health:
            return
        time.sleep(2)
    raise RuntimeError(f"candidate {spec.code} health failed after sibling {action}")


def _restore_services(enabled: bool) -> None:
    if not enabled:
        return
    _sh("sudo systemctl start taksitlio-fast-a.service taksitlio-fast-b.service", check=False)


def _load_baselines() -> tuple[dict, dict]:
    oracle = json.loads(BASELINE_ORACLE.read_text()) if BASELINE_ORACLE.exists() else {}
    e2e = json.loads(BASELINE_E2E.read_text()) if BASELINE_E2E.exists() else {}
    return oracle, e2e


def _metric(payload: dict, *keys: str) -> Optional[float]:
    for key in keys:
        if key in payload:
            val = payload[key]
            if isinstance(val, dict) and "value" in val:
                return float(val["value"]) if val["value"] is not None else None
            if isinstance(val, (int, float)):
                return float(val)
    return None


def _process_sample(pid: str) -> dict[str, Any]:
    if not pid or pid == "0":
        return {}
    return {
        "pid": pid,
        "ps": _sh(f"ps -p {pid} -o pid,pcpu,pmem,rss,vsz,nlwp --no-headers || true"),
        "status_vmrss": _sh(
            f"awk '/VmRSS|voluntary_ctxt|nonvoluntary/ {{print}}' /proc/{pid}/status 2>/dev/null || true"
        ),
    }


def _summarise_e2e_metrics(report_metrics: dict[str, Any]) -> dict[str, Any]:
    def _value(key: str):
        v = report_metrics.get(key)
        if isinstance(v, dict) and "value" in v:
            return v.get("value")
        return v

    return {
        "forbidden_candidate_violation_count": int(
            report_metrics.get("forbidden_candidate_violation_count") or 0
        ),
        "unsafe_auto_select_count": int(
            report_metrics.get("unsafe_auto_select_count") or 0
        ),
        "status_accuracy": _value("status_accuracy"),
        "top_1_accepted_accuracy": _value("top_1_accepted_accuracy"),
        "top_2_accepted_recall": _value("top_2_accepted_recall"),
        "required_candidate_recall": _value("required_candidate_recall"),
        "candidate_recall_at_pool": _value("candidate_recall_at_pool"),
    }


async def _run_e2e(spec: CandidateSpec, extractor, *, limit: int) -> dict[str, Any]:
    """E2E with isolated RemoteFastExtractor — never Deterministic / DEEP.

    Fixture catalog still uses the evaluation LexicalFallbackGateway for
    category vectors (fixture-key eval). FAST isolation is the variable under
    test; production pgvector category store is not used for fixture keys.
    """

    dataset = load_jsonl(DATASET)
    hr = human_reviewed_cases(dataset.cases)[:limit]
    limited = replace(dataset, cases=tuple(hr))
    handle = await build_fixture_catalog(fixture_path=FIXTURE)
    try:
        outcome = await run_matcher_on_dataset(
            limited,
            handle,
            policy=SemanticMatchPolicy(),
            config=RunnerConfig(
                mode=EvaluationMode.FULL,
                workers=1,
                embedding_dim=64,
                input_mode=EvaluationInputMode.END_TO_END_RUNTIME_INPUT,
            ),
            fast_extractor=extractor,
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
        metrics = _summarise_e2e_metrics(dict(report.metrics))
        gate = report.quality_gate
    finally:
        await dispose_fixture_catalog(handle)

    return {
        "case_count": len(hr),
        "category_embedding_path": "fixture_lexical_gateway",
        "fast_path": "RemoteFastExtractor_isolated",
        "deep_fallback_used": False,
        "metrics": metrics,
        "quality_gate": gate,
        "note": (
            "E2E isolates real FAST → validator → hybrid matcher on fixture catalog. "
            "Production pgvector category store is not used for fixture-key evaluation."
        ),
    }


async def run_candidate(
    key: str,
    *,
    candidates: dict[str, CandidateSpec],
    limit: int,
    concurrency_requests: int,
    phases: set[str],
    toggle_services: bool,
    timeout_ms: int,
    max_tokens: int,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    spec = candidates[key]
    if not spec.base_url:
        raise RuntimeError(f"candidate {key} base_url empty — set FAST_A_BASE_URL / FAST_B_BASE_URL")
    print(f"=== candidate {key} {spec.code} ===", flush=True)
    _toggle_sibling(spec, stop=True, enabled=toggle_services)

    extractor = build_isolated_extractor(
        spec, timeout_ms=timeout_ms, max_output_tokens=max_tokens
    )
    pid = (
        preflight.get("services", {})
        .get(spec.service_name, {})
        .get("main_pid")
    )
    # Refresh PID after toggle.
    pid = _sh(f"systemctl show {spec.service_name}.service -p MainPID --value") or pid

    out: dict[str, Any] = {
        "candidate": key,
        "deployment_reference": spec.code,
        "runtime_alias": spec.runtime_alias,
        "model_profile_reference": "FAST_UNDERSTANDING",
        "quantization": spec.quantization,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "dataset_version": "validation.v4",
        "policy_version": "V014",
        "timeout_ms": timeout_ms,
        "max_output_tokens": max_tokens,
        "temperature": 0.0,
        "isolation": {
            "deep_fallback_disabled": True,
            "model_router_unused": True,
            "sibling_stopped": toggle_services,
            "sibling_service": spec.sibling_service,
        },
        "process_sample_before": _process_sample(pid),
    }

    try:
        if "warmup" in phases or "all" in phases or "quality" in phases:
            warmup = await run_warmup(extractor, count=5)
            out["warmup"] = {
                "cold_latency_ms": warmup.cold_latency_ms,
                "fifth_warmup_latency_ms": warmup.fifth_warmup_latency_ms,
                "warmups_ms": list(warmup.warmups),
            }

        dataset = load_jsonl(DATASET)
        hr = human_reviewed_cases(dataset.cases)[:limit]
        draft_corr = draft_correction_cases(dataset.cases)

        quality_metrics = None
        quality_results = []
        if "quality" in phases or "latency" in phases or "all" in phases:
            print(f"[{key}] quality/latency on {len(hr)} HR cases...", flush=True)
            quality_metrics, quality_results, detail_safe = await run_quality_pass(
                extractor, hr
            )
            # Correction supplement from DRAFT annotations (flagged; dataset unchanged).
            corr_rows = []
            for case in draft_corr:
                result = await extract_one(extractor, case.utterance, locale=case.locale)
                corr_rows.append(
                    scoring_row(
                        expected_constraints=case.semantic_constraints or {},
                        result=result,
                    )
                )
            corr_metrics = score_fast_extraction(corr_rows).to_dict() if corr_rows else {}
            quality_payload = report_envelope(
                report_id=f"adr009-fast-{key.lower()}-quality",
                environment="nanobase-runtime",
                hardware=preflight["hardware"],
                model_profile_id="FAST_UNDERSTANDING",
                deployment_id=spec.code,
                policy_version="V014",
                dataset_version="validation.v4",
                extra={
                    "runtime_alias": spec.runtime_alias,
                    "quantization": spec.quantization,
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "runtime_arguments": preflight["runtime_args"],
                    "metrics": quality_metrics,
                    "correction_supplement_draft": {
                        "case_count": len(draft_corr),
                        "metrics": corr_metrics,
                        "note": (
                            "HR set has 0 correction annotations; DRAFT correction "
                            "pool scored separately without mutating dataset expectations."
                        ),
                    },
                    "case_detail_safe": detail_safe,
                    "isolation": out["isolation"],
                },
            )
            write_runtime_report(f"adr009-fast-{key.lower()}-quality.json", quality_payload)
            out["quality"] = quality_metrics
            out["correction_supplement_draft"] = corr_metrics

            lat = latency_summary_from_results(quality_results)
            lat_payload = report_envelope(
                report_id=f"adr009-fast-{key.lower()}-latency",
                environment="nanobase-runtime",
                hardware=preflight["hardware"],
                model_profile_id="FAST_UNDERSTANDING",
                deployment_id=spec.code,
                policy_version="V014",
                dataset_version="validation.v4",
                extra={
                    "runtime_alias": spec.runtime_alias,
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "warmup": out.get("warmup"),
                    "concurrency": 1,
                    "latency": lat,
                    "token_analysis": {
                        "max_tokens_configured": max_tokens,
                        "completion_tokens": lat.get("completion_tokens"),
                        "prompt_tokens": lat.get("prompt_tokens"),
                        "questions": {
                            "is_max_tokens_384_necessary": (
                                (lat.get("completion_tokens") or {}).get("share_under_256")
                            ),
                            "note": (
                                "usage.prompt_tokens/completion_tokens from llama.cpp when present; "
                                "TTFT not exposed by non-streaming chat completions."
                            ),
                        },
                    },
                    "targets": {"warm_p50_ms": 2000, "warm_p95_ms": 3000},
                    "isolation": out["isolation"],
                },
            )
            write_runtime_report(f"adr009-fast-{key.lower()}-latency.json", lat_payload)
            out["latency"] = lat

        if "regression" in phases or "all" in phases:
            print(f"[{key}] regression suite...", flush=True)
            reg_metrics, reg_details = await run_regression_pass(extractor)
            # Strip any accidental utterance fields.
            safe_details = [
                {k: v for k, v in d.items() if k != "utterance"} for d in reg_details
            ]
            out["regression"] = {"metrics": reg_metrics, "cases": safe_details}

        if "concurrency" in phases or "all" in phases:
            print(f"[{key}] concurrency ladder...", flush=True)
            utts = [c.utterance for c in hr] or ["tablet bakıyorum"]
            conc_reports, safety = await run_concurrency_levels(
                extractor,
                utts,
                timeout_ms=timeout_ms,
                requests_per_level=concurrency_requests,
            )
            conc_payload = report_envelope(
                report_id=f"adr009-fast-{key.lower()}-concurrency",
                environment="nanobase-runtime",
                hardware=preflight["hardware"],
                model_profile_id="FAST_UNDERSTANDING",
                deployment_id=spec.code,
                dataset_version="validation.v4",
                extra={
                    "runtime_alias": spec.runtime_alias,
                    "levels": conc_reports,
                    "safety_stop": safety,
                    "isolation": out["isolation"],
                    "process_sample": _process_sample(pid),
                },
            )
            write_runtime_report(
                f"adr009-fast-{key.lower()}-concurrency.json", conc_payload
            )
            out["concurrency"] = conc_reports
            out["concurrency_safety_stop"] = safety

        if "e2e" in phases or "all" in phases:
            print(f"[{key}] e2e...", flush=True)
            e2e = await _run_e2e(spec, extractor, limit=limit)
            e2e_payload = report_envelope(
                report_id=f"adr009-fast-{key.lower()}-e2e",
                environment="nanobase-runtime",
                hardware=preflight["hardware"],
                model_profile_id="FAST_UNDERSTANDING",
                deployment_id=spec.code,
                dataset_version="validation.v4",
                extra={
                    "runtime_alias": spec.runtime_alias,
                    "e2e": e2e,
                    "isolation": out["isolation"],
                },
            )
            write_runtime_report(f"adr009-fast-{key.lower()}-e2e.json", e2e_payload)
            out["e2e"] = e2e

        out["process_sample_after"] = _process_sample(pid)
    finally:
        await extractor.aclose()
        _toggle_sibling(spec, stop=False, enabled=toggle_services)

    return out


def _quality_ok(candidate: dict[str, Any]) -> bool:
    q = candidate.get("quality") or {}
    reg = (candidate.get("regression") or {}).get("metrics") or {}
    corr_sup = candidate.get("correction_supplement_draft") or {}
    neg = q.get("negative_constraint_recall")
    # Prefer HR negative recall; correction from regression ∪ draft supplement.
    corr = reg.get("correction_recall")
    if corr is None:
        corr = corr_sup.get("correction_recall")
    return (
        (q.get("invalid_schema_count") or 0) == 0
        and (q.get("forbidden_identifier_generation_count") or 0) == 0
        and neg is not None
        and neg >= 0.95
        and corr is not None
        and corr >= 0.90
    )


def _performance_ok(candidate: dict[str, Any]) -> bool:
    lat = (candidate.get("latency") or {}).get("latency") or {}
    p95 = lat.get("p95_ms")
    return p95 is not None and p95 < 3000


def _summarize_candidate(c: dict[str, Any]) -> dict[str, Any]:
    q = c.get("quality") or {}
    lat = (c.get("latency") or {}).get("latency") or {}
    conc = c.get("concurrency") or []
    by_level = {x.get("concurrency"): x for x in conc if isinstance(x, dict)}
    e2e_metrics = ((c.get("e2e") or {}).get("metrics") or {})
    reg = (c.get("regression") or {}).get("metrics") or {}
    return {
        "deployment": c.get("deployment_reference"),
        "schema_valid_rate": (
            None
            if not q.get("case_count")
            else 1.0 - float(q.get("invalid_schema_rate") or 0.0)
        ),
        "invalid_schema_count": q.get("invalid_schema_count"),
        "negative_recall": q.get("negative_constraint_recall"),
        "positive_recall": q.get("positive_constraint_recall"),
        "correction_recall_hr": q.get("correction_recall"),
        "correction_recall_regression": reg.get("correction_recall"),
        "correction_recall_draft_supplement": (c.get("correction_supplement_draft") or {}).get(
            "correction_recall"
        ),
        "clarification_accuracy": q.get("clarification_accuracy"),
        "no_match_intent_accuracy": q.get("no_match_intent_accuracy"),
        "p50_ms": lat.get("p50_ms"),
        "p95_ms": lat.get("p95_ms"),
        "p99_ms": lat.get("p99_ms"),
        "concurrency_1": by_level.get(1),
        "concurrency_4": by_level.get(4),
        "concurrency_8": by_level.get(8),
        "timeout_rate_c1": (
            (c.get("latency") or {}).get("timeout_count", 0)
            / max((c.get("latency") or {}).get("request_count") or 1, 1)
        ),
        "e2e": {
            "status": _metric(e2e_metrics, "status_accuracy"),
            "top_1": _metric(e2e_metrics, "top_1_accepted_accuracy"),
            "top_2": _metric(e2e_metrics, "top_2_accepted_recall"),
            "required": _metric(e2e_metrics, "required_candidate_recall"),
            "forbidden": e2e_metrics.get("forbidden_candidate_violation_count"),
            "unsafe": e2e_metrics.get("unsafe_auto_select_count"),
        },
        "quality_ok": _quality_ok(c),
        "performance_ok": _performance_ok(c),
    }


def _decide(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a = summaries.get("A") or {}
    b = summaries.get("B") or {}

    def quality_rank(s: dict) -> tuple:
        return (
            1 if s.get("quality_ok") else 0,
            float(s.get("negative_recall") or 0.0),
            float(s.get("correction_recall_regression") or 0.0),
            float(s.get("positive_recall") or 0.0),
        )

    def latency_rank(s: dict) -> float:
        return -float(s.get("p95_ms") or 1e18)

    quality_winner = None
    if a and b:
        quality_winner = "A" if quality_rank(a) >= quality_rank(b) else "B"
    elif a:
        quality_winner = "A"
    elif b:
        quality_winner = "B"

    latency_winner = None
    if a.get("p95_ms") is not None and b.get("p95_ms") is not None:
        latency_winner = "A" if a["p95_ms"] <= b["p95_ms"] else "B"

    # Selection rule: safety/quality first, then latency.
    recommended_primary = None
    reason = []
    for key in ("A", "B"):
        s = summaries.get(key) or {}
        if s.get("quality_ok") and s.get("performance_ok"):
            recommended_primary = key
            reason.append(f"{key} meets quality+performance floors")
            break
    if recommended_primary is None:
        for key in ("A", "B"):
            s = summaries.get(key) or {}
            if s.get("quality_ok"):
                recommended_primary = key
                reason.append(
                    f"{key} meets quality but not performance → RUNTIME_PERFORMANCE_REJECT"
                )
                break
    if recommended_primary is None:
        reason.append("neither candidate meets quality floors")
        recommended_primary = quality_winner

    challenger = "B" if recommended_primary == "A" else "A"
    return {
        "quality_winner": quality_winner,
        "latency_winner": latency_winner,
        "recommended_primary": recommended_primary,
        "recommended_challenger": challenger if recommended_primary else None,
        "reason": "; ".join(reason),
    }


async def main_async(args: argparse.Namespace) -> int:
    phases = set(args.phases.split(","))
    if "all" in phases:
        phases = {"all"}
    toggle = not args.skip_service_toggle
    timeout_ms = args.timeout_ms or default_timeout_ms()
    max_tokens = args.max_tokens or default_max_tokens()

    print("=== preflight ===", flush=True)
    preflight = _preflight()
    deps = probe_all_dependencies()
    write_runtime_report(
        "adr009-fast-ab-preflight.json",
        report_envelope(
            report_id="adr009-fast-ab-preflight",
            environment="nanobase-runtime",
            hardware=preflight["hardware"],
            extra={"preflight": preflight, "dependencies": deps.to_dict()},
        ),
    )

    keys = ["A", "B"] if args.candidate == "both" else [args.candidate.upper()]
    candidates = candidate_specs_from_env()
    # Default local opaque endpoints when env not fully populated (runner-only).
    defaults = {
        "A": "http://127.0.0.1:8021",
        "B": "http://127.0.0.1:8022",
    }
    for k, url in defaults.items():
        if not candidates[k].base_url:
            candidates[k] = CandidateSpec(
                code=candidates[k].code,
                role=candidates[k].role,
                base_url=url,
                model_reference=candidates[k].model_reference,
                runtime_alias=candidates[k].runtime_alias,
                service_name=candidates[k].service_name,
                sibling_service=candidates[k].sibling_service,
                quantization=candidates[k].quantization,
            )
    results: dict[str, Any] = {}
    try:
        for key in keys:
            results[key] = await run_candidate(
                key,
                candidates=candidates,
                limit=args.limit,
                concurrency_requests=args.concurrency_requests,
                phases=phases,
                toggle_services=toggle,
                timeout_ms=timeout_ms,
                max_tokens=max_tokens,
                preflight=preflight,
            )
    finally:
        _restore_services(toggle)

    summaries = {k: _summarize_candidate(v) for k, v in results.items()}
    decision = _decide(summaries)

    # Gate evaluation (isolated from 35B).
    any_quality = any(_quality_ok(v) for v in results.values())
    any_perf = any(_performance_ok(v) for v in results.values())
    both_fail_quality = results and all(not _quality_ok(v) for v in results.values())

    if not deps.all_available:
        runtime_gate = RuntimeGateStatus.BLOCKED_DEPENDENCY
    elif both_fail_quality:
        runtime_gate = RuntimeGateStatus.RUNTIME_QUALITY_REJECT
    elif any_quality and not any_perf:
        runtime_gate = RuntimeGateStatus.RUNTIME_PERFORMANCE_REJECT
    elif any_quality and any_perf:
        runtime_gate = RuntimeGateStatus.RUNTIME_READY
    else:
        runtime_gate = evaluate_runtime_gate(deps, quality_ok=False)

    if runtime_gate == RuntimeGateStatus.RUNTIME_READY:
        provisional = "PROVISIONAL_ACCEPT"
        campaign = CampaignGateStatus.READY_TO_OPEN
    else:
        provisional = runtime_gate.value
        campaign = CampaignGateStatus.CLOSED

    comparison = report_envelope(
        report_id="adr009-fast-ab-comparison",
        environment="nanobase-runtime",
        hardware=preflight["hardware"],
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "summaries": summaries,
            "decision": decision,
            "candidates": {
                k: {
                    "deployment_reference": v.get("deployment_reference"),
                    "runtime_alias": v.get("runtime_alias"),
                    "quality": v.get("quality"),
                    "latency": v.get("latency"),
                    "concurrency_safety_stop": v.get("concurrency_safety_stop"),
                    "regression_metrics": (v.get("regression") or {}).get("metrics"),
                    "e2e_note": (v.get("e2e") or {}).get("note"),
                }
                for k, v in results.items()
            },
        },
    )
    write_runtime_report("adr009-fast-ab-comparison.json", comparison)

    gate = report_envelope(
        report_id="adr009-fast-gate",
        environment="nanobase-runtime",
        hardware=preflight["hardware"],
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "quality_gate": "QUALITY_READY",
            "runtime_gate": runtime_gate.value,
            "provisional_gate": provisional,
            "campaign_gate": campaign.value,
            "decision": decision,
            "summaries": summaries,
            "blockers": [b.value for b in deps.blockers],
            "notes": [
                "35B DEEP fallback was not used in this benchmark",
                "Campaign remains CLOSED unless RUNTIME_READY",
            ],
        },
    )
    write_runtime_report("adr009-fast-gate.json", gate)

    print(json.dumps({"decision": decision, "runtime_gate": runtime_gate.value, "campaign": campaign.value}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", choices=["A", "B", "both", "a", "b"], default="both")
    p.add_argument(
        "--phases",
        default="all",
        help="Comma list: warmup,quality,latency,regression,concurrency,e2e,all",
    )
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--concurrency-requests", type=int, default=100)
    p.add_argument("--timeout-ms", type=int, default=0)
    p.add_argument("--max-tokens", type=int, default=0)
    p.add_argument("--skip-service-toggle", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
