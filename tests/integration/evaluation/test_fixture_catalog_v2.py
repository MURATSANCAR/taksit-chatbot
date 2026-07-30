"""ADR-006 §J: v2 fixture catalog builds with parent-child hierarchy."""

from __future__ import annotations

from pathlib import Path

import pytest

from taksitlio.evaluation.fixture_catalog import (
    build_fixture_catalog,
    dispose_fixture_catalog,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_V2_PATH = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v2.json"
)


@pytest.mark.asyncio
async def test_v2_fixture_publishes_with_parent_child_relationships() -> None:
    if not FIXTURE_V2_PATH.exists():
        pytest.skip("v2 fixture catalog missing")

    handle = await build_fixture_catalog(fixture_path=FIXTURE_V2_PATH)
    try:
        snapshot = await handle.service.get_published_snapshot(handle.catalog_id)
        assert snapshot is not None
        parents = {node.id: node for node in snapshot.nodes}
        # At least one node has an ancestor.
        assert any(node.ancestor_ids for node in snapshot.nodes), (
            "v2 fixture must expose parent-child hierarchy for ADR-006 tests"
        )
        # Ancestor ids must be resolvable in the snapshot.
        for node in snapshot.nodes:
            for ancestor in node.ancestor_ids:
                assert ancestor in parents
    finally:
        await dispose_fixture_catalog(handle)
