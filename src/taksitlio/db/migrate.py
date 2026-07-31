"""Apply ordered SQL migrations under ``db/migrations`` (ADR-009 runbook).

Not Alembic — repository ships Flyway-style ``VNNN__*.sql`` files. Re-running
is intended to be safe for IF NOT EXISTS / ON CONFLICT style statements.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    # src/taksitlio/db/migrate.py → parents[3] = repo root
    return Path(__file__).resolve().parents[3]


def migration_files(migrations_dir: Path | None = None) -> list[Path]:
    root = migrations_dir or (_repo_root() / "db" / "migrations")
    return sorted(root.glob("V*.sql"))


def apply_migrations(*, database_url: str | None = None) -> int:
    url = (database_url or os.environ.get("DATABASE_URL") or os.environ.get("PGVECTOR_URL") or "").strip()
    if not url:
        print("DATABASE_URL / PGVECTOR_URL required", file=sys.stderr)
        return 2
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("psycopg required: pip install -e '.[dev]'", file=sys.stderr)
        return 2

    files = migration_files()
    if not files:
        print("No V*.sql migrations found", file=sys.stderr)
        return 1

    applied = 0
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migration_history (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for path in files:
                cur.execute(
                    "SELECT 1 FROM schema_migration_history WHERE filename = %s",
                    (path.name,),
                )
                if cur.fetchone():
                    print(f"skip  {path.name}")
                    continue
                sql = path.read_text(encoding="utf-8")
                print(f"apply {path.name}")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migration_history (filename) VALUES (%s)",
                    (path.name,),
                )
                applied += 1
    print(f"done — newly applied={applied}, total_files={len(files)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return apply_migrations()


if __name__ == "__main__":
    raise SystemExit(main())
