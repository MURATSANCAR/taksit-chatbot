"""Unrestricted catalog fallback must never activate for INTERNAL prefer_search_ready."""

from __future__ import annotations

import asyncio
from typing import Any

from taksitlio.search_sessions.catalog_pool import refresh_orchestrator_from_catalog
from taksitlio.search_sessions.orchestrator import SearchOrchestrator
from taksitlio.search_sessions.repository import InMemorySearchSessionRepository


class _FakeCatalog:
    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("catalog.search must not run for prefer_search_ready empty pool")


class _EmptyReadyPool:
    def acquire(self) -> Any:
        return self

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def test_internal_empty_pool_does_not_fall_back_to_catalog(monkeypatch: Any) -> None:
    orch = SearchOrchestrator(repo=InMemorySearchSessionRepository())

    async def boom_catalog(*_a: Any, **_k: Any) -> list[Any]:
        raise AssertionError(
            "unrestricted catalog load must not run for prefer_search_ready"
        )

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool.load_search_candidates_from_catalog",
        boom_catalog,
    )

    n = asyncio.run(
        refresh_orchestrator_from_catalog(
            orch,
            catalog=_FakeCatalog(),  # type: ignore[arg-type]
            utterance="samsung telefon",
            pg_pool=_EmptyReadyPool(),
            prefer_search_ready=True,
            limit=50,
        )
    )
    assert n == 0
    assert orch.product_pool == []


def test_internal_category_no_result_stays_empty(monkeypatch: Any) -> None:
    orch = SearchOrchestrator(repo=InMemorySearchSessionRepository())

    async def empty_ready(*_a: Any, **_k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool._pool_rows_from_search_ready",
        empty_ready,
    )

    async def boom_catalog(*_a: Any, **_k: Any) -> list[Any]:
        raise AssertionError("must not unrestricted-fallback")

    monkeypatch.setattr(
        "taksitlio.search_sessions.catalog_pool.load_search_candidates_from_catalog",
        boom_catalog,
    )

    n = asyncio.run(
        refresh_orchestrator_from_catalog(
            orch,
            catalog=_FakeCatalog(),  # type: ignore[arg-type]
            utterance="xyzzy-no-such-product-qqq",
            pg_pool=object(),
            prefer_search_ready=True,
        )
    )
    assert n == 0
