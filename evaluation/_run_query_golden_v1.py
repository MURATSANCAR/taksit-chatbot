#!/usr/bin/env python3
"""Run ADR-013 Query Golden Set v1 lanes.

Usage:
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane parser
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane e2e
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LANES = (
    "parser",
    "retrieval",
    "finance",
    "bank_mapping",
    "clarification",
    "shadow",
    "product_data",
    "perf",
    "chaos",
    "e2e",
)


def _default_dataset() -> Path:
    return ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"


def _default_gates() -> Path:
    return ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"


def _default_out(lane: str) -> Path:
    return ROOT / "evaluation" / "reports" / f"query-golden-v1-{lane}.json"


def _write(report: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_parser(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import (
        load_query_golden_cases,
        summarize_buckets,
    )
    from taksitlio.evaluation.query_golden.metrics import (
        evaluate_parser_gate,
        evaluate_parser_lane,
    )

    cases = load_query_golden_cases(dataset)
    catalog = build_query_golden_test_catalog()
    metrics, details = evaluate_parser_lane(cases, catalog=catalog)
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    draft_count = sum(1 for c in cases if c.annotation.get("status") == "DRAFT")
    gate = evaluate_parser_gate(metrics, gates, draft_count=draft_count)
    _write(
        {
            "report_id": "query-golden-v1-parser",
            "lane": "parser",
            "dataset": str(dataset.relative_to(ROOT)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "bucket_counts": summarize_buckets(cases),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details[:20],
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "parser", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_retrieval(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.retrieval import (
        evaluate_retrieval_gate,
        evaluate_retrieval_lane,
    )

    cases = load_query_golden_cases(dataset)
    metrics, details = evaluate_retrieval_lane(
        cases, catalog=build_query_golden_test_catalog()
    )
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_retrieval_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-retrieval",
            "lane": "retrieval",
            "environment": "TEST",
            "dataset": str(dataset.relative_to(ROOT)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details[:20],
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "retrieval", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_finance(gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.finance import (
        evaluate_finance_gate,
        evaluate_finance_lane,
    )

    metrics, details = evaluate_finance_lane()
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_finance_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-finance",
            "lane": "finance",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details,
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "finance", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_bank_mapping(gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.bank_mapping import (
        evaluate_bank_mapping_gate,
        evaluate_bank_mapping_lane,
    )

    metrics, details = evaluate_bank_mapping_lane()
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_bank_mapping_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-bank-mapping",
            "lane": "bank_mapping",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details,
            "detail_count": len(details),
        },
        out,
    )
    print(
        json.dumps(
            {"lane": "bank_mapping", "gate": gate["status"], "out": str(out)},
            ensure_ascii=False,
        )
    )
    return 1 if gate["status"] == "FAIL" else 0


def run_clarification(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.clarification import (
        evaluate_clarification_gate,
        evaluate_clarification_lane,
    )
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases

    cases = load_query_golden_cases(dataset)
    draft_heavy = any(c.annotation.get("status") == "DRAFT" for c in cases)
    metrics, details = evaluate_clarification_lane(
        cases, catalog=build_query_golden_test_catalog()
    )
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_clarification_gate(metrics, gates, draft_heavy=draft_heavy)
    _write(
        {
            "report_id": "query-golden-v1-clarification",
            "lane": "clarification",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details[:20],
            "detail_count": len(details),
        },
        out,
    )
    print(
        json.dumps(
            {"lane": "clarification", "gate": gate["status"], "out": str(out)},
            ensure_ascii=False,
        )
    )
    return 1 if gate["status"] == "FAIL" else 0


def run_shadow(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.shadow import (
        evaluate_shadow_gate,
        evaluate_shadow_lane,
    )

    cases = load_query_golden_cases(dataset)
    metrics, details = evaluate_shadow_lane(
        cases, catalog=build_query_golden_test_catalog()
    )
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_shadow_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-shadow",
            "lane": "shadow",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": [d for d in details if d.get("diffs")][:30],
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "shadow", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_product_data(gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.product_data import (
        evaluate_product_data_gate,
        evaluate_product_data_lane,
    )

    metrics, details = evaluate_product_data_lane()
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_product_data_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-product-data",
            "lane": "product_data",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details[:15],
            "detail_count": len(details),
            "notes": "100 SKU/probes: api_feed / html_jsonld / store_only — staging bind open",
        },
        out,
    )
    print(
        json.dumps(
            {"lane": "product_data", "gate": gate["status"], "out": str(out)},
            ensure_ascii=False,
        )
    )
    return 1 if gate["status"] == "FAIL" else 0


def run_perf(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.loader import load_query_golden_cases
    from taksitlio.evaluation.query_golden.perf import evaluate_perf_gate, evaluate_perf_lane

    cases = load_query_golden_cases(dataset)
    metrics, details = evaluate_perf_lane(
        cases, catalog=build_query_golden_test_catalog(), warm_iters=1
    )
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_perf_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-perf",
            "lane": "perf",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details[:10],
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "perf", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_chaos(dataset: Path, gates_path: Path, out: Path) -> int:
    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.evaluation.query_golden.chaos import evaluate_chaos_gate, evaluate_chaos_lane

    metrics, details = evaluate_chaos_lane(catalog=build_query_golden_test_catalog())
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_chaos_gate(metrics, gates)
    _write(
        {
            "report_id": "query-golden-v1-chaos",
            "lane": "chaos",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics.to_dict(),
            "gate": gate,
            "detail_sample": details,
            "detail_count": len(details),
        },
        out,
    )
    print(json.dumps({"lane": "chaos", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_e2e(dataset: Path, gates_path: Path, out: Path) -> int:
    """Compose all TEST lanes; FAIL if any lane FAIL."""

    parts = [
        ("parser", lambda p: run_parser(dataset, gates_path, p)),
        ("retrieval", lambda p: run_retrieval(dataset, gates_path, p)),
        ("finance", lambda p: run_finance(gates_path, p)),
        ("bank_mapping", lambda p: run_bank_mapping(gates_path, p)),
        ("clarification", lambda p: run_clarification(dataset, gates_path, p)),
        ("shadow", lambda p: run_shadow(dataset, gates_path, p)),
        ("product_data", lambda p: run_product_data(gates_path, p)),
        ("perf", lambda p: run_perf(dataset, gates_path, p)),
        ("chaos", lambda p: run_chaos(dataset, gates_path, p)),
    ]
    statuses: dict[str, str] = {}
    for name, fn in parts:
        path = out.with_name(f"query-golden-v1-e2e-{name}.json")
        fn(path)
        statuses[name] = json.loads(path.read_text(encoding="utf-8"))["gate"]["status"]

    overall = "FAIL" if "FAIL" in statuses.values() else (
        "BOOTSTRAP" if "BOOTSTRAP" in statuses.values() else "PASS"
    )
    _write(
        {
            "report_id": "query-golden-v1-e2e",
            "lane": "e2e",
            "environment": "TEST",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lane_statuses": statuses,
            "gate": {"status": overall},
            "notes": "TEST composition; staging real-merchant E2E remains open",
        },
        out,
    )
    print(json.dumps({"lane": "e2e", "gate": overall, "out": str(out)}, ensure_ascii=False))
    return 1 if overall == "FAIL" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-013 Query Golden v1 lane runner")
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--dataset", type=Path, default=_default_dataset())
    parser.add_argument("--gates", type=Path, default=_default_gates())
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or _default_out(args.lane)

    dispatch = {
        "parser": lambda: run_parser(args.dataset, args.gates, out),
        "retrieval": lambda: run_retrieval(args.dataset, args.gates, out),
        "finance": lambda: run_finance(args.gates, out),
        "bank_mapping": lambda: run_bank_mapping(args.gates, out),
        "clarification": lambda: run_clarification(args.dataset, args.gates, out),
        "shadow": lambda: run_shadow(args.dataset, args.gates, out),
        "product_data": lambda: run_product_data(args.gates, out),
        "perf": lambda: run_perf(args.dataset, args.gates, out),
        "chaos": lambda: run_chaos(args.dataset, args.gates, out),
        "e2e": lambda: run_e2e(args.dataset, args.gates, out),
    }
    return dispatch[args.lane]()


if __name__ == "__main__":
    sys.exit(main())
