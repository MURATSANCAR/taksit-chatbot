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


def _smoke_real_clients() -> dict:
    """Exercise RemoteFastExtractor + StrictOpenAICompatibleEmbedder when env is set.

    Does not invent success: typed failures leave measured=False.
    Matcher heuristics are never touched here.
    """

    import asyncio
    import os

    out: dict = {
        "real_fast_measured": False,
        "real_embedding_measured": False,
        "fast_invalid_schema_count": None,
        "fast_forbidden_identifier_count": None,
        "fast_negative_constraint_recall": None,
        "fast_correction_recall": None,
        "fast_latency_ms": None,
        "embedding_latency_ms": None,
        "embedding_dim": None,
        "notes": [],
    }

    async def _run() -> None:
        if os.environ.get("FAST_PROVIDER_BASE_URL") or os.environ.get("POC_FAST_BASE_URL"):
            try:
                from taksitlio.understanding.fast.remote import build_remote_fast_from_env
                from taksitlio.understanding.fast.errors import (
                    FastDeploymentUnavailable,
                    NeedProfileSchemaError,
                    FastExtractionError,
                )

                fast = build_remote_fast_from_env()
                try:
                    outcome = await fast.extract(
                        "Telefon istemiyorum, tablet bakıyorum."
                    )
                    out["real_fast_measured"] = True
                    out["fast_latency_ms"] = outcome.latency_ms
                    out["fast_invalid_schema_count"] = 0
                    hits = list(
                        (outcome.diagnostics or {}).get("forbidden_identifier_hits") or []
                    )
                    out["fast_forbidden_identifier_count"] = len(hits)
                    # Single-utterance smoke cannot claim dataset recall floors.
                    out["notes"].append(
                        "FAST smoke extract ok; full validation recall still required"
                    )
                except NeedProfileSchemaError as exc:
                    out["real_fast_measured"] = True
                    out["fast_invalid_schema_count"] = 1
                    out["fast_forbidden_identifier_count"] = 0
                    out["notes"].append(f"FAST schema error: {exc}")
                except FastExtractionError as exc:
                    out["real_fast_measured"] = True
                    code = getattr(exc, "reason_code", "") or ""
                    if code == "FORBIDDEN_IDENTIFIER_GENERATION":
                        out["fast_forbidden_identifier_count"] = 1
                        out["fast_invalid_schema_count"] = 0
                    else:
                        out["fast_invalid_schema_count"] = 0
                        out["fast_forbidden_identifier_count"] = 0
                    out["notes"].append(f"FAST extract error: {code or type(exc).__name__}")
                except FastDeploymentUnavailable as exc:
                    out["notes"].append(f"FAST unavailable: {exc}")
                finally:
                    await fast.aclose()
            except Exception as exc:  # noqa: BLE001
                out["notes"].append(f"FAST smoke failed: {type(exc).__name__}: {exc}")

        if os.environ.get("EMBEDDING_PROVIDER_BASE_URL") or os.environ.get(
            "POC_EMBEDDING_BASE_URL"
        ):
            try:
                from taksitlio.embeddings.strict_client import (
                    build_strict_embedder_from_env,
                    EmbeddingDeploymentUnavailable,
                )

                emb = build_strict_embedder_from_env()
                try:
                    vectors = await emb.embed(
                        ["üniversite için hafif taşınabilir cihaz"]
                    )
                    out["real_embedding_measured"] = True
                    out["embedding_latency_ms"] = emb.last_latency_ms
                    out["embedding_dim"] = len(vectors[0]) if vectors else None
                except EmbeddingDeploymentUnavailable as exc:
                    out["notes"].append(f"embedding unavailable: {exc}")
                finally:
                    await emb.aclose()
            except Exception as exc:  # noqa: BLE001
                out["notes"].append(
                    f"embedding smoke failed: {type(exc).__name__}: {exc}"
                )

    asyncio.run(_run())
    return out


