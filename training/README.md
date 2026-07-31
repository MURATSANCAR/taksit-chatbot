# FAST LoRA / NeedProfile SFT scaffold (P17)

**Not a quality claim.** Campaign Gate stays **CLOSED**. Live FAST HR100 remains
REJECT until a real trained/deployed candidate passes ADR-009 gates.

## Layout

| Path | Role |
|---|---|
| `schemas/need_profile_sft_example.schema.json` | SFT row contract |
| `export_need_profile_sft.py` | CLI export from goldens / HR validation |
| `configs/lora_fast_need_profile.example.yaml` | Opaque LoRA hyperparams template |
| `train_lora_stub.py` | Loud-fail stub (missing deps / no GPU / not implemented) |
| `exports/` | Generated JSONL (gitignored) |

Library: `taksitlio.training.export_sft`.

## Export

```bash
# small golden need-understanding set
python training/export_need_profile_sft.py --source golden --limit 5 --stdout

# HR validation constraints → NeedProfile (concepts only; no fixture IDs in target)
python training/export_need_profile_sft.py --source hr-validation \
  --out training/exports/need_profile_sft.jsonl
```

## Train stub

```bash
python training/train_lora_stub.py --check-config
python training/train_lora_stub.py --config training/configs/lora_fast_need_profile.example.yaml
# → exit 2 without torch/peft/transformers; exit 3 without GPU
```

Real training happens in an ops GPU environment (not bundled). After deploy:

1. Point a new opaque `FAST_*` runtime at the adapter
2. Re-run `evaluation/_run_adr009_fast_ab.py` / HR100
3. Only then reconsider Quality / Provisional — never from this stub alone

See `docs/runbooks/ADR-009-fast-lora-scaffold.md`.
