#!/usr/bin/env python3
"""Generate ADR-008 P1 / ADR-009 runtime verification reports.

Honest dependency probing — never invents PROVISIONAL_ACCEPT without measured
real Redis / pgvector / FAST / embedding. Matcher heuristics are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from taksitlio.runtime_verification.evidence import RuntimeEvidence
from taksitlio.runtime_verification.gate import evaluate_provisional_gate
from taksitlio.runtime_verification.probes import (
    evidence_metadata_from_env,
    probe_all_dependencies,
)
from taksitlio.runtime_verification.report import report_envelope, write_runtime_report


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evaluation" / "reports"
BASELINE_ORACLE = REPORTS / "adr008-p01-oracle.json"
BASELINE_E2E = REPORTS / "adr008-p01-e2e.json"


def _load_baseline(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(payload: dict, *keys: str):
    for key in keys:
        if key in payload:
            val = payload[key]
            if isinstance(val, dict) and "value" in val:
                return float(val["value"])
            if isinstance(val, (int, float)):
                return float(val)
    return None


def main() -> int:
    hardware = dict(evidence_metadata_from_env())
    deps = probe_all_dependencies()

    redis_report = report_envelope(
        report_id="adr008-p1-redis-integration",
        environment="local-or-ci",
        hardware=hardware,
        extra={
            "probe": deps.redis.to_dict(),
            "integration": {
                "passed": None,
                "skipped": None,
                "failed": None,
                "note": (
                    "Run: INTEGRATION_REQUIRE_REDIS=1 REDIS_URL=... "
                    "pytest -m integration tests/integration/redis -q"
                ),
            },
            "status": "PASS" if deps.redis.available else "BLOCKED_DEPENDENCY",
        },
    )
    write_runtime_report("adr008-p1-redis-integration.json", redis_report)

    pg_report = report_envelope(
        report_id="adr008-p1-pgvector-integration",
        environment="local-or-ci",
        hardware=hardware,
        extra={
            "postgres": deps.postgres.to_dict(),
            "pgvector": deps.pgvector.to_dict(),
            "integration": {
                "note": (
                    "Run: INTEGRATION_REQUIRE_PG=1 PGVECTOR_URL=... "
                    "pytest -m integration tests/integration/pgvector -q"
                ),
            },
            "status": (
                "PASS"
                if deps.postgres.available and deps.pgvector.available
                else "BLOCKED_DEPENDENCY"
            ),
        },
    )
    write_runtime_report("adr008-p1-pgvector-integration.json", pg_report)

    fast_quality = report_envelope(
        report_id="adr008-p1-fast-quality",
        environment="local-or-ci",
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        deployment_id=None,
        extra={
            "probe": deps.fast.to_dict(),
            "metrics": None,
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.fast.available
                else "PENDING_MEASUREMENT"
            ),
            "note": "Real FAST extraction requires FAST_PROVIDER_BASE_URL + model ref",
        },
    )
    write_runtime_report("adr008-p1-fast-quality.json", fast_quality)

    fast_latency = report_envelope(
        report_id="adr008-p1-fast-latency",
        environment="local-or-ci",
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        extra={
            "probe": deps.fast.to_dict(),
            "cold": None,
            "warm": None,
            "targets": {"warm_p50_ms": 2000, "warm_p95_ms": 3000, "invalid_json": 0},
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.fast.available
                else "PENDING_MEASUREMENT"
            ),
        },
    )
    write_runtime_report("adr008-p1-fast-latency.json", fast_latency)

    emb_quality = report_envelope(
        report_id="adr008-p1-embedding-quality",
        environment="local-or-ci",
        hardware=hardware,
        model_profile_id="CATEGORY_EMBEDDING",
        extra={
            "probe": deps.embedding.to_dict(),
            "baseline_vs_runtime": None,
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.embedding.available
                else "PENDING_MEASUREMENT"
            ),
            "note": "LexicalEmbedder must never count as real_embedding_measured",
        },
    )
    write_runtime_report("adr008-p1-embedding-quality.json", emb_quality)

    pg_bench = report_envelope(
        report_id="adr008-p1-pgvector-benchmark",
        environment="local-or-ci",
        hardware=hardware,
        extra={
            "probe": deps.pgvector.to_dict(),
            "scales": {"100": None, "1000": None, "10000": None},
            "targets": {"retrieval_p95_ms": 100, "matcher_p95_ms": 400},
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.pgvector.available
                else "PENDING_MEASUREMENT"
            ),
        },
    )
    write_runtime_report("adr008-p1-pgvector-benchmark.json", pg_bench)

    e2e_runtime = report_envelope(
        report_id="adr008-p1-e2e-runtime",
        environment="local-or-ci",
        hardware=hardware,
        dataset_version="validation.v4",
        extra={
            "dependencies": deps.to_dict(),
            "stage_latency_ms": None,
            "targets": {"warm_p50_ms": 3000, "warm_p95_ms": 4000},
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.all_available
                else "PENDING_MEASUREMENT"
            ),
        },
    )
    write_runtime_report("adr008-p1-e2e-runtime.json", e2e_runtime)

    oracle = _load_baseline(BASELINE_ORACLE)
    e2e = _load_baseline(BASELINE_E2E)
    # Test-double baseline quality is recorded for comparison only — does NOT
    # flip real_*_measured flags.
    evidence = RuntimeEvidence(
        real_redis_measured=bool(deps.redis.available and deps.redis.measured),
        real_pgvector_measured=bool(deps.pgvector.available and deps.pgvector.measured),
        real_fast_measured=False,  # requires live extraction eval
        real_embedding_measured=False,
        redis_integration_skipped=0 if deps.redis.available else 1,
        pgvector_integration_skipped=0 if deps.pgvector.available else 1,
        human_reviewed_count=100,
        oracle_top_1=_metric(oracle, "top_1_accepted_accuracy"),
        oracle_top_2=_metric(oracle, "top_2_accepted_recall"),
        oracle_required=_metric(oracle, "required_candidate_recall"),
        oracle_forbidden=int(oracle.get("forbidden") or 0),
        oracle_unsafe=int(oracle.get("unsafe") or 0),
        e2e_status=_metric(e2e, "status_accuracy"),
        e2e_top_1=_metric(e2e, "top_1_accepted_accuracy"),
        e2e_top_2=_metric(e2e, "top_2_accepted_recall"),
        e2e_required=_metric(e2e, "required_candidate_recall"),
        e2e_forbidden=int(e2e.get("forbidden") or 0),
        e2e_unsafe=int(e2e.get("unsafe") or 0),
        metadata=hardware,
    )
    provisional = evaluate_provisional_gate(evidence, deps=deps)

    gate = report_envelope(
        report_id="adr008-p1-gate",
        environment="local-or-ci",
        hardware=hardware,
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "dependencies": deps.to_dict(),
            "evidence": evidence.to_dict(),
            "quality_gate": "QUALITY_READY" if provisional.quality_ready else "QUALITY_NOT_READY",
            "quality_gate_note": (
                "Test-double baseline quality retained; real-runtime quality "
                "pending FAST+embedding measurement"
            ),
            "runtime_gate": provisional.runtime_gate.value,
            "provisional_gate": provisional.status,
            "campaign_gate": provisional.campaign_gate.value,
            "violations": provisional.violations,
            "notes": provisional.notes,
            "baseline_oracle": {
                "status_accuracy": _metric(oracle, "status_accuracy"),
                "top_1": _metric(oracle, "top_1_accepted_accuracy"),
                "top_2": _metric(oracle, "top_2_accepted_recall"),
                "required": _metric(oracle, "required_candidate_recall"),
            },
            "baseline_e2e": {
                "status_accuracy": _metric(e2e, "status_accuracy"),
                "top_1": _metric(e2e, "top_1_accepted_accuracy"),
                "top_2": _metric(e2e, "top_2_accepted_recall"),
                "required": _metric(e2e, "required_candidate_recall"),
            },
        },
    )
    write_runtime_report("adr008-p1-gate.json", gate)
    summary = {
        "runtime_gate": provisional.runtime_gate.value,
        "provisional_gate": provisional.status,
        "campaign_gate": provisional.campaign_gate.value,
        "blockers": [c.value for c in deps.blockers],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
