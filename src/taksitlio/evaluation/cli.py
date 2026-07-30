"""Command line entrypoints for the evaluation package.

Subcommands (ADR-005 §10):

    validate-dataset          — schema + fixture key + split invariants
    run-category-eval         — run matcher on a dataset, emit report
    benchmark-category-match  — latency-focused run with concurrency setting
    compare-runs              — diff two reports with tolerance config
    tune-policy               — grid search on the validation split only

The CLI never mutates production tables and never auto-promotes
policies; challenger candidates are printed to stdout for the admin
tools to consume.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import Optional, Sequence

from taksitlio.evaluation.comparison import compare_reports
from taksitlio.evaluation.dataset import (
    assert_split_integrity,
    load_jsonl,
    split_from_path,
)
from taksitlio.evaluation.dataset_repository import FilesystemDatasetRepository
from taksitlio.evaluation.domain import DatasetSplit, EvaluationMode
from taksitlio.evaluation.errors import (
    DatasetValidationError,
    EvaluationError,
    HoldoutTuningRefused,
)
from taksitlio.evaluation.evaluator import (
    DEFAULT_CONFIG_PATH,
    EvaluationReport,
    evaluate,
    load_evaluation_config,
)
from taksitlio.evaluation.fixture_catalog import (
    DEFAULT_FIXTURE_PATH,
    build_fixture_catalog,
    dispose_fixture_catalog,
)
from taksitlio.evaluation.privacy import PRIVATE_DIR, REPORTS_DIR
from taksitlio.evaluation.reports import load_report, write_debug_log, write_report
from taksitlio.evaluation.runner import RunnerConfig, run_matcher_on_dataset
from taksitlio.semantic_matching import SemanticMatchPolicy


REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS_ROOT = REPO_ROOT / "evaluation" / "datasets"


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _metric_value(payload) -> float | None:
    """Read the ``value`` from a ProportionMetric-shaped dict, else scalar."""

    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    if payload is None:
        return None
    try:
        return float(payload)
    except (TypeError, ValueError):
        return None


def cmd_validate_dataset(args: argparse.Namespace) -> int:
    path = Path(args.dataset)
    try:
        dataset = load_jsonl(path)
    except DatasetValidationError as exc:
        print(f"INVALID {path}: {exc}", file=sys.stderr)
        for issue in exc.issues[:20]:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    summary = {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "split": dataset.split.value,
        "case_count": len(dataset.cases),
        "immutable_hash": dataset.immutable_hash,
        "fixture_catalog_ref": dataset.fixture_catalog_ref,
    }
    if args.check_split_integrity:
        repo = FilesystemDatasetRepository(DATASETS_ROOT)
        try:
            dev = [c for ds in repo.load_all(DatasetSplit.DEVELOPMENT) for c in ds.cases]
            val = [c for ds in repo.load_all(DatasetSplit.VALIDATION) for c in ds.cases]
            hold = [c for ds in repo.load_all(DatasetSplit.HOLDOUT) for c in ds.cases]
            assert_split_integrity(dev, val, hold)
            summary["split_integrity"] = "ok"
        except DatasetValidationError as exc:
            summary["split_integrity"] = "violated"
            summary["split_integrity_issues"] = exc.issues
            _print_json(summary)
            return 1
    _print_json(summary)
    return 0


def _resolve_policy(policy_json: Optional[str]) -> SemanticMatchPolicy:
    if not policy_json:
        return SemanticMatchPolicy()
    try:
        data = json.loads(policy_json)
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"invalid --policy-json: {exc}") from exc
    from taksitlio.semantic_matching.policy import SemanticMatchPolicyMapper

    return SemanticMatchPolicyMapper.from_storage(data)


async def _run_eval(
    dataset_path: Path,
    *,
    mode: EvaluationMode,
    policy: SemanticMatchPolicy,
    workers: int,
    write_debug: bool,
    config_path: Path,
    fixture_path: Path,
) -> EvaluationReport:
    dataset = load_jsonl(dataset_path)
    handle = await build_fixture_catalog(fixture_path=fixture_path)
    try:
        outcome = await run_matcher_on_dataset(
            dataset,
            handle,
            policy=policy,
            config=RunnerConfig(mode=mode, workers=workers),
        )
    finally:
        # Fixture catalog is fully in-memory but dispose is cheap and
        # matches the production isolation contract.
        pass
    config = load_evaluation_config(config_path)
    report = evaluate(
        dataset,
        outcome.predictions,
        mode=mode,
        policy={"policy_code": policy.policy_code, "policy_version": policy.policy_version},
        config=config,
        latency_values=outcome.latencies_ms,
        concurrency=outcome.concurrency.to_dict(),
    )
    if write_debug:
        debug_path = write_debug_log(
            report.run_id, dataset.cases, outcome.predictions
        )
        report.debug_log_path = str(debug_path.relative_to(REPO_ROOT))
    await dispose_fixture_catalog(handle)
    return report


def cmd_run_category_eval(args: argparse.Namespace) -> int:
    mode = EvaluationMode(args.mode)
    policy = _resolve_policy(args.policy_json)
    report = asyncio.run(
        _run_eval(
            Path(args.dataset),
            mode=mode,
            policy=policy,
            workers=int(args.workers),
            write_debug=bool(args.debug_utterances),
            config_path=Path(args.config or DEFAULT_CONFIG_PATH),
            fixture_path=Path(args.fixture or DEFAULT_FIXTURE_PATH),
        )
    )
    out = write_report(report)
    print(json.dumps(
        {
            "run_id": report.run_id,
            "report_path": str(out.relative_to(REPO_ROOT)),
            "quality_gate": report.quality_gate,
            "metrics": {
                "status_accuracy": _metric_value(report.metrics.get("status_accuracy")),
                "unsafe_auto_select_rate": _metric_value(
                    report.metrics.get("unsafe_auto_select_rate")
                ),
                "required_candidate_recall": _metric_value(
                    report.metrics.get("required_candidate_recall")
                ),
                "top_2_accepted_recall": _metric_value(
                    report.metrics.get("top_2_accepted_recall")
                ),
                "hit_rate_at_3": _metric_value(report.metrics.get("hit_rate_at_3")),
                "brier": report.metrics.get("brier"),
                "ece": report.metrics.get("ece"),
                "forbidden_candidate_violation_count": report.metrics.get(
                    "forbidden_candidate_violation_count"
                ),
            },
            "latency": report.latency,
            "case_count": report.dataset_ref["case_count"],
        },
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Same as run-category-eval but explicit latency-first defaults."""
    args.workers = args.workers or 4
    args.mode = args.mode or EvaluationMode.FULL.value
    return cmd_run_category_eval(args)


