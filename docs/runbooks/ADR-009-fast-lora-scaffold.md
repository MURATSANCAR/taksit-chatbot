# Runbook — FAST NeedProfile LoRA scaffold (P17)

## Status

| Item | State |
|---|---|
| Scaffold (export + YAML + stub) | Available |
| GPU fine-tune in this repo | **Not implemented** (stub fails loud) |
| ADR-009 Quality / Provisional | Unchanged — do **not** claim pass from scaffold |
| Campaign Gate | **CLOSED** |

## Why

Generic FAST A/B/C models failed the HR100 hybrid quality bar. Task-specific
SFT/LoRA is the intended quality path; this repo only prepares data export and
a safe entrypoint so ops can train elsewhere.

## Steps

### 1. Export SFT rows

```bash
cd /path/to/Fibabank_Chatbot
python training/export_need_profile_sft.py --source both \
  --out training/exports/need_profile_sft.jsonl
```

Targets are NeedProfile JSON (schema-validated). No merchant/bank names and no
category fixture IDs are written into `need_profile`.

### 2. Config

Copy and edit placeholders (opaque base model ref only):

```bash
cp training/configs/lora_fast_need_profile.example.yaml \
   training/exports/lora_fast_need_profile.yaml
```

### 3. Stub check (CI / laptop)

```bash
python training/train_lora_stub.py --check-config \
  --config training/configs/lora_fast_need_profile.example.yaml
```

### 4. Real train

**CPU (nanobase, slow OK):**

```bash
cd /data/nanobaseai/taksitlio-chatbot
export HF_HOME=$PWD/var/hf-cache
./var/lora-venv/bin/python training/train_lora.py \
  --config training/configs/lora_fast_need_profile.cpu.yaml \
  --allow-cpu --limit 40 --max-steps 20
```

Smoke uses `Qwen/Qwen2.5-0.5B-Instruct` (pipeline proof). For 9B intent use
`lora_fast_need_profile.9b.cpu.yaml` with HF (not GGUF) weights — multi-day on CPU.

**GPU host (preferred for 9B):** peft Trainer / same `train_lora.py` without
`--allow-cpu`, `runtime.require_gpu: true`.

Do not commit secrets or vendor model slugs into app migrations.


### 5. Evaluate (required before any quality claim)

```bash
# Point FAST_* env at the new deployment (opaque refs)
python evaluation/_run_adr009_fast_ab.py …
# or HR100 runner used in ADR-009 live verification
```

Gates: `docs/runbooks/ADR-009-live-runtime-verification.md`  
Floors: invalid_schema=0, forbidden_id=0, neg_recall≥0.95, corr_recall≥0.90, latency budgets.

## Guardrails

- Scaffold success ≠ QUALITY_READY
- Campaign Gate stays closed until ADR-009 provisional
- Never invent production merchant/bank/rate data for training
- Prefer HUMAN_REVIEWED HR rows for eval split
