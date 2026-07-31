"""Retrieval lane for Query Golden — TEST product fixture filters (ADR-013 L3-lite)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.query_golden.loader import QueryGoldenCase
from taksitlio.evaluation.query_golden.metrics import _names_match, predict_case
from taksitlio.query_understanding import CatalogHints
from taksitlio.semantic_matching.turkish_normalize import normalize_turkish


def _products_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "evaluation"
        / "fixtures"
        / "query_golden_test_products.json"
    )


@lru_cache(maxsize=1)
def load_test_products() -> tuple[dict[str, Any], ...]:
    data = json.loads(_products_path().read_text(encoding="utf-8"))
    return tuple(data.get("products") or [])


def _norm(s: str) -> str:
    return normalize_turkish(s).value


def filter_products_for_case(
    products: Sequence[Mapping[str, Any]],
    *,
    merchant_display: Optional[str],
    category_display: Optional[str],
    max_price: Optional[float],
    negative_categories: Sequence[str],
    ram_min: Optional[float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in products:
        if merchant_display:
            if not _names_match(str(p.get("merchant_display_name") or ""), merchant_display):
                continue
        if category_display:
            if not _names_match(str(p.get("category_name") or ""), category_display):
                continue
        cat = str(p.get("category_name") or "")
        if any(_names_match(cat, n) for n in negative_categories):
            continue
        if max_price is not None and float(p.get("price") or 0) > float(max_price):
            continue
        if ram_min is not None:
            attrs = p.get("attributes") or {}
            ram = attrs.get("ram_gb")
            if ram is None or float(ram) < float(ram_min):
                continue
        out.append(dict(p))
    return out


@dataclass
class RetrievalLaneMetrics:
    case_count: int = 0
    scored_cases: int = 0
    budget_filter_violations: int = 0
    merchant_filter_violations: int = 0
    negation_leak_count: int = 0
    ram_filter_violations: int = 0
    stale_or_unknown_in_results: int = 0
    hit_when_expected: Optional[float] = None
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_retrieval_lane(
    cases: Sequence[QueryGoldenCase],
    *,
    catalog: CatalogHints,
    products: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[RetrievalLaneMetrics, list[dict[str, Any]]]:
    pool = list(products) if products is not None else list(load_test_products())
    details: list[dict[str, Any]] = []
    scored = 0
    budget_v = merchant_v = neg_v = ram_v = stale_v = 0
    hit_ok = hit_tot = 0

    for case in cases:
        exp = case.expected
        if exp.get("route") not in (None, "FAST"):
            continue
        if exp.get("llm_required"):
            continue
        if (exp.get("clarification") or {}).get("should_ask"):
            continue

        merchant = (exp.get("merchant") or {}).get("display_name")
        category = (exp.get("category") or {}).get("display_name")
        if not merchant and not category:
            continue

        budget = exp.get("budget") or {}
        max_price = budget.get("maximum")
        if max_price is None:
            max_price = budget.get("value")
        neg = list((exp.get("exclusions") or {}).get("negative_categories") or [])
        ram_min = None
        for attr in exp.get("attributes") or []:
            if attr.get("attribute_id") == "ram_gb" and attr.get("operator") in ("GTE", "EQ"):
                ram_min = float(attr["value"])

        # Prefer parse-resolved merchant/category when present
        pred = predict_case(case, catalog=catalog)
        parse = pred.get("parse") or {}
        if parse.get("merchant") and parse["merchant"].get("display_name"):
            merchant = parse["merchant"]["display_name"]
        pos = parse.get("positive_categories") or []
        if pos and pos[0].get("display_name"):
            category = pos[0]["display_name"]
        if parse.get("budget"):
            max_price = parse["budget"].get("maximum") or parse["budget"].get("value") or max_price
        if parse.get("negative_categories"):
            neg = [c.get("display_name") for c in parse["negative_categories"] if c.get("display_name")]

        hits = filter_products_for_case(
            pool,
            merchant_display=merchant,
            category_display=category,
            max_price=float(max_price) if max_price is not None else None,
            negative_categories=neg,
            ram_min=ram_min,
        )
        scored += 1

        # Integrity checks on filtered set
        for h in hits:
            if max_price is not None and float(h["price"]) > float(max_price) + 1e-6:
                budget_v += 1
            if merchant and not _names_match(str(h.get("merchant_display_name") or ""), str(merchant)):
                merchant_v += 1
            if any(_names_match(str(h.get("category_name") or ""), n) for n in neg):
                neg_v += 1
            if ram_min is not None:
                ram = (h.get("attributes") or {}).get("ram_gb")
                if ram is None or float(ram) < ram_min:
                    ram_v += 1
            if h.get("price_freshness") == "STALE" or h.get("stock_status") == "UNKNOWN":
                # Fixture includes stale row — must not pass filter when freshness gated.
                # Current filter does not drop STALE; count for awareness only if returned.
                stale_v += 1

        # Expected hit: fixture has at least one FRESH/AVAILABLE match under constraints
        expected_pool = [
            p
            for p in pool
            if p.get("price_freshness") == "FRESH" and p.get("stock_status") == "AVAILABLE"
        ]
        expected_hits = filter_products_for_case(
            expected_pool,
            merchant_display=merchant,
            category_display=category,
            max_price=float(max_price) if max_price is not None else None,
            negative_categories=neg,
            ram_min=ram_min,
        )
        if expected_hits:
            hit_tot += 1
            fresh_hits = [
                h
                for h in hits
                if h.get("price_freshness") == "FRESH" and h.get("stock_status") == "AVAILABLE"
            ]
            hit_ok += int(bool(fresh_hits))

        details.append(
            {
                "case_id": case.case_id,
                "hit_count": len(hits),
                "merchant": merchant,
                "category": category,
                "max_price": max_price,
            }
        )

    metrics = RetrievalLaneMetrics(
        case_count=len(cases),
        scored_cases=scored,
        budget_filter_violations=budget_v,
        merchant_filter_violations=merchant_v,
        negation_leak_count=neg_v,
        ram_filter_violations=ram_v,
        stale_or_unknown_in_results=stale_v,
        hit_when_expected=(hit_ok / hit_tot) if hit_tot else None,
        support={"scored": scored, "expected_hit": hit_tot},
    )
    return metrics, details


def evaluate_retrieval_gate(
    metrics: RetrievalLaneMetrics,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = gates.get("retrieval_gate_thresholds") or {
        "budget_filter_violations": {"max_count": 0},
        "merchant_filter_violations": {"max_count": 0},
        "negation_leak_count": {"max_count": 0},
        "ram_filter_violations": {"max_count": 0},
    }
    violations: list[str] = []
    for key in (
        "budget_filter_violations",
        "merchant_filter_violations",
        "negation_leak_count",
        "ram_filter_violations",
    ):
        rule = thresholds.get(key) or {}
        if "max_count" in rule and int(getattr(metrics, key)) > int(rule["max_count"]):
            violations.append(f"{key}: {getattr(metrics, key)} > {rule['max_count']}")

    status = "FAIL" if violations else "PASS"
    return {"status": status, "violations": violations, "notes": list(violations)}
