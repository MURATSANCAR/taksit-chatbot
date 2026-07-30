"""pgvector integration coverage (ADR-006 §J).

The test only runs when ``PGVECTOR_URL`` is exported *and* the database
has the ``vector`` extension available. Otherwise the test:

* fails loudly when ``INTEGRATION_REQUIRE_PG=1`` (CI opts in to strict
  mode) so we never mistake a skipped test for a passing one;
* skips with a clear reason when running locally without a database.

The test is deliberately *pytest.mark.integration* so it is excluded
from the fast unit suite by default.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.integration


def _resolve_pgvector_url() -> str | None:
    return os.getenv("PGVECTOR_URL") or os.getenv("DATABASE_URL")


def _integration_required() -> bool:
    return os.getenv("INTEGRATION_REQUIRE_PG", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def pg_connection():
    url = _resolve_pgvector_url()
    if not url:
        if _integration_required():
            pytest.fail(
                "INTEGRATION_REQUIRE_PG=1 set but PGVECTOR_URL/DATABASE_URL missing"
            )
        pytest.skip(
            "PGVECTOR_URL not set — pgvector integration test skipped "
            "(set INTEGRATION_REQUIRE_PG=1 to fail instead)"
        )
    try:
        import psycopg  # type: ignore
    except ImportError:  # pragma: no cover - optional dep
        if _integration_required():
            pytest.fail("INTEGRATION_REQUIRE_PG=1 but psycopg not installed")
        pytest.skip("psycopg not installed; skipping pgvector integration test")

    conn = psycopg.connect(url, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _ensure_pgvector(conn) -> None:
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:  # noqa: BLE001
            if _integration_required():
                pytest.fail(f"pgvector extension unavailable: {exc}")
            pytest.skip(f"pgvector extension unavailable: {exc}")


def test_pgvector_roundtrip_dimension_check(pg_connection) -> None:
    _ensure_pgvector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS taksitlio_pgvector_probe")
        cur.execute(
            "CREATE TABLE taksitlio_pgvector_probe ("
            "  category_id TEXT PRIMARY KEY,"
            "  revision INTEGER NOT NULL,"
            "  profile_id TEXT NOT NULL,"
            "  active BOOLEAN NOT NULL DEFAULT TRUE,"
            "  embedding vector(4)"
            ")"
        )
        cur.execute(
            "INSERT INTO taksitlio_pgvector_probe VALUES "
            "  ('a', 1, 'p1', TRUE, '[1,0,0,0]'),"
            "  ('b', 1, 'p1', TRUE, '[0,1,0,0]'),"
            "  ('c', 1, 'p1', FALSE, '[0,0,1,0]'),"
            "  ('d', 2, 'p1', TRUE, '[0,0,0,1]')"
        )
        # Revision + profile + active filter should drop inactive + old revs.
        cur.execute(
            "SELECT category_id FROM taksitlio_pgvector_probe "
            "WHERE profile_id = 'p1' AND active AND revision = 1 "
            "ORDER BY category_id"
        )
        rows = [row[0] for row in cur.fetchall()]
        assert rows == ["a", "b"]
        # Dimension mismatch must fail.
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO taksitlio_pgvector_probe VALUES "
                "  ('bad', 1, 'p1', TRUE, '[1,0,0]')"
            )
        cur.execute("DROP TABLE taksitlio_pgvector_probe")


def test_pgvector_small_benchmark_stub(pg_connection) -> None:
    """Small-scale insertion probe (100 rows) with cosine ordering."""

    _ensure_pgvector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS taksitlio_pgvector_bench")
        cur.execute(
            "CREATE TABLE taksitlio_pgvector_bench ("
            "  category_id TEXT PRIMARY KEY,"
            "  embedding vector(8)"
            ")"
        )
        rows = []
        for i in range(100):
            vec = [0.0] * 8
            vec[i % 8] = 1.0
            rows.append((f"c{i}", "[" + ",".join(f"{v:.3f}" for v in vec) + "]"))
        cur.executemany(
            "INSERT INTO taksitlio_pgvector_bench VALUES (%s, %s)", rows
        )
        cur.execute(
            "SELECT category_id "
            "FROM taksitlio_pgvector_bench "
            "ORDER BY embedding <-> '[1,0,0,0,0,0,0,0]' "
            "LIMIT 3"
        )
        matches = [r[0] for r in cur.fetchall()]
        assert matches, "top-k query must return at least one candidate"
        cur.execute("DROP TABLE taksitlio_pgvector_bench")
