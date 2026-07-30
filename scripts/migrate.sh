#!/usr/bin/env bash
# Apply SQL migrations in order against DATABASE_URL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${DATABASE_URL:?DATABASE_URL is required}"

if command -v psql >/dev/null 2>&1; then
  for f in "$ROOT"/db/migrations/V*.sql; do
    echo "Applying $(basename "$f")..."
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
  done
  echo "Done."
else
  echo "psql not found. Install PostgreSQL client or use docker compose." >&2
  exit 1
fi
