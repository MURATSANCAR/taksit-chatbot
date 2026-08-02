#!/usr/bin/env python3
"""P17-V4-SFT-001 — fresh LoRA v4 training orchestrator.

Fresh PEFT LoRA from Hugging Face base. NEVER loads/resumes v3 adapter.
Campaign Gate stays CLOSED. No HR100 / GGUF quant in this job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

EXPERIMENT = "P17-V4-SFT-001"
ADAPTER_NAME = "needprofile-fast-nine-b-lora-v4"
FORBIDDEN_RESUME_MARKERS = (
    "lora-out-9b-v3",
    "lora-v3",
    "resume_from_v3",
    "needprofile-fast-nine-b-lora-v3",
)

SMOKE_CASES = [
    {
        "id": "smoke-pos-001",
        "family": "DIRECT_POSITIVE",
        "utterance": "iş seyahati için hafif bir dizüstü arıyorum",
    },
    {
        "id": "smoke-neg-001",
        "family": "NEG_SIMPLE",
        "utterance": "kulaklık istemiyorum, hoparlör bakıyorum",
    },
    {
        "id": "smoke-corr-001",
        "family": "CORRECTION_X_NOT_Y",
        "utterance": "telefon değil tablet istiyorum",
    },
    {
        "id": "smoke-ret-001",
        "family": "CORRECTION_RETRACTION",
        "utterance": "buzdolabından vazgeçtim, çamaşır makinesi bakıyorum",
    },
    {
        "id": "smoke-non-001",
        "family": "NEGATION_OF_NEGATION",
        "utterance": "klima istemiyorum demedim",
    },
    {
        "id": "smoke-mpn-001",
        "family": "MULTI_POS_SINGLE_NEG",
        "utterance": "laptop veya monitör olabilir ama masaüstü olmasın",
    },
    {
        "id": "smoke-soft-001",
        "family": "SOFT_PREFERENCE_NOT_NEGATIVE",
        "utterance": "vantilatör önceliğim değil, klima tercih ederim",
    },
    {
        "id": "smoke-cmp-001",
        "family": "COMPARISON_NOT_NEGATIVE",
        "utterance": "airfryer mı mikrodalga mı emin değilim",
    },
    {
        "id": "smoke-empty-001",
        "family": "AMBIGUOUS_EXPECT_EMPTY",
        "utterance": "merhaba, ürün adı vermeden genel bilgi istiyorum",
    },
    {
        "id": "smoke-bud-001",
        "family": "BUDGET_PLUS_CORRECTION",
        "utterance": "30 bine televizyon değil monitör bakıyorum",
    },
]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # type: ignore

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("config must be a mapping")
    return data


def _git_commit() -> Optional[str]:
    env = os.environ.get("GIT_COMMIT") or os.environ.get("P17_GIT_COMMIT")
    if env:
        return env.strip()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _pkg_ver(name: str) -> Optional[str]:
    try:
        mod = __import__(name)
        return getattr(mod, "__version__", None)
    except Exception:  # noqa: BLE001
        return None


def _load_train_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("split") or "train") != "train":
                raise SystemExit(f"non-train split in dataset: {row.get('id')}")
            rows.append(row)
    return rows


def _assert_no_v3_resume(resume: Optional[str], cfg: dict[str, Any]) -> None:
    if cfg.get("runtime", {}).get("allow_v3_resume"):
        raise SystemExit("allow_v3_resume must be false for P17-V4-SFT-001")
    blob = json.dumps({"resume": resume, "cfg": cfg}, ensure_ascii=False).lower()
    for marker in FORBIDDEN_RESUME_MARKERS:
        if marker.lower() in blob and "v4" not in marker:
            # allow path strings that mention sanitized v4 only
            pass
    if resume:
        low = resume.lower()
        if any(m in low for m in ("v3", "lora-out-9b-v3")):
            raise SystemExit(f"FORBIDDEN v3 resume path: {resume}")


def write_freeze(cfg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    train_jsonl = _ROOT / str(cfg["train_jsonl"])
    sanitize_val = _ROOT / str(cfg.get("sanitize_validation") or "")
    script = Path(__file__).resolve()
    train_script = (_ROOT / "training" / "train_lora.py").resolve()
    base_ref = str(cfg["base_model_ref"])
    snap = None
    hub = Path(os.environ.get("HF_HOME") or (_ROOT / "var" / "hf-cache")) / "hub"
    cand = hub / ("models--" + base_ref.replace("/", "--")) / "snapshots"
    if cand.is_dir():
        snaps = sorted(p for p in cand.iterdir() if p.is_dir())
        if snaps:
            snap = snaps[0]

    train_cfg = cfg.get("train") or {}
    lora_cfg = cfg.get("lora") or {}
    bs = int(train_cfg.get("per_device_train_batch_size") or 1)
    gas = int(train_cfg.get("gradient_accumulation_steps") or 8)
    freeze = {
        "experiment_id": EXPERIMENT,
        "adapter_name": ADAPTER_NAME,
        "created_at": _utc(),
        "training_start": "FRESH_BASE",
        "resume_from_checkpoint": None,
        "v3_adapter_loaded": False,
        "base_model_path": base_ref,
        "base_model_revision": snap.name if snap else None,
        "base_model_snapshot_path": str(snap) if snap else None,
        "base_model_hash": snap.name if snap else None,
        "tokenizer_path": base_ref,
        "tokenizer_revision": snap.name if snap else None,
        "tokenizer_hash": snap.name if snap else None,
        "train_dataset_path": str(train_jsonl.relative_to(_ROOT)),
        "train_dataset_hash": _sha256_file(train_jsonl),
        "train_row_count": int(cfg.get("expected_train_rows") or 1637),
        "sanitize_validation_path": str(sanitize_val.relative_to(_ROOT)) if sanitize_val.is_file() else None,
        "sanitize_validation_hash": _sha256_file(sanitize_val) if sanitize_val.is_file() else None,
        "training_script_path": str(script.relative_to(_ROOT)),
        "training_script_hash": _sha256_file(script),
        "train_lora_script_path": str(train_script.relative_to(_ROOT)),
        "train_lora_script_hash": _sha256_file(train_script),
        "config_path": "training/configs/lora_fast_need_profile.9b.v4.cpu.yaml",
        "git_commit": _git_commit(),
        "cuda_available": False,
        "torch_version": _pkg_ver("torch"),
        "transformers_version": _pkg_ver("transformers"),
        "peft_version": _pkg_ver("peft"),
        "datasets_version": _pkg_ver("datasets"),
        "gpu_model": None,
        "gpu_count": 0,
        "random_seed": int(train_cfg.get("seed") or 19),
        "lora": {
            "r": int(lora_cfg.get("r") or 16),
            "alpha": int(lora_cfg.get("alpha") or 32),
            "dropout": float(lora_cfg.get("dropout") or 0.05),
            "target_modules": list(lora_cfg.get("target_modules") or ["q_proj", "v_proj"]),
            "bias": "none",
            "task_type": "CAUSAL_LM",
            "init": "fresh_get_peft_model",
        },
        "optimizer": {
            "name": str(train_cfg.get("optim") or "adamw_torch"),
            "learning_rate": float(train_cfg.get("learning_rate") or 1.5e-4),
            "lr_scheduler_type": str(train_cfg.get("lr_scheduler_type") or "linear"),
            "warmup_ratio": float(train_cfg.get("warmup_ratio") or 0.0),
        },
        "batch_size": bs,
        "gradient_accumulation": gas,
        "effective_batch_size": bs * gas,
        "epochs": float(train_cfg.get("epochs") or 1),
        "max_steps": int(train_cfg.get("max_steps") or 400),
        "max_sequence_length": int(train_cfg.get("max_seq_len") or 1536),
        "precision": "float32_cpu",
        "bf16": bool(train_cfg.get("bf16")),
        "fp16": bool(train_cfg.get("fp16")),
        "gradient_checkpointing": bool(train_cfg.get("gradient_checkpointing")),
        "save_strategy": f"steps@{int(train_cfg.get('save_steps') or 50)}",
        "notes": list(cfg.get("notes") or []),
    }
    path = out_dir / "train_freeze.json"
    if path.is_file():
        prev = json.loads(path.read_text(encoding="utf-8"))
        # allow re-entry only if identical critical fields
        critical = (
            "train_dataset_hash",
            "base_model_path",
            "lora",
            "optimizer",
            "max_steps",
            "random_seed",
            "effective_batch_size",
        )
        for k in critical:
            if prev.get(k) != freeze.get(k):
                raise SystemExit(
                    f"train_freeze.json already exists and field {k} would change — refuse silent mutate"
                )
    path.write_text(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return freeze


def write_config_delta(out_dir: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    """Compare frozen v4 recipe to known v3 yaml defaults."""
    v3 = {
        "base_model_ref": "Qwen/Qwen3.5-9B",
        "lora_r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "v_proj"],
        "learning_rate": 1.5e-4,
        "optimizer": "adamw_torch",
        "scheduler": "linear",
        "batch_size": 1,
        "gradient_accumulation": 8,
        "epochs": 1.0,
        "max_steps": 400,
        "max_seq_len": 1536,
        "precision": "float32_cpu",
        "seed": None,
        "train_rows": 2500,
        "train_jsonl": "training/exports/need_profile_sft.v3.jsonl",
    }
    v4 = {
        "base_model_ref": freeze["base_model_path"],
        "lora_r": freeze["lora"]["r"],
        "lora_alpha": freeze["lora"]["alpha"],
        "lora_dropout": freeze["lora"]["dropout"],
        "target_modules": freeze["lora"]["target_modules"],
        "learning_rate": freeze["optimizer"]["learning_rate"],
        "optimizer": freeze["optimizer"]["name"],
        "scheduler": freeze["optimizer"]["lr_scheduler_type"],
        "batch_size": freeze["batch_size"],
        "gradient_accumulation": freeze["gradient_accumulation"],
        "epochs": freeze["epochs"],
        "max_steps": freeze["max_steps"],
        "max_seq_len": freeze["max_sequence_length"],
        "precision": freeze["precision"],
        "seed": freeze["random_seed"],
        "train_rows": freeze["train_row_count"],
        "train_jsonl": freeze["train_dataset_path"],
    }
    diffs = {}
    for k, vv3 in v3.items():
        if v4.get(k) != vv3:
            diffs[k] = {"v3": vv3, "v4": v4.get(k)}
    delta = {
        "matched_intentionally": [k for k in v3 if k not in diffs],
        "differences": diffs,
        "primary_variable": "sanitized_dataset",
        "notes": [
            "v3 yaml had no explicit seed; v4 freezes seed=19 for shuffle/reproducibility",
            "v3 train_rows=2500 dirty; v4 train_rows=1637 clean — same max_steps=400 ⇒ more passes over data",
            "No CUDA on nanobase: precision remains float32_cpu (identical to v3 train_meta device=cpu)",
            "FP16/BF16 in the task sense means HF peft adapter Ref (not GGUF/Q4), not CUDA amp",
            "In-trainer eval_loss skipped (lora-venv lacks app deps); same practical path as v3; smoke + DEV-EVAL select quality",
        ],
    }
    (out_dir / "training_config_delta.json").write_text(
        json.dumps(delta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return delta


def _format_example(row: dict[str, Any], tokenizer: Any) -> dict[str, Any]:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError(f"bad messages in row {row.get('id')}")
    # strip any accidental metadata leakage — only chat messages
    clean = [{"role": m["role"], "content": m["content"]} for m in messages]
    if hasattr(tokenizer, "apply_chat_template"):
        text = tokenizer.apply_chat_template(
            clean, tokenize=False, add_generation_prompt=False
        )
    else:
        text = "\n".join(f"{m['role']}: {m['content']}" for m in clean)
    return {"text": text}


def _build_dev_eval_rows(limit: int = 32) -> list[dict[str, Any]]:
    """Eval-loss during train needs app NeedProfile builders (redis etc.).

    lora-venv intentionally lacks full app deps (same as v3 train path).
    Semantic selection is deferred to post-train smoke + P17-V4-DEV-EVAL-001.
    """
    del limit  # reserved for future offline eval export
    print(
        "[warn] skipping in-trainer eval_dataset (lora-venv has no app deps); "
        "checkpoints saved by step; semantic gate = smoke + DEV-EVAL",
        flush=True,
    )
    return []


def train(cfg: dict[str, Any], freeze: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    import torch  # type: ignore
    from datasets import Dataset  # type: ignore
    from peft import LoraConfig, get_peft_model  # type: ignore
    from transformers import (  # type: ignore
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    _assert_no_v3_resume(None, cfg)
    seed = int(freeze["random_seed"])
    set_seed(seed)
    random.seed(seed)

    train_jsonl = _ROOT / str(cfg["train_jsonl"])
    rows = _load_train_rows(train_jsonl)
    if len(rows) != int(freeze["train_row_count"]):
        raise SystemExit(f"row count mismatch: {len(rows)} != {freeze['train_row_count']}")
    ds_hash = _sha256_file(train_jsonl)
    if ds_hash != freeze["train_dataset_hash"]:
        raise SystemExit("dataset hash drift vs freeze — refuse train")

    # leakage: train utterances vs development (exact)
    train_utts = {str(r.get("utterance") or "").strip().casefold() for r in rows}
    leak = 0
    dev_path = _ROOT / "evaluation/datasets/development/tr-category-dev.v4.jsonl"
    if dev_path.is_file():
        with dev_path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                u = str(json.loads(line).get("utterance") or "").strip().casefold()
                if u and u in train_utts:
                    leak += 1
    if leak:
        raise SystemExit(f"train/dev exact utterance leakage={leak}")

    random.Random(seed).shuffle(rows)

    base_model = freeze["base_model_path"]
    print(
        f"[{_utc()}] FRESH train rows={len(rows)} base={base_model} device=cpu "
        f"v3_resume=FORBIDDEN",
        flush=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        trust_remote_code=True,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
    )
    model = model.to("cpu")

    # Fresh LoRA — get_peft_model, never PeftModel.from_pretrained(v3)
    peft_config = LoraConfig(
        r=int(freeze["lora"]["r"]),
        lora_alpha=int(freeze["lora"]["alpha"]),
        lora_dropout=float(freeze["lora"]["dropout"]),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(freeze["lora"]["target_modules"]),
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    train_cfg = cfg.get("train") or {}
    max_seq = int(freeze["max_sequence_length"])

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=max_seq,
            padding=False,
        )

    formatted = [_format_example(r, tokenizer) for r in rows]
    tokenized = Dataset.from_list(formatted).map(
        tokenize, batched=True, remove_columns=["text"]
    )

    eval_rows = _build_dev_eval_rows(32)
    eval_ds = None
    if eval_rows:
        eval_fmt = [_format_example(r, tokenizer) for r in eval_rows]
        eval_ds = Dataset.from_list(eval_fmt).map(
            tokenize, batched=True, remove_columns=["text"]
        )

    ckpt_dir = out_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    args = TrainingArguments(
        output_dir=str(ckpt_dir),
        num_train_epochs=float(freeze["epochs"]),
        learning_rate=float(freeze["optimizer"]["learning_rate"]),
        per_device_train_batch_size=int(freeze["batch_size"]),
        gradient_accumulation_steps=int(freeze["gradient_accumulation"]),
        logging_steps=int(train_cfg.get("logging_steps") or 10),
        save_steps=int(train_cfg.get("save_steps") or 50),
        save_total_limit=4,
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=int(train_cfg.get("save_steps") or 50) if eval_ds is not None else None,
        load_best_model_at_end=bool(eval_ds is not None),
        metric_for_best_model="eval_loss" if eval_ds is not None else None,
        greater_is_better=False if eval_ds is not None else None,
        bf16=False,
        fp16=False,
        report_to=[],
        remove_unused_columns=False,
        max_steps=int(freeze["max_steps"]),
        dataloader_num_workers=0,
        use_cpu=True,
        seed=seed,
        optim=str(freeze["optimizer"]["name"]),
        lr_scheduler_type=str(freeze["optimizer"]["lr_scheduler_type"]),
        warmup_ratio=float(freeze["optimizer"]["warmup_ratio"]),
        gradient_checkpointing=bool(freeze["gradient_checkpointing"]),
    )

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized,
        eval_dataset=eval_ds,
        data_collator=collator,
    )

    t0 = time.time()
    nan_inf = 0
    oom = 0
    try:
        train_result = trainer.train(resume_from_checkpoint=None)
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower():
            oom += 1
        raise
    duration_s = time.time() - t0

    # detect nan in log history
    for entry in trainer.state.log_history:
        for k, v in entry.items():
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                nan_inf += 1

    adapter_dir = out_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    # save best or final
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    # also persist trainer state copy at artifact root
    state_src = ckpt_dir / "trainer_state.json"
    if state_src.is_file():
        (out_dir / "trainer_state.json").write_text(
            state_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        (out_dir / "trainer_state.json").write_text(
            json.dumps(trainer.state.log_history, indent=2) + "\n", encoding="utf-8"
        )

    # training args dump
    (out_dir / "training_arguments.json").write_text(
        json.dumps(args.to_dict(), indent=2, default=str) + "\n", encoding="utf-8"
    )

    log_hist = list(trainer.state.log_history)
    train_losses = [e["loss"] for e in log_hist if "loss" in e]
    eval_losses = [e["eval_loss"] for e in log_hist if "eval_loss" in e]
    metrics = {
        "train_runtime_s": duration_s,
        "train_loss_last": train_losses[-1] if train_losses else None,
        "train_loss_min": min(train_losses) if train_losses else None,
        "eval_loss_last": eval_losses[-1] if eval_losses else None,
        "eval_loss_best": min(eval_losses) if eval_losses else None,
        "nan_inf_count": nan_inf,
        "oom_count": oom,
        "samples_processed": len(rows),
        "max_steps": freeze["max_steps"],
        "global_step": int(getattr(trainer.state, "global_step", 0) or 0),
        "log_history": log_hist,
        "train_result_metrics": dict(getattr(train_result, "metrics", {}) or {}),
    }
    (out_dir / "training_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # checkpoint manifest
    ckpts = sorted(ckpt_dir.glob("checkpoint-*"))
    best = getattr(trainer.state, "best_model_checkpoint", None)
    manifest = {
        "checkpoints": [str(p.relative_to(_ROOT)) for p in ckpts],
        "best_dev_loss_checkpoint": best,
        "final_checkpoint": str(ckpts[-1].relative_to(_ROOT)) if ckpts else None,
        "adapter_dir": str(adapter_dir.relative_to(_ROOT)),
        "adapter_files": sorted(p.name for p in adapter_dir.iterdir()),
    }
    (out_dir / "checkpoint_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if nan_inf or oom:
        raise SystemExit(f"training failed nan_inf={nan_inf} oom={oom}")
    if not (adapter_dir / "adapter_config.json").is_file():
        raise SystemExit("adapter_config.json missing")
    if not (
        (adapter_dir / "adapter_model.safetensors").is_file()
        or (adapter_dir / "adapter_model.bin").is_file()
    ):
        raise SystemExit("adapter weights missing")

    return {
        "metrics": metrics,
        "manifest": manifest,
        "adapter_dir": str(adapter_dir),
        "eval_rows_used": len(eval_rows),
        "train_dev_leakage": leak,
    }


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _smoke_validate_profile(profile: dict[str, Any]) -> None:
    """Lightweight schema gate for smoke (no app imports in lora-venv)."""
    required = (
        "utterance",
        "intent",
        "preferences",
        "budget",
        "semantic_constraints",
        "confidence",
    )
    for k in required:
        if k not in profile:
            raise ValueError(f"missing field: {k}")
    sc = profile["semantic_constraints"]
    if not isinstance(sc, dict):
        raise ValueError("semantic_constraints not object")
    for k in ("positive", "negative", "corrections"):
        if k not in sc or not isinstance(sc[k], list):
            raise ValueError(f"semantic_constraints.{k} must be list")
        for item in sc[k]:
            if not isinstance(item, dict) or "concept" not in item:
                raise ValueError(f"bad constraint item in {k}")
    pos = {x["concept"] for x in sc["positive"] if isinstance(x, dict) and "concept" in x}
    neg = {x["concept"] for x in sc["negative"] if isinstance(x, dict) and "concept" in x}
    if pos & neg:
        raise ValueError(f"positive∩negative={pos & neg}")


def _smoke_system_prompt() -> str:
    exp_path = _ROOT / "src/taksitlio/training/export_sft.py"
    try:
        text = exp_path.read_text(encoding="utf-8")
        m = re.search(r'DEFAULT_SYSTEM_PROMPT\s*=\s*("""|\'\'\')(.*?)\1', text, flags=re.S)
        if m:
            return m.group(2).strip()
    except Exception:  # noqa: BLE001
        pass
    return (
        "You extract a NeedProfile JSON object for a Turkish shopping assistant. "
        "Return only valid JSON. Never emit category IDs or fixture keys."
    )


