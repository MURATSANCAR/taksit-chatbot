#!/usr/bin/env python3
"""Run ADR-013 Query Golden Set v1 lanes.

Usage:
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane parser
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane retrieval
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane finance
  PYTHONPATH=src python evaluation/_run_query_golden_v1.py --lane e2e
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _default_dataset() -> Path:
    return ROOT / "evaluation" / "datasets" / "query_golden" / "v1" / "query_golden.v1.jsonl"


def _default_gates() -> Path:
    return ROOT / "evaluation" / "config" / "query_golden_gates.v1.json"


def _default_out(lane: str) -> Path:
    return ROOT / "evaluation" / "reports" / f"query-golden-v1-{lane}.json"


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

    report = {
        "report_id": "query-golden-v1-parser",
        "lane": "parser",
        "dataset": str(dataset.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bucket_counts": summarize_buckets(cases),
        "metrics": metrics.to_dict(),
        "gate": gate,
        "detail_sample": details[:20],
        "detail_count": len(details),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    catalog = build_query_golden_test_catalog()
    metrics, details = evaluate_retrieval_lane(cases, catalog=catalog)
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gate = evaluate_retrieval_gate(metrics, gates)
    report = {
        "report_id": "query-golden-v1-retrieval",
        "lane": "retrieval",
        "environment": "TEST",
        "dataset": str(dataset.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics.to_dict(),
        "gate": gate,
        "detail_sample": details[:20],
        "detail_count": len(details),
        "notes": "TEST fixture products only — not staging/production catalog",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    report = {
        "report_id": "query-golden-v1-finance",
        "lane": "finance",
        "environment": "TEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics.to_dict(),
        "gate": gate,
        "detail_sample": details,
        "detail_count": len(details),
        "notes": "TEST eligibility + payment golden scenarios — staging real agreements next",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"lane": "finance", "gate": gate["status"], "out": str(out)}, ensure_ascii=False))
    return 1 if gate["status"] == "FAIL" else 0


def run_e2e(dataset: Path, gates_path: Path, out: Path) -> int:
    """Compose parser + retrieval + finance TEST lanes; fail if any FAIL."""

    parser_out = out.with_name("query-golden-v1-e2e-parser.json")
    retrieval_out = out.with_name("query-golden-v1-e2e-retrieval.json")
    finance_out = out.with_name("query-golden-v1-e2e-finance.json")
    codes = [
        run_parser(dataset, gates_path, parser_out),
        run_retrieval(dataset, gates_path, retrieval_out),
        run_finance(gates_path, finance_out),
    ]
    statuses = []
    for path in (parser_out, retrieval_out, finance_out):
        statuses.append(json.loads(path.read_text(encoding="utf-8"))["gate"]["status"])
    overall = "FAIL" if "FAIL" in statuses else (
        "BOOTSTRAP" if "BOOTSTRAP" in statuses else "PASS"
    )
    report = {
        "report_id": "query-golden-v1-e2e",
        "lane": "e2e",
        "environment": "TEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lane_statuses": {
            "parser": statuses[0],
            "retrieval": statuses[1],
            "finance": statuses[2],
        },
        "gate": {"status": overall},
        "notes": "TEST composition only; staging real-merchant E2E remains open",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"lane": "e2e", "gate": overall, "out": str(out)}, ensure_ascii=False))
    return 1 if overall == "FAIL" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-013 Query Golden v1 lane runner")
    parser.add_argument(
        "--lane",
        choices=("parser", "retrieval", "finance", "e2e"),
        required=True,
    )
    parser.add_argument("--dataset", type=Path, default=_default_dataset())
    parser.add_argument("--gates", type=Path, default=_default_gates())
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or _default_out(args.lane)

    if args.lane == "parser":
        return run_parser(args.dataset, args.gates, out)
    if args.lane == "retrieval":
        return run_retrieval(args.dataset, args.gates, out)
    if args.lane == "finance":
        return run_finance(args.gates, out)
    if args.lane == "e2e":
        return run_e2e(args.dataset, args.gates, out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
