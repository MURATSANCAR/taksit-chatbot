#!/usr/bin/env python3
"""Run guest intent-router suite (1000 cases) — no server required.

Evaluates deterministic routing (+ FAQ key, complex constraints presence).
Exit code 1 if accuracy below threshold.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taksitlio.guest.intent_router import route_intent, GuestIntent  # noqa: E402
from taksitlio.guest.complex_constraints import extract_complex_constraints  # noqa: E402
from taksitlio.guest.faq import answer_faq  # noqa: E402

CASES = ROOT / "tests" / "golden" / "guest_suite" / "cases.jsonl"
REPORT = ROOT / "tests" / "golden" / "guest_suite" / "report.json"

# Acceptable alternate intents for soft grading
SOFT = {
    "COMPLEX_NEED": {"NEEDS_ANALYSIS", "COMPLEX_NEED"},
    "NEEDS_ANALYSIS": {"NEEDS_ANALYSIS", "COMPLEX_NEED"},
}


def main() -> int:
    if not CASES.exists():
        print(f"Missing cases: {CASES}. Run generate_guest_suite.py first.")
        return 2

    total = 0
    hard_ok = 0
    soft_ok = 0
    by_tier: dict[str, Counter] = defaultdict(Counter)
    failures: list[dict] = []

    with CASES.open(encoding="utf-8") as f:
        for line in f:
            case = json.loads(line)
            total += 1
            phase = case.get("phase_hint")
            d = route_intent(case["utterance"], phase=phase)
            expected = case["expect_intent"]
            got = d.intent.value

            tier = case["tier"]
            if got == expected:
                hard_ok += 1
                soft_ok += 1
                by_tier[tier]["ok"] += 1
            elif got in SOFT.get(expected, set()):
                soft_ok += 1
                by_tier[tier]["soft"] += 1
            else:
                by_tier[tier]["fail"] += 1
                if len(failures) < 40:
                    failures.append(
                        {
                            "id": case["id"],
                            "tier": tier,
                            "utterance": case["utterance"],
                            "expected": expected,
                            "got": got,
                            "reason": d.reason,
                        }
                    )

            # FAQ key check
            if expected == "FAQ" and case.get("expect_faq_key"):
                if d.faq_key != case["expect_faq_key"]:
                    by_tier[tier]["faq_key_mismatch"] += 1

            # Complex constraints smoke
            if expected == "COMPLEX_NEED":
                c = extract_complex_constraints(case["utterance"])
                if not c.category_hints and not c.budget_value:
                    by_tier[tier]["constraint_empty"] += 1

    hard_acc = hard_ok / total if total else 0
    soft_acc = soft_ok / total if total else 0

    report = {
        "total": total,
        "hard_ok": hard_ok,
        "soft_ok": soft_ok,
        "hard_accuracy": round(hard_acc, 4),
        "soft_accuracy": round(soft_acc, 4),
        "by_tier": {k: dict(v) for k, v in sorted(by_tier.items())},
        "failures_sample": failures,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Total: {total}")
    print(f"Hard accuracy: {hard_acc:.1%} ({hard_ok}/{total})")
    print(f"Soft accuracy: {soft_acc:.1%} ({soft_ok}/{total})")
    print(f"Report → {REPORT}")
    if failures:
        print("Sample failures:")
        for f in failures[:10]:
            print(f"  {f['id']} [{f['tier']}] expected={f['expected']} got={f['got']} | {f['utterance'][:60]}")

    # Thresholds: hard >= 0.85, soft >= 0.92
    if hard_acc < 0.85 or soft_acc < 0.92:
        print("BELOW THRESHOLD")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
