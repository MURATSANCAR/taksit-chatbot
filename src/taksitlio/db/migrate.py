"""Apply ordered SQL migrations under ``db/migrations`` (ADR-009 runbook).

Not Alembic — repository ships Flyway-style ``VNNN__*.sql`` files.

Enhancements (PROD-FINAL):
- ``content_sha256`` recorded per applied file
- Harness-applied V034–V038 can be reconciled into history only after
  non-destructive object verification probes pass (no blind re-execute)
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def migration_files(migrations_dir: Path | None = None) -> list[Path]:
    root = migrations_dir or (_repo_root() / "db" / "migrations")
    return sorted(root.glob("V*.sql"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Non-destructive probes: object must exist before history row is recorded.
RECONCILE_PROBES: dict[str, tuple[str, ...]] = {
    "V034__p3_7_product_search_golden_lifecycle.sql": (
        "SELECT 1 FROM information_schema.tables WHERE table_name='search_release_cohort_lifecycle_events'",
        "SELECT 1 FROM information_schema.columns WHERE table_name='continuous_golden_cases' AND column_name='review_decision'",
    ),
    "V035__p3_7_golden_review_history.sql": (
        "SELECT 1 FROM information_schema.tables WHERE table_name='continuous_golden_review_history'",
    ),
    "V036__p4_public_readiness_shadow_uat_canary.sql": (
        "SELECT 1 FROM information_schema.tables WHERE table_name='public_shadow_observations'",
        "SELECT 1 FROM information_schema.tables WHERE table_name='public_canary_assignments'",
    ),
    "V037__p4_1_canary_evidence_hardening.sql": (
        "SELECT 1 FROM information_schema.columns WHERE table_name='search_release_cohort_versions' AND column_name='package_state'",
        "SELECT 1 FROM information_schema.columns WHERE table_name='search_release_cohort_versions' AND column_name='traffic_state'",
        "SELECT 1 FROM information_schema.columns WHERE table_name='continuous_golden_cases' AND column_name='provenance_class'",
    ),
    "V038__p4_2_human_evidence_closeout.sql": (
        "SELECT 1 FROM information_schema.tables WHERE table_name='public_real_shadow_unique_queries'",
    ),
}


def _ensure_history_schema(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_history (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        ALTER TABLE schema_migration_history
          ADD COLUMN IF NOT EXISTS content_sha256 TEXT
        """
    )
    cur.execute(
        """
        ALTER TABLE schema_migration_history
          ADD COLUMN IF NOT EXISTS application_method TEXT
        """
    )
    cur.execute(
        """
        ALTER TABLE schema_migration_history
          ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ
        """
    )


def _probes_pass(cur: Any, probes: tuple[str, ...]) -> bool:
    for sql in probes:
        cur.execute(sql)
        if cur.fetchone() is None:
            return False
    return True


def reconcile_harness_migrations(cur: Any, *, migrations_dir: Path) -> list[dict[str, Any]]:
    """Record V034–V038 in history only when objects already exist and checksum matches file."""

    results: list[dict[str, Any]] = []
    for filename, probes in RECONCILE_PROBES.items():
        path = migrations_dir / filename
        sha = file_sha256(path) if path.is_file() else None
        cur.execute(
            "SELECT content_sha256, application_method FROM schema_migration_history WHERE filename = %s",
            (filename,),
        )
        existing = cur.fetchone()
        if existing is not None:
            row_sha = existing[0]
            mismatch = bool(row_sha and sha and row_sha != sha)
            results.append(
                {
                    "filename": filename,
                    "action": "already_recorded",
                    "checksum_mismatch": mismatch,
                    "content_sha256": row_sha or sha,
                }
            )
            continue
        if not path.is_file():
            results.append({"filename": filename, "action": "missing_file", "ok": False})
            continue
        if not _probes_pass(cur, probes):
            results.append(
                {
                    "filename": filename,
                    "action": "probe_failed",
                    "ok": False,
                    "note": "Objects missing — do not invent history; apply via official runner only",
                }
            )
            continue
        cur.execute(
            """
            INSERT INTO schema_migration_history (filename, content_sha256, application_method, verified_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (filename) DO NOTHING
            """,
            (filename, sha, "reconcile_verified_objects"),
        )
        results.append(
            {
                "filename": filename,
                "action": "reconciled",
                "ok": True,
                "content_sha256": sha,
                "application_method": "reconcile_verified_objects",
            }
        )
    return results


def apply_migrations(
    *,
    database_url: str | None = None,
    reconcile_only: bool = False,
) -> int:
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

    migrations_dir = files[0].parent
    applied = 0
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            _ensure_history_schema(cur)
            recon = reconcile_harness_migrations(cur, migrations_dir=migrations_dir)
            for r in recon:
                print(f"reconcile {r.get('filename')}: {r.get('action')} ok={r.get('ok', True)}")
            if reconcile_only:
                print(f"done — reconcile_only, files={len(files)}")
                return 0

            for path in files:
                sha = file_sha256(path)
                cur.execute(
                    "SELECT content_sha256 FROM schema_migration_history WHERE filename = %s",
                    (path.name,),
                )
                row = cur.fetchone()
                if row is not None:
                    if row[0] and row[0] != sha:
                        print(
                            f"FAIL checksum mismatch {path.name}: history={row[0][:16]}… file={sha[:16]}…",
                            file=sys.stderr,
                        )
                        return 3
                    print(f"skip  {path.name}")
                    continue
                sql = path.read_text(encoding="utf-8")
                print(f"apply {path.name}")
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO schema_migration_history
                      (filename, content_sha256, application_method, verified_at)
                    VALUES (%s, %s, %s, NOW())
                    """,
                    (path.name, sha, "official_runner"),
                )
                applied += 1
    print(f"done — newly applied={applied}, total_files={len(files)}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv or sys.argv[1:])
    reconcile_only = "--reconcile-only" in args
    return apply_migrations(reconcile_only=reconcile_only)


if __name__ == "__main__":
    raise SystemExit(main())
