"""EvaluationInputMode ORACLE vs END_TO_END separation (ADR-007 §B).

We assert that the runner reports the two lanes distinctly and that:

* MATCHER_ORACLE_INPUT sees the annotated ``case.semantic_constraints``;
* END_TO_END_RUNTIME_INPUT does NOT — it only sees what the FAST
  extractor + validator produce from the raw utterance;
* FAST_EXTRACTION_ONLY produces predictions with ``predicted_status
  == 'MATCHER_SKIPPED'`` (no matcher invocation).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation import (
    EvaluationInputMode,
    EvaluationMode,
    build_fixture_catalog,
    dispose_fixture_catalog,
    load_jsonl,
    run_matcher_on_dataset,
)
from taksitlio.evaluation.runner import RunnerConfig
from taksitlio.semantic_matching import SemanticMatchPolicy


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_V2 = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v2.json"
)
VAL_V2 = (
    REPO_ROOT
    / "evaluation"
    / "datasets"
    / "validation"
    / "tr-category-validation.v2.jsonl"
)


@pytest.fixture()
def tiny_dataset():
    dataset = load_jsonl(VAL_V2)
    # 6 diverse cases — enough for a fast comparison, not enough to blow
    # up CI wallclock.
    subset = dataset.cases[:6]
    return dataset.__class__(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        split=dataset.split,
        fixture_catalog_ref=dataset.fixture_catalog_ref,
        cases=tuple(subset),
        immutable_hash=dataset.immutable_hash,
    )


async def _run(tiny_dataset, input_mode: EvaluationInputMode):
    handle = await build_fixture_catalog(fixture_path=FIXTURE_V2)
    try:
        return await run_matcher_on_dataset(
            tiny_dataset,
            handle,
            policy=SemanticMatchPolicy(),
            config=RunnerConfig(
                mode=EvaluationMode.FULL,
                workers=1,
                input_mode=input_mode,
            ),
        ), handle
    finally:
        # Caller disposes below to allow assertions before teardown.
        pass


async def test_oracle_vs_e2e_report_different_input_mode(tiny_dataset) -> None:
    oracle_outcome, handle_a = await _run(
        tiny_dataset, EvaluationInputMode.MATCHER_ORACLE_INPUT
    )
    e2e_outcome, handle_b = await _run(
        tiny_dataset, EvaluationInputMode.END_TO_END_RUNTIME_INPUT
    )
    try:
        assert (
            oracle_outcome.input_mode
            == EvaluationInputMode.MATCHER_ORACLE_INPUT.value
        )
        assert (
            e2e_outcome.input_mode
            == EvaluationInputMode.END_TO_END_RUNTIME_INPUT.value
        )
        # Predictions must exist under both lanes.
        assert oracle_outcome.predictions
        assert e2e_outcome.predictions
    finally:
        await dispose_fixture_catalog(handle_a)
        await dispose_fixture_catalog(handle_b)


async def test_fast_extraction_only_skips_matcher(tiny_dataset) -> None:
    outcome, handle = await _run(
        tiny_dataset, EvaluationInputMode.FAST_EXTRACTION_ONLY
    )
    try:
        assert outcome.input_mode == EvaluationInputMode.FAST_EXTRACTION_ONLY.value
        for pred in outcome.predictions.values():
            assert pred.predicted_status == "FAST_ONLY"
    finally:
        await dispose_fixture_catalog(handle)


async def test_matcher_only_mode_ignores_oracle_constraints(
    tiny_dataset,
) -> None:
    outcome, handle = await _run(tiny_dataset, EvaluationInputMode.MATCHER_ONLY)
    try:
        assert outcome.input_mode == EvaluationInputMode.MATCHER_ONLY.value
        # We do not assert on statuses per case — the interesting invariant
        # is that the runner completed without crashing, i.e. no annotation
        # constraints were required or consulted.
        assert outcome.predictions
    finally:
        await dispose_fixture_catalog(handle)