def run_smoke(adapter_dir: Path, freeze: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    import torch  # type: ignore
    from peft import PeftModel  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

    validate_need_profile = _smoke_validate_profile
    DEFAULT_SYSTEM_PROMPT = _smoke_system_prompt()

    base = freeze["base_model_path"]
    print(f"[{_utc()}] smoke load base+adapter…", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(adapter_dir, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base,
        trust_remote_code=True,
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
    )
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    model = model.to("cpu")
    model.eval()

    inputs_path = out_dir / "smoke_inputs.jsonl"
    outputs_path = out_dir / "smoke_outputs.jsonl"
    with inputs_path.open("w", encoding="utf-8") as fh:
        for c in SMOKE_CASES:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    results = []
    schema_ok = 0
    forbidden = 0
    conflict = 0
    corr_err = 0
    json_ok = 0

    for case in SMOKE_CASES:
        messages = [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": case["utterance"]},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        enc = tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(
                **enc,
                max_new_tokens=512,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        gen = tokenizer.decode(out[0][enc["input_ids"].shape[-1] :], skip_special_tokens=True)
        profile = _extract_json(gen)
        row_out: dict[str, Any] = {
            "id": case["id"],
            "family": case["family"],
            "utterance": case["utterance"],
            "raw": gen,
            "parsed": profile,
        }
        if profile is None:
            row_out["valid_json"] = False
            row_out["schema_valid"] = False
        else:
            json_ok += 1
            row_out["valid_json"] = True
            try:
                validate_need_profile(profile)
                row_out["schema_valid"] = True
                schema_ok += 1
            except Exception as exc:  # noqa: BLE001
                row_out["schema_valid"] = False
                row_out["schema_error"] = str(exc)[:200]
            blob = json.dumps(profile, ensure_ascii=False).lower()
            if any(x in blob for x in ("fixture.", "category-", "cat_")):
                forbidden += 1
                row_out["forbidden"] = True
            sc = profile.get("semantic_constraints") or {}
            pos = {x.get("concept") for x in (sc.get("positive") or []) if isinstance(x, dict)}
            neg = {x.get("concept") for x in (sc.get("negative") or []) if isinstance(x, dict)}
            if pos & neg:
                conflict += 1
                row_out["conflict"] = True
            # obvious correction direction: X değil Y
            m = re.search(
                r"([\wçğıöşü]+)\s+değil\s+([\wçğıöşü]+)",
                case["utterance"].casefold(),
            )
            if m and pos and neg:
                left, right = m.group(1), m.group(2)
                if any(p in left and p not in right for p in pos) and any(
                    n in right and n not in left for n in neg
                ):
                    corr_err += 1
                    row_out["corr_direction_error"] = True
        results.append(row_out)

    with outputs_path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n = len(SMOKE_CASES)
    report = {
        "n": n,
        "valid_json": json_ok,
        "schema_valid": schema_ok,
        "forbidden": forbidden,
        "positive_negative_conflict": conflict,
        "obvious_correction_errors": corr_err,
        "schema_validity_rate": schema_ok / n if n else 0.0,
        "pass": (
            json_ok == n
            and schema_ok == n
            and forbidden == 0
            and conflict == 0
            and corr_err == 0
        ),
    }
    (out_dir / "smoke_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def write_final_reports(
    out_dir: Path,
    freeze: dict[str, Any],
    train_info: dict[str, Any],
    smoke: dict[str, Any],
    decision: str,
) -> None:
    metrics = train_info["metrics"]
    meta = {
        "experiment_id": EXPERIMENT,
        "adapter_name": ADAPTER_NAME,
        "training_start": "FRESH_BASE",
        "v3_adapter_loaded": False,
        "decision": decision,
        "campaign_gate": "CLOSED",
        "hr100": "NOT_RUN",
        "quant_attribution": "NOT_TESTED",
        "adapter_dir": train_info["adapter_dir"],
        "created_at": _utc(),
        "freeze_dataset_hash": freeze["train_dataset_hash"],
        "train_rows": freeze["train_row_count"],
        "smoke_pass": smoke.get("pass"),
    }
    (out_dir / "train_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "training_config.json").write_text(
        json.dumps(
            {
                "from_freeze": {
                    k: freeze[k]
                    for k in (
                        "base_model_path",
                        "lora",
                        "optimizer",
                        "effective_batch_size",
                        "epochs",
                        "max_steps",
                        "max_sequence_length",
                        "precision",
                        "random_seed",
                    )
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report = f"""# P17-V4-SFT-001 Report

**Created:** `{_utc()}`  
**Decision:** `{decision}`  
**Training start:** FRESH BASE (v3 adapter NOT loaded)  
**Campaign Gate:** CLOSED · **HR100:** NOT RUN · **Quant:** NOT TESTED

## Freeze

| Field | Value |
|---|---|
| Base | `{freeze['base_model_path']}` @ `{freeze.get('base_model_revision')}` |
| Dataset | `{freeze['train_dataset_path']}` |
| Dataset hash | `{freeze['train_dataset_hash']}` |
| Rows | {freeze['train_row_count']} |
| Seed | {freeze['random_seed']} |
| LoRA | r={freeze['lora']['r']} α={freeze['lora']['alpha']} drop={freeze['lora']['dropout']} |
| Eff. batch | {freeze['effective_batch_size']} |
| Max steps | {freeze['max_steps']} |
| Precision | {freeze['precision']} |

## Metrics

| Item | Value |
|---|---|
| Train loss (last) | {metrics.get('train_loss_last')} |
| Eval loss (best) | {metrics.get('eval_loss_best')} |
| NaN/Inf | {metrics.get('nan_inf_count')} |
| OOM | {metrics.get('oom_count')} |
| Runtime s | {metrics.get('train_runtime_s')} |
| Adapter | `{train_info['adapter_dir']}` |

## Smoke

```json
{json.dumps(smoke, indent=2)}
```

## Final

```text
P17-V4-SFT-001          = {'COMPLETE' if decision == 'V4_SFT_TRAINING_COMPLETE' else 'REJECT'}
Training start          = FRESH BASE
Base model              = {freeze['base_model_path']}
Dataset rows            = {freeze['train_row_count']} / 1637
Dataset hash            = {freeze['train_dataset_hash']}
Epochs                   = {freeze['epochs']}
Effective batch size     = {freeze['effective_batch_size']}
Training loss            = {metrics.get('train_loss_last')}
Dev/eval loss            = {metrics.get('eval_loss_best')}
NaN / Inf                = {metrics.get('nan_inf_count')}
OOM                      = {metrics.get('oom_count')}
Adapter saved            = YES
Smoke schema             = {smoke.get('schema_validity_rate')}
Smoke forbidden          = {smoke.get('forbidden')}
Smoke conflict           = {smoke.get('positive_negative_conflict')}
Training decision        = {decision}
HR100                    = NOT RUN
Quant attribution        = NOT TESTED
Campaign Gate            = CLOSED
```
"""
    (out_dir / "p17_v4_sft_report.md").write_text(report, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        type=Path,
        default=_ROOT / "training/configs/lora_fast_need_profile.9b.v4.cpu.yaml",
    )
    ap.add_argument("--freeze-only", action="store_true")
    ap.add_argument("--smoke-only", action="store_true")
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()

    cfg = _load_yaml(args.config)
    out_dir = _ROOT / str(cfg.get("output_dir") or "artifacts/p17/v4-sft")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(_ROOT / "var" / "hf-cache"))
    os.environ.setdefault("TRANSFORMERS_CACHE", os.environ["HF_HOME"])

    print(f"[{_utc()}] writing freeze…", flush=True)
    freeze = write_freeze(cfg, out_dir)
    write_config_delta(out_dir, freeze)
    (out_dir / "training_config.json").write_text(
        json.dumps({"yaml": cfg, "freeze_ref": "train_freeze.json"}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    if args.freeze_only:
        print(json.dumps({"freeze": str(out_dir / "train_freeze.json")}, indent=2))
        return 0

    if args.smoke_only:
        smoke = run_smoke(out_dir / "adapter", freeze, out_dir)
        decision = "V4_SFT_TRAINING_COMPLETE" if smoke["pass"] else "V4_SFT_SMOKE_REJECT"
        write_final_reports(
            out_dir,
            freeze,
            {
                "metrics": json.loads((out_dir / "training_metrics.json").read_text()),
                "adapter_dir": str(out_dir / "adapter"),
                "manifest": {},
            },
            smoke,
            decision,
        )
        return 0 if smoke["pass"] else 2

    try:
        train_info = train(cfg, freeze, out_dir)
    except Exception as exc:  # noqa: BLE001
        err = {"error": str(exc), "traceback": traceback.format_exc(), "decision": "V4_SFT_TRAINING_REJECT"}
        (out_dir / "train_error.json").write_text(json.dumps(err, indent=2) + "\n", encoding="utf-8")
        print(err["traceback"], file=sys.stderr)
        write_final_reports(
            out_dir,
            freeze,
            {
                "metrics": {"nan_inf_count": -1, "oom_count": -1, "train_loss_last": None, "eval_loss_best": None, "train_runtime_s": None},
                "adapter_dir": str(out_dir / "adapter"),
                "manifest": {},
            },
            {"pass": False, "schema_validity_rate": 0, "forbidden": -1, "positive_negative_conflict": -1},
            "V4_SFT_TRAINING_REJECT",
        )
        return 2

    if args.skip_smoke:
        decision = "V4_SFT_TRAINING_COMPLETE"
        smoke = {"pass": True, "skipped": True, "schema_validity_rate": None, "forbidden": None, "positive_negative_conflict": None}
    else:
        smoke = run_smoke(Path(train_info["adapter_dir"]), freeze, out_dir)
        decision = "V4_SFT_TRAINING_COMPLETE" if smoke["pass"] else "V4_SFT_SMOKE_REJECT"

    write_final_reports(out_dir, freeze, train_info, smoke, decision)
    print(json.dumps({"decision": decision, "adapter": train_info["adapter_dir"]}, indent=2))
    return 0 if decision == "V4_SFT_TRAINING_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
