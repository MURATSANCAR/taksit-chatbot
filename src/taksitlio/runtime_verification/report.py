"""Write ADR-008 P1 / ADR-009 runtime verification JSON reports."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from taksitlio.evaluation.privacy import REPORTS_DIR, assert_report_is_safe


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def report_envelope(
    *,
    report_id: str,
    environment: str,
    hardware: Mapping[str, Any],
    model_profile_id: Optional[str] = None,
    deployment_id: Optional[str] = None,
    policy_version: Optional[str] = None,
    catalog_revision: Optional[str | int] = None,
    dataset_version: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "report_id": report_id,
        "git_commit": _git_commit(),
        "environment": environment,
        "hardware": dict(hardware),
        "model_profile_id": model_profile_id,
        "deployment_id": deployment_id,
        "policy_version": policy_version,
        "catalog_revision": catalog_revision,
        "dataset_version": dataset_version,
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(dict(extra))
    return payload


def write_runtime_report(
    filename: str,
    payload: Mapping[str, Any],
    *,
    reports_dir: Path = REPORTS_DIR,
) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    assert_report_is_safe(body)
    out = reports_dir / filename
    with out.open("w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return out
