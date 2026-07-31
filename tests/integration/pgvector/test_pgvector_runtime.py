"""pgvector integration — skip=0 under INTEGRATION_REQUIRE_PG=1 (ADR-009 §4)."""

from __future__ import annotations

import os
import time

import pytest


pytestmark = pytest.mark.integration


def _resolve_url() -> str | None:
    return os.getenv("PGVECTOR_URL") or os.getenv("DATABASE_URL")


def _required() -> bool:
    return os.getenv("INTEGRATION_REQUIRE_PG", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def pg_connection():
    url = _resolve_url()
    if not url:
        if _required():
            pytest.fail("INTEGRATION_REQUIRE_PG=1 but PGVECTOR_URL/DATABASE_URL missing")
        pytest.skip("PGVECTOR_URL not set")
    try:
        import psycopg  # type: ignore
    except ImportError:
        if _required():
            pytest.fail("psycopg not installed")
        pytest.skip("psycopg not installed")

    try:
        conn = psycopg.connect(url, autocommit=True)
    except Exception as exc:  # noqa: BLE001
        if _required():
            pytest.fail(f"postgres unavailable: {exc}")
        pytest.skip(f"postgres unavailable: {exc}")
    try:
        yield conn
    finally:
        conn.close()


def _ensure_vector(conn) -> None:
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:  # noqa: BLE001
            if _required():
                pytest.fail(f"pgvector unavailable: {exc}")
            pytest.skip(f"pgvector unavailable: {exc}")


def test_vector_extension_present(pg_connection) -> None:
    _ensure_vector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
        assert cur.fetchone() is not None


def test_dimension_check_and_filters(pg_connection) -> None:
    _ensure_vector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS taksitlio_pgvector_p1")
        cur.execute(
            """
            CREATE TABLE taksitlio_pgvector_p1 (
              category_id TEXT PRIMARY KEY,
              catalog_id TEXT NOT NULL,
              catalog_revision INTEGER NOT NULL,
              locale TEXT NOT NULL,
              embedding_profile_id TEXT NOT NULL,
              status TEXT NOT NULL,
              matchable BOOLEAN NOT NULL,
              embedding vector(4)
            )
            """
        )
        cur.execute(
            """
            INSERT INTO taksitlio_pgvector_p1 VALUES
              ('a','cat',1,'tr-TR','p1','ACTIVE',TRUE,'[1,0,0,0]'),
              ('b','cat',1,'tr-TR','p1','ACTIVE',TRUE,'[0,1,0,0]'),
              ('c','cat',1,'tr-TR','p1','INACTIVE',TRUE,'[0,0,1,0]'),
              ('d','cat',1,'tr-TR','p1','ACTIVE',FALSE,'[0,0,0,1]'),
              ('e','cat',2,'tr-TR','p1','ACTIVE',TRUE,'[1,1,0,0]'),
              ('f','cat',1,'tr-TR','p2','ACTIVE',TRUE,'[1,0,1,0]'),
              ('g','other',1,'tr-TR','p1','DRAFT',TRUE,'[1,0,0,1]')
            """
        )
        cur.execute(
            """
            SELECT category_id FROM taksitlio_pgvector_p1
            WHERE catalog_id='cat'
              AND catalog_revision=1
              AND locale='tr-TR'
              AND embedding_profile_id='p1'
              AND status='ACTIVE'
              AND matchable=TRUE
            ORDER BY category_id
            """
        )
        assert [r[0] for r in cur.fetchall()] == ["a", "b"]
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO taksitlio_pgvector_p1 VALUES "
                "('bad','cat',1,'tr-TR','p1','ACTIVE',TRUE,'[1,0,0]')"
            )
        cur.execute("DROP TABLE taksitlio_pgvector_p1")


def test_hnsw_index_and_deterministic_topk(pg_connection) -> None:
    _ensure_vector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS taksitlio_pgvector_hnsw")
        cur.execute(
            """
            CREATE TABLE taksitlio_pgvector_hnsw (
              category_id TEXT PRIMARY KEY,
              catalog_revision INTEGER NOT NULL,
              embedding_profile_id TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'ACTIVE',
              matchable BOOLEAN NOT NULL DEFAULT TRUE,
              embedding vector(8)
            )
            """
        )
        rows = []
        for i in range(64):
            vec = [0.0] * 8
            vec[i % 8] = 1.0
            rows.append(
                (
                    f"c{i}",
                    1,
                    "p1",
                    "ACTIVE",
                    True,
                    "[" + ",".join(f"{v:.3f}" for v in vec) + "]",
                )
            )
        cur.executemany(
            "INSERT INTO taksitlio_pgvector_hnsw VALUES (%s,%s,%s,%s,%s,%s)",
            rows,
        )
        # HNSW may require vector_cosine_ops / l2; use vector_l2_ops.
        cur.execute(
            "CREATE INDEX ON taksitlio_pgvector_hnsw "
            "USING hnsw (embedding vector_l2_ops)"
        )
        cur.execute(
            """
            SELECT category_id FROM taksitlio_pgvector_hnsw
            WHERE catalog_revision=1 AND embedding_profile_id='p1'
              AND status='ACTIVE' AND matchable=TRUE
            ORDER BY embedding <-> '[1,0,0,0,0,0,0,0]'
            LIMIT 5
            """
        )
        first = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            SELECT category_id FROM taksitlio_pgvector_hnsw
            WHERE catalog_revision=1 AND embedding_profile_id='p1'
              AND status='ACTIVE' AND matchable=TRUE
            ORDER BY embedding <-> '[1,0,0,0,0,0,0,0]'
            LIMIT 5
            """
        )
        second = [r[0] for r in cur.fetchall()]
        assert first == second
        assert first, "Top-K must be non-empty"
        cur.execute(
            "EXPLAIN SELECT category_id FROM taksitlio_pgvector_hnsw "
            "ORDER BY embedding <-> '[1,0,0,0,0,0,0,0]' LIMIT 5"
        )
        plan = "\n".join(r[0] for r in cur.fetchall()).lower()
        assert "hnsw" in plan or "index" in plan
        cur.execute("DROP TABLE taksitlio_pgvector_hnsw")


def test_small_scale_benchmark_100(pg_connection) -> None:
    _ensure_vector(pg_connection)
    with pg_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS taksitlio_pgvector_bench100")
        cur.execute(
            "CREATE TABLE taksitlio_pgvector_bench100 ("
            " category_id TEXT PRIMARY KEY, embedding vector(8))"
        )
        t0 = time.perf_counter()
        rows = []
        for i in range(100):
            vec = [0.0] * 8
            vec[i % 8] = 1.0
            rows.append((f"c{i}", "[" + ",".join(f"{v:.3f}" for v in vec) + "]"))
        cur.executemany(
            "INSERT INTO taksitlio_pgvector_bench100 VALUES (%s,%s)", rows
        )
        insert_ms = (time.perf_counter() - t0) * 1000.0
        latencies = []
        for _ in range(20):
            q0 = time.perf_counter()
            cur.execute(
                "SELECT category_id FROM taksitlio_pgvector_bench100 "
                "ORDER BY embedding <-> '[1,0,0,0,0,0,0,0]' LIMIT 5"
            )
            cur.fetchall()
            latencies.append((time.perf_counter() - q0) * 1000.0)
        assert insert_ms >= 0
        assert latencies
        cur.execute("DROP TABLE taksitlio_pgvector_bench100")
