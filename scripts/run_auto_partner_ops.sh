#!/usr/bin/env bash
# Respawn auto_partner_ops forever (nanobase). No interactive prompts.
set -u
cd /data/nanobaseai/taksitlio-chatbot || exit 1
mkdir -p /tmp/taksitlio-partner-crawls
export PYTHONUNBUFFERED=1
export CRAWL_GLOBAL_PRODUCT_CAP="${CRAWL_GLOBAL_PRODUCT_CAP:-1000000}"
export AUTO_MAX_PARALLEL_CRAWLS="${AUTO_MAX_PARALLEL_CRAWLS:-4}"
export AUTO_COMPLETE_ONLY="${AUTO_COMPLETE_ONLY:-1}"

# Load runtime env for ingest/backfill children
if [[ -f .env.runtime ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.runtime
  set +a
fi

while true; do
  echo "$(date -Is) starting auto_partner_ops" >> /tmp/auto-partner-ops.log
  .venv-crawl/bin/python -u scripts/auto_partner_ops.py >> /tmp/auto-partner-ops.log 2>&1
  rc=$?
  echo "$(date -Is) auto_partner_ops exited rc=$rc — restart in 15s" >> /tmp/auto-partner-ops.log
  sleep 15
done
