"""Category-match evaluation package (ADR-005).

Public entry points:

    from taksitlio.evaluation import (
        DatasetSplit,
        EvaluationMode,
        FixtureCatalog,
        build_fixture_catalog,
        load_jsonl,
        evaluate,
        run_matcher_on_dataset,
    )

Fixture keys — never production category names — are the only
identifiers the runtime evaluation code understands.
"""

from taksitlio.evaluation.comparison import ComparisonResult, compare_reports
from taksitlio.evaluation.dataset import (
    assert_split_integrity,
    load_jsonl,
    split_from_path,
    write_jsonl,
)
from taksitlio.evaluation.dataset_repository import FilesystemDatasetRepository
from taksitlio.evaluation.domain import (
    AnnotationStatus,
    CandidatePrediction,
    CaseAnnotation,
    CaseDimensions,
    CaseExpected,
    CasePrediction,
    CasePrivacy,
    DatasetSplit,
    EvaluationCase,
    EvaluationDataset,
    EvaluationInputMode,
    EvaluationMode,
    ExpectedStatus,
    QualityGateStatus,
)
from taksitlio.evaluation.errors import (
    BaselineComparisonError,
    DatasetValidationError,
    EvaluationError,
    FixtureCatalogError,
    HoldoutTuningRefused,
    PrivacyViolationError,
    UnknownFixtureKeyError,
)
from taksitlio.evaluation.evaluator import (
    EvaluationReport,
    evaluate,
    load_evaluation_config,
)
from taksitlio.evaluation.fixture_catalog import (
    DEFAULT_FIXTURE_PATH,
    FixtureCatalog,
    all_fixture_keys,
    build_fixture_catalog,
    dispose_fixture_catalog,
)
from taksitlio.evaluation.privacy import (
    PRIVATE_DIR,
    REPORTS_DIR,
    assert_report_is_safe,
    redact_report,
)
from taksitlio.evaluation.reports import load_report, write_debug_log, write_report
from taksitlio.evaluation.runner import RunOutcome, RunnerConfig, run_matcher_on_dataset

__all__ = [
    "AnnotationStatus",
    "BaselineComparisonError",
    "CandidatePrediction",
    "CaseAnnotation",
    "CaseDimensions",
    "CaseExpected",
    "CasePrediction",
    "CasePrivacy",
    "ComparisonResult",
    "DEFAULT_FIXTURE_PATH",
    "DatasetSplit",
    "DatasetValidationError",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationError",
    "EvaluationInputMode",
    "EvaluationMode",
    "EvaluationReport",
    "ExpectedStatus",
    "FilesystemDatasetRepository",
    "FixtureCatalog",
    "FixtureCatalogError",
    "HoldoutTuningRefused",
    "PRIVATE_DIR",
    "PrivacyViolationError",
    "QualityGateStatus",
    "REPORTS_DIR",
    "RunOutcome",
    "RunnerConfig",
    "UnknownFixtureKeyError",
    "all_fixture_keys",
    "assert_report_is_safe",
    "assert_split_integrity",
    "build_fixture_catalog",
    "compare_reports",
    "dispose_fixture_catalog",
    "evaluate",
    "load_evaluation_config",
    "load_jsonl",
    "load_report",
    "redact_report",
    "run_matcher_on_dataset",
    "split_from_path",
    "write_debug_log",
    "write_jsonl",
    "write_report",
]
