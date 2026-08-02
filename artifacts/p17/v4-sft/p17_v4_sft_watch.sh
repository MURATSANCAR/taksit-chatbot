#!/bin/bash
LOG=/data/nanobaseai/taksitlio-chatbot/logs/p17-v4-sft-001.log
OUT=/data/nanobaseai/taksitlio-chatbot/artifacts/p17/v4-sft/WATCH_STATUS.txt
echo 3771717 > /tmp/p17_v4_sft_train.pid
while true; do
  TP=$(cat /tmp/p17_v4_sft_train.pid 2>/dev/null)
  if grep -qE '"decision":|V4_SFT_TRAINING|V4_SFT_SMOKE|Traceback \(most recent|train_error' "$LOG" 2>/dev/null; then
    echo "DONE $(date -Is)" | tee "$OUT"
    exit 0
  fi
  if ! kill -0 "$TP" 2>/dev/null; then
    NP=$(pgrep -f 'python -u training/run_p17_v4_sft' | head -1)
    if [ -n "$NP" ]; then echo "$NP" > /tmp/p17_v4_sft_train.pid
    else echo "EXITED $(date -Is)" | tee "$OUT"; exit 0; fi
  fi
  STEP=$(tr '\r' '\n' < "$LOG" | grep -oE '[0-9]+/400' | tail -1)
  echo "RUNNING $(date -Is) step=${STEP:-?} pid=$(cat /tmp/p17_v4_sft_train.pid)" > "$OUT"
  sleep 300
done
