"""Fixture catalog builds via prepare_embed_and_publish and cleans up."""

from __future__ import annotations

import pytest

from taksitlio.evaluation.fixture_catalog import (
    all_fixture_keys,
    build_fixture_catalog,
    dispose_fixture_catalog,
)


@pytest.mark.asyncio
async def test_fixture_catalog_publishes_and_exposes_uuid_map():
    handle = await build_fixture_catalog()
    try:
        keys = all_fixture_keys()
        assert set(handle.key_to_uuid.keys()) == set(keys)
        for key, uuid in handle.key_to_uuid.items():
            assert uuid  # non-empty
            assert handle.reverse(uuid) == key
        snapshot = await handle.service.get_published_snapshot(handle.catalog_id)
        assert snapshot is not None
        assert snapshot.revision == handle.revision
        assert len(snapshot.nodes) == len(keys)
    finally:
        await dispose_fixture_catalog(handle)


@pytest.mark.asyncio
async def test_dispose_fixture_catalog_archives_all_entries():
    handle = await build_fixture_catalog()
    await dispose_fixture_catalog(handle)
    for cat_id in handle.uuid_to_key:
        cat = await handle.service._repo.get_category(cat_id)
        assert cat is not None
        assert cat.status.value == "ARCHIVED"
