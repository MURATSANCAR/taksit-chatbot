"""Crawl registry coverage + fixture product dry-run (ADR-010 StormCrawler bridge)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from taksitlio.ingestion.binding import SourceBinding, build_default_registry
from taksitlio.ingestion.runner import run_ingestion_dry

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "crawler" / "ops" / "crawl-registry.yaml"
PRODUCT_FIXTURE = ROOT / "crawler" / "feeds" / "fixtures" / "src-m-teknosa.json"


def test_crawl_registry_has_at_least_20_merchants_and_banks() -> None:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    merchants = data["merchants"]
    banks = data["banks"]
    assert len(merchants) >= 20
    assert len(banks) >= 15
    # Opaque codes only — no empty seed lists
    for m in merchants:
        assert m["merchant_code"]
        assert m["source_code"]
        assert m["seed_urls"]
        assert m["adapter_code"] == "generic.json_feed.v1"
    for b in banks:
        assert b["institution_code"]
        assert b["source_code"]
        assert b["seed_urls"]
        assert b["adapter_code"] == "generic.campaign_feed.v1"


def test_seed_plan_metadata_keys() -> None:
    # Import without installing scripts package
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bind_crawl_feeds", ROOT / "scripts" / "bind_crawl_feeds.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    registry = mod.load_registry(REGISTRY)
    seeds = mod.inject_seeds_plan(registry)
    assert len(seeds) >= 20 + 15
    channels = {s["metadata"]["taksitlio.channel"] for s in seeds}
    assert channels == {"PRODUCT", "CAMPAIGN"}


@pytest.mark.asyncio
async def test_product_fixture_ingestion_dry() -> None:
    assert PRODUCT_FIXTURE.exists()
    binding = SourceBinding(
        source_code="src-m-teknosa",
        adapter_code="generic.json_feed.v1",
        merchant_id="ops-local",
        config={"feed_path": str(PRODUCT_FIXTURE)},
    )
    result = await run_ingestion_dry(binding, registry=build_default_registry())
    assert result.discovered == 1
    assert result.succeeded == 1
    assert result.items[0].product is not None
    assert result.items[0].offers[0].current_price == 42999.0


def test_fixture_json_shapes() -> None:
    prod = json.loads(PRODUCT_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(prod["products"], list)
    camp_path = ROOT / "crawler" / "feeds" / "fixtures" / "src-b-fibabanka.json"
    camp = json.loads(camp_path.read_text(encoding="utf-8"))
    assert isinstance(camp["campaigns"], list)
