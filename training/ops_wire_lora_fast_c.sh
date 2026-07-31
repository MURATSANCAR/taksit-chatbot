#!/usr/bin/env bash
# Wire NeedProfile LoRA adapter into a side-car llama-server for ADR-009 eval.
# Does NOT claim QUALITY_READY. Does NOT mutate production taksitlio-fast-c.service.
#
# Usage (nanobase):
#   cd /data/nanobaseai/taksitlio-chatbot
#   bash training/ops_wire_lora_fast_c.sh              # convert final adapter + start :8026
#   bash training/ops_wire_lora_fast_c.sh --convert-only
#   bash training/ops_wire_lora_fast_c.sh --stop
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADAPTER_DIR="${ADAPTER_DIR:-$ROOT/training/exports/lora-out-9b-cpu/adapter}"
OUT_GGUF="${OUT_GGUF:-$ROOT/training/exports/lora-out-9b-cpu/gguf/need-profile-lora-f16.gguf}"
BASE_SNAP="${BASE_SNAP:-}"
BASE_GGUF="${BASE_GGUF:-/data/nanobaseai/models/taksitlio-fast-c/Qwen_Qwen3.5-9B-Q4_K_M.gguf}"
LLAMA_BIN="${LLAMA_BIN:-/opt/nanobaseai/bin/llama-server}"
CONVERT_PY="${CONVERT_PY:-/data/nanobaseai/src/llama.cpp/convert_lora_to_gguf.py}"
VENV_PY="${VENV_PY:-$ROOT/var/lora-venv/bin/python}"
PORT="${PORT:-8026}"
ALIAS="${ALIAS:-poc-fast-nine-b-lora}"
PID_FILE="${PID_FILE:-$ROOT/var/run/fast-c-lora.pid}"
LOG_FILE="${LOG_FILE:-$ROOT/logs/fast-c-lora.log}"

mode="${1:-wire}"

if [[ -z "$BASE_SNAP" ]]; then
  BASE_SNAP="$(ls -d "$ROOT"/var/hf-cache/hub/models--Qwen--Qwen3.5-9B/snapshots/* 2>/dev/null | head -1 || true)"
fi

convert() {
  if [[ ! -d "$ADAPTER_DIR" ]]; then
    echo "adapter missing: $ADAPTER_DIR" >&2
    exit 2
  fi
  if [[ -z "$BASE_SNAP" || ! -d "$BASE_SNAP" ]]; then
    echo "base HF snapshot missing; set BASE_SNAP" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$OUT_GGUF")"
  echo "[$(date -Is)] convert $ADAPTER_DIR -> $OUT_GGUF"
  "$VENV_PY" "$CONVERT_PY" \
    --base "$BASE_SNAP" \
    --outtype f16 \
    --outfile "$OUT_GGUF" \
    "$ADAPTER_DIR"
  ls -lah "$OUT_GGUF"
}

stop_server() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "[$(date -Is)] stop pid=$pid"
      kill "$pid" || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  # also clear any leftover listener on PORT
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" 2>/dev/null || true
  fi
}

start_server() {
  if [[ ! -f "$OUT_GGUF" ]]; then
    echo "gguf missing: $OUT_GGUF (run convert first)" >&2
    exit 2
  fi
  if [[ ! -f "$BASE_GGUF" ]]; then
    echo "base gguf missing: $BASE_GGUF" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"
  stop_server
  echo "[$(date -Is)] start $LLAMA_BIN --lora $OUT_GGUF on :$PORT alias=$ALIAS"
  nohup "$LLAMA_BIN" \
    --model "$BASE_GGUF" \
    --lora "$OUT_GGUF" \
    --alias "$ALIAS" \
    --host 127.0.0.1 \
    --port "$PORT" \
    -c 8192 \
    -t 16 \
    -b 512 \
    -ub 256 \
    -np 2 \
    --cache-type-k q4_0 \
    --cache-type-v q4_0 \
    --jinja \
    --reasoning off \
    --metrics \
    >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  for i in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "[$(date -Is)] healthy http://127.0.0.1:${PORT} alias=$ALIAS"
      echo "Eval hint:"
      echo "  FAST_C_BASE_URL=http://127.0.0.1:${PORT} FAST_C_MODEL_REFERENCE=${ALIAS} \\"
      echo "  python evaluation/_run_adr009_b_hr100.py --candidate C --skip-service-toggle ..."
      return 0
    fi
    sleep 2
  done
  echo "health check failed; see $LOG_FILE" >&2
  exit 3
}

case "$mode" in
  --convert-only|convert)
    convert
    ;;
  --stop|stop)
    stop_server
    echo stopped
    ;;
  --start-only|start)
    start_server
    ;;
  wire|--wire|"")
    convert
    start_server
    ;;
  *)
    echo "usage: $0 [--convert-only|--start-only|--stop|wire]" >&2
    exit 1
    ;;
esac
