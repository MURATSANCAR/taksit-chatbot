"""P17 — NeedProfile SFT export + LoRA stub."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from taksitlio.training.export_sft import (
    iter_golden_sft_rows,
    iter_hr_validation_sft_rows,
    need_profile_from_golden_expected,
    need_profile_from_hr_constraints,
)
from taksitlio.understanding.fast.schema_utils import validate_need_profile

ROOT = Path(__file__).resolve().parents[3]


def test_golden_expected_builds_valid_need_profile() -> None:
    profile = need_profile_from_golden_expected(
        "Telefon bakıyoruz, 40 bin civarı.",
        {
            "intent": {"type": "PRODUCT_PURCHASE"},
            "budget": {"type": "APPROXIMATE", "value": 40000},
            "category_hint": "MOBILE_PHONE",
        },
    )
    validate_need_profile(profile)
    assert profile["budget"]["value"] == 40000
    assert any("category_hint:MOBILE_PHONE" in p["concept"] for p in profile["preferences"])
    sc = profile["semantic_constraints"]
    assert sc["positive"]
    assert any(c["concept"] == "mobile phone" for c in sc["positive"])


def test_hr_constraints_exclude_fixture_ids() -> None:
    profile = need_profile_from_hr_constraints(
        "telefon istemiyorum, bilgisayar arıyorum",
        {
            "positive": [{"concept": "laptop", "confidence": 0.95}],
            "negative": [{"concept": "telefon", "confidence": 0.99}],
            "corrections": [],
        },
    )
    validate_need_profile(profile)
    blob = json.dumps(profile)
    assert "fixture." not in blob
    assert "laptop" in blob


def test_iter_golden_rows_limited() -> None:
    rows = list(iter_golden_sft_rows(limit=3))
    assert len(rows) == 3
    assert rows[0]["messages"][0]["role"] == "system"
    assert rows[0]["messages"][-1]["role"] == "assistant"
    validate_need_profile(rows[0]["need_profile"])


def test_iter_hr_rows_human_reviewed() -> None:
    rows = list(iter_hr_validation_sft_rows(limit=2))
    assert len(rows) == 2
    assert rows[0]["annotation_status"] == "HUMAN_REVIEWED"
    validate_need_profile(rows[0]["need_profile"])


def test_train_stub_check_config() -> None:
    script = ROOT / "training" / "train_lora_stub.py"
    cfg = ROOT / "training" / "configs" / "lora_fast_need_profile.example.yaml"
    proc = subprocess.run(
        [sys.executable, str(script), "--check-config", "--config", str(cfg)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Campaign Gate CLOSED" in proc.stdout


def test_train_stub_missing_deps_or_not_implemented() -> None:
    script = ROOT / "training" / "train_lora_stub.py"
    cfg = ROOT / "training" / "configs" / "lora_fast_need_profile.example.yaml"
    proc = subprocess.run(
        [sys.executable, str(script), "--config", str(cfg), "--allow-cpu"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    # Either missing ML deps (2) or stub not implemented (4)
    assert proc.returncode in {2, 4}
    assert proc.returncode != 0
