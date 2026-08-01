#!/usr/bin/env python3
"""Real NeedProfile LoRA SFT trainer (CPU/GPU).

Does NOT claim ADR-009 QUALITY_READY / PROVISIONAL_ACCEPT.
Use a small base_model_ref for smoke; 9B on CPU is valid but very slow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # type: ignore

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SystemExit("config must be a mapping")
    return data


def _check_ml_deps() -> list[str]:
    missing: list[str] = []
    for mod in ("torch", "transformers", "peft", "datasets"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def _gpu_available() -> bool:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return True
        mps = getattr(torch.backends, "mps", None)
        return bool(mps and mps.is_available())
    except Exception:  # noqa: BLE001
        return False


def _load_rows(
    path: Path, *, limit: int | None, include_eval_split: bool = False
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped_eval = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not include_eval_split and str(row.get("split") or "train") == "eval":
                skipped_eval += 1
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    if skipped_eval:
        print(f"skipped_eval_split={skipped_eval}", file=sys.stderr)
    if not rows:
        raise SystemExit(f"no training rows in {path}")
    return rows


def _format_example(row: dict[str, Any], tokenizer: Any) -> dict[str, Any]:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError(f"bad messages in row {row.get('id')}")
    if hasattr(tokenizer, "apply_chat_template"):
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
    else:
        parts = []
        for m in messages:
            parts.append(f"{m.get('role')}: {m.get('content')}")
        text = "\n".join(parts)
    return {"text": text}


def train(
    cfg: dict[str, Any],
    *,
    allow_cpu: bool,
    limit: int | None,
    max_steps: int | None,
    include_eval_split: bool = False,
) -> int:
    missing = _check_ml_deps()
    if missing:
        print(f"MISSING_DEPS: {', '.join(missing)}", file=sys.stderr)
        return 2

    import torch  # type: ignore
    from datasets import Dataset  # type: ignore
    from peft import LoraConfig, get_peft_model  # type: ignore
    from transformers import (  # type: ignore
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    require_gpu = True if runtime.get("require_gpu", True) else False
    if require_gpu and not allow_cpu and not _gpu_available():
        print("GPU_REQUIRED: pass --allow-cpu to train on CPU", file=sys.stderr)
        return 3

    base_model = str(cfg.get("base_model_ref") or "").strip()
    if not base_model or base_model == "opaque-base-model-ref":
        print(
            "Set base_model_ref to a HuggingFace model id "
            "(GGUF llama.cpp weights cannot be used by peft).",
            file=sys.stderr,
        )
        return 4

    train_jsonl = Path(str(cfg.get("train_jsonl") or "training/exports/need_profile_sft.jsonl"))
    if not train_jsonl.is_file():
        print(f"train_jsonl missing: {train_jsonl}", file=sys.stderr)
        return 4

    output_dir = Path(str(cfg.get("output_dir") or "training/exports/lora-out"))
    output_dir.mkdir(parents=True, exist_ok=True)

    lora_cfg = cfg.get("lora") if isinstance(cfg.get("lora"), dict) else {}
    train_cfg = cfg.get("train") if isinstance(cfg.get("train"), dict) else {}

    rows = _load_rows(
        train_jsonl, limit=limit, include_eval_split=include_eval_split
    )
    print(f"rows={len(rows)} base_model={base_model} device={'cuda' if torch.cuda.is_available() else 'cpu'}")

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.float32
    if torch.cuda.is_available() and bool(train_cfg.get("bf16")):
        dtype = torch.bfloat16

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        trust_remote_code=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    )
    if not torch.cuda.is_available():
        model = model.to("cpu")

    peft_config = LoraConfig(
        r=int(lora_cfg.get("r") or 16),
        lora_alpha=int(lora_cfg.get("alpha") or 32),
        lora_dropout=float(lora_cfg.get("dropout") or 0.05),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(lora_cfg.get("target_modules") or ["q_proj", "v_proj"]),
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    formatted = [_format_example(r, tokenizer) for r in rows]
    ds = Dataset.from_list(formatted)

    max_seq = int(train_cfg.get("max_seq_len") or 1024)

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=max_seq,
            padding=False,
        )

    tokenized = ds.map(tokenize, batched=True, remove_columns=["text"])

    steps = max_steps
    if steps is None:
        steps = int(train_cfg.get("max_steps") or 0) or None

    args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=float(train_cfg.get("epochs") or 1),
        learning_rate=float(train_cfg.get("learning_rate") or 2e-4),
        per_device_train_batch_size=int(train_cfg.get("per_device_train_batch_size") or 1),
        gradient_accumulation_steps=int(train_cfg.get("gradient_accumulation_steps") or 8),
        logging_steps=int(train_cfg.get("logging_steps") or 5),
        save_steps=int(train_cfg.get("save_steps") or 50),
        save_total_limit=2,
        bf16=bool(train_cfg.get("bf16")) and torch.cuda.is_available(),
        fp16=False,
        report_to=[],
        remove_unused_columns=False,
        max_steps=steps if steps else -1,
        dataloader_num_workers=0,
        use_cpu=not torch.cuda.is_available(),
    )

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized,
        data_collator=collator,
    )
    trainer.train()
    model.save_pretrained(str(output_dir / "adapter"))
    tokenizer.save_pretrained(str(output_dir / "adapter"))
    meta = {
        "base_model_ref": base_model,
        "rows": len(rows),
        "output_dir": str(output_dir / "adapter"),
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "quality_claim": False,
        "note": "LoRA adapter saved — run ADR-009 eval before any quality claim",
    }
    (output_dir / "train_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False))
    print("DONE — no QUALITY_READY claim.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NeedProfile LoRA SFT trainer")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("training/configs/lora_fast_need_profile.cpu.yaml"),
    )
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Cap training rows")
    parser.add_argument("--max-steps", type=int, default=None, help="Override max_steps")
    parser.add_argument(
        "--include-eval-split",
        action="store_true",
        help="Train on rows with split=eval (leaks HR val; off by default)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate config + data only",
    )
    args = parser.parse_args(argv)

    if not args.config.is_file():
        print(f"config not found: {args.config}", file=sys.stderr)
        return 4
    cfg = _load_yaml(args.config)
    train_jsonl = Path(str(cfg.get("train_jsonl") or ""))
    if args.check_config:
        ok_data = train_jsonl.is_file()
        print(
            json.dumps(
                {
                    "config": str(args.config),
                    "keys": sorted(cfg.keys()),
                    "train_jsonl_exists": ok_data,
                    "base_model_ref": cfg.get("base_model_ref"),
                },
                ensure_ascii=False,
            )
        )
        return 0 if ok_data else 4

    return train(
        cfg,
        allow_cpu=args.allow_cpu,
        limit=args.limit,
        max_steps=args.max_steps,
        include_eval_split=args.include_eval_split,
    )


if __name__ == "__main__":
    raise SystemExit(main())
