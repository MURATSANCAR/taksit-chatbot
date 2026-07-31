#!/usr/bin/env python3
"""Loud-fail LoRA train stub (P17). Never claims success or invents metrics.

Exit codes:
  0 — dry-check only (--check-config)
  2 — missing ML deps (torch/peft/transformers)
  3 — GPU required but unavailable
  4 — config / data missing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        # Minimal fallback: accept empty / comment-only presence check
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise SystemExit("empty config")
        return {"_raw": True, "path": str(path)}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SystemExit("config must be a mapping")
    return data


def _check_ml_deps() -> list[str]:
    missing: list[str] = []
    for mod in ("torch", "transformers", "peft"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def _gpu_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available() or getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FAST LoRA train stub — fails loud without deps/GPU"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("training/configs/lora_fast_need_profile.example.yaml"),
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Only validate config file presence; do not require GPU",
    )
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="Skip GPU requirement (still requires ML deps; training not implemented)",
    )
    args = parser.parse_args(argv)

    if not args.config.is_file():
        print(f"config not found: {args.config}", file=sys.stderr)
        return 4

    cfg = _load_yaml(args.config)
    print(f"loaded config keys: {sorted(k for k in cfg if not str(k).startswith('_'))}")

    if args.check_config:
        print(
            "check-config ok — scaffold only; no training executed; "
            "Campaign Gate CLOSED; no quality claim."
        )
        return 0

    missing = _check_ml_deps()
    if missing:
        print(
            "MISSING_DEPS: "
            + ", ".join(missing)
            + " — install peft/transformers/torch in an ops GPU env. "
            "This stub does not train.",
            file=sys.stderr,
        )
        return 2

    require_gpu = True
    runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    if isinstance(runtime, dict) and runtime.get("require_gpu") is False:
        require_gpu = False
    if require_gpu and not args.allow_cpu and not _gpu_available():
        print(
            "GPU_REQUIRED: CUDA/MPS not available. "
            "Re-run on a GPU host or pass --allow-cpu (still no training here).",
            file=sys.stderr,
        )
        return 3

    print(
        "TRAIN_NOT_IMPLEMENTED: deps present but this repository ships a stub only. "
        "Wire peft Trainer in ops; then re-run evaluation/_run_adr009_fast_ab.py. "
        "Do not treat this exit as QUALITY_READY.",
        file=sys.stderr,
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
