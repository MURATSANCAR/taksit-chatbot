#!/usr/bin/env python3
"""P17.1 post-process — enrich meta, human-review queue, end validation.

Safe to run after ``p17_v3_residual_export.py`` finishes. Does **not** call the
model. Does **not** change max_tokens / runtime knobs (baseline stays frozen).

Usage (nanobase):
  PYTHONPATH=src python evaluation/p17_v3_residual_postprocess.py \\
    --out-dir artifacts/p17/v3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "P17-V3-RESIDUAL-001"

# Causes that always require human review before v4 planning.
ALWAYS_REVIEW_PRIMARIES = {
    "RUNTIME_TIMEOUT",
    "RUNTIME_TRUNCATED",
    "RUNTIME_NON_JSON",
    "RUNTIME_SCHEMA_FAIL",
    "MODEL_FORBIDDEN_FIELD",
    "MODEL_HALLUCINATED_ENTITY",
    "MODEL_CONFLICT",
    "MATCHER",
}

SUCCESS_SAMPLE_RATE = 0.10


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(cwd: Path) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(cwd), text=True, stderr=subprocess.DEVNULL
        )
        return out.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _dataset_hash(path: Path) -> Optional[str]:
    return _sha256_file(path)


def auto_cause_confidence(row: Mapping[str, Any]) -> str:
    """high | medium | low — drives human-review sampling."""
    primary = row.get("primary_cause")
    secondary = list(row.get("secondary_causes") or [])
    if primary is None:
        return "high"
    if primary in ALWAYS_REVIEW_PRIMARIES:
        return "low"
    if len(secondary) >= 2:
        return "low"
    if primary.startswith("RUNTIME_"):
        return "low"
    if len(secondary) == 1:
        return "medium"
    return "medium"


def needs_human_review(row: Mapping[str, Any], *, rng: random.Random) -> tuple[bool, str]:
    primary = row.get("primary_cause")
    secondary = list(row.get("secondary_causes") or [])
    conf = auto_cause_confidence(row)

    if primary is None:
        if rng.random() < SUCCESS_SAMPLE_RATE:
            return True, "success_random_10pct"
        return False, ""

    if primary in ALWAYS_REVIEW_PRIMARIES or (
        isinstance(primary, str) and primary.startswith("RUNTIME_")
    ):
        return True, f"mandatory:{primary}"
    if len(secondary) >= 1 and primary in {
        "MODEL_FORBIDDEN_FIELD",
        "MODEL_HALLUCINATED_ENTITY",
        "MODEL_CONFLICT",
        "MATCHER",
    }:
        return True, f"mandatory:{primary}+secondary"
    if len(secondary) >= 2:
        return True, "multi_secondary"
    if conf == "low":
        return True, "low_confidence"
    return False, ""


def load_raw(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def recompute_metrics_from_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "p17_v3_residual_export", ROOT / "evaluation" / "p17_v3_residual_export.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_metrics(rows)


def validate_end(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_n: int,
    metrics_path: Path,
    patterns_path: Path,
) -> dict[str, Any]:
    processed = len(rows)
    failed = sum(1 for r in rows if r.get("primary_cause"))
    skipped = sum(1 for r in rows if r.get("skipped"))
    success = sum(1 for r in rows if not r.get("skipped") and not r.get("primary_cause"))
    timeout_count = sum(1 for r in rows if r.get("timeout"))
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    # Accounted rows: success + failed + skipped must equal dataset size.
    accounted = success + failed + skipped
    add(
        "accounted_rows_equal_dataset",
        accounted == expected_n and processed == expected_n,
        {
            "processed": processed,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "accounted": accounted,
            "expected": expected_n,
        },
    )
    add("residual_raw_row_count", processed == expected_n, {"n": processed})
    missing_raw = [
        r.get("utterance_id")
        for r in rows
        if not str(r.get("raw_response") or "").strip() and not r.get("timeout")
    ]
    missing_key = [r.get("utterance_id") for r in rows if "raw_response" not in r]
    add(
        "raw_response_present",
        len(missing_key) == 0,
        {"missing_key": missing_key[:10], "empty_non_timeout": missing_raw[:10]},
    )
    missing_primary = [
        r.get("utterance_id")
        for r in rows
        if not r.get("skipped")
        and not (r.get("flags") or {}).get("model_ok")
        and r.get("primary_cause") is None
        and (
            r.get("timeout")
            or r.get("truncated")
            or r.get("non_json")
            or r.get("status") not in (None, "ok")
            or any(
                (r.get("flags") or {}).get(k)
                for k in (
                    "pos_miss",
                    "neg_miss",
                    "corr_miss",
                    "conflict",
                    "over_extract",
                    "empty",
                    "forbidden",
                )
            )
        )
    ]
    add(
        "failing_rows_have_primary_cause",
        len(missing_primary) == 0,
        {"missing": missing_primary[:20]},
    )

    quant_hits = [
        r.get("utterance_id")
        for r in rows
        if r.get("primary_cause") == "QUANT"
        or "QUANT" in (r.get("secondary_causes") or [])
    ]
    add("no_quant_assigned", len(quant_hits) == 0, {"hits": quant_hits})

    recomputed = recompute_metrics_from_rows(rows)
    stored = {}
    if metrics_path.is_file():
        stored = json.loads(metrics_path.read_text(encoding="utf-8"))
    metric_keys = [
        "pos_recall",
        "pos_precision",
        "neg_recall",
        "neg_precision",
        "corr_recall",
        "schema_validity",
        "forbidden_count",
        "conflict_count",
    ]
    metric_deltas = {}
    metrics_ok = True
    for k in metric_keys:
        a, b = stored.get(k), recomputed.get(k)
        if isinstance(a, float) and isinstance(b, float):
            ok = abs(a - b) < 1e-9
        else:
            ok = a == b
        if not ok:
            metrics_ok = False
            metric_deltas[k] = {"stored": a, "recomputed": b}
    add("metrics_recomputable_from_rows", metrics_ok, metric_deltas)

    patterns = {}
    if patterns_path.is_file():
        patterns = json.loads(patterns_path.read_text(encoding="utf-8"))
    cause_counts = Counter(
        r["primary_cause"] for r in rows if r.get("primary_cause")
    )
    stored_causes = patterns.get("cause_counts") or {}
    cause_ok = dict(cause_counts) == {k: int(v) for k, v in stored_causes.items()}
    add("pattern_cause_counts_consistent", cause_ok, {
        "from_rows": dict(cause_counts),
        "from_patterns_file": stored_causes,
    })

    decision = patterns.get("decision_hint") or {}
    add(
        "model_runtime_matcher_summary_present",
        all(k in decision for k in ("model_weighted", "runtime_weighted", "matcher_weighted", "next_step")),
        decision,
    )
    v4 = patterns.get("v4_target_example_counts") or {}
    add("v4_targets_per_pattern_present", bool(v4), v4)

    return {
        "experiment_id": EXPERIMENT_ID,
        "validated_at": _utc_now(),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "timeout_count": timeout_count,
        "all_ok": all(c["ok"] for c in checks),
        "checks": checks,
        "recomputed_metrics": recomputed,
    }


def enrich_meta(
    *,
    out_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    dataset_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    meta_path = out_dir / "experiment_meta.json"
    if meta_path.is_file():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))

    adapter = Path(existing.get("checkpoint_adapter") or args.adapter)
    if not adapter.is_absolute():
        adapter = ROOT / adapter
    base_gguf = Path(existing.get("base_gguf") or args.base_gguf)
    lora_gguf = Path(existing.get("lora_gguf") or args.lora_gguf)
    if not lora_gguf.is_absolute():
        lora_gguf = ROOT / lora_gguf

    failed = sum(1 for r in rows if r.get("primary_cause"))
    timeout_count = sum(1 for r in rows if r.get("timeout"))
    skipped = sum(1 for r in rows if r.get("skipped"))

    meta = {
        **existing,
        "experiment_id": EXPERIMENT_ID,
        "started_at": existing.get("started_at") or existing.get("created_at"),
        "finished_at": _utc_now(),
        "runner_git_commit": _git_commit(ROOT),
        "runner_command": existing.get("runner_command")
        or (
            "PYTHONPATH=src .venv/bin/python -u evaluation/p17_v3_residual_export.py"
        ),
        "pid": existing.get("pid") or args.pid,
        "dataset_id": dataset_path.name,
        "dataset_path": str(dataset_path),
        "dataset_hash": _dataset_hash(dataset_path),
        "dataset_row_count": len(rows),
        "base_gguf_path": str(base_gguf),
        "base_gguf_hash": _sha256_file(base_gguf),
        "adapter_path": str(adapter),
        "adapter_hash": _sha256_file(adapter / "adapter_model.safetensors")
        or _sha256_file(adapter),
        "lora_gguf_path": str(lora_gguf),
        "lora_gguf_hash": _sha256_file(lora_gguf),
        "prompt_id": existing.get("prompt_version") or existing.get("prompt_id"),
        "schema_id": existing.get("schema_version") or existing.get("schema_id"),
        "server_alias": existing.get("model_alias") or existing.get("server_alias"),
        "server_port": existing.get("eval_port") or existing.get("server_port"),
        "llama_server_flags": existing.get("server_flags") or existing.get("llama_server_flags"),
        "max_tokens": int(existing.get("max_tokens") or 512),
        "temperature": float(existing.get("temperature") if existing.get("temperature") is not None else 0.0),
        "threads": int(existing.get("threads") or 16),
        "warmup_count": int(existing.get("warmup_count") or 3),
        "completed_count": len(rows),
        "failed_count": failed,
        "timeout_count": timeout_count,
        "skipped_count": skipped,
        "previous_failed_start": "var/run missing (ops note only; not an experiment result)",
        "max_tokens_policy": (
            "FROZEN at 512 for P17-V3-RESIDUAL-001 baseline. "
            "96–128 token experiments require a separate experiment_id under runtime gate."
        ),
        "campaign_gate": "CLOSED",
        "quality_claim": False,
        "quant_assignable": False,
    }
    # Enforce freeze
    if int(meta["max_tokens"]) != 512:
        meta["max_tokens_warning"] = (
            f"expected 512 for baseline, found {meta['max_tokens']}"
        )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def write_human_review_queue(
    rows: Sequence[Mapping[str, Any]], out_dir: Path, *, seed: int = 17
) -> dict[str, Any]:
    rng = random.Random(seed)
    queue: list[dict[str, Any]] = []
    for r in rows:
        conf = auto_cause_confidence(r)
        need, reason = needs_human_review(r, rng=rng)
        r["auto_cause_confidence"] = conf
        r["human_review_required"] = need
        r["human_review_reason"] = reason
        if need:
            queue.append(
                {
                    "utterance_id": r.get("utterance_id"),
                    "utterance": r.get("utterance"),
                    "primary_cause": r.get("primary_cause") or "",
                    "secondary_causes": "|".join(r.get("secondary_causes") or []),
                    "auto_cause_confidence": conf,
                    "human_review_reason": reason,
                    "gold_positive": "|".join((r.get("gold") or {}).get("positive") or []),
                    "pred_positive": "|".join(
                        (r.get("parsed_response") or {}).get("positive") or []
                    ),
                    "gold_negative": "|".join((r.get("gold") or {}).get("negative") or []),
                    "pred_negative": "|".join(
                        (r.get("parsed_response") or {}).get("negative") or []
                    ),
                    "review_notes": "",
                    "reviewer_override_cause": "",
                }
            )

    path = out_dir / "human_review_queue.csv"
    fields = [
        "utterance_id",
        "utterance",
        "primary_cause",
        "secondary_causes",
        "auto_cause_confidence",
        "human_review_reason",
        "gold_positive",
        "pred_positive",
        "gold_negative",
        "pred_negative",
        "review_notes",
        "reviewer_override_cause",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in queue:
            w.writerow(row)

    # Rewrite residual_raw with confidence/review flags
    raw_path = out_dir / "residual_raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {
        "human_review_queue_count": len(queue),
        "human_review_queue_path": str(path),
        "success_sample_rate": SUCCESS_SAMPLE_RATE,
        "always_review_primaries": sorted(ALWAYS_REVIEW_PRIMARIES),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="P17.1 residual post-process")
    p.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "p17" / "v3")
    p.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "evaluation" / "datasets" / "development" / "tr-category-dev.v4.jsonl",
    )
    p.add_argument("--expected-n", type=int, default=179)
    p.add_argument("--pid", default=None)
    p.add_argument("--adapter", default="training/exports/lora-out-9b-v3-cpu/adapter")
    p.add_argument(
        "--base-gguf",
        default="/data/nanobaseai/models/taksitlio-fast-c/Qwen_Qwen3.5-9B-Q4_K_M.gguf",
    )
    p.add_argument("--seed", type=int, default=17)
    args = p.parse_args()

    out_dir = args.out_dir
    raw_path = out_dir / "residual_raw.jsonl"
    if not raw_path.is_file():
        raise SystemExit(f"missing {raw_path}; wait for export to finish")

    rows = load_raw(raw_path)
    if len(rows) < args.expected_n:
        raise SystemExit(
            f"incomplete residual_raw.jsonl: {len(rows)}/{args.expected_n} — not finished"
        )

    review_info = write_human_review_queue(rows, out_dir, seed=args.seed)
    meta = enrich_meta(out_dir=out_dir, rows=rows, dataset_path=args.dataset, args=args)
    validation = validate_end(
        rows,
        expected_n=args.expected_n,
        metrics_path=out_dir / "metrics.json",
        patterns_path=out_dir / "failure_patterns.json",
    )
    validation["human_review"] = review_info
    validation["max_tokens_frozen"] = meta.get("max_tokens") == 512
    (out_dir / "p17_1_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "rows": len(rows),
                "validation_all_ok": validation["all_ok"],
                "human_review_queue": review_info["human_review_queue_count"],
                "max_tokens": meta.get("max_tokens"),
                "campaign_gate": "CLOSED",
                "artifacts": {
                    "experiment_meta": str(out_dir / "experiment_meta.json"),
                    "human_review_queue": review_info["human_review_queue_path"],
                    "validation": str(out_dir / "p17_1_validation.json"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
