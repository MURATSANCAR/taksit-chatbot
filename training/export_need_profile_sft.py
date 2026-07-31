#!/usr/bin/env python3
"""CLI: export NeedProfile SFT JSONL from goldens / HR validation (P17).

Examples:
  python training/export_need_profile_sft.py --source golden --limit 5 --stdout
  python training/export_need_profile_sft.py --source hr-validation --out training/exports/sft.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running without editable install when cwd is repo root.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from taksitlio.training.export_sft import (  # noqa: E402
    iter_golden_sft_rows,
    iter_hr_validation_sft_rows,
    write_jsonl,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export NeedProfile SFT rows")
    parser.add_argument(
        "--source",
        choices=("golden", "hr-validation", "both"),
        default="golden",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument(
        "--include-non-hr",
        action="store_true",
        help="Include non-HUMAN_REVIEWED rows for hr-validation",
    )
    args = parser.parse_args(argv)

    rows: list[dict] = []
    if args.source in {"golden", "both"}:
        rows.extend(list(iter_golden_sft_rows(limit=args.limit)))
    if args.source in {"hr-validation", "both"}:
        lim = args.limit
        if args.source == "both" and args.limit is not None:
            lim = max(0, args.limit - len(rows)) or None
        rows.extend(
            list(
                iter_hr_validation_sft_rows(
                    limit=lim,
                    human_reviewed_only=not args.include_non_hr,
                )
            )
        )

    if args.stdout or args.out is None:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    if args.out is not None:
        n = write_jsonl(rows, args.out)
        print(f"wrote {n} rows → {args.out}", file=sys.stderr)
    print(
        "NOTE: export scaffold only — does not train or claim ADR-009 quality pass.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
