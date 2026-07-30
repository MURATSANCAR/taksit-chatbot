"""tune-policy must refuse to run on the holdout split (ADR-005 §6, ADR-006 §K)."""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation import cli
from taksitlio.evaluation.errors import HoldoutTuningRefused


REPO_ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_PATH = (
    REPO_ROOT
    / "evaluation"
    / "datasets"
    / "golden"
    / "tr-category-holdout.v1.jsonl"
)


def test_tune_policy_refuses_holdout() -> None:
    if not HOLDOUT_PATH.exists():
        pytest.skip("holdout dataset not present in this checkout")

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "tune-policy",
            "--dataset",
            str(HOLDOUT_PATH),
            "--grid-steps",
            "2",
        ]
    )
    with pytest.raises(HoldoutTuningRefused):
        cli.cmd_tune_policy(args)


def test_audit_hook_is_callable_and_returns_none() -> None:
    """Default audit hook is a no-op — but it exists as a promotion gate."""

    assert cli._default_audit_hook({"event": "test"}) is None