def cmd_compare_runs(args: argparse.Namespace) -> int:
    baseline = load_report(Path(args.baseline))
    candidate = load_report(Path(args.candidate))
    tolerances_path = Path(args.tolerances) if args.tolerances else None
    if tolerances_path:
        with tolerances_path.open(encoding="utf-8") as fh:
            tolerances = json.load(fh)
    else:
        tolerances = load_evaluation_config()["comparison_tolerances"]
    result = compare_reports(baseline, candidate, tolerances=tolerances)
    _print_json(result.to_dict())
    return 0 if not result.regressions else 2


def cmd_tune_policy(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    split = split_from_path(dataset_path)
    if split is DatasetSplit.HOLDOUT:
        raise HoldoutTuningRefused(
            "tune-policy refuses to run on the holdout split (ADR-005 §6)"
        )
    if split is DatasetSplit.DEVELOPMENT:
        print(
            "warning: development split — final challenger must be verified on validation",
            file=sys.stderr,
        )
    base_policy = _resolve_policy(args.policy_json)
    grid_min_score = [round(x, 2) for x in _linspace(0.40, 0.75, int(args.grid_steps))]
    grid_gap = [round(x, 2) for x in _linspace(0.05, 0.20, int(args.grid_steps))]
    config_path = Path(args.config or DEFAULT_CONFIG_PATH)
    fixture_path = Path(args.fixture or DEFAULT_FIXTURE_PATH)
    best_score = float("-inf")
    best_report = None
    best_policy = base_policy
    for min_score, gap in product(grid_min_score, grid_gap):
        if gap >= min_score:
            continue
        cand_policy = replace(
            base_policy,
            minimum_candidate_score=min_score,
            minimum_auto_select_score=max(min_score, base_policy.minimum_auto_select_score),
            minimum_auto_select_gap=gap,
        )
        report = asyncio.run(
            _run_eval(
                dataset_path,
                mode=EvaluationMode.FULL,
                policy=cand_policy,
                workers=int(args.workers),
                write_debug=False,
                config_path=config_path,
                fixture_path=fixture_path,
            )
        )
        score = report.quality_gate.get("objective_score", 0.0)
        if score > best_score:
            best_score = score
            best_report = report
            best_policy = cand_policy
    if best_report is None:
        print("no candidate produced (empty grid)", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "candidate_policy": {
                "policy_code": best_policy.policy_code,
                "minimum_candidate_score": best_policy.minimum_candidate_score,
                "minimum_auto_select_score": best_policy.minimum_auto_select_score,
                "minimum_auto_select_gap": best_policy.minimum_auto_select_gap,
                "policy_version": best_policy.policy_version + 1,
            },
            "objective_score": best_score,
            "quality_gate": best_report.quality_gate,
            "notes": [
                "challenger only — NOT promoted to ACTIVE",
                "verify challenger on validation split then run holdout gate",
            ],
        },
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


def _linspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 1:
        return [start]
    step = (stop - start) / (count - 1)
    return [start + i * step for i in range(count)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taksitlio-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate-dataset")
    p_validate.add_argument("--dataset", required=True)
    p_validate.add_argument("--check-split-integrity", action="store_true")
    p_validate.set_defaults(func=cmd_validate_dataset)

    p_run = sub.add_parser("run-category-eval")
    p_run.add_argument("--dataset", required=True)
    p_run.add_argument("--mode", default=EvaluationMode.FULL.value, choices=[m.value for m in EvaluationMode])
    p_run.add_argument("--workers", default=4, type=int)
    p_run.add_argument("--policy-json", default=None)
    p_run.add_argument("--debug-utterances", action="store_true")
    p_run.add_argument("--config", default=None)
    p_run.add_argument("--fixture", default=None)
    p_run.set_defaults(func=cmd_run_category_eval)

    p_bench = sub.add_parser("benchmark-category-match")
    p_bench.add_argument("--dataset", required=True)
    p_bench.add_argument("--mode", default=EvaluationMode.FULL.value, choices=[m.value for m in EvaluationMode])
    p_bench.add_argument("--workers", default=8, type=int)
    p_bench.add_argument("--policy-json", default=None)
    p_bench.add_argument("--debug-utterances", action="store_true")
    p_bench.add_argument("--config", default=None)
    p_bench.add_argument("--fixture", default=None)
    p_bench.set_defaults(func=cmd_benchmark)

    p_compare = sub.add_parser("compare-runs")
    p_compare.add_argument("--baseline", required=True)
    p_compare.add_argument("--candidate", required=True)
    p_compare.add_argument("--tolerances", default=None)
    p_compare.set_defaults(func=cmd_compare_runs)

    p_tune = sub.add_parser("tune-policy")
    p_tune.add_argument("--dataset", required=True)
    p_tune.add_argument("--workers", default=4, type=int)
    p_tune.add_argument("--policy-json", default=None)
    p_tune.add_argument("--grid-steps", default=4, type=int)
    p_tune.add_argument("--config", default=None)
    p_tune.add_argument("--fixture", default=None)
    p_tune.set_defaults(func=cmd_tune_policy)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
