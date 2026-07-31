#!/usr/bin/env bash
# After 9B NeedProfile LoRA finishes: convert → sidecar :8026 → HR100 (no quality claim).
# Usage (nanobase):
#   bash training/ops_eval_lora_hr100.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8026}"
ALIAS="${ALIAS:-poc-fast-nine-b-lora}"
LOG="${LOG:-$ROOT/logs/lora-9b-hr100.log}"

bash training/ops_wire_lora_fast_c.sh wire

export FAST_C_BASE_URL="http://127.0.0.1:${PORT}"
export FAST_C_MODEL_REFERENCE="$ALIAS"
export FAST_C_RUNTIME_ALIAS="$ALIAS"
export FAST_C_TIMEOUT_MS="${FAST_C_TIMEOUT_MS:-60000}"
export FAST_C_MAX_OUTPUT_TOKENS="${FAST_C_MAX_OUTPUT_TOKENS:-512}"
# Prefer project venv if present
PY="${PY:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY=python3
  fi
fi

echo "[$(date -Is)] HR100 candidate C via $FAST_C_BASE_URL ($ALIAS)" | tee -a "$LOG"
set -a
# shellcheck disable=SC1091
[[ -f "$ROOT/.env.runtime" ]] && source "$ROOT/.env.runtime" || true
set +a
# re-apply LoRA sidecar overrides after .env.runtime
export FAST_C_BASE_URL="http://127.0.0.1:${PORT}"
export FAST_C_MODEL_REFERENCE="$ALIAS"
export FAST_C_RUNTIME_ALIAS="$ALIAS"

PYTHONPATH=src "$PY" evaluation/_run_adr009_b_hr100.py \
  --candidate C \
  --skip-service-toggle \
  2>&1 | tee -a "$LOG"
