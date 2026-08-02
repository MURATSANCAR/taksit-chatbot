"""Evidence provenance helpers for verification sprint metrics.

Forbidden source types: HARDCODED_VALUE, SCRIPT_CONSTANT, ESTIMATED_WITHOUT_SOURCE.
Every report metric must carry source metadata.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

ALLOWED_SOURCE_TYPES = frozenset(
    {
        "DATABASE_QUERY",
        "HTTP_TEST_RESULT",
        "BROWSER_TEST_RESULT",
        "SSE_TRACE",
        "MANUAL_REVIEW",
        "POLICY_EVALUATION",
    }
)

FORBIDDEN_SOURCE_TYPES = frozenset(
    {
        "HARDCODED_VALUE",
        "SCRIPT_CONSTANT",
        "ESTIMATED_WITHOUT_SOURCE",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def query_hash(sql: str, params: Optional[Mapping[str, Any]] = None) -> str:
    payload = json.dumps(
        {"sql": " ".join((sql or "").split()), "params": params or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def evidence_metric(
    *,
    metric_name: str,
    metric_value: Any,
    source_type: str,
    source_table_or_endpoint: Optional[str] = None,
    source_query_hash: Optional[str] = None,
    catalog_revision: Optional[str] = None,
    cohort_id: Optional[int] = None,
    cohort_version: Optional[int] = None,
    measured_at: Optional[str] = None,
) -> dict[str, Any]:
    st = str(source_type or "").upper()
    if st in FORBIDDEN_SOURCE_TYPES:
        raise ValueError(f"forbidden evidence source_type={st}")
    if st not in ALLOWED_SOURCE_TYPES:
        raise ValueError(f"unsupported evidence source_type={st}")
    return {
        "metric_name": metric_name,
        "metric_value": metric_value,
        "source_type": st,
        "source_table_or_endpoint": source_table_or_endpoint,
        "source_query_hash": source_query_hash,
        "catalog_revision": catalog_revision,
        "cohort_id": cohort_id,
        "cohort_version": cohort_version,
        "measured_at": measured_at or _now().isoformat(),
    }


def evaluate_provenance_gate(
    metrics: list[dict[str, Any]],
    *,
    db_counts: Optional[Mapping[str, int]] = None,
    artifact_counts: Optional[Mapping[str, int]] = None,
    report_counts: Optional[Mapping[str, int]] = None,
) -> dict[str, Any]:
    """EVIDENCE_PROVENANCE_GATE: reject hardcoded / untraceable / mismatched metrics."""

    failures: list[str] = []
    hardcoded = 0
    untraceable = 0
    mismatches: list[dict[str, Any]] = []

    for m in metrics:
        st = str((m or {}).get("source_type") or "")
        if st in FORBIDDEN_SOURCE_TYPES:
            hardcoded += 1
            failures.append(f"hardcoded:{m.get('metric_name')}")
            continue
        if st not in ALLOWED_SOURCE_TYPES:
            untraceable += 1
            failures.append(f"bad_source:{m.get('metric_name')}:{st}")
            continue
        if not (m.get("source_table_or_endpoint") or m.get("source_query_hash")):
            # MANUAL_REVIEW / POLICY_EVALUATION may omit SQL hash but need endpoint/table label
            if st not in {"MANUAL_REVIEW", "POLICY_EVALUATION"}:
                untraceable += 1
                failures.append(f"untraceable:{m.get('metric_name')}")

    db_counts = dict(db_counts or {})
    artifact_counts = dict(artifact_counts or {})
    report_counts = dict(report_counts or {})
    for key in sorted(set(db_counts) | set(artifact_counts) | set(report_counts)):
        db_v = db_counts.get(key)
        art_v = artifact_counts.get(key)
        rep_v = report_counts.get(key)
        if db_v is not None and art_v is not None and int(db_v) != int(art_v):
            mismatches.append({"key": key, "db": db_v, "artifact": art_v})
            failures.append(f"db_artifact_mismatch:{key}")
        if art_v is not None and rep_v is not None and int(art_v) != int(rep_v):
            mismatches.append({"key": key, "artifact": art_v, "report": rep_v})
            failures.append(f"artifact_report_mismatch:{key}")

    passed = hardcoded == 0 and untraceable == 0 and not mismatches
    return {
        "gate": "EVIDENCE_PROVENANCE_GATE",
        "status": "PASS" if passed else "FAIL",
        "pass": passed,
        "hardcoded_evidence_metric": hardcoded,
        "untraceable_report_metric": untraceable,
        "db_artifact_mismatch": len(
            [m for m in mismatches if "db" in m and "artifact" in m]
        ),
        "mismatches": mismatches,
        "failures": failures,
        "metrics_checked": len(metrics),
    }


async def persist_metrics(
    conn: Any,
    *,
    sprint_code: str,
    metrics: list[dict[str, Any]],
) -> int:
    """Best-effort insert into verification_evidence_metrics when table exists."""

    exists = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.tables
          WHERE table_name='verification_evidence_metrics'
        )
        """
    )
    if not exists:
        return 0
    n = 0
    for m in metrics:
        await conn.execute(
            """
            INSERT INTO verification_evidence_metrics (
              sprint_code, metric_name, metric_value, source_type,
              source_table_or_endpoint, source_query_hash,
              catalog_revision, cohort_id, cohort_version, measured_at
            ) VALUES (
              $1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,COALESCE($10::timestamptz, NOW())
            )
            """,
            sprint_code,
            m["metric_name"],
            json.dumps(m.get("metric_value"), default=str),
            m["source_type"],
            m.get("source_table_or_endpoint"),
            m.get("source_query_hash"),
            m.get("catalog_revision"),
            m.get("cohort_id"),
            m.get("cohort_version"),
            m.get("measured_at"),
        )
        n += 1
    return n


__all__ = [
    "ALLOWED_SOURCE_TYPES",
    "FORBIDDEN_SOURCE_TYPES",
    "evidence_metric",
    "evaluate_provenance_gate",
    "persist_metrics",
    "query_hash",
]
