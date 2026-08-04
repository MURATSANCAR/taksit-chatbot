"""Unit tests for campaign category code → id linking (no invent)."""

from __future__ import annotations

from typing import Any

import pytest

from taksitlio.campaign_catalog.postgres import (
    link_campaign_categories,
    normalize_category_codes,
    resolve_category_ids_by_code,
)


def test_normalize_category_codes_uppercases_and_dedupes() -> None:
    assert normalize_category_codes(["mobile_phone", " TABLET ", "MOBILE_PHONE", ""]) == (
        "MOBILE_PHONE",
        "TABLET",
    )


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        codes = set(args[0])
        return [r for r in self._rows if r["category_code"] in codes]

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql.strip().split("\n", 1)[0], args))
        return "OK"


@pytest.mark.asyncio
async def test_resolve_category_ids_skips_unknown_codes() -> None:
    conn = _FakeConn(
        [
            {"id": 1, "category_code": "MOBILE_PHONE"},
            {"id": 3, "category_code": "TABLET"},
        ]
    )
    ids, missing = await resolve_category_ids_by_code(
        conn, ["MOBILE_PHONE", "NOT_A_REAL_CAT", "tablet"]
    )
    assert ids == (1, 3)
    assert missing == ("NOT_A_REAL_CAT",)


@pytest.mark.asyncio
async def test_link_campaign_categories_replaces_when_non_empty() -> None:
    conn = _FakeConn([])
    n = await link_campaign_categories(conn, campaign_id=9, category_ids=(1, 3))
    assert n == 2
    assert conn.executed[0][0].startswith("DELETE FROM campaign_categories")
    assert sum(1 for sql, _ in conn.executed if sql.startswith("INSERT INTO campaign_categories")) == 2


@pytest.mark.asyncio
async def test_link_campaign_categories_noop_when_empty() -> None:
    conn = _FakeConn([])
    n = await link_campaign_categories(conn, campaign_id=9, category_ids=())
    assert n == 0
    assert conn.executed == []