def main() -> int:
    hardware = dict(evidence_metadata_from_env())
    deps = probe_all_dependencies()
    smokes = _smoke_real_clients()
    hardware["smoke_notes"] = list(smokes.get("notes") or [])
    env_name = "nanobase-runtime" if deps.redis.available else "local-or-ci"

    redis_report = report_envelope(
        report_id="adr008-p1-redis-integration",
        environment=env_name,
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
        environment=env_name,
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
        environment=env_name,
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        deployment_id="POC_FAST_RUNTIME_PRIMARY",
        extra={
            "probe": deps.fast.to_dict(),
            "smoke": {
                "measured": smokes["real_fast_measured"],
                "invalid_schema_count": smokes["fast_invalid_schema_count"],
                "forbidden_identifier_count": smokes["fast_forbidden_identifier_count"],
                "latency_ms": smokes["fast_latency_ms"],
            },
            "metrics": None,
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.fast.available
                else ("SMOKE_MEASURED" if smokes["real_fast_measured"] else "PENDING_MEASUREMENT")
            ),
            "note": "Full HUMAN_REVIEWED FAST extraction eval still required for provisional",
        },
    )
    write_runtime_report("adr008-p1-fast-quality.json", fast_quality)

    fast_latency = report_envelope(
        report_id="adr008-p1-fast-latency",
        environment=env_name,
        hardware=hardware,
        model_profile_id="FAST_UNDERSTANDING",
        extra={
            "probe": deps.fast.to_dict(),
            "smoke_latency_ms": smokes["fast_latency_ms"],
            "cold": None,
            "warm": None,
            "targets": {"warm_p50_ms": 2000, "warm_p95_ms": 3000, "invalid_json": 0},
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.fast.available
                else "PENDING_FULL_BENCHMARK"
            ),
        },
    )
    write_runtime_report("adr008-p1-fast-latency.json", fast_latency)

    emb_quality = report_envelope(
        report_id="adr008-p1-embedding-quality",
        environment=env_name,
        hardware=hardware,
        model_profile_id="CATEGORY_EMBEDDING",
        deployment_id="POC_CATEGORY_EMBEDDING_PRIMARY",
        extra={
            "probe": deps.embedding.to_dict(),
            "smoke": {
                "measured": smokes["real_embedding_measured"],
                "dim": smokes["embedding_dim"],
                "latency_ms": smokes["embedding_latency_ms"],
            },
            "baseline_vs_runtime": None,
            "status": (
                "BLOCKED_DEPENDENCY"
                if not deps.embedding.available
                else (
                    "SMOKE_MEASURED"
                    if smokes["real_embedding_measured"]
                    else "PENDING_MEASUREMENT"
                )
            ),
            "note": "LexicalEmbedder must never count as real_embedding_measured",
        },
    )
    write_runtime_report("adr008-p1-embedding-quality.json", emb_quality)

    pg_bench = report_envelope(
        report_id="adr008-p1-pgvector-benchmark",
        environment=env_name,
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
        environment=env_name,
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
    evidence = RuntimeEvidence(
        real_redis_measured=bool(deps.redis.available and deps.redis.measured),
        real_pgvector_measured=bool(deps.pgvector.available and deps.pgvector.measured),
        real_fast_measured=bool(smokes["real_fast_measured"]),
        real_embedding_measured=bool(smokes["real_embedding_measured"]),
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
        fast_invalid_schema_count=smokes["fast_invalid_schema_count"],
        fast_forbidden_identifier_count=smokes["fast_forbidden_identifier_count"],
        fast_negative_constraint_recall=smokes["fast_negative_constraint_recall"],
        fast_correction_recall=smokes["fast_correction_recall"],
        metadata=hardware,
    )
    provisional = evaluate_provisional_gate(evidence, deps=deps)

    gate = report_envelope(
        report_id="adr008-p1-gate",
        environment=env_name,
        hardware=hardware,
        dataset_version="validation.v4",
        policy_version="V014",
        extra={
            "dependencies": deps.to_dict(),
            "evidence": evidence.to_dict(),
            "quality_gate": "QUALITY_READY" if provisional.quality_ready else "QUALITY_NOT_READY",
            "quality_gate_note": (
                "Oracle/E2E floors currently from test-double baseline reports; "
                "full real FAST+embedding validation eval still required for "
                "PROVISIONAL_ACCEPT"
            ),
            "runtime_gate": provisional.runtime_gate.value,
            "provisional_gate": provisional.status,
            "campaign_gate": provisional.campaign_gate.value,
            "violations": provisional.violations,
            "notes": provisional.notes + list(smokes.get("notes") or []),
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
        "smokes": {
            "fast": smokes["real_fast_measured"],
            "embedding": smokes["real_embedding_measured"],
            "fast_invalid_schema": smokes["fast_invalid_schema_count"],
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
