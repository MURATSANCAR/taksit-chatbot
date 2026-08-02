"""Unrestricted catalog fallback must never activate for INTERNAL prefer_search_ready."""

from __future__ import annotations

import pytest

from taksitlio.search_sessions.catalog_pool import hydrate_orchestrator_catalog
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.repository import InMemorySearchSessionRepository


class _EmptyReadyPool:
    async def acquire(self):  # noqa: ANN001
        return self

    async def __aenter__(self):  # noqa: ANN001
        return self

    async def __aexit__(self, *args):  # noqa: ANN001
        return False

    async def fetch(self, *args, **kwargs):  # noqa: ANN001
        return []


@pytest.mark.asyncio
async def test_internal_empty_pool_does_not_fall_back_to_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = SearchOrchestrator(repo=InMemorySearchSessionRepository())
    # Seed a non-cohort product that would leak if unrestricted fallback ran
    orch.product_pool = []

    async def boom_catalog(*_a, **_k):  # noqa: ANN001
        raise AssertionError("unrestricted catalog load must not run for prefer_search_ready")

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool.load_search_candidates_from_catalog",
        boom_catalog,
    )

    n = await hydrate_orchestrator_catalog(
        orch,
        utterance="samsung telefon",
        pg_pool=_EmptyReadyPool(),
        prefer_search_ready=True,
        limit=50,
    )
    assert n == 0
    assert orch.product_pool == []


@pytest.mark.asyncio
async def test_internal_category_no_result_stays_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = SearchOrchestrator(repo=InMemorySearchSessionRepository())

    async def empty_ready(*_a, **_k):  # noqa: ANN001
        return []

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool._pool_rows_from_search_ready",
        empty_ready,
    )

    async def boom_catalog(*_a, **_k):  # noqa: ANN001
        raise AssertionError("must not unrestricted-fallback")

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool.load_search_candidates_from_catalog",
        boom_catalog,
    )

    n = await hydrate_orchestrator_catalog(
        orch,
        utterance="xyzzy-no-such-product-qqq",
        pg_pool=object(),
        prefer_search_ready=True,
    )
    assert n == 0
