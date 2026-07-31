"""Controlled product data gate (ADR-013 L3) — TEST fixture golden."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def default_product_golden_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "datasets"
        / "query_golden"
        / "v1"
        / "product_golden.v1.jsonl"
    )


def load_product_golden(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or default_product_golden_path()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _compare_product(truth: Mapping[str, Any], system: Mapping[str, Any]) -> dict[str, int]:
    defects = {
        "wrong_name": 0,
        "wrong_price": 0,
        "wrong_url": 0,
        "wrong_variant": 0,
        "broken_image": 0,
    }
    if str(truth.get("display_name") or "") != str(system.get("display_name") or ""):
        defects["wrong_name"] = 1
    if abs(float(truth.get("price") or 0) - float(system.get("price") or 0)) > 0.01:
        defects["wrong_price"] = 1
    if str(truth.get("product_url") or "") != str(system.get("product_url") or ""):
        defects["wrong_url"] = 1
    t_attrs = truth.get("attributes") or {}
    s_attrs = system.get("attributes") or {}
    if t_attrs.get("ram_gb") is not None and s_attrs.get("ram_gb") != t_attrs.get("ram_gb"):
        defects["wrong_variant"] = 1
    img = str(system.get("primary_image_url") or "")
    if not img or not img.startswith("http"):
        defects["broken_image"] = 1
    return defects


@dataclass
class ProductDataMetrics:
    total_rows: int = 0
    by_merchant_type: dict[str, int] = field(default_factory=dict)
    clean_rows: int = 0
    known_defect_rows: int = 0
    wrong_name: int = 0
    wrong_price: int = 0
    wrong_url: int = 0
    wrong_variant: int = 0
    broken_image_on_clean: int = 0
    primary_image_coverage: Optional[float] = None
    fresh_price_coverage: Optional[float] = None
    invented_products_on_store_only: int = 0
    defect_detection_misses: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_product_data_lane(
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[ProductDataMetrics, list[dict[str, Any]]]:
    data = list(rows) if rows is not None else load_product_golden()
    details: list[dict[str, Any]] = []
    by_type: dict[str, int] = {}
    clean = known = 0
    wn = wp = wu = wv = bi = 0
    img_ok = img_tot = 0
    fresh_ok = fresh_tot = 0
    invented = 0
    miss = 0

    for row in data:
        mtype = str(row.get("merchant_type") or "unknown")
        by_type[mtype] = by_type.get(mtype, 0) + 1
        exp = row.get("expected_defects") or {}
        is_known_bad = any(int(exp.get(k) or 0) for k in (
            "wrong_name", "wrong_price", "wrong_url", "wrong_variant", "broken_image"
        ))

        if mtype == "store_only":
            sys = row.get("system_record") or {}
            invented += int(sys.get("invented_products") or 0)
            if sys.get("product_catalog") is True:
                invented += 1
            details.append({"sku_id": row["sku_id"], "merchant_type": mtype, "ok": invented == 0})
            continue

        truth = row.get("source_of_truth") or {}
        system = row.get("system_record") or {}
        found = _compare_product(truth, system)

        if is_known_bad:
            known += 1
            # Must detect at least the flagged defects
            for key in ("wrong_name", "wrong_price", "wrong_url", "wrong_variant", "broken_image"):
                if int(exp.get(key) or 0) and not found.get(key):
                    miss += 1
        else:
            clean += 1
            wn += found["wrong_name"]
            wp += found["wrong_price"]
            wu += found["wrong_url"]
            wv += found["wrong_variant"]
            bi += found["broken_image"]
            img_tot += 1
            if system.get("primary_image_url") and str(system["primary_image_url"]).startswith("http"):
                img_ok += 1
            fresh_tot += 1
            if system.get("price_freshness", "FRESH") == "FRESH":
                fresh_ok += 1

        details.append(
            {
                "sku_id": row["sku_id"],
                "merchant_type": mtype,
                "known_bad": is_known_bad,
                "found": found,
            }
        )

    metrics = ProductDataMetrics(
        total_rows=len(data),
        by_merchant_type=by_type,
        clean_rows=clean,
        known_defect_rows=known,
        wrong_name=wn,
        wrong_price=wp,
        wrong_url=wu,
        wrong_variant=wv,
        broken_image_on_clean=bi,
        primary_image_coverage=(img_ok / img_tot) if img_tot else None,
        fresh_price_coverage=(fresh_ok / fresh_tot) if fresh_tot else None,
        invented_products_on_store_only=invented,
        defect_detection_misses=miss,
        support={"clean": clean, "known_bad": known, "store_only": by_type.get("store_only", 0)},
    )
    return metrics, details


def evaluate_product_data_gate(
    metrics: ProductDataMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("product_data_gate_thresholds") or {
        "wrong_name": {"max_count": 0},
        "wrong_price": {"max_count": 0},
        "wrong_url": {"max_count": 0},
        "wrong_variant": {"max_count": 0},
        "broken_image_on_clean": {"max_count": 0},
        "invented_products_on_store_only": {"max_count": 0},
        "defect_detection_misses": {"max_count": 0},
        "primary_image_coverage": {"min": 0.95},
        "fresh_price_coverage": {"min": 0.95},
    }
    violations: list[str] = []
    for key in (
        "wrong_name",
        "wrong_price",
        "wrong_url",
        "wrong_variant",
        "broken_image_on_clean",
        "invented_products_on_store_only",
        "defect_detection_misses",
    ):
        rule = thresholds.get(key) or {}
        if "max_count" in rule and int(getattr(metrics, key)) > int(rule["max_count"]):
            violations.append(f"{key}: {getattr(metrics, key)} > {rule['max_count']}")
    for key in ("primary_image_coverage", "fresh_price_coverage"):
        rule = thresholds.get(key) or {}
        val = getattr(metrics, key)
        if "min" in rule and (val is None or float(val) < float(rule["min"])):
            violations.append(f"{key}: {val} < {rule['min']}")
    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "notes": list(violations),
    }
